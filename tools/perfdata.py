#!/usr/bin/env python3
'''
Shared helpers for the archived performance benchmark results: the data
layout written by tools/fetch_performance.py and tools/perf_reference.py,
and the way the website reads it.  The counterpart of tools/rundata.py for
the test runs.

The layout is:

    data/performance/<machine>/<runid>/run.json
    data/performance/references.json

<machine> is the prefix the benchmark machine publishes its files under on
download.lammps.org/performance/ ("gpu", "cpu", "intel"; MACHINES), and
<runid> is "<ISO timestamp>_<short sha>" as for the test runs, so that it
sorts chronologically.  A run.json holds a "metadata" object (title,
generated, branch, version, sha, source_url, and the description of the
machine and the build as "properties") and an "entries" object keyed by the
name of the benchmark:

    {"category": <CATEGORIES key>, "input": <deck>, "size": ..., "precision":
     ..., "package": ..., "api": ..., "flags": ..., "ranks": <MPI ranks>,
     "threads": <threads per rank>, "gpus": <GPUs>, "natoms": <atoms>,
     "steps": <MD steps>, "samples": [<atom-steps/s of each copy>, ...],
     "status": "ok" | "failed", "other_cpu": <cores used by other processes>}

The GPU machine runs every benchmark once per GPU at the same time, so its
entries carry several samples; the CPU machines run each once.

references.json records, per machine and benchmark, what a result is
measured against, as a list of the references it had over time:

    {"<machine>": {"<name>": [{"run": <runid>, "value": <atom-steps/s>,
                               "note": <why it was set>}, ...]}}

A reference holds from the run it names on, until the next one in the list:
a code change that makes a benchmark permanently faster or slower is
acknowledged by a new reference, without rewriting how the runs before it
compared to the one they were measured against.  A benchmark is given its
first reference automatically, from its first successful run
(tools/fetch_performance.py); later ones are set by hand
(tools/perf_reference.py).
'''

import datetime
import functools
import json
import math
import os

PERFDIR = 'performance'
REFERENCES = 'references.json'

# the machines, by the prefix of the files they publish, in the order the
# website lists them.  the prefixes are names of roles rather than of hosts:
# the machine behind one can change
MACHINES = ('gpu', 'cpu', 'intel')
MACHINE_DETAILS = {
    'gpu': 'the machine with NVIDIA V100 GPUs: the GPU and KOKKOS packages'
           ' through CUDA, and the GPU package through OpenCL',
    'cpu': 'the machine that runs the full regression tests: benchmarks of'
           ' the CPU styles, with GCC and MPICH',
    'intel': 'the machine with Intel Xeon CPUs: the INTEL package with the'
             ' Intel oneAPI compilers, and GCC builds for comparison',
}

# the categories a benchmark is reported in, as (key, short label, title,
# description), in the order the website stacks and lists them.  the key is
# the "category" field of the published results.  a category is measured on
# whichever machines run benchmarks of it, so the CPU/MPI and KOKKOS/OpenMP
# numbers merge the CPU machine and the GCC builds of the Intel machine
CATEGORIES = (
    ('gpu', 'GPU', 'GPU package',
     'GPU package through CUDA and OpenCL, in all precisions'),
    ('kokkos-cuda', 'KK/CUDA', 'KOKKOS/CUDA',
     'KOKKOS package on its CUDA backend, one GPU per run'),
    ('kokkos-omp', 'KK/OpenMP', 'KOKKOS/OpenMP',
     'KOKKOS package on its OpenMP backend'),
    ('cpu-mpi', 'CPU/MPI', 'CPU/MPI',
     'plain, OPENMP, and OPT styles of the standard benchmark decks with MPI'),
    ('cpu-only', 'CPU only', 'CPU only',
     'the benchmark decks of styles only the CPU machines run, any package'),
    ('intel', 'INTEL', 'INTEL package',
     'INTEL package with the Intel oneAPI compilers, mixed and double precision'),
)
CATEGORY_KEYS = tuple(key for key, _, _, _ in CATEGORIES)

def perfdir(datadir):
    return os.path.join(datadir, PERFDIR)

def list_machines(datadir):
    '''the machines with at least one archived run, in the order of MACHINES
       (any other one after them, alphabetically)'''
    base = perfdir(datadir)
    if not os.path.isdir(base):
        return []
    found = [name for name in os.listdir(base)
             if os.path.isdir(os.path.join(base, name)) and list_runs(datadir, name)]
    return sorted(found, key=lambda name: (MACHINES.index(name)
                                           if name in MACHINES else len(MACHINES), name))

