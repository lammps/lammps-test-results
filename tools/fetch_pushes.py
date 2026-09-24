#!/usr/bin/env python3
'''
Fetch when the branches the test machines run on have moved, and store it as
data/external/pushes.json for the dashboard.

The test machines (the full regression runs, the native unit test run, the
GPU builds: the suites collected from download.lammps.org by
tools/fetch_regression.py and tools/fetch_unittest.py) run at most once a day
and only when the branch they test has changed.  The age of their newest
result therefore says nothing by itself: develop sat still from 2026-09-15 to
2026-09-21, and the results of the 15th were as current as they could be all
week.  What tells a machine that has stopped from one that had nothing to do
is whether the branch moved on from the commit it tested, and when.  The
repository activity API of GitHub records exactly that for every push, merge,
and force push to a branch: the commit it moved from, the one it moved to,
and the time.

The branches are those named by the newest run of each suite a test machine
publishes (the runs that record a source_url).  A branch that is not on
GitHub - the machines are pointed at branches of their own while their setup
is being worked on - comes back without any pushes, and a result of it is
not judged.  The judgment itself is made by the site generator
(OVERDUE_HOURS there), since it depends on the time the site is built.

A branch whose activity cannot be fetched keeps what the previous file held
for it, and the file is left alone where nothing could be fetched at all.

Usage: python3 tools/fetch_pushes.py [--repo lammps/lammps] [--datadir data]
'''

from argparse import ArgumentParser
import datetime
import json
import os
import subprocess
import sys
import urllib.parse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import rundata

OUTPUT = os.path.join('external', 'pushes.json')

def gh_api(path):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return result.stdout

def tested_branches(datadir):
    '''the branches the newest published run of each suite was of'''
    branches = set()
    for suite in rundata.list_suites(datadir):
        runs = rundata.list_runs(datadir, suite)
        metadata = rundata.load_run(datadir, suite, runs[-1]).get('metadata', {})
        if metadata.get('source_url') and metadata.get('branch'):
            branches.add(metadata['branch'])
    return sorted(branches)

def pushes(repo, branch):
    '''the latest 100 moves of a branch, newest first'''
    ref = urllib.parse.quote(f'refs/heads/{branch}', safe='/')
    activity = json.loads(gh_api(f"repos/{repo}/activity?ref={ref}&per_page=100"))
    return [{'timestamp': entry.get('timestamp', ''),
             'type': entry.get('activity_type', ''),
             'before': entry.get('before', ''),
             'after': entry.get('after', '')} for entry in activity]

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch the pushes to the tested branches")
    parser.add_argument("--repo", default="lammps/lammps", help="Repository")
    parser.add_argument("--datadir", default="data", help="Data directory")
    args = parser.parse_args()

    path = os.path.join(args.datadir, OUTPUT)
    try:
        with open(path) as f:
            previous = json.load(f).get('branches', {})
    except (OSError, ValueError):
        previous = {}

    branches = {}
    fetched = 0
    for branch in tested_branches(args.datadir):
        try:
            branches[branch] = pushes(args.repo, branch)
            fetched += 1
        except (RuntimeError, ValueError) as err:
            print(f"WARNING: could not fetch the pushes to {branch}: {err}",
                  file=sys.stderr)
            if branch in previous:
                branches[branch] = previous[branch]
    if not fetched:
        print("no pushes fetched, leaving the existing file alone")
        sys.exit(0)

    data = {
        'generated': datetime.datetime.now(datetime.timezone.utc)
                     .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'repo': args.repo,
        'branches': branches,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"{path}: " + ', '.join(f"{branch} {len(moves)} push(es)"
                                  for branch, moves in branches.items()))
