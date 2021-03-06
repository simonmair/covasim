'''
Defines the Person class and functions associated with making people.
'''

#%% Imports
import numpy as np
import sciris as sc
from collections import defaultdict
from . import utils as cvu
from . import defaults as cvd
from . import base as cvb
from . import plotting as cvplt


__all__ = ['People']

class People(cvb.BasePeople):
    '''
    A class to perform all the operations on the people. This class is usually
    not invoked directly, but instead is created automatically by the sim. Most
    initialization happens in BasePeople. The only required input argument is the
    population size, but typically the full parameters dictionary will get passed
    instead since it will be needed before the People object is initialized.

    Args:
        pars (dict): the sim parameters, e.g. sim.pars -- alternatively, if a number, interpreted as pop_size
        strict (bool): whether or not to only create keys that are already in self.meta.person; otherwise, let any key be set
        kwargs (dict): the actual data, e.g. from a popdict, being specified

    ::Examples::

        ppl1 = cv.People(2000)

        sim = cv.Sim()
        ppl2 = cv.People(sim.pars)
    '''

    def __init__(self, pars, strict=True, **kwargs):
        super().__init__(pars)

        # Handle contacts, if supplied (note: they usually are)
        if 'contacts' in kwargs:
            self.add_contacts(kwargs.pop('contacts'))

        # Handle all other values, e.g. age
        for key,value in kwargs.items():
            if strict:
                self.set(key, value)
            else:
                self[key] = value

        self._pending_quarantine = defaultdict(list)  # Internal cache to record people that need to be quarantined on each timestep {t:(person, quarantine_end_day)}
        return


    def initialize(self):
        ''' Perform initializations '''
        self.set_prognoses()
        self.validate()
        self.initialized = True
        return


    def set_prognoses(self):
        '''
        Set the prognoses for each person based on age during initialization. Need
        to reset the seed because viral loads are drawn stochastically.
        '''

        pars = self.pars # Shorten
        if 'prognoses' not in pars:
            errormsg = 'This people object does not have the required parameters ("prognoses"). Create a sim (or parameters), then do e.g. people.set_pars(sim.pars).'
            raise sc.KeyNotFoundError(errormsg)

        def find_cutoff(age_cutoffs, age):
            '''
            Find which age bin each person belongs to -- e.g. with standard
            age bins 0, 10, 20, etc., ages [5, 12, 4, 58] would be mapped to
            indices [0, 1, 0, 5]. Age bins are not guaranteed to be uniform
            width, which is why this can't be done as an array operation.
            '''
            return np.nonzero(age_cutoffs <= age)[0][-1]  # Index of the age bin to use

        cvu.set_seed(pars['rand_seed'])

        progs = pars['prognoses'] # Shorten the name
        inds = np.fromiter((find_cutoff(progs['age_cutoffs'], this_age) for this_age in self.age), dtype=cvd.default_int, count=len(self)) # Convert ages to indices
        self.symp_prob[:]   = progs['symp_probs'][inds] # Probability of developing symptoms
        self.severe_prob[:] = progs['severe_probs'][inds]*progs['comorbidities'][inds] # Severe disease probability is modified by comorbidities
        self.crit_prob[:]   = progs['crit_probs'][inds] # Probability of developing critical disease
        self.death_prob[:]  = progs['death_probs'][inds] # Probability of death
        self.rel_sus[:]     = progs['sus_ORs'][inds] # Default susceptibilities
        self.rel_trans[:]   = progs['trans_ORs'][inds]*cvu.sample(**self.pars['beta_dist'], size=len(inds)) # Default transmissibilities, with viral load drawn from a distribution

        return


    def update_states_pre(self, t):
        ''' Perform all state updates at the current timestep '''

        # Initialize
        self.t = t
        self.is_exp = self.true('exposed') # For storing the interim values since used in every subsequent calculation

        # Perform updates
        self.flows  = {key:0 for key in cvd.new_result_flows}
        self.flows['new_infectious']  += self.check_infectious() # For people who are exposed and not infectious, check if they begin being infectious
        self.flows['new_symptomatic'] += self.check_symptomatic()
        self.flows['new_severe']      += self.check_severe()
        self.flows['new_critical']    += self.check_critical()
        self.flows['new_deaths']      += self.check_death()
        self.flows['new_recoveries']  += self.check_recovery()

        return

    def update_states_post(self):
        ''' Perform post-timestep updates '''
        self.flows['new_diagnoses']   += self.check_diagnosed()
        self.flows['new_quarantined'] += self.check_quar()
        del self.is_exp  # Tidy up

        return


    def update_contacts(self):
        ''' Refresh dynamic contacts, e.g. community '''

        # Figure out if anything needs to be done -- e.g. {'h':False, 'c':True}
        dynam_keys = [lkey for lkey,is_dynam in self.pars['dynam_layer'].items() if is_dynam]

        # Loop over dynamic keys
        for lkey in dynam_keys:
            # Remove existing contacts
            self.contacts.pop(lkey)

            # Choose how many contacts to make
            pop_size   = len(self)
            n_contacts = self.pars['contacts'][lkey]
            n_new = int(n_contacts*pop_size/2) # Since these get looped over in both directions later

            # Create the contacts
            new_contacts = {} # Initialize
            new_contacts['p1']   = np.array(cvu.choose_r(max_n=pop_size, n=n_new), dtype=cvd.default_int) # Choose with replacement
            new_contacts['p2']   = np.array(cvu.choose_r(max_n=pop_size, n=n_new), dtype=cvd.default_int)
            new_contacts['beta'] = np.ones(n_new, dtype=cvd.default_float)

            # Add to contacts
            self.add_contacts(new_contacts, lkey=lkey)
            self.contacts[lkey].validate()

        return self.contacts


    #%% Methods for updating state

    def check_inds(self, current, date, filter_inds=None):
        ''' Return indices for which the current state is false and which meet the date criterion '''
        if filter_inds is None:
            not_current = cvu.false(current)
        else:
            not_current = cvu.ifalsei(current, filter_inds)
        has_date = cvu.idefinedi(date, not_current)
        inds     = cvu.itrue(self.t >= date[has_date], has_date)
        return inds


    def check_infectious(self):
        ''' Check if they become infectious '''
        inds = self.check_inds(self.infectious, self.date_infectious, filter_inds=self.is_exp)
        self.infectious[inds] = True
        return len(inds)


    def check_symptomatic(self):
        ''' Check for new progressions to symptomatic '''
        inds = self.check_inds(self.symptomatic, self.date_symptomatic, filter_inds=self.is_exp)
        self.symptomatic[inds] = True
        return len(inds)


    def check_severe(self):
        ''' Check for new progressions to severe '''
        inds = self.check_inds(self.severe, self.date_severe, filter_inds=self.is_exp)
        self.severe[inds] = True
        return len(inds)


    def check_critical(self):
        ''' Check for new progressions to critical '''
        inds = self.check_inds(self.critical, self.date_critical, filter_inds=self.is_exp)
        self.critical[inds] = True
        return len(inds)


    def check_recovery(self):
        ''' Check for recovery '''
        inds = self.check_inds(self.recovered, self.date_recovered, filter_inds=self.is_exp)
        self.exposed[inds]     = False
        self.infectious[inds]  = False
        self.symptomatic[inds] = False
        self.severe[inds]      = False
        self.critical[inds]    = False
        self.recovered[inds]   = True
        return len(inds)


    def check_death(self):
        ''' Check whether or not this person died on this timestep  '''
        inds = self.check_inds(self.dead, self.date_dead, filter_inds=self.is_exp)
        self.exposed[inds]     = False
        self.infectious[inds]  = False
        self.symptomatic[inds] = False
        self.severe[inds]      = False
        self.critical[inds]    = False
        self.recovered[inds]   = False
        self.dead[inds]        = True
        return len(inds)


    def check_diagnosed(self):
        '''
        Check for new diagnoses. Since most data are reported with diagnoses on
        the date of the test, this function reports counts not for the number of
        people who received a positive test result on a day, but rather, the number
        of people who were tested on that day who are schedule to be diagnosed in
        the future.
        '''

        # Handle people who tested today who will be diagnosed in future
        test_pos_inds = self.check_inds(self.diagnosed, self.date_pos_test, filter_inds=None) # Find people who will be diagnosed in future
        self.date_pos_test[test_pos_inds] = np.nan # Clear date of having will-be-positive test

        # Handle people who were actually diagnosed today
        diag_inds  = self.check_inds(self.diagnosed, self.date_diagnosed, filter_inds=None) # Find who was actually diagnosed on this timestep
        self.diagnosed[diag_inds]   = True # Set these people to be diagnosed
        quarantined = cvu.itruei(self.quarantined, diag_inds)
        self.date_end_quarantine[quarantined] = self.t # Set end quarantine date to match when the person left quarantine (and entered isolation)
        self.quarantined[diag_inds] = False # If you are diagnosed, you are isolated, not in quarantine

        return len(test_pos_inds)


    def check_quar(self):
        '''Update quarantine state'''

        n_quarantined = 0
        for ind, end_day in self._pending_quarantine[self.t]:
            if self.quarantined[ind]:
                self.date_end_quarantine[ind] = max(self.date_end_quarantine[ind], end_day) # Extend quarantine if required
            elif not (self.dead[ind] | self.recovered[ind] | self.diagnosed[ind]):
                self.quarantined[ind] = True
                self.date_quarantined[ind] = self.t
                self.date_end_quarantine[ind] = end_day
                n_quarantined += 1

        # If someone on quarantine has reached the end of their quarantine, release them
        end_inds = self.check_inds(~self.quarantined, self.date_end_quarantine, filter_inds=None) # Note the double-negative here
        self.quarantined[end_inds] = False # Release from quarantine

        return n_quarantined


    #%% Methods to make events occur (infection and diagnosis)

    def make_susceptible(self, inds):
        '''
        Make person susceptible. This is used during dynamic resampling
        '''
        for key in self.meta.states:
            if key == 'susceptible':
                self[key][inds] = True
            else:
                self[key][inds] = False

        for key in self.meta.dates + self.meta.durs:
            self[key][inds] = np.nan

        return


    def infect(self, inds, hosp_max=None, icu_max=None, source=None, layer=None):
        '''
        Infect people and determine their eventual outcomes.
            * Every infected person can infect other people, regardless of whether they develop symptoms
            * Infected people that develop symptoms are disaggregated into mild vs. severe (=requires hospitalization) vs. critical (=requires ICU)
            * Every asymptomatic, mildly symptomatic, and severely symptomatic person recovers
            * Critical cases either recover or die

        Args:
            inds     (array): array of people to infect
            hosp_max (bool):  whether or not there is an acute bed available for this person
            icu_max  (bool):  whether or not there is an ICU bed available for this person
            source   (array): source indices of the people who transmitted this infection (None if an importation or seed infection)
            layer    (str):   contact layer this infection was transmitted on

        Returns:
            count (int): number of people infected
        '''

        # Remove duplicates
        unique = np.unique(inds, return_index=True)[1]
        inds = inds[unique]
        if source is not None:
            source = source[unique]

        # Keep only susceptibles
        keep = self.susceptible[inds] # Unique indices in inds and source that are also susceptible
        inds = inds[keep]
        if source is not None:
            source = source[keep]

        n_infections = len(inds)
        durpars      = self.pars['dur']

        # Set states
        self.susceptible[inds]   = False
        self.exposed[inds]       = True
        self.date_exposed[inds]  = self.t
        self.flows['new_infections'] += len(inds)

        # Record transmissions
        for i, target in enumerate(inds):
            self.infection_log.append(dict(source=source[i] if source is not None else None, target=target, date=self.t, layer=layer))

        # Calculate how long before this person can infect other people
        self.dur_exp2inf[inds] = cvu.sample(**durpars['exp2inf'], size=n_infections)
        self.date_infectious[inds] = self.dur_exp2inf[inds] + self.t

        # Use prognosis probabilities to determine what happens to them
        symp_probs = self.pars['rel_symp_prob']*self.symp_prob[inds] # Calculate their actual probability of being symptomatic
        is_symp = cvu.binomial_arr(symp_probs) # Determine if they develop symptoms
        symp_inds = inds[is_symp]
        asymp_inds = inds[~is_symp] # Asymptomatic

        # CASE 1: Asymptomatic: may infect others, but have no symptoms and do not die
        dur_asym2rec = cvu.sample(**durpars['asym2rec'], size=len(asymp_inds))
        self.date_recovered[asymp_inds] = self.date_infectious[asymp_inds] + dur_asym2rec  # Date they recover
        self.dur_disease[asymp_inds] = self.dur_exp2inf[asymp_inds] + dur_asym2rec  # Store how long this person had COVID-19

        # CASE 2: Symptomatic: can either be mild, severe, or critical
        n_symp_inds = len(symp_inds)
        self.dur_inf2sym[symp_inds] = cvu.sample(**durpars['inf2sym'], size=n_symp_inds) # Store how long this person took to develop symptoms
        self.date_symptomatic[symp_inds] = self.date_infectious[symp_inds] + self.dur_inf2sym[symp_inds] # Date they become symptomatic
        sev_probs = self.pars['rel_severe_prob'] * self.severe_prob[symp_inds] # Probability of these people being severe
        is_sev = cvu.binomial_arr(sev_probs) # See if they're a severe or mild case
        sev_inds = symp_inds[is_sev]
        mild_inds = symp_inds[~is_sev] # Not severe

        # CASE 2.1: Mild symptoms, no hospitalization required and no probaility of death
        dur_mild2rec = cvu.sample(**durpars['mild2rec'], size=len(mild_inds))
        self.date_recovered[mild_inds] = self.date_symptomatic[mild_inds] + dur_mild2rec  # Date they recover
        self.dur_disease[mild_inds] = self.dur_exp2inf[mild_inds] + self.dur_inf2sym[mild_inds] + dur_mild2rec  # Store how long this person had COVID-19

        # CASE 2.2: Severe cases: hospitalization required, may become critical
        self.dur_sym2sev[sev_inds] = cvu.sample(**durpars['sym2sev'], size=len(sev_inds)) # Store how long this person took to develop severe symptoms
        self.date_severe[sev_inds] = self.date_symptomatic[sev_inds] + self.dur_sym2sev[sev_inds]  # Date symptoms become severe
        crit_probs = self.pars['rel_crit_prob'] * self.crit_prob[sev_inds] * (self.pars['no_hosp_factor'] if hosp_max else 1.)# Probability of these people becoming critical - higher if no beds available
        is_crit = cvu.binomial_arr(crit_probs)  # See if they're a critical case
        crit_inds = sev_inds[is_crit]
        non_crit_inds = sev_inds[~is_crit]

        # CASE 2.2.1 Not critical - they will recover
        dur_sev2rec = cvu.sample(**durpars['sev2rec'], size=len(non_crit_inds))
        self.date_recovered[non_crit_inds] = self.date_severe[non_crit_inds] + dur_sev2rec  # Date they recover
        self.dur_disease[non_crit_inds] = self.dur_exp2inf[non_crit_inds] + self.dur_inf2sym[non_crit_inds] + self.dur_sym2sev[non_crit_inds] + dur_sev2rec  # Store how long this person had COVID-19

        # CASE 2.2.2: Critical cases: ICU required, may die
        self.dur_sev2crit[crit_inds] = cvu.sample(**durpars['sev2crit'], size=len(crit_inds))
        self.date_critical[crit_inds] = self.date_severe[crit_inds] + self.dur_sev2crit[crit_inds]  # Date they become critical
        death_probs = self.pars['rel_death_prob'] * self.death_prob[crit_inds] * (self.pars['no_icu_factor'] if icu_max else 1.) # Probability they'll die
        is_dead = cvu.binomial_arr(death_probs)  # Death outcome
        dead_inds = crit_inds[is_dead]
        alive_inds = crit_inds[~is_dead]

        # CASE 2.2.2.1: Did not die
        dur_crit2rec = cvu.sample(**durpars['crit2rec'], size=len(alive_inds))
        self.date_recovered[alive_inds] = self.date_critical[alive_inds] + dur_crit2rec # Date they recover
        self.dur_disease[alive_inds] = self.dur_exp2inf[alive_inds] + self.dur_inf2sym[alive_inds] + self.dur_sym2sev[alive_inds] + self.dur_sev2crit[alive_inds] + dur_crit2rec  # Store how long this person had COVID-19

        # CASE 2.2.2.2: Did die
        dur_crit2die = cvu.sample(**durpars['crit2die'], size=len(dead_inds))
        self.date_dead[dead_inds] = self.date_critical[dead_inds] + dur_crit2die # Date of death
        self.dur_disease[dead_inds] = self.dur_exp2inf[dead_inds] + self.dur_inf2sym[dead_inds] + self.dur_sym2sev[dead_inds] + self.dur_sev2crit[dead_inds] + dur_crit2die   # Store how long this person had COVID-19

        return n_infections # For incrementing counters


    def test(self, inds, test_sensitivity=1.0, loss_prob=0.0, test_delay=0):
        '''
        Method to test people

        Args:
            inds: indices of who to test
            test_sensitivity (float): probability of a true positive
            loss_prob (float): probability of loss to follow-up
            test_delay (int): number of days before test results are ready

        Returns:
            Whether or not this person tested positive
        '''

        inds = np.unique(inds)
        self.tested[inds] = True
        self.date_tested[inds] = self.t # Only keep the last time they tested

        is_infectious = cvu.itruei(self.infectious, inds)
        pos_test      = cvu.n_binomial(test_sensitivity, len(is_infectious))
        is_inf_pos    = is_infectious[pos_test]

        not_diagnosed = is_inf_pos[np.isnan(self.date_diagnosed[is_inf_pos])]
        not_lost      = cvu.n_binomial(1.0-loss_prob, len(not_diagnosed))
        final_inds    = not_diagnosed[not_lost]

        # Store the date the person will be diagnosed, as well as the date they took the test which will come back positive
        self.date_diagnosed[final_inds] = self.t + test_delay
        self.date_pos_test[final_inds] = self.t

        return


    def quarantine(self, inds, start_date=None, period=None):
        '''
        Schedule a quarantine

        This function will create a request to quarantine a person on the start_date for
        a period of time. Whether they are on an existing quarantine that gets extended, or
        whether they are no longer eligible for quarantine, will be checked when the start_date
        is reached.

        Args:
            inds (int): indices of who to quarantine, specified by check_quar()
            start_date (int): day to begin quarantine (defaults to the current day, `sim.t`)
            period (int): quarantine duration (defaults to `pars['quar_period']`)
        '''

        start_date = self.t if start_date is None else int(start_date)
        period = self.pars['quar_period'] if period is None else int(period)
        for ind in inds:
            self._pending_quarantine[start_date].append((ind, start_date + period))
        return


    def trace(self, inds, trace_probs, trace_time):
        '''
        Trace the contacts of the people provided
        Args:
            inds (array): indices of whose contacts to trace
            trace_probs (dict): probability of being able to trace people at each contact layer - should have the same keys as contacts
            trace_time (dict): days it'll take to trace people at each contact layer - should have the same keys as contacts
        '''

        # Extract the indices of the people who'll be contacted
        traceable_layers = {k:v for k,v in trace_probs.items() if v != 0.} # Only trace if there's a non-zero tracing probability
        for lkey,this_trace_prob in traceable_layers.items():
            if self.pars['beta_layer'][lkey]: # Skip if beta is 0 for this layer
                this_trace_time = trace_time[lkey]

                # Find all the contacts of these people
                traceable_inds = self.contacts[lkey].find_contacts(inds)
                if len(traceable_inds):
                    contact_inds = cvu.binomial_filter(this_trace_prob, traceable_inds) # Filter the indices according to the probability of being able to trace this layer
                    if len(contact_inds):
                        self.known_contact[contact_inds] = True
                        self.date_known_contact[contact_inds]  = np.fmin(self.date_known_contact[contact_inds], self.t+this_trace_time) # Record just first time they were notified
                        self.quarantine(contact_inds, self.t+this_trace_time, self.pars['quar_period']-this_trace_time) # Schedule quarantine for the notified people to start on the date they will be notified. Note that the quarantine duration is based on the time since last contact, rather than time since notified

        return

    #%% Analysis methods

    def plot(self, *args, **kwargs):
        '''
        Plot statistics of the population -- age distribution, numbers of contacts,
        and overall weight of contacts (number of contacts multiplied by beta per
        layer).

        Args:
            bins (arr): age bins to use (default, 0-100 in one-year bins)
            width (float): bar width
            font_size (float): size of font
            alpha (float): transparency of the plots
            fig_args (dict): passed to pl.figure()
            axis_args (dict): passed to pl.subplots_adjust()
            plot_args (dict): passed to pl.plot()
        '''
        fig = cvplt.plot_people(people=self, *args, **kwargs)
        return fig


    def story(self, uid, *args):
        '''
        Print out a short history of events in the life of the specified individual.

        Args:
            uid (int/list): the person or people whose story is being regaled
            args (list): these people will tell their stories too

        **Example**::

            sim = cv.Sim(pop_type='hybrid', verbose=0)
            sim.run()
            sim.people.story(12)
            sim.people.story(795)
        '''

        def label_lkey(lkey):
            ''' Friendly name for common layer keys '''
            if lkey.lower() == 'a':
                llabel = 'default contact'
            if lkey.lower() == 'h':
                llabel = 'household'
            elif lkey.lower() == 's':
                llabel = 'school'
            elif lkey.lower() == 'w':
                llabel = 'workplace'
            elif lkey.lower() == 'c':
                llabel = 'community'
            else:
                llabel = f'"{lkey}"'
            return llabel

        uids = sc.promotetolist(uid)
        uids.extend(args)

        for uid in uids:

            p = self[uid]
            sex = 'female' if p.sex == 0 else 'male'

            intro = f'\nThis is the story of {uid}, a {p.age:.0f} year old {sex}'

            if not p.susceptible:
                if np.isnan(p.date_symptomatic):
                    print(f'{intro}, who had asymptomatic COVID.')
                else:
                    print(f'{intro}, who had symptomatic COVID.')
            else:
                print(f'{intro}, who did not contract COVID.')

            total_contacts = 0
            no_contacts = []
            for lkey in p.contacts.keys():
                llabel = label_lkey(lkey)
                n_contacts = len(p.contacts[lkey])
                total_contacts += n_contacts
                if n_contacts:
                    print(f'{uid} is connected to {n_contacts} people in the {llabel} layer')
                else:
                    no_contacts.append(llabel)
            if len(no_contacts):
                nc_string = ', '.join(no_contacts)
                print(f'{uid} has no contacts in the {nc_string} layer(s)')
            print(f'{uid} has {total_contacts} contacts in total')

            events = []

            dates = {
            'date_critical'       : 'became critically ill and needed ICU care',
            'date_dead'           : 'died ☹',
            'date_diagnosed'      : 'was diagnosed with COVID',
            'date_end_quarantine' : 'ended quarantine',
            'date_infectious'     : 'became infectious',
            'date_known_contact'  : 'was notified they may have been exposed to COVID',
            'date_pos_test'       : 'recieved their positive test result',
            'date_quarantined'    : 'entered quarantine',
            'date_recovered'      : 'recovered',
            'date_severe'         : 'developed severe symptoms and needed hospitalization',
            'date_symptomatic'    : 'became symptomatic',
            'date_tested'         : 'was tested for COVID',
            }

            for attribute, message in dates.items():
                date = getattr(p,attribute)
                if not np.isnan(date):
                    events.append((date, message))

            for infection in self.infection_log:
                lkey = infection['layer']
                llabel = label_lkey(lkey)
                if infection['target'] == uid:
                    if lkey:
                        events.append((infection['date'], f'was infected with COVID by {infection["source"]} via the {llabel} layer'))
                    else:
                        events.append((infection['date'], f'was infected with COVID as a seed infection'))

                if infection['source'] == uid:
                    x = len([a for a in self.infection_log if a['source'] == infection['target']])
                    events.append((infection['date'],f'gave COVID to {infection["target"]} via the {llabel} layer ({x} secondary infections)'))

            if len(events):
                for day, event in sorted(events, key=lambda x: x[0]):
                    print(f'On day {day:.0f}, {uid} {event}')
            else:
                print(f'Nothing happened to {uid} during the simulation.')
        return