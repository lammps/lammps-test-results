#!/usr/bin/env python3
'''
Fetch the status of the automated LAMMPS manual builds and store it as
data/external/docs.json for the dashboard.

Three variants of the manual are built and published independently, one per
tracked branch (see SOURCES below).  Each of them carries a status.json in
its document root that records the branch, the documented commit, the build
time, and the outcome and duration of the individual build steps.

The build is attempted at most once per commit hash, so an unchanged
status.json does not mean the build machine is alive - it usually means
there was nothing to do.  The only reliable freshness check is therefore a
comparison of the documented commit against the current head of the branch
it tracks, which is queried from GitHub here and evaluated by the site
generator (which owns the staleness policy, see DOCS_STALE_HOURS there).

A status file that cannot be fetched (webserver down, network hiccup) does
not blank out the panel: the last known entry is kept and annotated with the
error, so the dashboard keeps showing the last known state and says how old
it is.  Only the Python standard library is required; the "gh" CLI is used
for the branch heads (as in fetch_activity.py) with an unauthenticated
"git ls-remote" as fallback.

Usage: python3 tools/fetch_docs.py [--repo lammps/lammps]
                                   [--output data/external/docs.json]
'''

from argparse import ArgumentParser
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

# the published manual variants in the order they are shown on the dashboard:
# development version, current patch release, most recent stable release
SOURCES = (
    ('develop', 'https://docs.lammps.org/latest/'),
    ('release', 'https://docs.lammps.org/'),
    ('stable', 'https://docs.lammps.org/stable/'),
)

SCHEMA = 1
ATTEMPTS = 3

def fetch_json(url, attempts=ATTEMPTS):
    '''fetch and parse a JSON document, retrying transient network errors'''
    error = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={'User-Agent': 'lammps-test-results ingest'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as err:
            error = err
            if attempt + 1 < attempts:
                time.sleep(5)
    raise error

def branch_heads(repo, branches):
    '''current head commit and its date for each branch from the GitHub API;
       falls back to git ls-remote, which needs no authentication but does
       not report commit dates.  branches whose head cannot be determined are
       left out, which makes the generator skip the freshness check for them
       rather than compare against a possibly outdated commit hash'''
    owner, name = repo.split('/')
    refs = ' '.join(
        f'{branch}: ref(qualifiedName: "refs/heads/{branch}")'
        ' { target { ... on Commit { oid committedDate } } }'
        for branch in branches)
    query = ('query($owner: String!, $name: String!) { repository('
             'owner: $owner, name: $name) { ' + refs + ' } }')
    try:
        result = subprocess.run(
            ['gh', 'api', 'graphql', '-f', 'query=' + query,
             '-F', 'owner=' + owner, '-F', 'name=' + name],
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        repository = json.loads(result.stdout)['data']['repository']
        heads = {}
        for branch in branches:
            target = (repository.get(branch) or {}).get('target') or {}
            if target.get('oid'):
                heads[branch] = {'commit': target['oid'],
                                 'date': target.get('committedDate', '')}
        if not heads:
            raise RuntimeError('no branch refs in the reply')
        return heads
    except Exception as err:
        print(f"WARNING: could not query branch heads via gh: {err}",
              file=sys.stderr)

    try:
        result = subprocess.run(
            ['git', 'ls-remote', '--heads', f'https://github.com/{repo}.git']
            + list(branches), capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        heads = {}
        for line in result.stdout.splitlines():
            commit, _, ref = line.partition('\t')
            branch = ref.rsplit('/', 1)[-1]
            if branch in branches and commit:
                heads[branch] = {'commit': commit}
        return heads
    except Exception as err:
        print(f"WARNING: could not query branch heads via git: {err}",
              file=sys.stderr)
    return {}

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch documentation build status")
    parser.add_argument("--repo", default="lammps/lammps",
                        help="Repository the manuals are built from")
    parser.add_argument("--output", default="data/external/docs.json",
                        help="Output JSON file")
    args = parser.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # the previous state is the fallback for sources that cannot be reached
    previous = {}
    try:
        with open(args.output) as f:
            for entry in json.load(f).get('branches', []):
                previous[entry.get('branch')] = entry
    except (OSError, ValueError):
        pass

    heads = branch_heads(args.repo, [branch for branch, _ in SOURCES])

    branches = []
    for branch, url in SOURCES:
        entry = {'branch': branch, 'url': url}
        try:
            status = fetch_json(url + 'status.json')
            if not isinstance(status, dict):
                raise RuntimeError('not a JSON object')
        except Exception as err:
            print(f"WARNING: {url}status.json: {err}", file=sys.stderr)
            entry = dict(previous.get(branch, entry))
            entry.update({'branch': branch, 'url': url,
                          'error': str(err), 'error_seen': now})
        else:
            if status.get('schema') != SCHEMA:
                print(f"WARNING: {url}status.json: unexpected schema version "
                      f"{status.get('schema')!r}, reading it anyway",
                      file=sys.stderr)
            for key in ('commit', 'version', 'built', 'steps'):
                if key in status:
                    entry[key] = status[key]
            if status.get('branch') not in (None, branch):
                print(f"WARNING: {url}status.json: reports branch "
                      f"{status['branch']!r}, expected {branch!r}",
                      file=sys.stderr)
                entry['reported_branch'] = status['branch']
            entry['checked'] = now
        # a stale head would produce a bogus "not built yet" warning, so the
        # previous one is deliberately not carried over
        if branch in heads:
            entry['head'] = heads[branch]
        else:
            entry.pop('head', None)
        branches.append(entry)

    data = {'fetched': now, 'url': 'https://docs.lammps.org/',
            'branches': branches}
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

    ok = sum(1 for entry in branches if 'error' not in entry)
    print(f"{args.output}: {ok}/{len(branches)} status files, "
          f"{len(heads)}/{len(SOURCES)} branch heads")
