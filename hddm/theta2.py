from __future__ import division
import numpy as np
import pymc as pm
import matplotlib.pyplot as plt
from copy import copy
import matplotlib.pyplot as plt
import numpy.lib.recfunctions as rec
import os.path
import os
from ordereddict import OrderedDict

import hddm

def load_scalp_data(continuous=True, remove_outliers=.4, shift_theta=False):
    import numpy.lib.recfunctions as rec
    subjs = range(1,15)
    if continuous:
        file_prefix = 'data/theta_continuous/m3tst3_auc_'
        dtype = np.dtype([('subj_idx', np.int), ('stim', 'S8'), ('rt', np.float), ('response', np.float), ('prer1', np.float), ('prer2', np.float), ('theta', np.float), ('cue2', np.float), ('dbs', np.int)])

    else:
        file_prefix = 'data/theta/m3tst3_auc_'
        dtype = np.dtype([('subj_idx', np.int), ('stim', 'S8'), ('rt', np.float), ('response', np.float), ('prer1', np.float), ('prer2', np.float), ('theta', 'S8'), ('cue2', np.float), ('dbs', np.int)])

    all_data = []
    for subj_idx, subj in enumerate(subjs):
        for onoff in ['on', 'off']:
            data = np.recfromtxt(file_prefix+str(subj)+onoff+'.txt', dtype=dtype)
            data['subj_idx'] = subj_idx

            if continuous: # Normalize theta
                mu = np.mean(data['theta'])
                sigma = np.std(data['theta'])
                data['theta'] = (data['theta']-mu)/sigma
                if shift_theta:
                    # Shift the theta values one back
                    tmp = copy(data['theta'][:-1])
                    data['theta'][1:] = tmp
                    data = data[1:]

            all_data.append(data)

    all_data = np.concatenate(all_data)
    all_data = rec.append_fields(all_data,
                                 data=(all_data['stim'], all_data['dbs'], all_data['dbs'], all_data['dbs']),
                                 names=('conf', 'dbs_effect', 'dbs_effect_inv', 'dbs_inv'),
                                 usemask=False)
    
    # Remove outliers
    if remove_outliers:
        all_data = all_data[all_data['rt']>remove_outliers]
    # Set names for stim, theta and dbs
    all_data['conf'][all_data['conf'] == '1'] = 'HC'
    all_data['conf'][all_data['conf'] == '2'] = 'HC'
    all_data['conf'][all_data['conf'] == '3'] = 'LC'
    all_data['stim'][all_data['stim'] == '1'] = 'WW'
    all_data['stim'][all_data['stim'] == '2'] = 'LL'
    all_data['stim'][all_data['stim'] == '3'] = 'WL'

    #all_data['theta'][all_data['theta'] == '0'] = 'low'
    #all_data['theta'][all_data['theta'] == '1'] = 'high'

    all_data['dbs_effect'][all_data['dbs_effect'] == 0] = -1
    all_data['dbs_effect_inv'] = -all_data['dbs_effect']
    all_data['dbs_inv'] = 1-all_data['dbs']
        
    return all_data

def load_intraop_data(continuous=True):
    subjs = range(1,9)
    file_prefix = 'data/stn/inoptst3_'
    all_data = []

    dtype = np.dtype([('subj_idx', np.int), ('stim', 'S8'), ('rt', np.float), ('response', np.float), ('vent', np.float), ('mid', np.float), ('dors', np.float)])

    for subj_idx,subj in enumerate(subjs):
        data = np.recfromtxt(file_prefix+str(subj)+'.txt', dtype=dtype)
        data['subj_idx'] = subj_idx
        valid_rows = data['response'] != 10000
        data = data[valid_rows]
        if continuous: # Normalize theta
            for col in ('vent', 'mid', 'dors'):
                mu = np.mean(data[col])
                sigma = np.std(data[col])
                data[col] = (data[col]-mu)/sigma
        all_data.append(data)

    all_data = np.concatenate(all_data)

    all_data = rec.append_fields(all_data, names=('conf',),
                                 data=(all_data['stim'],), dtypes=('S8', np.int, np.int), usemask=False)
    # Remove outliers
    all_data = all_data[all_data['rt']>.4]
    # Set names for stim, theta and dbs
    all_data['conf'][all_data['stim'] == '3'] = 'HC'
    all_data['conf'][all_data['stim'] == '4'] = 'LC'
    all_data['conf'][all_data['stim'] == '5'] = 'HC'
    all_data['stim'][all_data['stim'] == '3'] = 'WW'
    all_data['stim'][all_data['stim'] == '4'] = 'WL'
    all_data['stim'][all_data['stim'] == '5'] = 'LL'
    
    return all_data


