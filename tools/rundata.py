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

A test that ran into the time limit of the test harness is reported as an
error like any other, but says nothing about the code: whether it expires
depends on the time limit in force, on how many tests run beside it, and on
the machine. Those runs are classified as "timeout" here (status_of()) and
counted apart from the errors, so that a slower machine or a lowered limit
does not read as a regression. They stay visible in their own right, since
a test that starts hanging because of a code change lands there too.
'''

import json
import os
import re

# statuses that count as broken; a timeout is not among them, see above
BAD = ('failed', 'error')
# how the test harness reports hitting its time limit, e.g.
# "failed, no Total wall time in the output, timeout (180s expired)"
TIMEOUT = re.compile(r'\btimeout \((\d+)s expired\)')

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

def config_label(suite, title):
    '''how a run describes its own configuration, where that says more than
       the name of the suite does: the "Full Regression Test / KOKKOS/OpenMP"
       of "full-regression/kokkos" is "KOKKOS/OpenMP", while the
       "... / Serial" of "full-regression/serial" only repeats the suite name
       and is dropped.  the configurations that run the same input decks in
       different ways are told apart by this, and by little else: several of
       them share one configuration file'''
    config = suite.partition('/')[2]
    if not config or '/' not in title:
        return ''
    label = title.split('/', 1)[1].strip()
    return '' if label.lower() == config.lower() else label

def status_of(entry):
    '''the status of one test, with a run that hit the time limit of the test
       harness classified as "timeout" rather than as an error'''
    status = entry.get('status', '')
    if status in BAD and TIMEOUT.search(entry.get('message', '')):
        return 'timeout'
    return status

def counts(run):
    '''the test counts of a run by classified status. the totals are counted
       from the results rather than taken from the metadata, which knows
       nothing about timeouts; the number of tests and the walltime are read
       from the metadata, which is where the harness records them'''
    tally = dict.fromkeys(('passed', 'failed', 'error', 'timeout', 'skipped'), 0)
    for entry in run.get('tests', {}).values():
        status = status_of(entry)
        tally[status] = tally.get(status, 0) + 1
    result = dict(run.get('metadata', {}).get('counts', {}))
    result.update(tally)
    return result

def broken(counts):
    '''how many tests of a run are broken; timeouts are not counted, they
       say nothing about the code (see the module docstring)'''
    return counts.get('failed', 0) + counts.get('error', 0)

def time_limits(run):
    '''the time limits the test harness enforced in a run, as seen in the
       messages of the tests that hit them (sorted, in seconds); empty when
       no test timed out, which is also when the limit cannot be observed'''
    limits = set()
    for entry in run.get('tests', {}).values():
        found = TIMEOUT.search(entry.get('message', ''))
        if found:
            limits.add(int(found.group(1)))
    return sorted(limits)

def compare_runs(previous, current, earlier=()):
    '''classify the changes between two runs (run.json dicts); returns a dict
       of sorted lists of test keys.

       a test that timed out in the previous run has no verdict there, so the
       comparison falls back to the most recent run before it that does have
       one, taken from "earlier" (older runs, newest first, loaded lazily).
       without that, a failing test which flaps through a timeout is
       announced as a new failure every time it comes back'''
    tests_prev = previous.get('tests', {})
    tests_curr = current.get('tests', {})
    reported = {k: status_of(v) for k, v in tests_prev.items()}
    curr = {k: status_of(v) for k, v in tests_curr.items()}

    prev = dict(reported)
    pending = {k for k, status in prev.items()
               if status == 'timeout' and k in curr}
    older_runs = iter(earlier)
    while pending:
        try:
            older = next(older_runs)
        except StopIteration:
            break
        for key in sorted(pending):
            entry = older.get('tests', {}).get(key)
            if entry is not None and status_of(entry) != 'timeout':
                prev[key] = status_of(entry)
                pending.discard(key)

    both = [k for k in curr if k in prev]
    return {
        'new_failures': sorted(k for k in both if curr[k] in BAD
                               and prev[k] not in BAD),
        'still_failing': sorted(k for k in both if curr[k] in BAD
                                and prev[k] in BAD),
        'fixed': sorted(k for k in both if curr[k] == 'passed'
                        and prev[k] in BAD),
        # a test that newly runs out of time is reported apart from the
        # failures: it is as likely to be the machine as it is the code.
        # this one goes by what the previous run reported, not by the last
        # verdict: a test that timed out twice in a row is not news
        'new_timeouts': sorted(k for k in both if curr[k] == 'timeout'
                               and reported[k] != 'timeout'),
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
