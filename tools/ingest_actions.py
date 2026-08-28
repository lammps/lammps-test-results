#!/usr/bin/env python3
'''
Ingest test result artifacts from GitHub Actions runs of lammps/lammps into
the data/ tree of this repository.

Only runs on the develop branch are considered, whether post-merge (push),
manually dispatched, or cron-scheduled; test results from pull request
branches are for the submitters and are not published. Accepting dispatch
and schedule events matters for the regression suites, which are too costly
to run on every push and are instead triggered manually or on a schedule.
Ingestion is idempotent: a run whose data directory already exists is
skipped, so this script can run from a nightly schedule.

Three kinds of workflow are read: the regression workflows upload a merged
run.json (REGRESSION_WORKFLOWS), the unit test workflows one JUnit XML file
per configuration named junit-<config> (UNITTEST_WORKFLOWS), and the example
input check the JUnit XML of run_tests.py as it is (JUNIT_WORKFLOWS); the
two latter are converted with tools/junit_to_json.py.

Nothing that goes wrong during a pass aborts it.  What could not be taken
in is written to data/external/ingest.json instead, which the site
generator reads and marks on the dashboard: a poll that comes back short
must still publish the runs it did get and say what is missing, because a
failed job publishes nothing at all and hides the gap rather than showing
it.  Runs that failed for a reason another pass may not hit are retried by
run id on the following passes, independently of the run listing.

Requires the "gh" CLI (authenticated; in GitHub Actions the default
GITHUB_TOKEN is sufficient since lammps/lammps is public).

Usage: python3 tools/ingest_actions.py [--repo lammps/lammps] [--datadir data]
                                       [--max-runs 50] [--dry-run]
'''

from argparse import ArgumentParser
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import junit_to_json

# the workflows are told apart by their file name in .github/workflows,
# which is what the runs API reports as "path" and what the badges on the
# dashboard are addressed by: the display name of a workflow is edited far
# more freely than its file is renamed, and the workflows API even reports
# a different name than the runs of the same file carry while a workflow is
# being worked on in a pull request.  the names are noted for reading
#
# workflow file -> suite of the merged run.json its artifact carries.  the
# full regression tests have moved off GitHub Actions to a dedicated machine
# and are collected by tools/fetch_regression.py; that suite is not ingested
# from here anymore so that a stray manual run of the (still present)
# workflow cannot mix results from two very differently configured LAMMPS
# binaries into one history
REGRESSION_WORKFLOWS = {
    'quick-regression.yml': 'quick-regression',    # Quick Regression Test
    'kokkos-regression.yaml': 'kokkos-regression', # Kokkos OpenMP Regression Test
}
# artifacts named junit-<config> from the unit test workflows
UNITTEST_WORKFLOWS = (
    'unittest-linux.yml',     # Unittest for Linux /w LAMMPS_BIGBIG
    'unittest-single.yml',    # Unittest for Linux /w -DFFT_SINGLE=ON
    'unittest-fftw.yaml',     # Unittest for Linux, FFTW3 and KOKKOS OpenMP
    'unittest-macos.yml',     # Unittest for MacOS
    'unittest-arm64.yml',     # Unittest for Linux on ARM64
    'unittest-kokkos.yml',    # Unittest for KOKKOS host backends
    'compile-msvc.yml',       # Windows Unit Tests
)
# workflows that upload the JUnit XML of run_tests.py as it is, without the
# merge_results.py pass that produces a run.json: workflow file -> (suite,
# artifact name, title of the run).  the example input check runs every
# input with "-skiprun", so its results are runtests and skips by design;
# what it reports is the inputs that crash before the first step
JUNIT_WORKFLOWS = {
    'check-examples.yml': ('check-examples', 'check-examples-results',
                           'Example input -skiprun check'),  # Check example inputs /w -skiprun
}
# trigger events whose runs are ingested (all restricted to develop)
INGEST_EVENTS = ('push', 'workflow_dispatch', 'schedule')

