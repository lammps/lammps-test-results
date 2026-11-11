#!/usr/bin/env python3
'''
Fetch the results of the example input check from the LAMMPS buildbot
(https://ci.lammps.org) and archive them under
data/check-examples/<runid>/run.json.

The check - every input script of the examples tree run with "-skiprun" on 2
MPI tasks, see "Example input check" in the README - moved from the GitHub
Actions workflow check-examples.yml to the builder check-examples of the
buildbot, which runs it for every merge into develop and maintenance (and for
pull requests, in a builder of its own that is not looked at here).  Its step
"examples" keeps the JUnit XML of run_tests.py as a second log, "junit", which
the public REST API of the buildbot serves without a token.

A build is archived when it is complete, of the develop branch of
lammps/lammps - forced builds of develop included, as the manually dispatched
runs of the workflow were, but not those of a fork or of another branch - and
has that log.  A build that failed before the check, e.g. in its compile, has
none, and there is nothing to archive.  The run directory is named as the ones
of the workflow runs (tools/ingest_actions.py), from the time the build started
and the commit it built; the run links to the build page and records the log
it came from as its source_url.  Ingestion is keyed on the run directory, so
this script can run as often as necessary.

The API serves a log with three lines in front of it (builder, build number,
worker), which are dropped up to the XML declaration.

An unreachable buildbot, or a log that does not parse or has no test cases,
is skipped with a warning, leaving the archived runs untouched.

Usage: python3 tools/fetch_buildbot.py [--datadir data] [--days 7] [--dry-run]
'''

from argparse import ArgumentParser
import datetime
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import junit_to_json

BUILDBOT = 'https://ci.lammps.org'
API = BUILDBOT + '/api/v2'
BUILDER = 'check-examples'
STEP = 'examples'
LOG = 'junit'
SUITE = 'check-examples'
TITLE = 'Example input -skiprun check'
BRANCH = 'develop'
# the builds of the scheduler carry the repository of its poller; a forced
# build carries none, unless a fork was named in the force dialog
REPOSITORIES = ('', 'https://github.com/lammps/lammps.git')
# as far back as tools/ingest_actions.py looks for workflow runs
LOOKBACK_DAYS = 7
MAX_BUILDS = 200

def api(path):
    '''the raw answer of the REST API to a path below /api/v2'''
    request = urllib.request.Request(
        API + path, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()

def candidate_builds(days):
    '''the builder id and the complete builds of develop of the last days,
       newest first'''
    builders = json.loads(api(f'/builders?name={BUILDER}&field=name&field=builderid'))['builders']
    if not builders:
        raise RuntimeError(f'the buildbot has no builder {BUILDER}')
    builderid = builders[0]['builderid']
    since = int(time.time() - days * 86400)
    # the API refuses a filter on a field that is not selected, and returns
    # no properties unless they are selected as a field as well
    query = (f'/builders/{builderid}/builds?complete=true&started_at__ge={since}'
             f'&order=-number&limit={MAX_BUILDS}'
             '&field=buildid&field=number&field=started_at&field=complete&field=results'
             '&field=properties&property=branch&property=got_revision&property=repository')
    builds = []
    for build in json.loads(api(query))['builds']:
        props = {name: value[0] for name, value in build.get('properties', {}).items()}
        if props.get('branch') == BRANCH and props.get('repository', '') in REPOSITORIES \
           and props.get('got_revision'):
            build['sha'] = props['got_revision']
            builds.append(build)
    return builderid, builds

def ingest(builderid, build, datadir, dry_run=False):
    '''archive the JUnit log of one build; returns 1 if it was new'''
    started = datetime.datetime.fromtimestamp(build['started_at'], datetime.timezone.utc)
    runid = f"{started.strftime('%Y-%m-%dT%H-%M-%SZ')}_{build['sha'][:10]}"
    rundir = os.path.join(datadir, SUITE, runid)
    if os.path.isdir(rundir):
        return 0
    path = f"/builds/{build['buildid']}/steps/{STEP}/logs/{LOG}/raw"
    try:
        raw = api(path)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            print(f"build {build['number']} of {BUILDER} has no {LOG} log: nothing to archive")
            return 0
        print(f"WARNING: skipping build {build['number']}: {API}{path}: {err}", file=sys.stderr)
        return 0
    except Exception as err:
        print(f"WARNING: skipping build {build['number']}: {API}{path}: {err}", file=sys.stderr)
        return 0
    start = raw.find(b'<?xml')
    if start < 0:
        start = raw.find(b'<')
    try:
        if start < 0:
            raise ET.ParseError('no XML in the log')
        properties, tests = junit_to_json.parse_junit(io.BytesIO(raw[start:]))
    except ET.ParseError as err:
        print(f"WARNING: skipping build {build['number']}: {API}{path}: {err}", file=sys.stderr)
        return 0
    if not tests:
        print(f"WARNING: skipping build {build['number']}: {API}{path}: no test cases",
              file=sys.stderr)
        return 0
    if dry_run:
        print(f"would ingest {SUITE}/{runid} from build {build['number']}")
        return 1
    data = junit_to_json.run_json(
        properties, tests, TITLE, started.strftime('%Y-%m-%dT%H:%M:%SZ'),
        sha=build['sha'], branch=BRANCH,
        run_url=f"{BUILDBOT}/#/builders/{builderid}/builds/{build['number']}",
        source_url=API + path)
    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, 'run.json'), 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"ingested {SUITE}/{runid} from build {build['number']}")
    return 1

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch the example input check from the LAMMPS buildbot")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS,
                        help="How many days back to look for builds")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be ingested")
    args = parser.parse_args()

    try:
        builderid, builds = candidate_builds(args.days)
    except Exception as err:
        print(f"WARNING: skipping {SUITE}: {API}: {err}", file=sys.stderr)
        sys.exit(0)
    created = sum(ingest(builderid, build, args.datadir, args.dry_run) for build in builds)
    print(f"{created} new {SUITE} run(s) of {len(builds)} build(s) of {BRANCH}")
