#!/usr/bin/env python3
'''
Fetch the performance benchmark results published on download.lammps.org and
archive them under data/performance/<machine>/<runid>/run.json.

Three machines run benchmarks after their nightly tests and publish them under
https://download.lammps.org/performance/, each under a prefix of its own
(perfdata.MACHINES): "gpu" (the machine with the V100 GPUs, after the unit
tests of the GPU builds), "cpu" (the machine of the full regression tests),
and "intel" (the machine with the Intel Xeon CPUs, after the unit tests of the
oneAPI build).  Each publishes <prefix>-summary.json, which this script reads,
next to a table of every sample (<prefix>-results.tsv) and the logs of the
runs (<prefix>-logs.tar.gz), which it does not: the summary carries every
sample as well.  The summary records the commit in full, the branch, the git
describe string, the UTC date of the run, the machine and the build, and per
benchmark its category on the dashboard, the deck and the layout of the run,
the atom-steps per second of every sample, and whether it ran at all.

The benchmarks run only when the branch a machine tests has changed, and only
the latest results are published, so the same rules apply as for the unit
test runs (tools/fetch_unittest.py): results whose date and commit are already
archived are skipped, and results of a commit that is already archived
replace the run archived for it.  A commit is benchmarked twice only when the
run was repeated by hand, because the first one was disturbed or broke off.

A benchmark that has no reference yet is given one from the run it first
succeeds in (perfdata.REFERENCES): it is what its later runs are measured
against, until a new reference is set with tools/perf_reference.py.

A source that is unreachable or does not deliver a usable summary is skipped
with a warning, leaving the archived runs untouched.

Usage: python3 tools/fetch_performance.py [--datadir data] [--dry-run]
                                          [--machine MACHINE --url URL]

Without --url, the summaries of all machines are fetched.  --url also takes
a file:// URL, which is how runs that were saved before this script existed
can be archived.
'''

from argparse import ArgumentParser
import json
import os
import shutil
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import fetch_unittest
import perfdata
import rundata

URL = 'https://download.lammps.org/performance/{}-summary.json'
# the fields of the summary that describe the machine and the build rather
# than the run, kept as properties of it.  the "cpu_" ones are those of the
# GCC builds the Intel machine runs beside its oneAPI build
SUMMARY_PROPERTIES = ('compiler', 'operating_system', 'mpi', 'cpu', 'gpus',
                      'gpu_driver', 'cpu_compiler', 'cpu_mpi')
# what is kept of a benchmark: the time of each loop is left out, the
# samples are the same number per atom and step
ENTRY_FIELDS = ('category', 'input', 'size', 'precision', 'package', 'api',
                'flags', 'ranks', 'threads', 'gpus', 'natoms', 'steps',
                'samples', 'status', 'other_cpu')

def ingest(url, datadir, machine, dry_run=False):
    '''archive the published results of one machine; returns 1 if they were new'''
    try:
        raw, published = fetch_unittest.fetch_url(url)
        summary = json.loads(raw.decode('utf-8'))
    except Exception as err:
        print(f"WARNING: skipping {machine}: {url}: {err}", file=sys.stderr)
        return 0
    results = summary.get('results') if isinstance(summary, dict) else None
    if not isinstance(results, dict) or not results:
        print(f"WARNING: skipping {machine}: {url}: no results", file=sys.stderr)
        return 0
    sha = str(summary.get('commit', ''))
    generated = summary.get('date', '')
    if not sha or not rundata.utc_stamp(generated):
        print(f"WARNING: skipping {machine}: {url}: no commit or no UTC date",
              file=sys.stderr)
        return 0

    entries = {}
    unknown = []
    for name, result in results.items():
        # a benchmark that is not assigned to a category cannot be shown
        # anywhere, which is a mistake on the publishing side
        if result.get('category') not in perfdata.CATEGORY_KEYS:
            unknown.append(name)
            continue
        entries[name] = {key: result[key] for key in ENTRY_FIELDS if key in result}
    if unknown:
        print(f"WARNING: {machine}: {url}: {len(unknown)} result(s) without a"
              f" known category left out, e.g. {unknown[0]}", file=sys.stderr)
    if not entries:
        print(f"WARNING: skipping {machine}: {url}: no usable results", file=sys.stderr)
        return 0

    suite = f'{perfdata.PERFDIR}/{machine}'
    for runid in perfdata.list_runs(datadir, machine):
        meta = perfdata.load_run(datadir, machine, runid)['metadata']
        if meta.get('generated') == generated and meta.get('sha', '')[:10] == sha[:10]:
            print(f"{suite}: already archived as {runid}")
            return 0
    # one run per commit: a commit benchmarked again was benchmarked again
    # for a reason, and the repeat is what stands
    superseded = [runid for runid in perfdata.list_runs(datadir, machine)
                  if perfdata.load_run(datadir, machine, runid)['metadata']
                  .get('sha', '')[:10] == sha[:10]]
    runid = rundata.run_id_string(published, generated, sha)
    rundir = os.path.join(perfdata.perfdir(datadir), machine, runid)
    if os.path.isdir(rundir):
        print(f"{suite}: {runid} already archived")
        return 0

    properties = {key: summary[key] for key in SUMMARY_PROPERTIES if summary.get(key)}
    meta = {'title': f'Performance {machine}',
            'generated': generated,
            'properties': properties,
            'branch': summary.get('branch', ''),
            'version': summary.get('version', ''),
            'sha': sha,
            'source_url': url}
    failed = sum(1 for entry in entries.values() if entry.get('status') != 'ok')

    if dry_run:
        print(f"would ingest {suite}/{runid}: {len(entries)} benchmarks,"
              f" {failed} failed")
        for old in superseded:
            print(f"would replace {suite}/{old}: same commit {sha[:10]}")
        return 1

    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, 'run.json'), 'w') as f:
        json.dump({'metadata': meta, 'entries': entries}, f, indent=2)
        f.write('\n')
    print(f"ingested {suite}/{runid} from {url}: {len(entries)} benchmarks,"
          f" {failed} failed")
    # as for the test runs: the old run goes only once the new one is on disk
    for old in superseded:
        shutil.rmtree(os.path.join(perfdata.perfdir(datadir), machine, old))
        print(f"{suite}: replaced {old}, same commit {sha[:10]}")
    perfdata.load_run.cache_clear()

    # the first successful run of a benchmark is its reference
    refs = perfdata.load_references(datadir)
    added = 0
    for name, entry in entries.items():
        value = perfdata.entry_value(entry)
        known = refs.setdefault(machine, {}).setdefault(name, [])
        if value and not known:
            known.append({'run': runid, 'value': value, 'note': 'first run'})
            added += 1
    if added:
        perfdata.save_references(datadir, refs)
        print(f"{suite}: {added} new reference(s) from {runid}")
    return 1

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch the published performance benchmarks")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--machine", default=None,
                        help="Machine the results of --url are archived under")
    parser.add_argument("--url", default=None,
                        help="URL of a summary JSON file (default: all machines)")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be ingested")
    args = parser.parse_args()

    if args.url and not args.machine:
        parser.error("--url needs --machine")
    sources = [(machine, URL.format(machine)) for machine in perfdata.MACHINES]
    if args.url:
        sources = [(args.machine, args.url)]
    total = 0
    for machine, url in sources:
        total += ingest(url, args.datadir, machine, args.dry_run)
    print(f"ingested {total} new benchmark run(s)")
