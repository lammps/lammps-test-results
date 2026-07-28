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

# The problems the harness reports in the "attention" field of a test, as
# (label, marker) pairs.  The marker is the part of the message that is
# stable across its wordings: the same problem is reported as 'velocity
# create with the default "loop all" and atoms from create_atoms: cannot
# match log.15May22.ehex.g++.8, ...' and as 'velocity create with "loop
# local": cannot match ...', and the rest of the message names the log file
# of that particular test.  A field can carry several problems, separated
# by "; ".  See tools/regression-tests/REPORTING.md in lammps/lammps.
ATTENTION_KINDS = (
    ('velocities depend on the MPI count', 'velocity create with'),
    ('reference log file matches no input', 'no reference log file matches'),
    ('production sized run', 'production sized run'),
    ('production sized run', 'hits the timeout of'),
    ('style exists in no package', 'does not exist in any package'),
)
# what a configuration of the full regression suite actually runs.  neither
# the name of the configuration nor the title of the run says it: "Parallel"
# does not say how many ranks, and "MPI+OpenMP" and "KOKKOS/OpenMP" name a
# package rather than the decomposition, which reads as if the two ran
# something different from one another
CONFIG_DETAILS = {
    'serial': '1 process',
    'parallel': '4 MPI tasks',
    'openmp': '2 MPI tasks with 2 OpenMP threads via the OPENMP package',
    'kokkos': '2 MPI tasks with 2 OpenMP threads via the KOKKOS package',
}
# the order the configurations are worth reading in: by how much they add to
# the one before, which is not the alphabetical order
CONFIG_ORDER = ('serial', 'parallel', 'openmp', 'kokkos')

# statuses that are not verdicts: the input was not really tested, and each
# kind implies different work, so they are counted apart from one another
NOT_TESTED_KINDS = (
    ('needs a multi-partition run', 'needs a multi-partition run'),
    ('no reference log file, run as a crash test', 'no reference log file, only checked'),
    ('no reference log file, not shortened', 'numerical checks skipped due to missing'),
    ('log file format not understood', 'unsupported log file format'),
    ('package not installed', 'package not installed'),
)

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

def config_sort_key(config):
    '''sort configurations the way they are worth reading where that order is
       known (CONFIG_ORDER), alphabetically for all the others'''
    if config in CONFIG_ORDER:
        return (CONFIG_ORDER.index(config), '')
    return (len(CONFIG_ORDER), config)

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
        for config in sorted(os.listdir(path), key=config_sort_key):
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

def config_detail(suite):
    '''what a configuration of a suite runs, in words (CONFIG_DETAILS), or
       the empty string where nothing is recorded for it.  the cards on the
       dashboard are too small to carry this, so it is shown on the run page
       they link to'''
    return CONFIG_DETAILS.get(suite.partition('/')[2], '')

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

def attention_kinds(entry):
    '''the problems the "attention" field of a test names, as labels. it is
       set independently of the verdict, so a test that passes can carry one;
       a wording that matches none of the known kinds is reported as "other"
       rather than dropped, so that a new kind cannot go unnoticed'''
    text = entry.get('attention') or ''
    if not text:
        return []
    kinds = []
    for label, marker in ATTENTION_KINDS:
        if marker in text and label not in kinds:
            kinds.append(label)
    return kinds or ['other']

def attention_groups(run):
    '''the tests of a run that need a fix in the examples tree, grouped by
       kind. this is a work list against the repository rather than against
       the code: an input that cannot match its reference log file, or that
       runs for a production number of steps, stays broken until somebody
       edits it, however healthy the code is'''
    groups = {}
    for key, entry in run.get('tests', {}).items():
        for kind in attention_kinds(entry):
            groups.setdefault(kind, []).append(key)
    return {kind: sorted(keys) for kind, keys in sorted(groups.items())}

def divergence(entry):
    '''when a failing test starts deviating from its reference log, as one of
       "setup", "early", "late", "chaotic", or "nosteps"; None where the run
       carries no divergence data at all.

       a classical MD trajectory is chaotic: the smallest difference in the
       computed forces grows until it reaches the printed precision, so a
       deviation that appears late says nothing about the code, while one
       that is there in the first thermo output cannot be rounding'''
    if 'diverged_row' not in entry:
        return None
    if entry.get('diverged_row') == 0:
        return 'setup'
    at = entry.get('diverged_at')
    if at is None:
        # no Step column in the thermo output: when it deviates is unknown
        return 'nosteps'
    if at <= 200:
        return 'early'
    if at <= 1000:
        return 'late'
    return 'chaotic'

def sparse_thermo(entry):
    '''whether a run prints its thermo output too rarely for the divergence
       rule to mean anything: the deviation cannot be seen before the first
       output after it starts, so one that is first seen thousands of steps
       in, at the first or second output, may have started at any time'''
    return (entry.get('diverged_row') is not None
            and entry.get('diverged_row') <= 1
            and (entry.get('diverged_at') or 0) > 1000)

def not_tested_kind(entry):
    '''which kind of "was not really tested" a status is, or None'''
    message = entry.get('message', '')
    for label, marker in NOT_TESTED_KINDS:
        if marker in message:
            return label
    return None

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

def compare_configs(runs):
    '''compare the same input decks run in different configurations ("runs"
       maps a configuration name to a run, all of the same commit); returns
       the (comparable, differing) lists of test keys.

       a test is only comparable where every configuration reaches a verdict
       on it: one that carries an "attention" field cannot match its
       reference log file there for a reason of its own - most of them
       because the log was written with a different number of MPI processes -
       and one that timed out has no verdict at all. without that filter the
       comparison drowns in the problems of the examples tree'''
    if len(runs) < 2:
        return [], []
    tests = [run.get('tests', {}) for run in runs.values()]
    comparable, differing = [], []
    for key in sorted(set().union(*(set(t) for t in tests))):
        entries = [t.get(key) for t in tests]
        if any(e is None or e.get('attention') or status_of(e) == 'timeout'
               for e in entries):
            continue
        comparable.append(key)
        if len({status_of(e) for e in entries}) > 1:
            differing.append(key)
    return comparable, differing

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
