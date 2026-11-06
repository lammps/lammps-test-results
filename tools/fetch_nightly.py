#!/usr/bin/env python3
'''
Fetch the status of the newest nightly package build from download.lammps.org
and archive it as data/nightly/<folder>.json (see tools/nightlydata.py for the
layout).  Only the Python standard library is required.

The packages of LAMMPS and LAMMPS-GUI are built by create-nightly.sh in the
lammps-release repository, and only when develop has changed since the last
build, so the status file stays what it is on the other nights.  Only the
status of the newest build is published, which is why it has to be archived
here for the dashboard to have a history at all.  A folder is rewritten when
a build is repeated on the same day, and so is its file here.

A status file that is unreachable or not usable is skipped with a warning,
leaving the archive untouched.

Usage: python3 tools/fetch_nightly.py [--datadir data] [--url URL] [--dry-run]

--url also takes a file:// URL.
'''

from argparse import ArgumentParser
import json
import os
import sys
import urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import nightlydata

URL = 'https://download.lammps.org/coverage/nightly.json'

def fetch_json(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch the status of the nightly package build")
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
    problem = nightlydata.usable(status)
    if problem:
        print(f"WARNING: skipping {args.url}: {problem}", file=sys.stderr)
        sys.exit(0)

    outdir = os.path.join(args.datadir, nightlydata.NIGHTLYDIR)
    outfile = os.path.join(outdir, status['folder'] + '.json')
    failed = [task for task in status['tasks'] if not nightlydata.passed(status, task)]
    summary = (f"{len(status['tasks'])} tasks, {len(failed)} failed"
               + (f" ({', '.join(failed)})" if failed else ''))
    if args.dry_run:
        print(f"would write {outfile}: {summary}")
        sys.exit(0)
    os.makedirs(outdir, exist_ok=True)
    with open(outfile, 'w') as f:
        json.dump(status, f, indent=2)
        f.write('\n')
    print(f"updated {outfile} from {args.url}: {summary}")