# the report of a pass, below the data directory, that the site generator
# reads back to mark an incomplete dashboard (generator/build_site.py)
STATUS_FILE = os.path.join('external', 'ingest.json')
# how often a run that did not ingest is retried on later passes before it
# is given up on and reported as a problem instead
MAX_ATTEMPTS = 5
# a pass whose run listing did not hold up asks the next one to look at a
# wider window: whatever the bad listing skipped is then picked up on the
# next round instead of scrolling out of reach.  the window is the newest N
# runs of a repository that produces some 55 a day on develop, so widening
# it costs one artifact query per run and is worth doing only on suspicion
RECHECK_FACTOR = 2
RECHECK_MAX = 600
# how many upstream runs that carried nothing to ingest are reported per
# suite: they are worth seeing, but a workflow that has just been corrected
# leaves a tail of them behind and the list should not fill up with it
ABSENT_LIMIT = 3

def gh_api(path, jq=None):
    cmd = ['gh', 'api', path]
    if jq:
        cmd += ['--jq', jq]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return result.stdout

def workflow_file(run):
    '''the file name of the workflow a run is of (e.g. "unittest-linux.yml"),
       which is what the workflow tables above are keyed by'''
    return os.path.basename(run.get('path', ''))

def run_id_string(run):
    '''directory name for a workflow run: sortable timestamp + short sha'''
    stamp = run['run_started_at'].replace(':', '-')
    return f"{stamp}_{run['head_sha'][:10]}"

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def load_status(datadir):
    '''the report of the previous pass, or an empty one where there is none
       yet or it cannot be read: the report is a hint about what to look at,
       never a precondition for looking'''
    try:
        with open(os.path.join(datadir, STATUS_FILE)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_status(datadir, status):
    path = os.path.join(datadir, STATUS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(status, f, indent=2)
        f.write('\n')

def newest_ingested(datadir, suite):
    '''the newest run id a suite already holds, across both data layouts -
       runs directly below the suite directory, or one directory per
       configuration - or the empty string for a suite with nothing in it'''
    suitedir = os.path.join(datadir, suite)
    if not os.path.isdir(suitedir):
        return ''
    newest = ''
    for entry in os.listdir(suitedir):
        path = os.path.join(suitedir, entry)
        if os.path.isfile(os.path.join(path, 'run.json')):
            newest = max(newest, entry)
        elif os.path.isdir(path):
            for sub in os.listdir(path):
                if os.path.isfile(os.path.join(path, sub, 'run.json')):
                    newest = max(newest, sub)
    return newest

def listing_order(runs):
    '''where a run listing stops being newest-first, as a sentence, or the
       empty string where it holds up.  the runs API documents that order
       and the window kept here is the newest N of it, so a listing that
       comes back out of order need not reach the newest runs at all: the
       pass then finds nothing new and, unchecked, reports a quiet success
       while the results it was meant to pick up scroll out of the window'''
    for newer, older in zip(runs, runs[1:]):
        if older['created_at'] > newer['created_at']:
            return (f"not newest-first: {older['created_at']} follows"
                    f" {newer['created_at']}")
    return ''

class Report:
    '''what one pass saw, kept as data rather than raised as an error.
       "problems" is what is wrong now and wants an eye on it; "pending" is
       what another pass may well manage and is retried by run id until it
       works or the tries run out.  both end up in data/external/ingest.json
       and on the dashboard'''

    def __init__(self, previous):
        self.problems = []
        # carried over from the previous pass, keyed by workflow run id
        self.pending = {entry['key']: dict(entry)
                        for entry in previous.get('pending', [])}
        self.window = {}
        self.recheck = False
        # runs that carried nothing to ingest, per suite, filtered by finish()
        self.absent = {}

    def problem(self, kind, detail, suite='', runid='', run_url=''):
        '''something that is wrong and that trying again will not mend'''
        self.problems.append({'kind': kind, 'detail': detail, 'suite': suite,
                              'runid': runid, 'run_url': run_url})
        print(f"PROBLEM: {kind}: {suite or '-'} {runid}: {detail}", file=sys.stderr)

    def retry(self, run, failures):
        '''a run that did not come in and is worth another pass.  the whole
           run is queued rather than the single artifact that failed: taking
           it in again is idempotent, and one query then covers all of the
           configurations it carries'''
        key = str(run['id'])
        entry = self.pending.get(key) or {'key': key, 'attempts': 0,
                                          'since': utcnow()}
        entry.update({'run_id': run['id'], 'runid': run_id_string(run),
                      'workflow': workflow_file(run),
                      'run_url': run.get('html_url', ''),
                      'reasons': [f'{kind}: {detail}' for kind, detail in failures]})
        self.count_attempt(entry)

    def count_attempt(self, entry):
        '''one more try used up on a pending run, given up on past the limit
           so that a run nothing can be done about stops coming back'''
        entry['attempts'] += 1
        if entry['attempts'] > MAX_ATTEMPTS:
            self.pending.pop(entry['key'], None)
            self.problem('repeatedly failed', '; '.join(entry.get('reasons', ()))
                         + f" (unresolved after {MAX_ATTEMPTS} tries)",
                         entry.get('workflow', ''), entry.get('runid', ''),
                         entry.get('run_url', ''))
        else:
            self.pending[entry['key']] = entry
            print(f"PENDING: {entry.get('runid', '')} ({entry.get('workflow', '')}):"
                  f" {'; '.join(entry.get('reasons', ()))} - try"
                  f" {entry['attempts']} of {MAX_ATTEMPTS}", file=sys.stderr)

    def resolved(self, run):
        '''a run that is in now, whether this pass took it or an earlier one'''
        self.pending.pop(str(run['id']), None)

    def missing(self, suite, run, detail):
        '''an upstream run that carries nothing this script can read'''
        self.absent.setdefault(suite, []).append(
            {'kind': 'nothing to ingest', 'detail': detail, 'suite': suite,
             'runid': run_id_string(run), 'run_url': run.get('html_url', '')})

    def finish(self, datadir):
        '''fold the runs that carried nothing into the problems, keeping the
           ones that would have advanced a suite.  a run older than what the
           suite already holds is not a gap in its history - the workflows
           that were corrected leave a tail of such runs behind, and they
           would otherwise be reported for as long as they stay in the
           window'''
        for suite, entries in sorted(self.absent.items()):
            newest = newest_ingested(datadir, suite)
            fresh = sorted((entry for entry in entries if entry['runid'] > newest),
                           key=lambda entry: entry['runid'], reverse=True)
            self.problems += fresh[:ABSENT_LIMIT]

def download_artifact(repo, artifact, destdir):
    '''download and unpack one artifact zip; returns the extraction dir'''
    url = f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        subprocess.run(['gh', 'api', url], stdout=tmp, check=True)
        tmpname = tmp.name
    os.makedirs(destdir, exist_ok=True)
    with zipfile.ZipFile(tmpname) as zf:
        zf.extractall(destdir)
    os.unlink(tmpname)
    return destdir

def ingest_run(repo, run, datadir, report, dry_run=False):
    '''ingest all relevant artifacts of one completed workflow run;
       returns the number of new data directories created.  whatever goes
       wrong is recorded on the report and the pass carries on: a run that
       may yet come in is queued for the next one, the rest is reported'''
    workflow = workflow_file(run)
    runid = run_id_string(run)
    created = 0
    failures = []

    # the suites a run can only produce one of are settled by the workflow
    # file alone, so a run that is already in needs no artifact query at all
    if workflow in REGRESSION_WORKFLOWS:
        done = os.path.isdir(os.path.join(datadir, REGRESSION_WORKFLOWS[workflow], runid))
    elif workflow in JUNIT_WORKFLOWS:
        done = os.path.isdir(os.path.join(datadir, JUNIT_WORKFLOWS[workflow][0], runid))
    else:
        done = False
    if done:
        report.resolved(run)
        return 0

    try:
        artifacts = json.loads(gh_api(
            f"repos/{repo}/actions/runs/{run['id']}/artifacts"))['artifacts']
    except (RuntimeError, ValueError) as err:
        report.retry(run, [('artifacts unreadable', str(err))])
        return 0

    if workflow in REGRESSION_WORKFLOWS:
        suite = REGRESSION_WORKFLOWS[workflow]
        rundir = os.path.join(datadir, suite, runid)
        names = [a['name'] for a in artifacts]
        for artifact in artifacts:
            if not artifact['name'].endswith('-results'):
                continue
            if dry_run:
                print(f"would ingest {suite} run {runid}: {artifact['name']}")
                created += 1
                continue
            with tempfile.TemporaryDirectory() as tmpdir:
                try:
                    download_artifact(repo, artifact, tmpdir)
                except (subprocess.CalledProcessError, OSError,
                        zipfile.BadZipFile) as err:
                    failures.append((f"{artifact['name']} did not download", str(err)))
                    continue
                srcjson = os.path.join(tmpdir, 'run.json')
                if not os.path.isfile(srcjson):
                    failures.append((f"{artifact['name']} is incomplete",
                                     'no run.json in the artifact'))
                    continue
                # amend the run.json with the workflow run metadata
                try:
                    with open(srcjson) as f:
                        data = json.load(f)
                    data['metadata']['sha'] = run['head_sha']
                    data['metadata']['branch'] = run['head_branch']
                    data['metadata']['run_url'] = run['html_url']
                except (OSError, ValueError, KeyError, TypeError) as err:
                    failures.append((f"{artifact['name']} could not be read", str(err)))
                    continue
                os.makedirs(rundir, exist_ok=True)
                with open(os.path.join(rundir, 'run.json'), 'w') as f:
                    json.dump(data, f, indent=2)
                    f.write('\n')
                print(f"ingested {suite}/{runid} from {artifact['name']}")
                created += 1
        if created == 0 and not failures:
            report.missing(suite, run, f"no merged-results artifact (found: {names})")

    elif workflow in UNITTEST_WORKFLOWS:
        configs = [a for a in artifacts if a['name'].startswith('junit-')]
        for artifact in configs:
            config = artifact['name'][len('junit-'):]
            created += ingest_junit(repo, run, artifact, datadir,
                                    f'unit-tests/{config}', f'Unit Tests {config}',
                                    report, failures, dry_run)
        if not configs:
            report.missing('unit-tests', run, 'no junit-<config> artifact'
                           f" (found: {[a['name'] for a in artifacts]})")

    elif workflow in JUNIT_WORKFLOWS:
        suite, name, title = JUNIT_WORKFLOWS[workflow]
        wanted = [a for a in artifacts if a['name'] == name]
        for artifact in wanted:
            created += ingest_junit(repo, run, artifact, datadir, suite, title,
                                    report, failures, dry_run)
        if not wanted:
            report.missing(suite, run, f"no {name} artifact"
                           f" (found: {[a['name'] for a in artifacts]})")

    if failures:
        report.retry(run, failures)
    else:
        report.resolved(run)
    return created

def ingest_junit(repo, run, artifact, datadir, suite, title, report, failures,
                 dry_run=False):
    '''archive the JUnit XML file of one artifact as the run.json of "suite";
       returns 1 if a new data directory was created.  the commit, branch and
       time of the run come from the workflow run: what the XML records of
       them (the git_info property of run_tests.py) is the detached HEAD of
       the checkout and an abbreviated hash.  an artifact that does not come
       down or does not hold what it should is appended to "failures", which
       queues its run for another pass rather than ending this one'''
    runid = run_id_string(run)
    rundir = os.path.join(datadir, suite, runid)
    if os.path.isdir(rundir):
        return 0
    if dry_run:
        print(f"would ingest {suite} run {runid} from {artifact['name']}")
        return 1
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            download_artifact(repo, artifact, tmpdir)
        except (subprocess.CalledProcessError, OSError, zipfile.BadZipFile) as err:
            failures.append((f"{artifact['name']} did not download", str(err)))
            return 0
        xmls = [name for name in os.listdir(tmpdir) if name.endswith('.xml')]
        if not xmls:
            failures.append((f"{artifact['name']} is incomplete",
                             'no JUnit XML in the artifact'))
            return 0
        try:
            properties, tests = junit_to_json.parse_junit(os.path.join(tmpdir, xmls[0]))
        except Exception as err:
            # the XML is written by a test run that may itself have been cut
            # short; anything unreadable in it is the run's problem, not this
            # script's, and must not take the rest of the pass down with it
            failures.append((f"{artifact['name']} could not be parsed", str(err)))
            return 0
    data = junit_to_json.run_json(properties, tests, title, run['run_started_at'],
                                  sha=run['head_sha'], branch=run['head_branch'],
                                  run_url=run['html_url'])
    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, 'run.json'), 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"ingested {suite}/{runid} from {artifact['name']}")
    return 1

