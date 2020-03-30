'''
Set the parameters for Covasim.
'''

import pandas as pd
from datetime import datetime


__all__ = ['make_pars', 'load_data']


def make_pars():
    '''
    Set parameters for the simulation.

    NOTE: If you update these values or add a new parameter, please update README.md
    in this folder as well.
    '''
    pars = {}

    # Simulation parameters
    pars['scale']      = 1 # Factor by which to scale results -- e.g. 0.6*100 with n=10e3 assumes 60% of a population of 1m

    pars['n']          = 20e3 # Number ultimately susceptible to CoV
    pars['n_infected'] = 10 # Number of seed cases
    pars['start_day']  = datetime(2020, 3, 1) # Start day of the simulation
    pars['n_days']     = 60 # Number of days of run, if end_day isn't used
    pars['seed']       = 1 # Random seed, if None, don't reset
    pars['verbose']    = 1 # Whether or not to display information during the run -- options are 0 (silent), 1 (default), 2 (everything)
    pars['usepopdata'] = 'random' # Whether or not to load actual population data
    pars['timelimit']  = 3600 # Time limit for a simulation (seconds)
    pars['stop_func']  = None # A function to call to stop the sim partway through
    pars['window']     = 7 # Integration window for doubling time and R_eff

    # Disease transmission
    pars['beta']           = 0.015 # Beta per symptomatic contact; absolute
    pars['asymp_factor']   = 0.8 # Multiply beta by this factor for asymptomatic cases
    pars['diag_factor']    = 0.0 # Multiply beta by this factor for diganosed cases -- baseline assumes no isolation
    pars['cont_factor']    = 1.0 # Multiply beta by this factor for people who've been in contact with known positives  -- baseline assumes no isolation
    pars['contacts']       = 20 # Estimated number of contacts
    pars['beta_pop']       = {'H': 1.5,  'S': 1.5,   'W': 1.5,  'R': 0.5} # Per-population beta weights; relative
    pars['contacts_pop']   = {'H': 4.11, 'S': 11.41, 'W': 8.07, 'R': 20.0} # default flu-like weights # Number of contacts per person per day, estimated

    # Disease progression
    pars['serial']         = 4.0 # Serial interval: days after exposure before a person can infect others (see e.g. https://www.ncbi.nlm.nih.gov/pubmed/32145466)
    pars['serial_std']     = 1.0 # Standard deviation of the serial interval
    pars['incub']          = 5.0 # Incubation period: days until an exposed person develops symptoms
    pars['incub_std']      = 1.0 # Standard deviation of the incubation period
    pars['severe']         = 3.0 # Number of days after symptom onset before hospitalization is required (for severe cases)
    pars['severe_std']     = 1.0 # Standard deviation of the above period

    # Recovery
    pars['dur']            = 8.0 # Mean recovery time for asymptomatic and mild cases
    pars['dur_std']        = 2.0 # Variance in duration
    pars['dur_sev']        = 11.0 # Mean length of hospital stay for severe cases
    pars['dur_sev_std']    = 3.0 # Variance in duration of hospital stay for severe cases

    # Mortality and severity
    pars['timetodie']           = 21 # Days until death
    pars['timetodie_std']       = 2 # STD
    pars['prog_by_age']         = True # Whether or not to use age-specific probabilities of prognosis (symptoms/severe symptoms/death)
    pars['default_symp_prob']   = 0.7 # If not using age-specific values: overall proportion of symptomatic cases
    pars['default_severe_prob'] = 0.3 # If not using age-specific values: proportion of symptomatic cases that become severe (default 0.2 total)
    pars['default_death_prob']  = 0.07 # If not using age-specific values: proportion of severe cases that result in death (default 0.02 CFR)
    pars['OR_no_treat']         = 2. # Odds ratio for how much more likely people are to die if no treatment available

    # Events and interventions
    pars['interventions'] = []  #: List of Intervention instances
    pars['interv_func'] = None # Custom intervention function

    # Health system parameters
    pars['n_beds'] = pars['n']  # Baseline assumption is that there's enough beds for the whole population (i.e., no constraints)

    return pars


def load_data(datafile, datacols=None, **kwargs):
    '''
    Load data for comparing to the model output.

    Args:
        datafile (str): the name of the file to load
        datacols (list): list of required column names
        kwargs (dict): passed to pd.read_excel()

    Returns:
        data (dataframe): pandas dataframe of the loaded data
    '''

    if datacols is None:
        datacols = ['day', 'date', 'new_tests', 'new_positives']

    # Load data
    raw_data = pd.read_excel(datafile, **kwargs)

    # Confirm data integrity and simplify
    for col in datacols:
        assert col in raw_data.columns, f'Column "{col}" is missing from the loaded data'
    data = raw_data[datacols]

    return data



