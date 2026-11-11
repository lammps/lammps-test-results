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

The same API gives the statistics of the buildbot card of the dashboard: one
file per day in New York time, data/buildbot/<day>.json, and the live state,
data/external/buildbot.json (both documented in tools/buildbotdata.py).  A day
is archived once it is over and nothing of it is still running - no build
that ran in it, and no CPU or VM build of a merge of that day - and never
again, so the card shows the same numbers on every update of the following
day, at whatever time it runs.  Days that are missing are filled in from the
API as far back as --stats-days, but not before FIRST_DAY.

Usage: python3 tools/fetch_buildbot.py [--datadir data] [--days 7]
                                       [--stats-days 14] [--dry-run]
'''

from argparse import ArgumentParser
from collections import Counter
import datetime
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import buildbotdata
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

# the statistics: days in the time zone of the build windows, from the first
# day with all builders on (the builds of pull requests began on the evening
# of November 9; the days before were the setup, with many failed builds)
TIMEZONE = 'America/New_York'
FIRST_DAY = datetime.date(2026, 11, 10)
STATS_DAYS = 14
# the hours a day a worker may build, as DAY_HOURS (gpu), CPU_HOURS (rstudio)
# and VM_START (the Windows VM) in master.cfg of lammps-buildbot; 24 elsewhere
WINDOW_HOURS = {'gpu': 14, 'rstudio': 16, 'compute-windows': 17}
# the hardware of the machines, from the monitor page of the buildbot
HOSTS_URL = BUILDBOT + '/monitor/hosts.json'
# results of buildbot: 2 failure, 4 exception, 6 cancelled
FAILED = (2, 4)
CANCELLED = 6

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

def get(path):
    return json.loads(api(path))

def pool_of(worker):
    '''the pool of a worker for the busy hours: "gpu", "cpu", or "vm"'''
    if worker.startswith('gpu-'):
        return 'gpu'
    if re.fullmatch(r'(rstudio|compute|c\d\d)-\d+', worker):
        return 'cpu'
    return 'vm'

def window_hours(worker):
    return WINDOW_HOURS.get(worker, WINDOW_HOURS.get(worker.rsplit('-', 1)[0], 24))

def day_stats(day, tz):
    '''the statistics of one day, or None while something of it still runs'''
    t0 = int(datetime.datetime.combine(day, datetime.time(), tz).timestamp())
    t1 = int(datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(),
                                       tz).timestamp())
    builders = {b['builderid']: b for b in
                get('/builders?field=builderid&field=name&field=tags')['builders']}
    workers = [w for w in get('/workers?field=workerid&field=name&field=configured_on')['workers']
               if not w['name'].startswith('__')]
    names = {w['workerid']: w['name'] for w in workers}
    # no build runs for a day, so those started the day before are enough for
    # the hours, and requests of three days for the waits (a GPU build of the
    # evening starts the next morning, a Windows one may wait a night)
    builds = get(f'/builds?started_at__ge={t0 - 86400}&started_at__lt={t1}'
                 '&field=buildid&field=builderid&field=workerid&field=started_at'
                 '&field=complete_at&field=complete&field=results&field=buildrequestid')['builds']
    requests = {r['buildrequestid']: r for r in
                get(f'/buildrequests?submitted_at__ge={t0 - 3 * 86400}&submitted_at__lt={t1 + 86400}'
                    '&field=buildrequestid&field=buildsetid&field=builderid&field=submitted_at'
                    '&field=complete&field=complete_at')['buildrequests']}
    changes = get(f'/changes?when_timestamp__ge={t0}&when_timestamp__lt={t1}'
                  '&field=changeid&field=when_timestamp&field=category&field=branch'
                  '&field=revision&field=sourcestamp')['changes']
    buildsets = get(f'/buildsets?submitted_at__ge={t0}&submitted_at__lt={t1 + 86400}'
                    '&field=bsid&field=reason&field=submitted_at&field=sourcestamps')['buildsets']

    def kind(build):
        builder = builders.get(build['builderid'], {})
        if not builder.get('tags'):     # worker-check, __Janitor
            return None
        return 'pr' if 'pr' in builder['tags'] else 'branch'

    if any(not b['complete'] for b in builds if kind(b)):
        return None
    merges = [c for c in changes if not (c['category'] or '').startswith('pr-')
              and c['branch'] in ('develop', 'maintenance')]
    stats = {'day': day.isoformat(), 'timezone': TIMEZONE,
             'merges': dict(sorted(Counter(c['branch'] for c in merges).items())),
             'pr_pushes': len({c['revision'] for c in changes
                               if (c['category'] or '').startswith('pr-')})}

    today = [b for b in builds if t0 <= b['started_at'] < t1 and kind(b)]
    failed = sorted({(b['builderid'], builders[b['builderid']]['name']) for b in today
                     if kind(b) == 'branch' and b['results'] in FAILED}, key=lambda f: f[1])
    stats['builds'] = {
        'branch': sum(1 for b in today if kind(b) == 'branch'),
        'pr': sum(1 for b in today if kind(b) == 'pr'),
        'failed': sum(1 for b in today if kind(b) == 'branch' and b['results'] in FAILED),
        'pr_failed': sum(1 for b in today if kind(b) == 'pr' and b['results'] in FAILED),
        'canceled': sum(1 for b in today if b['results'] == CANCELLED)}
    stats['failed_builders'] = [list(f) for f in failed]

    # from a merge to the last of its CPU and VM builds; the GPU builds wait
    # for their window and are left out.  the set of a merge has its commit
    merge_of = {c['sourcestamp']['ssid']: c for c in merges if c.get('sourcestamp')}
    stats['merge_minutes'] = []
    for bs in buildsets:
        if not ("named 'develop'" in bs['reason'] or "named 'maintenance'" in bs['reason']):
            continue
        ssids = [ss['ssid'] for ss in bs.get('sourcestamps', [])]
        change = next((merge_of[ssid] for ssid in ssids if ssid in merge_of), None)
        if change is None:
            continue
        mine = [r for r in requests.values() if r['buildsetid'] == bs['bsid']
                and 'gpu' not in builders.get(r['builderid'], {}).get('tags', [])]
        if not mine:
            continue
        if not all(r['complete'] for r in mine):
            return None
        last = max(r['complete_at'] for r in mine)
        stats['merge_minutes'].append(round((last - change['when_timestamp']) / 60, 1))

    # how long the CPU builds waited for a worker: they have no time window
    # outside rstudio, so a long wait means that all workers were busy
    stats['queue_seconds'] = sorted(
        max(0, b['started_at'] - requests[b['buildrequestid']]['submitted_at'])
        for b in today if 'cpu' in builders[b['builderid']]['tags']
        and b['buildrequestid'] in requests)

    busy, available = Counter(), Counter()
    for b in builds:
        if kind(b) and b['workerid'] in names and b['complete_at']:
            overlap = min(b['complete_at'], t1) - max(b['started_at'], t0)
            if overlap > 0:
                busy[pool_of(names[b['workerid']])] += overlap / 3600
    for w in workers:
        if w['configured_on']:
            available[pool_of(w['name'])] += window_hours(w['name'])
    stats['busy_hours'] = {pool: round(hours, 2) for pool, hours in sorted(busy.items())}
    stats['available_hours'] = dict(sorted(available.items()))
    return stats

def archive_days(datadir, back, dry_run=False):
    '''archive the days that are over and missing; returns how many'''
    tz = ZoneInfo(TIMEZONE)
    today = datetime.datetime.now(tz).date()
    folder = os.path.join(datadir, buildbotdata.BUILDBOTDIR)
    created = 0
    for offset in range(back, 0, -1):
        day = today - datetime.timedelta(days=offset)
        path = os.path.join(folder, f'{day.isoformat()}.json')
        if day < FIRST_DAY or os.path.exists(path):
            continue
        try:
            stats = day_stats(day, tz)
        except Exception as err:
            print(f"WARNING: skipping the statistics of {day}: {API}: {err}", file=sys.stderr)
            continue
        if stats is None:
            print(f"the statistics of {day} wait for builds that still run")
            continue
        if dry_run:
            print(f"would archive {path}: {stats['builds']}")
            created += 1
            continue
        os.makedirs(folder, exist_ok=True)
        # the lists of numbers (a few hundred waits a day) one line each
        text = re.sub(r'\[([-\d.,\s]+)\]', lambda m: '[' + ' '.join(m.group(1).split()) + ']',
                      json.dumps(stats, indent=1))
        with open(path, 'w') as f:
            f.write(text + '\n')
        print(f"archived {path}")
        created += 1
    return created

def snapshot(path, dry_run=False):
    '''the live state of the buildbot: builders, workers, and hardware'''
    builders = [b for b in get('/builders?field=name&field=tags&field=masterids')['builders']
                if b['masterids'] and b['tags']]
    workers = [w for w in get('/workers?field=name&field=configured_on&field=connected_to')['workers']
               if w['configured_on'] and not w['name'].startswith('__')]
    pr = sum(1 for b in builders if 'pr' in b['tags'])
    state = {'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'url': BUILDBOT, 'builders': len(builders) - pr, 'pr_builders': pr,
             'workers': len(workers),
             'disconnected': sorted(w['name'] for w in workers if not w['connected_to'])}
    try:
        request = urllib.request.Request(HOSTS_URL,
                                         headers={'User-Agent': 'lammps-test-results ingest'})
        with urllib.request.urlopen(request, timeout=60) as response:
            hosts = json.loads(response.read())
        if isinstance(hosts.get('machines'), dict):
            state['hardware'] = hosts
    except Exception as err:
        print(f"WARNING: no hardware: {HOSTS_URL}: {err}", file=sys.stderr)
    if dry_run:
        print(f"would write {path}: {state['builders']} builders, {state['workers']} workers")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(state, f, indent=1)
        f.write('\n')

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch the example input check and the statistics"
                                        " from the LAMMPS buildbot")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS,
                        help="How many days back to look for builds of the example input check")
    parser.add_argument("--stats-days", type=int, default=STATS_DAYS,
                        help="How many days back to fill in missing statistics")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be ingested")
    args = parser.parse_args()

    try:
        builderid, builds = candidate_builds(args.days)
        created = sum(ingest(builderid, build, args.datadir, args.dry_run) for build in builds)
        print(f"{created} new {SUITE} run(s) of {len(builds)} build(s) of {BRANCH}")
    except Exception as err:
        print(f"WARNING: skipping {SUITE}: {API}: {err}", file=sys.stderr)
    print(f"{archive_days(args.datadir, args.stats_days, args.dry_run)} new day(s) of statistics")
    try:
        snapshot(os.path.join(args.datadir, 'external', 'buildbot.json'), args.dry_run)
    except Exception as err:
        print(f"WARNING: no live state: {API}: {err}", file=sys.stderr)