def worker():
    from mpi4py import MPI
    rank = MPI.COMM_WORLD.Get_rank()
    proc_name = MPI.Get_processor_name()
    status = MPI.Status()

    print "Worker %i on %s: ready!" % (rank, proc_name)
    # Send ready
    MPI.COMM_WORLD.send([{'rank':rank, 'name':proc_name}], dest=0, tag=10)

    # Start main data loop
    while True:
        # Get some data
        print "Worker %i on %s: waiting for data" % (rank, proc_name)
        recv = MPI.COMM_WORLD.recv(source=0, tag=MPI.ANY_TAG, status=status)
        print "Worker %i on %s: received data, tag: %i" % (rank, proc_name, status.tag)

        if status.tag == 2:
            print "Worker %i on %s: received kill signal" % (rank, proc_name)
            MPI.COMM_WORLD.send([], dest=0, tag=2)
            return

        if status.tag == 10:
            # Run emergent
            #print "Worker %i on %s: Running %s" % (rank, proc_name, recv)
            #recv['debug'] = True
            retry = 0
            while retry < 5:
                try:
                    print "Running %s:\n" % recv[0]
                    result = run_model(recv[0], recv[1])
                    break
                except pm.ZeroProbability:
                    retry +=1
            if retry == 5:
                result = None
                print "Job %s failed" % recv[0]

        print("Worker %i on %s: finished one job" % (rank, proc_name))
        MPI.COMM_WORLD.send((recv[0], result), dest=0, tag=15)

    MPI.COMM_WORLD.send([], dest=0, tag=2)
        
def run_model(name, params, load=False):
    if params.has_key('model_type'):
        model_type = params['model_type']
    else:
        model_type = 'simple'

    data = params.pop('data')

    dbname = os.path.join('/','users', 'wiecki', 'scratch', 'theta', name+'.db')

    if params.has_key('effect_on'):
        m = pm.MCMC(hddm.model.HDDMRegressor(data, **params).create(), db='hdf5', dbname=dbname)
    else:
        m = pm.MCMC(hddm.model.HDDM(data, **params).create(), db='hdf5', dbname=dbname)
    
    if not load:
        try:
            os.remove(dbname)
        except OSError:
            pass
        m.sample(samples=30000, burn=25000)
        m.db.close()
        print "*************************************\nModel: %s\n%s" %(name, m.summary())
        return m.summary()
    else:
        print "Loading %s" %name
        m = pm.database.hdf5.load(dbname)
        m.mcmc_load_from_db(dbname=dbname)
        return m


