#!/usr/bin/env python3
'''
Shared helpers for reading archived test run data.

The data layout is:

    data/<suite>/<runid>/run.json

where <suite> is e.g. "quick-regression", "full-regression/<config>", or
"unit-tests/<config>" (one level deeper wherever a suite is run in more
than one configuration: the per-platform unit test matrix, and the serial
and parallel full regression runs), and <runid> sorts chronologically
(ISO timestamp + short sha).

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
    '''return all suites that have at least one run; a suite that is run in
       several configurations keeps them in subdirectories and is listed once
       per configuration as "<suite>/<config>" (e.g. "unit-tests/linux-arm64"
       or "full-regression/serial")'''
    suites = []
    if not os.path.isdir(datadir):
        return suites
    for entry in sorted(os.listdir(datadir)):
        path = os.path.join(datadir, entry)
        if not os.path.isdir(path) or entry == 'external':
            continue
        # runs directly below the suite directory: a single configuration.
        # both layouts are recognized side by side, so a suite can grow
        # configurations later without invalidating its existing runs
        if list_runs(datadir, entry):
            suites.append(entry)
        for config in sorted(os.listdir(path)):
            if list_runs(datadir, f'{entry}/{config}'):
                suites.append(f'{entry}/{config}')
    return suites

def suite_title(suite):
    '''readable name of a suite: "unit-tests/linux-arm64" becomes
       "Unit Tests: linux-arm64", "full-regression" becomes
       "Full Regression"'''
    base, _, config = suite.partition('/')
    title = base.replace('-', ' ').title()
    return f'{title}: {config}' if config else title

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
