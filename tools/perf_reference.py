#!/usr/bin/env python3
'''
Set new references for archived performance benchmarks.

A benchmark is measured against the reference it was given from its first
successful run (tools/fetch_performance.py).  When a change of the code, the
compilers, or the machine makes it faster or slower for good, that is
acknowledged here: the benchmarks named take their value of the run given as
their new reference, from that run on.  The runs before it keep being
measured against the reference they had, so the history of a category still
shows the step where the change came in.

A reference is a single run, the newest one by default, or the median of the
runs from the one given up to the newest with --through-latest, which is the
better choice once a few runs of the new state have come in.

Usage: python3 tools/perf_reference.py --machine MACHINE
                                       [--match REGEX] [--category CATEGORY]
                                       [--run RUNID] [--through-latest]
                                       --note TEXT [--datadir data] [--dry-run]

Every benchmark of the machine whose name matches --match (a regular
expression searched for in the name) and that is in --category is reset; at
least one of the two has to be given.
'''

from argparse import ArgumentParser
import os
import re
import statistics
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import perfdata

if __name__ == "__main__":
    parser = ArgumentParser(description="Set new performance references")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--machine", required=True, choices=perfdata.MACHINES)
    parser.add_argument("--match", default=None,
                        help="Regular expression for the benchmark names")
    parser.add_argument("--category", default=None, choices=perfdata.CATEGORY_KEYS)
    parser.add_argument("--run", default=None,
                        help="Run id the reference is taken from (default: newest)")
    parser.add_argument("--through-latest", action='store_true', default=False,
                        help="Use the median of the runs from --run to the newest")
    parser.add_argument("--note", required=True, help="Why the reference is reset")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be changed")
    args = parser.parse_args()

    if not args.match and not args.category:
        parser.error("give --match, --category, or both")
    runs = perfdata.list_runs(args.datadir, args.machine)
    if not runs:
        sys.exit(f"no archived runs of {args.machine}")
    first = args.run or runs[-1]
    if first not in runs:
        sys.exit(f"{args.machine} has no run {first}")
    used = runs[runs.index(first):] if args.through_latest else [first]
    pattern = re.compile(args.match) if args.match else None

    refs = perfdata.load_references(args.datadir)
    entries = perfdata.load_run(args.datadir, args.machine, first)['entries']
    changed = 0
    for name, entry in sorted(entries.items()):
        if pattern and not pattern.search(name):
            continue
        if args.category and entry.get('category') != args.category:
            continue
        values = [perfdata.entry_value(perfdata.load_run(
            args.datadir, args.machine, runid)['entries'].get(name, {}))
                  for runid in used]
        values = [value for value in values if value]
        if not values:
            print(f"{name}: no successful run to take a reference from, left alone")
            continue
        value = statistics.median(values)
        old = perfdata.reference(refs, args.machine, name, first)
        change = f'{100 * (value / old - 1):+.2f}%' if old else 'new'
        print(f"{name}: {value:.6g} atom-steps/s from {len(values)} run(s) ({change})")
        known = refs.setdefault(args.machine, {}).setdefault(name, [])
        # a reference set again for the same run replaces the one set before
        known[:] = [ref for ref in known if ref['run'] != first]
        known.append({'run': first, 'value': value, 'note': args.note})
        known.sort(key=lambda ref: ref['run'])
        changed += 1
    if changed and not args.dry_run:
        perfdata.save_references(args.datadir, refs)
    print(f"{'would set' if args.dry_run else 'set'} {changed} reference(s)"
          f" from {first}")