def create_jobs_pd():
    # Load data
    data_pd = np.recfromcsv('PD_PS.csv')
    data_dbs_off = data_pd[data_pd['dbs'] == 0]
    data_dbs_on = data_pd[data_pd['dbs'] == 1]

    models = OrderedDict()
    # Create PD models
    models['PD_paper_effect_on_as_0'] = {'data': data_pd, 'effect_on':['a'], 'depends_on':{'v':['stim'], 'e_theta':['conf'], 'e_inter':['conf']}}
    models['PD_paper_dummy_on_as_0'] = {'data': data_pd, 'effect_coding':False, 'effect_on':['a'], 'depends_on':{'v':['stim'], 'e_theta':['conf'], 'e_inter':['conf']}}
    models['PD_paper_dummy_on_as_1'] = {'data': data_pd, 'effect_coding':False, 'on_as_1':True, 'effect_on':['a'], 'depends_on':{'v':['stim'], 'e_theta':['conf'], 'e_inter':['conf']}}
    models['PD_paper_effect_on_as_1'] = {'data': data_pd, 'on_as_1':True, 'effect_on':['a'], 'depends_on':{'v':['stim'], 'e_theta':['conf'], 'e_inter':['conf']}}
    models['dbs_off_PD_paper'] = {'data': data_dbs_off, 'effect_on':['a'], 'depends_on':{'v':['stim'], 'e_theta':['conf']}}
    models['dbs_on_PD_paper'] = {'data': data_dbs_on, 'effect_on':['a'], 'depends_on':{'v':['stim'], 'e_theta':['conf']}}

    return models

def create_models_nodbs(data, full=False):
    models = []
    model_types = ['simple']
    if full:
        model_types.append('full_mc')
        
    effects_on = ['a', 't']
    vs_on = ['stim', 'conf']
    e_thetas_on = ['stim', 'conf', ['stim','resp'], ['conf','resp']]
    
    for effect in effects_on:
        for v_on in vs_on:
            for e_theta_on in e_thetas_on:
                models.append({'data': data, 'effect_on':[effect], 'depends_on':{'v':[vs_on], 'e_theta':[e_theta_on]}})
                models.append({'data': data, 'effect_on':[effect], 'depends_on':{'v':[vs_on], 'e_theta':[e_theta_on], 'a':['stim']}})
                models.append({'data': data, 'effect_on':[effect, 'z'], 'depends_on':{'v':[vs_on], 'e_theta':[e_theta_on]}})
                models.append({'data': data, 'effect_on':[effect], 'depends_on':{'v':[vs_on], 'e_theta':[e_theta_on, 'rt_split']}})
                models.append({'data': data, 'effect_on':[effect], 'depends_on':{'v':[vs_on], 'e_theta':[e_theta_on, 'rt_split'], 'a':['rt_split']}})
                models.append({'data': data, 'effect_on':[effect], 'depends_on':{'v':[vs_on], 'e_theta':[e_theta_on, 'rt_split'], 'a':['stim', 'rt_split']}})

    
    models.append({'data': data, 'depends_on':{'v':['stim'], 'a':['theta_split']}})
    models.append({'data': data, 'depends_on':{'v':['conf'], 'a':['theta_split']}})
    
    return models

def load_models(pd=False, full_mc=False):
    if pd:
        jobs = create_jobs_pd()
    elif full_mc:
        jobs = create_jobs_full()
    else:
        jobs = create_jobs()

    models = OrderedDict()
    for name, params in jobs.iteritems():
        models[name] = run_model(name, params, load=True)

    return models

def controller(samples=200, burn=15, reps=5):
    process_list = range(1, MPI.COMM_WORLD.Get_size())
    rank = MPI.COMM_WORLD.Get_rank()
    proc_name = MPI.Get_processor_name()
    status = MPI.Status()

    print "Controller %i on %s: ready!" % (rank, proc_name)

    models = create_jobs_full()
    task_iter = models.iteritems()
    results = {}

    while(True):
        status = MPI.Status()
        recv = MPI.COMM_WORLD.recv(source=MPI.ANY_SOURCE, tag=MPI.ANY_TAG, status=status)
        print "Controller: received tag %i from %s" % (status.tag, status.source)
        if status.tag == 15:
            results[recv[0]] = recv[1]

        if status.tag == 10 or status.tag == 15:
            try:
                name, task = task_iter.next()
                print "Controller: Sending task"
                MPI.COMM_WORLD.send((name, task), dest=status.source, tag=10)
            except StopIteration:
                # Task queue is empty
                print "Controller: Task queue is empty"
                print "Controller: Sending kill signal"
                MPI.COMM_WORLD.send([], dest=status.source, tag=2)

        elif status.tag == 2: # Exit
            process_list.remove(status.source)
            print 'Process %i exited' % status.source
            print 'Processes left: ' + str(process_list)
        else:
            print 'Unkown tag %i with msg %s' % (status.tag, str(data))
            
        if len(process_list) == 0:
            print "No processes left"
            break

    return results

