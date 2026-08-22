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
"classname/name" with {"status", "time", "message"} values, where
"status" is one of passed, failed, error, runtest, skipped.

A test that ran into the time limit of the test harness is reported as an
error like any other, but says nothing about the code: whether it expires
depends on the time limit in force, on how many tests run beside it, and on
the machine. Those runs are classified as "timeout" here (status_of()) and
counted apart from the errors, so that a slower machine or a lowered limit
does not read as a regression. They stay visible in their own right, since
a test that starts hanging because of a code change lands there too.

A test that ran to completion but could not be checked against anything -
no reference log file, a log file the harness cannot parse, no thermo output
at all, or a "-skiprun" dry run that only parses the input and takes one
step - is reported as "runtest" by the harness since lammps/lammps#5144:
only the run itself was tested, so it is neither passed nor skipped.  The
runs archived before that reported the same outcomes as "skipped", with a
message that starts with "completed"; status_of() reads those as "runtest"
as well, so that the archive is one vocabulary from end to end and the
trend bars do not jump where the harness changed its wording.

Beside reading the archive, this module carries the helpers the tools that
add to it share (tools/fetch_regression.py, tools/fetch_unittest.py): the
results they pick up are published as a single "latest" file that is
rewritten in place, so what is new has to be decided from the contents of a
run rather than from the file being new.
'''

import datetime
import functools
import json
import os
import re

# statuses that count as broken; a timeout is not among them, see above
BAD = ('failed', 'error')
# statuses a test can be classified as (status_of()), in the order they are
# worth reading: the verdicts, then the runs that are not verdicts
STATUSES = ('passed', 'failed', 'error', 'timeout', 'runtest', 'skipped')
# statuses a broken test is counted as mended by: a run that completes
# without a check (e.g. because the reference log file went away) has still
# stopped crashing, which is what merge_results.py in lammps/lammps reports
# as fixed too
OK = ('passed', 'runtest')
# how the harness worded the outcomes it now reports as "runtest" while it
# still reported them as skipped: every one of them says the run completed
LEGACY_RUNTEST = re.compile(r'^completed\b')
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
# the same for the suites: the verdicts first, then the check that reaches
# none, then the unit test matrix (which the dashboard sets apart anyway)
SUITE_ORDER = ('full-regression', 'quick-regression', 'check-examples', 'unit-tests')
# what a suite run in a single configuration does, where the name does not
# say (CONFIG_DETAILS is the same for the configurations of a suite), and
# the title to give it where the name reads badly as one
SUITE_DETAILS = {
    'check-examples': 'every example input run with "-skiprun" on 2 MPI tasks in'
                      ' the GitHub Actions build: parsed, set up, and taken one'
                      ' step, with nothing checked numerically',
}
SUITE_TITLES = {
    'check-examples': 'Example Input Check',
}

# statuses that are not verdicts, as (label, marker) pairs: the input was
# not really tested, and each kind implies different work, so they are
# counted apart from one another.  most of them are "runtest" results, which
# ran to completion and only lack a check - a reference log file, usually -
# or "skipped" ones, which were never run; a few are reported as errors by
# the harness ("package not installed") and are not verdicts on the code
# either.  the marker is the part of the message that is stable across
# tests: the rest names the log file or the command of that particular
# input.  the first marker that matches wins, so the more specific wording
# of a kind is listed before the more general one it also contains
NOT_TESTED_KINDS = (
    ('no reference log file, run as a crash test', 'no reference log file, only checked'),
    ('no reference log file, not shortened', 'numerical checks skipped due to missing'),
    ('log file format not understood', 'unsupported log file format'),
    ('log file could not be parsed', 'error parsing log.'),
    ('no thermo output to compare', 'no Step nor Loop'),
    ('only a -skiprun check', '-skiprun check only'),
    ('numerical checks turned off', 'skipping numerical checks'),
    ('needs a multi-partition run', 'needs a multi-partition run'),
    ('package not installed', 'package not installed'),
    ('needs a package or feature the binary lacks', 'not included in the tested binary'),
    ('needs a package or feature the binary lacks', '-skiprun check:'),
    ('cannot be checked with -skiprun', 'cannot be checked with -skiprun'),
    ('couples to another code or graphics demo', 'couples LAMMPS'),
    ('excluded by the test configuration', 'as specified in the test'),
)
# the statuses that are never verdicts, in the order they are worth reading
NOT_TESTED = ('runtest', 'skipped')

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

@functools.lru_cache(maxsize=256)
def load_run(datadir, suite, runid):
    '''load one run.json.

       the result is cached: building the website reads the archive in nested
       passes - every run page walks back through the older runs to find when
       each of its broken tests last passed - which parses the same files
       dozens of times over.  the runs are read and never modified, so one
       parsed copy can be shared; the cache is bounded so that a growing
       archive cannot hold all of it in memory at once'''
    with open(os.path.join(datadir, suite, runid, 'run.json')) as f:
        return json.load(f)

def config_sort_key(config):
    '''sort configurations the way they are worth reading where that order is
       known (CONFIG_ORDER), alphabetically for all the others'''
    if config in CONFIG_ORDER:
        return (CONFIG_ORDER.index(config), '')
    return (len(CONFIG_ORDER), config)

def suite_sort_key(suite):
    '''the same for the suites (SUITE_ORDER)'''
    if suite in SUITE_ORDER:
        return (SUITE_ORDER.index(suite), '')
    return (len(SUITE_ORDER), suite)

def list_suites(datadir):
    '''return all suites that have at least one run; a suite that is run in
       several configurations keeps them in subdirectories and is listed once
       per configuration as "<suite>/<config>" (e.g. "unit-tests/linux-arm64"
       or "full-regression/serial")'''
    suites = []
    if not os.path.isdir(datadir):
        return suites
    for entry in sorted(os.listdir(datadir), key=suite_sort_key):
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
       "Full Regression", and "check-examples" what SUITE_TITLES says'''
    base, _, config = suite.partition('/')
    title = SUITE_TITLES.get(base) or base.replace('-', ' ').title()
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
       what a suite of a single configuration does (SUITE_DETAILS), or the
       empty string where nothing is recorded for it.  the cards on the
       dashboard are too small to carry this, so it is shown on the run page
       they link to'''
    return CONFIG_DETAILS.get(suite.partition('/')[2], '') or SUITE_DETAILS.get(suite, '')

def status_of(entry):
    '''the status of one test, with a run that hit the time limit of the test
       harness classified as "timeout" rather than as an error, and a run
       that completed without a check classified as "runtest" whether the
       harness reported it so or, before it had the word, as skipped'''
    status = entry.get('status', '')
    message = entry.get('message', '')
    if status in BAD and TIMEOUT.search(message):
        return 'timeout'
    if status == 'skipped' and LEGACY_RUNTEST.match(message):
        return 'runtest'
    return status

def metadata_counts(tests):
    '''the counts the harness records in the metadata of a run: the tests by
       their reported status, and the walltime.  this is what the tools that
       build a run.json from a JUnit file write there; counts() is what the
       archive is read with, and classifies further'''
    tally = {'tests': len(tests), 'passed': 0, 'failed': 0, 'error': 0,
             'runtest': 0, 'skipped': 0, 'time': 0.0}
    for entry in tests.values():
        tally[entry['status']] = tally.get(entry['status'], 0) + 1
        tally['time'] += entry.get('time', 0.0)
    return tally

def counts(run):
    '''the test counts of a run by classified status. the totals are counted
       from the results rather than taken from the metadata, which knows
       nothing about timeouts and, before the harness had the word, nothing
       about runtests; the number of tests and the walltime are read from
       the metadata, which is where the harness records them'''
    tally = dict.fromkeys(STATUSES, 0)
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
    '''which kind of "was not really tested" a test is, as a label, or None
       for a test that reached a verdict (or ran out of time).  the kinds are
       read off the message, so that the errors the harness reports for a
       missing package count too; a test that was not run, or ran without a
       check, with a wording that matches none of the known kinds is reported
       as "other" rather than dropped, so that a new kind cannot go unnoticed'''
    message = entry.get('message', '')
    for label, marker in NOT_TESTED_KINDS:
        if marker in message:
            return label
    if status_of(entry) in NOT_TESTED:
        return 'other'
    return None

def not_tested_groups(run):
    '''the tests of a run that were not really tested, counted per kind and
       grouped by classified status - {"runtest": {label: count}, "skipped":
       {...}, ...} - with the statuses that are never verdicts first
       (NOT_TESTED), the rest in the order of STATUSES, and the kinds of
       each status by count'''
    tally = {}
    for entry in run.get('tests', {}).values():
        label = not_tested_kind(entry)
        if label:
            group = tally.setdefault(status_of(entry), {})
            group[label] = group.get(label, 0) + 1
    order = NOT_TESTED + tuple(s for s in STATUSES if s not in NOT_TESTED)
    return {status: dict(sorted(tally[status].items(), key=lambda item: -item[1]))
            for status in order if status in tally}

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
        'fixed': sorted(k for k in both if curr[k] in OK
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
    '''return the most recent run id in which the given test was not broken
       (OK: passed, or completed without a check), or None'''
    for runid in reversed(runs):
        run = load_run(datadir, suite, runid)
        entry = run.get('tests', {}).get(test)
        if entry and status_of(entry) in OK:
            return runid
    return None

# --------------------------------------------------------------------------
# helpers for archiving newly published runs, shared by the fetch tools

def parse_git_info(info):
    '''split a "Git info (<branch> / <describe>)" string into branch, describe
       string, and commit; the value looks like
       "Git info (develop / patch_4Jul2026-856-g961d389cc7)" where the last
       part may also be a plain (short) commit hash and may carry a
       "-modified" suffix when the working tree was not clean. a value that
       comes without the "Git info (...)" wrapper is parsed just the same.

       the regression runs publish this as a property of the run and the unit
       test runs print it in the output of the tests themselves; either way it
       is the only place the branch of a published run is recorded'''
    match = re.search(r'\((.*)\)\s*$', info)
    if match:
        info = match.group(1)
    branch, _, version = (part.strip() for part in info.partition('/'))
    sha = ''
    described = re.search(r'-g([0-9a-f]{7,40})', version)
    if described:
        sha = described.group(1)
    elif re.fullmatch(r'[0-9a-f]{7,40}', version):
        sha = version
    return branch, version, sha

def utc_stamp(text):
    '''an ISO timestamp as UTC, or None if it is unparsable or carries no
       time zone (and hence cannot be placed on the UTC time line)'''
    try:
        when = datetime.datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.astimezone(datetime.timezone.utc)

def run_id_string(published, generated, sha):
    '''directory name for a run: sortable UTC timestamp + short commit hash.
       the Z marks the timestamp as UTC, as for the runs that used to come
       from GitHub Actions.  the time the results were generated is preferred
       over the time they were published, so that re-publishing the same
       results unchanged does not look like another run; if neither is on the
       UTC time line, the local time of the test machine is the best stamp
       available and is left unmarked'''
    when = utc_stamp(generated) or published
    if when:
        stamp = when.astimezone(datetime.timezone.utc).strftime(
            '%Y-%m-%dT%H-%M-%SZ')
    else:
        stamp = str(generated).replace(':', '-')
    return f'{stamp}_{sha[:10]}' if sha else stamp

def verdicts(tests):
    '''the outcome of every test of a run, which is what a change consists
       of: the wall times differ between two runs of the same code, and the
       messages carry them, so neither can be compared'''
    return {key: status_of(entry) for key, entry in tests.items()}

def already_archived(datadir, suite, generated, sha):
    '''whether these results have been archived before; the published file is
       rewritten (with a new modification time) even when a run was skipped
       for lack of changes, so the contents decide, not the run id'''
    for runid in list_runs(datadir, suite):
        meta = load_run(datadir, suite, runid)['metadata']
        # the commit is compared abbreviated: the same run republished with
        # more complete metadata reports the same commit at a different length
        if (meta.get('generated') == generated
                and meta.get('sha', '')[:10] == sha[:10]):
            return runid
    return None

def archived_with_commit(datadir, suite, sha):
    '''the archived runs of one commit, oldest first.

       the test machine only runs when the monitored branch has changed, so a
       commit that appears twice was run again while the test scripts
       themselves were being worked on.  the archive keeps one run per commit
       - the last one published of it, which is the one run with the scripts
       as they ended up - so that every bar of the trend on the dashboard is
       a commit of its own'''
    found = []
    if not sha:
        return found
    for runid in list_runs(datadir, suite):
        meta = load_run(datadir, suite, runid)['metadata']
        if meta.get('sha', '')[:10] == sha[:10]:
            found.append(runid)
    return found

def verdicts_moved(datadir, suite, runid, tests):
    '''how many verdicts of an archived run these results change'''
    before = verdicts(load_run(datadir, suite, runid).get('tests', {}))
    now = verdicts(tests)
    return sum(1 for key in set(before) | set(now)
               if before.get(key) != now.get(key))
