#!/usr/bin/env python3
'''
Shared helpers for the archived statistics of the LAMMPS buildbot
(https://ci.lammps.org): the data layout written by tools/fetch_buildbot.py,
and the way the website reads it.

The layout is one file per day, in New York time, the time of the build
windows of the CI:

    data/buildbot/<day>.json

<day> is "YYYY-MM-DD", so that the files sort chronologically.  A day is only
written once it is over and nothing of it is still running (or two days after
it ended at the latest, see "unfinished" below), and it is never written again,
so every update of the website on the following days shows the same numbers
for it:

    {"day": <day>, "timezone": "America/New_York",
     "merges": {"develop": <n>, "maintenance": <n>},
     "pr_pushes": <distinct head commits of pull requests that were built>,
     "builds": {"branch": <n>, "pr": <n>, "failed": <n>, "pr_failed": <n>,
                "canceled": <n>},
     "failed_builders": [[<builder id>, <name>], ...],
     "merge_minutes": [<minutes from a merge to its last CPU and VM build>, ...],
     "queue_seconds": [<seconds a CPU build waited for a worker>, ...],
     "busy_hours": {<pool>: <hours of builds>},
     "available_hours": {<pool>: <hours the workers of the pool may build>},
     "unfinished": {"builds": <n>, "merges": <n>}}

"unfinished" is only there for a day that was archived before everything of it
had finished, two days after it ended (tools/fetch_buildbot.py, FINAL_DAYS):
the builds still running then, and the merges whose CPU and VM builds had not
all finished, which have no time from merge to results.

"branch" builds are those of develop and maintenance, forced builds, and
rebuilds; "failed" counts those that failed or broke (pull request builds
fail by design whenever the pull request does, so they are counted apart).
The pools are "gpu" (the GPU machine, its build window only), "cpu" (compute,
c01-c08, and rstudio within its window), and "vm" (the VMs on compute).

The live state at the time of the update - builders, workers, and the
hardware the monitor page publishes - is data/external/buildbot.json:

    {"generated": <ISO time>, "url": <the buildbot>,
     "builders": <n>, "pr_builders": <n>, "workers": <n>,
     "disconnected": [<worker name>, ...],
     "hardware": {"updated": ..., "machines": {<name>: {"model": ...,
                  "sockets": <n>, "cores": <n>, "threads": <n>,
                  "memory_gib": <n>, "gpus": <n>, "gpu_memory_gib": [...],
                  "master": <bool>}}}}
'''

import datetime
import json
import os
import re
import statistics

BUILDBOTDIR = 'buildbot'
DAY = re.compile(r'\d{4}-\d{2}-\d{2}\.json$')
# the days the card averages over
WEEK = 7

# the unit test results taken from builds of develop (tools/fetch_buildbot.py):
# builder -> configuration of the unit-tests suite.  the first ones continue
# the configurations that came from GitHub Actions until November 2026, under
# the same names, so that their history goes on (tools/ingest_actions.py takes
# them from there no more); the others are covered by no other source.  the
# builders that another source covers more broadly are left out: those of the
# GPU machine (its nightly runs, six builds), kokkos-openmp-* (the OpenCL
# builds of the GPU machine), intel-mkl (the oneAPI run of rstudio, with Intel
# MPI), macos-x86_64 (the macOS 12 VM with OpenMP of the coverage machine)
UNITTEST_BUILDERS = {
    'kokkos-serial-double': 'kokkos-serial-double',
    'kokkos-serial-mixed': 'kokkos-serial-mixed',
    'kokkos-serial-single': 'kokkos-serial-single',
    'gcc-bigbig': 'linux-x86_64-bigbig',
    'gcc-fftsingle': 'linux-x86_64-fftsingle',
    'windows-msvc': 'windows-msvc',
    'gcc-basic': 'linux-x86_64-basic',
    'gcc11-alma9': 'linux-x86_64-alma9',
    'clang-cxx17': 'linux-x86_64-clang',
    'cpp23-gcc': 'linux-x86_64-cpp23-gcc',
    'cpp23-clang': 'linux-x86_64-cpp23-clang',
    'macos26-x86_64': 'macos26-x86_64',
    'freebsd-x86_64': 'freebsd-x86_64',
}
# everything taken from builds of develop: builder -> (step, suite, title of
# the run).  the step keeps the JUnit file as its log "junit"
RESULTS = {'check-examples': ('examples', 'check-examples', 'Example input -skiprun check')}
RESULTS.update({builder: ('ctest', f'unit-tests/{config}', f'Unit Tests {config}')
                for builder, config in UNITTEST_BUILDERS.items()})

def list_days(datadir):
    folder = os.path.join(datadir, BUILDBOTDIR)
    if not os.path.isdir(folder):
        return []
    return sorted(name[:-5] for name in os.listdir(folder) if DAY.fullmatch(name))

def load_days(datadir, count=WEEK):
    '''the newest "count" archived days, oldest first'''
    days = []
    for day in list_days(datadir)[-count:]:
        try:
            with open(os.path.join(datadir, BUILDBOTDIR, f'{day}.json')) as f:
                days.append(json.load(f))
        except (OSError, ValueError):
            continue
    return days

def percentile(values, fraction):
    '''the value below which "fraction" of the values lie (nearest rank)'''
    values = sorted(values)
    if not values:
        return None
    return values[min(len(values) - 1, max(0, round(fraction * len(values)) - 1))]

def totals(days):
    '''the sums, lists, and per-day means of a list of days, for the card'''
    out = {'days': len(days), 'merges': 0, 'pr_pushes': 0, 'branch': 0, 'pr': 0,
           'failed': 0, 'pr_failed': 0, 'merge_minutes': [], 'queue_seconds': [],
           'busy_hours': {}, 'available_hours': {}}
    for day in days:
        out['merges'] += sum(day.get('merges', {}).values())
        out['pr_pushes'] += day.get('pr_pushes', 0)
        builds = day.get('builds', {})
        for key in ('branch', 'pr', 'failed', 'pr_failed'):
            out[key] += builds.get(key, 0)
        out['merge_minutes'] += day.get('merge_minutes', [])
        out['queue_seconds'] += day.get('queue_seconds', [])
        for key in ('busy_hours', 'available_hours'):
            for pool, hours in day.get(key, {}).items():
                out[key][pool] = out[key].get(pool, 0) + hours
    return out

def median(values):
    return statistics.median(values) if values else None

def utilization(sums, pool):
    '''busy share of the available hours of a pool in percent, or None'''
    available = sums['available_hours'].get(pool, 0)
    if not available:
        return None
    return 100 * sums['busy_hours'].get(pool, 0) / available

def newest_day(days):
    if not days:
        return None
    try:
        return datetime.date.fromisoformat(days[-1]['day'])
    except (KeyError, ValueError):
        return None