def list_runs(datadir, machine):
    '''the run ids of a machine, oldest first'''
    path = os.path.join(perfdir(datadir), machine)
    if not os.path.isdir(path):
        return []
    return sorted(entry for entry in os.listdir(path)
                  if os.path.isfile(os.path.join(path, entry, 'run.json')))

@functools.lru_cache(maxsize=64)
def load_run(datadir, machine, runid):
    with open(os.path.join(perfdir(datadir), machine, runid, 'run.json')) as f:
        return json.load(f)

def load_references(datadir):
    path = os.path.join(perfdir(datadir), REFERENCES)
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)

def save_references(datadir, refs):
    path = os.path.join(perfdir(datadir), REFERENCES)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(refs, f, indent=2, sort_keys=True)
        f.write('\n')

def entry_value(entry):
    '''the atom-steps/s a benchmark is judged by, or None for one that failed.

       of several samples, the best: the copies on the four GPUs run side by
       side, and what sets one of them apart is what disturbed it - over the
       clean nights of the calibration runs the best copy moved by 0.7% at
       most from night to night, the median by up to 2.2%'''
    if entry.get('status') != 'ok':
        return None
    samples = [value for value in entry.get('samples', []) if value]
    return max(samples) if samples else None

def reference(refs, machine, name, runid):
    '''the reference value a benchmark of this run is measured against: the
       last one set at or before this run, or None'''
    found = None
    for ref in refs.get(machine, {}).get(name, []):
        if ref['run'] <= runid:
            found = ref
    return found['value'] if found else None

def night_of(runid):
    '''the UTC day a run belongs to.  the machines run in the night between
       04:00 and 10:00 UTC, one after the other, so the runs of one night
       share their day'''
    try:
        return datetime.datetime.strptime(runid.split('_')[0],
                                          '%Y-%m-%dT%H-%M-%SZ').date()
    except ValueError:
        return None

def geometric_mean(values):
    return math.exp(sum(math.log(value) for value in values) / len(values))

def ratios(datadir, refs, machine, runid):
    '''every benchmark of a run as (name, entry, value, ratio to its
       reference); value and ratio are None where the run failed or has no
       reference to be measured against yet'''
    out = []
    for name, entry in sorted(load_run(datadir, machine, runid)['entries'].items()):
        value = entry_value(entry)
        ref = reference(refs, machine, name, runid)
        ratio = value / ref if value and ref else None
        out.append((name, entry, value, ratio))
    return out

def nightly(datadir, refs, nights):
    '''the performance of every category over the last "nights" days, as a
       list of (day, {machine: (runid, fresh)}, {category: score}) from the
       oldest day to the newest, which is the newest day any machine ran.

       a machine runs only when the branch it tests has changed, so on a
       night without a run of its own the results of its last run stand: the
       code did not change.  a category therefore always merges the newest
       results of every machine that runs benchmarks of it, and "fresh"
       tells a run of that very night from one carried over.  a score is

           {"percent": <geometric mean of the ratios to the references>,
            "entries": <benchmarks measured>, "failed": <benchmarks that
            failed>, "fresh": <any of those runs is of that night>,
            "machines": {machine: runid}}

       the geometric mean, since the numbers are ratios: a benchmark that
       runs twice as fast and one that runs half as fast cancel out'''
    runs = {machine: [(night_of(runid), runid) for runid in list_runs(datadir, machine)]
            for machine in list_machines(datadir)}
    days = [day for listed in runs.values() for day, _ in listed if day]
    if not days:
        return []
    newest = max(days)
    out = []
    for back in range(nights - 1, -1, -1):
        day = newest - datetime.timedelta(days=back)
        current = {}
        for machine, listed in runs.items():
            older = [(when, runid) for when, runid in listed if when and when <= day]
            if older:
                current[machine] = (older[-1][1], older[-1][0] == day)
        scores = {}
        for machine, (runid, fresh) in current.items():
            for _, entry, value, ratio in ratios(datadir, refs, machine, runid):
                score = scores.setdefault(entry.get('category', ''), {
                    'ratios': [], 'failed': 0, 'fresh': False, 'machines': {}})
                score['machines'][machine] = runid
                score['fresh'] = score['fresh'] or fresh
                if value is None:
                    score['failed'] += 1
                elif ratio is not None:
                    score['ratios'].append(ratio)
        for score in scores.values():
            found = score.pop('ratios')
            score['entries'] = len(found)
            score['percent'] = 100 * geometric_mean(found) if found else None
        out.append((day, current, scores))
    return out