if __name__ == "__main__":
    parser = ArgumentParser(description="Ingest test artifacts from GitHub Actions")
    parser.add_argument("--repo", default="lammps/lammps", help="Source repository")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--max-runs", type=int, default=200,
                        help="Number of recent workflow runs to examine")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be ingested")
    args = parser.parse_args()

    previous = load_status(args.datadir)
    report = Report(previous)
    wanted = set(REGRESSION_WORKFLOWS) | set(UNITTEST_WORKFLOWS) | set(JUNIT_WORKFLOWS)
    # every suite this script writes, for judging a run listing against what
    # is already on disk
    suites = (set(REGRESSION_WORKFLOWS.values())
              | {suite for suite, _, _ in JUNIT_WORKFLOWS.values()} | {'unit-tests'})
    total = 0

    # a pass that did not trust its listing asked this one to look further
    # back, so that what the bad listing skipped is picked up rather than
    # lost: the window is the only thing that decides how far back a run can
    # still be found, and ingesting is idempotent, so a wider look is safe
    max_runs = args.max_runs
    if previous.get('recheck'):
        max_runs = max(max_runs, min(max_runs * RECHECK_FACTOR, RECHECK_MAX))
        print(f"the previous pass could not trust its run listing:"
              f" examining {max_runs} runs instead of {args.max_runs}")

    # the runs queued by an earlier pass are fetched by id, which does not
    # depend on the listing at all: a run that has meanwhile scrolled out of
    # the window is still reachable this way
    for entry in sorted(report.pending.values(), key=lambda e: e.get('runid', '')):
        try:
            queued = json.loads(gh_api(f"repos/{args.repo}/actions/runs/{entry['run_id']}"))
        except (RuntimeError, ValueError) as err:
            entry['reasons'] = [f'run unreachable: {err}']
            report.count_attempt(entry)
            continue
        print(f"retrying {entry.get('runid', '')} ({entry.get('workflow', '')}),"
              f" try {entry['attempts'] + 1} of {MAX_ATTEMPTS}")
        total += ingest_run(args.repo, queued, args.datadir, report, args.dry_run)

    # the runs API accepts only a single "event" value per query, so fetch
    # all completed runs on develop (paginated) and filter by event below
    runs = []
    page = 1
    while len(runs) < max_runs:
        try:
            batch = json.loads(gh_api(
                f"repos/{args.repo}/actions/runs?branch=develop&status=completed"
                f"&per_page=100&page={page}"))['workflow_runs']
        except (RuntimeError, ValueError) as err:
            report.problem('run listing failed', f'page {page}: {err}')
            report.recheck = True
            break
        if not batch:
            break
        runs += batch
        page += 1
    runs = runs[:max_runs]

    # what the listing is worth.  it is not verified in order to refuse it -
    # whatever it does hold is still ingested below - but a window that does
    # not reach the newest runs would otherwise pass for an idle poll, and
    # the runs it skipped would scroll out before anyone noticed
    disorder = listing_order(runs)
    if disorder:
        report.problem('run listing out of order', disorder)
        report.recheck = True
    newest_seen = max((run_id_string(run) for run in runs
                       if workflow_file(run) in wanted), default='')
    newest_held = max((newest_ingested(args.datadir, suite) for suite in suites),
                      default='')
    if newest_seen and newest_held and newest_seen < newest_held:
        report.problem('run listing behind the archive',
                       f'the newest run in the window ({newest_seen}) is older than'
                       f' the newest run already ingested ({newest_held})')
        report.recheck = True
    report.window = {'requested': max_runs, 'examined': len(runs),
                     'newest': runs[0]['created_at'] if runs else '',
                     'oldest': runs[-1]['created_at'] if runs else '',
                     'widened': max_runs != args.max_runs}

    for run in runs:
        if workflow_file(run) not in wanted or run['event'] not in INGEST_EVENTS:
            continue
        total += ingest_run(args.repo, run, args.datadir, report, args.dry_run)

    report.finish(args.datadir)
    print(f"ingested {total} new data set(s) from {args.repo}")
    if report.problems:
        print(f"{len(report.problems)} problem(s) reported on the dashboard")
    if report.pending:
        print(f"{len(report.pending)} run(s) queued for the next pass")
    if not args.dry_run:
        save_status(args.datadir, {
            'generated': utcnow(),
            'repo': args.repo,
            'window': report.window,
            'ingested': total,
            'recheck': report.recheck,
            'problems': report.problems,
            'pending': sorted(report.pending.values(),
                              key=lambda e: e.get('runid', '')),
        })
    # signal to the workflow whether a site rebuild is needed
    if 'GITHUB_OUTPUT' in os.environ:
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f"new_data={'true' if total > 0 else 'false'}\n")
