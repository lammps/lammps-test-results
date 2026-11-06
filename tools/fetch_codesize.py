#!/usr/bin/env python3
'''
Fetch the lines of code of LAMMPS from download.lammps.org and archive them as
data/codesize/<week>.json (see tools/codesizedata.py for the layout).  Only the
Python standard library is required.

The lines are counted once a week by codesize.py in the lammps-analyze
repository, which is why nothing changes here on the other days.  Only the
newest week is published, which is why it has to be archived here for the
dashboard to have a history at all.

A status file that is unreachable or not usable is skipped with a warning,
leaving the archive untouched.

Usage: python3 tools/fetch_codesize.py [--datadir data] [--url URL] [--dry-run]

--url also takes a file:// URL.
'''

from argparse import ArgumentParser
import json
import os
import sys
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import codesizedata

URL = 'https://download.lammps.org/analysis/codesize.json'

def fetch_json(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch the lines of code of LAMMPS")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--url", default=URL, help="URL of the status file")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be archived")
    args = parser.parse_args()

    try:
        status = fetch_json(args.url)
    except Exception as err:
        print(f"WARNING: skipping {args.url}: {err}", file=sys.stderr)
        sys.exit(0)
    problem = codesizedata.usable(status)
    if problem:
        print(f"WARNING: skipping {args.url}: {problem}", file=sys.stderr)
        sys.exit(0)

    outdir = os.path.join(args.datadir, codesizedata.CODESIZEDIR)
    outfile = os.path.join(outdir, status['week'] + '.json')
    summary = f"{sum(status['lines'].values())} lines in {len(status['lines'])} folders"
    if args.dry_run:
        print(f"would write {outfile}: {summary}")
        sys.exit(0)
    os.makedirs(outdir, exist_ok=True)
    with open(outfile, 'w') as f:
        json.dump(status, f, indent=2, sort_keys=True)
        f.write('\n')
    print(f"updated {outfile} from {args.url}: {summary}")
