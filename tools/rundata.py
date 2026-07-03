#!/usr/bin/env python3
'''
Shared helpers for reading archived test run data.

The data layout is:

    data/<suite>/<runid>/run.json

where <suite> is e.g. "full-regression", "quick-regression", or
"unit-tests/<config>" (one level deeper for the per-platform unit test
matrix), and <runid> sorts chronologically (ISO timestamp + short sha).

Each run.json follows the format written by merge_results.py in
lammps/lammps (tools/regression-tests): a "metadata" object with
"counts" and "properties", and a "tests" object keyed by
"classname/name" with {"status", "time", "message"} values.
'''

import json
import os

# statuses that count as broken
BAD = ('failed', 'error')

def list_runs(datadir, suite):
    '''return the sorted list of run ids for a suite (oldest first)'''
    suitedir = os.path.join(datadir, suite)
    if not os.path.isdir(suitedir):
        return []
    runs = []
    for entry in sorted(os.listdir(suitedir)):
        if os.path.isfile(os.path.join(suitedir, entry, 'run.json')):
            runs.append(entry)
    return runs

def load_run(datadir, suite, runid):
    '''load one run.json'''
    with open(os.path.join(datadir, suite, runid, 'run.json')) as f:
        return json.load(f)

def list_suites(datadir):
    '''return all suites that have at least one run, unit-test configs listed
       individually as "unit-tests/<config>"'''
    suites = []
    if not os.path.isdir(datadir):
        return suites
    for entry in sorted(os.listdir(datadir)):
        path = os.path.join(datadir, entry)
        if not os.path.isdir(path) or entry == 'external':
            continue
        if entry == 'unit-tests':
            for config in sorted(os.listdir(path)):
                if list_runs(datadir, f'unit-tests/{config}'):
                    suites.append(f'unit-tests/{config}')
        elif list_runs(datadir, entry):
            suites.append(entry)
    return suites

def compare_runs(previous, current):
    '''classify the changes between two runs (run.json dicts);
       returns a dict of sorted lists of test keys'''
    tests_prev = previous.get('tests', {})
    tests_curr = current.get('tests', {})
    return {
        'new_failures': sorted(k for k in tests_curr if k in tests_prev
                               and (tests_curr[k]['status'] in BAD)
                               and (tests_prev[k]['status'] not in BAD)),
        'still_failing': sorted(k for k in tests_curr if k in tests_prev
                                and (tests_curr[k]['status'] in BAD)
                                and (tests_prev[k]['status'] in BAD)),
        'fixed': sorted(k for k in tests_curr if k in tests_prev
                        and (tests_curr[k]['status'] == 'passed')
                        and (tests_prev[k]['status'] in BAD)),
        'new_tests': sorted(k for k in tests_curr if k not in tests_prev),
        'removed_tests': sorted(k for k in tests_prev if k not in tests_curr),
    }

def last_ok_run(datadir, suite, runs, test):
    '''return the most recent run id in which the given test passed, or None'''
    for runid in reversed(runs):
        run = load_run(datadir, suite, runid)
        entry = run.get('tests', {}).get(test)
        if entry and entry['status'] == 'passed':
            return runid
    return None
