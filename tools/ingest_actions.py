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

Requires the "gh" CLI (authenticated; in GitHub Actions the default
GITHUB_TOKEN is sufficient since lammps/lammps is public).

Usage: python3 tools/ingest_actions.py [--repo lammps/lammps] [--datadir data]
                                       [--max-runs 50] [--dry-run]
'''

from argparse import ArgumentParser
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

def ingest_run(repo, run, datadir, dry_run=False):
    '''ingest all relevant artifacts of one completed workflow run;
       returns the number of new data directories created'''
    workflow = workflow_file(run)
    runid = run_id_string(run)
    meta = ['--meta', f"sha={run['head_sha']}",
            '--meta', f"branch={run['head_branch']}",
            '--meta', f"run_url={run['html_url']}"]
    created = 0

    artifacts = json.loads(gh_api(
        f"repos/{repo}/actions/runs/{run['id']}/artifacts"))['artifacts']

    if workflow in REGRESSION_WORKFLOWS:
        suite = REGRESSION_WORKFLOWS[workflow]
        rundir = os.path.join(datadir, suite, runid)
        if os.path.isdir(rundir):
            return 0
        names = [a['name'] for a in artifacts]
        for artifact in artifacts:
            if not artifact['name'].endswith('-results'):
                continue
            if dry_run:
                print(f"would ingest {suite} run {runid}: {artifact['name']}")
                created += 1
                continue
            with tempfile.TemporaryDirectory() as tmpdir:
                download_artifact(repo, artifact, tmpdir)
                srcjson = os.path.join(tmpdir, 'run.json')
                if not os.path.isfile(srcjson):
                    print(f"WARNING: no run.json in {artifact['name']} of run {run['id']}",
                          file=sys.stderr)
                    continue
                # amend the run.json with the workflow run metadata
                with open(srcjson) as f:
                    data = json.load(f)
                data['metadata']['sha'] = run['head_sha']
                data['metadata']['branch'] = run['head_branch']
                data['metadata']['run_url'] = run['html_url']
                os.makedirs(rundir, exist_ok=True)
                with open(os.path.join(rundir, 'run.json'), 'w') as f:
                    json.dump(data, f, indent=2)
                    f.write('\n')
                print(f"ingested {suite}/{runid} from {artifact['name']}")
                created += 1
        if created == 0 and not dry_run:
            print(f"NOTE: {run['name']} run {runid} has no merged-results artifact"
                  f" (found: {names})", file=sys.stderr)

    elif workflow in UNITTEST_WORKFLOWS:
        for artifact in artifacts:
            if not artifact['name'].startswith('junit-'):
                continue
            config = artifact['name'][len('junit-'):]
            created += ingest_junit(repo, run, artifact, datadir,
                                    f'unit-tests/{config}', f'Unit Tests {config}',
                                    dry_run)

    elif workflow in JUNIT_WORKFLOWS:
        suite, name, title = JUNIT_WORKFLOWS[workflow]
        for artifact in artifacts:
            if artifact['name'] == name:
                created += ingest_junit(repo, run, artifact, datadir, suite, title,
                                        dry_run)
    return created

def ingest_junit(repo, run, artifact, datadir, suite, title, dry_run=False):
    '''archive the JUnit XML file of one artifact as the run.json of "suite";
       returns 1 if a new data directory was created.  the commit, branch and
       time of the run come from the workflow run: what the XML records of
       them (the git_info property of run_tests.py) is the detached HEAD of
       the checkout and an abbreviated hash'''
    runid = run_id_string(run)
    rundir = os.path.join(datadir, suite, runid)
    if os.path.isdir(rundir):
        return 0
    if dry_run:
        print(f"would ingest {suite} run {runid} from {artifact['name']}")
        return 1
    with tempfile.TemporaryDirectory() as tmpdir:
        download_artifact(repo, artifact, tmpdir)
        xmls = [name for name in os.listdir(tmpdir) if name.endswith('.xml')]
        if not xmls:
            print(f"WARNING: no JUnit XML in {artifact['name']} of run {run['id']}",
                  file=sys.stderr)
            return 0
        properties, tests = junit_to_json.parse_junit(os.path.join(tmpdir, xmls[0]))
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

    # the runs API accepts only a single "event" value per query, so fetch
    # all completed runs on develop (paginated) and filter by event below
    runs = []
    page = 1
    while len(runs) < args.max_runs:
        batch = json.loads(gh_api(
            f"repos/{args.repo}/actions/runs?branch=develop&status=completed"
            f"&per_page=100&page={page}"))['workflow_runs']
        if not batch:
            break
        runs += batch
        page += 1
    runs = runs[:args.max_runs]

    wanted = set(REGRESSION_WORKFLOWS) | set(UNITTEST_WORKFLOWS) | set(JUNIT_WORKFLOWS)
    total = 0
    for run in runs:
        if workflow_file(run) not in wanted or run['event'] not in INGEST_EVENTS:
            continue
        total += ingest_run(args.repo, run, args.datadir, args.dry_run)

    print(f"ingested {total} new data set(s) from {args.repo}")
    # signal to the workflow whether a site rebuild is needed
    if 'GITHUB_OUTPUT' in os.environ:
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f"new_data={'true' if total > 0 else 'false'}\n")