def add_median_fields(data):
    theta_median = np.empty(data.shape, dtype=[('theta_split','S8')])
    rt_split_cor_inc = np.empty(data.shape, dtype=[('rt_split_cor_inc','S8')])
    rt_split = np.empty(data.shape, dtype=[('rt_split','S8')])

    for subj in np.unique(data['subj_idx']):
        subj_idx = data['subj_idx']==subj

        # Set median
        med = np.median(data[subj_idx]['theta'])
        theta_median[subj_idx & (data['theta'] < med)] = 'low'
        theta_median[subj_idx & (data['theta'] >= med)] = 'high'

        # Set high/low RT
        cor_idx = data['response']==1
        inc_idx = data['response']==0
        med_cor = np.median(data[subj_idx & cor_idx]['rt'])
        med_inc = np.median(data[subj_idx & inc_idx]['rt'])
        med = np.median(data[subj_idx]['rt'])

        rt_split_cor_inc[subj_idx & cor_idx & (data['rt'] < med_cor)] = 'fast'
        rt_split_cor_inc[subj_idx & cor_idx & (data['rt'] >= med_cor)] = 'slow'
        rt_split_cor_inc[subj_idx & inc_idx & (data['rt'] < med_inc)] = 'fast'
        rt_split_cor_inc[subj_idx & inc_idx & (data['rt'] >= med_inc)] = 'slow'
        rt_split[subj_idx & (data['rt'] < med)] = 'fast'
        rt_split[subj_idx & (data['rt'] >= med)] = 'slow'
        
    data = rec.append_fields(data, names=('theta_split','rt_split', 'rt_split_cor_inc'),
                             data=(theta_median,rt_split,rt_split_cor_inc), dtypes=('S8', 'S8', 'S8'), usemask=False)

    return data

def load_csv_jim(*args, **kwargs):
    data = np.recfromtxt(*args, dtype=[('subj_idx', '<i8'), ('stim', 'S8'), ('rt', '<f8'), ('response', '<i8'), ('theta', '<f8'), ('conf', 'S8')], delimiter=',', skip_header=True)

    data['stim'][data['stim'] == '1'] = 'WW'
    data['stim'][data['stim'] == '2'] = 'LL'
    data['stim'][data['stim'] == '3'] = 'WL'

    data['conf'][data['conf'] == '1'] = 'HC'
    data['conf'][data['conf'] == '2'] = 'LC'

    data = add_median_fields(data)

    return data[data['rt'] > .4]

def set_proposals(mc, tau=.1, effect=.1, a=.5, v=.5):
    for var in mc.variables:
        if var.__name__.endswith('tau'):
            # Change proposal SD
            mc.use_step_method(pm.Metropolis, var, proposal_sd = tau)
        if var.__name__.startswith('e1') or var.__name__.startswith('e2') or var.__name__.startswith('e_inter'):
            # Change proposal SD
            mc.use_step_method(pm.Metropolis, var, proposal_sd = effect)
        if var.__name__.startswith('a'):
            # Change proposal SD
            mc.use_step_method(pm.Metropolis, var, proposal_sd = a)
        if var.__name__.startswith('v'):
            # Change proposal SD
            mc.use_step_method(pm.Metropolis, var, proposal_sd = v)
    return


if __name__=='__main__':
    #import sys
    #parse_config_file(sys.argv[1])
    from mpi4py import MPI

    rank = MPI.COMM_WORLD.Get_rank()
    if rank == 0:
        results = controller()
        for name, model in results.iteritems():
            print "****************************************\n%s:\n%s\n" %(name, model)
    else:
        worker()
        
