#!/usr/bin/env python3
'''
Fetch the summary JSON files that the nightly server-side analysis script
publishes next to its HTML reports on download.lammps.org and store them
under data/external/ for the dashboard. Only the Python standard library
is required.

A source that is unreachable, returns invalid JSON, or is not a JSON
object is skipped with a warning and the existing file is left untouched,
so the dashboard keeps showing the last known values.

Usage: python3 tools/fetch_external.py [--datadir data]
'''

from argparse import ArgumentParser
import json
import os
import sys
import urllib.request

SOURCES = {
    'analysis': 'https://download.lammps.org/analysis/summary.json',
    'coverage': 'https://download.lammps.org/coverage/summary.json',
}

def fetch_json(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch external report summaries")
    parser.add_argument("--datadir", default="data", help="Data directory")
    args = parser.parse_args()

    extdir = os.path.join(args.datadir, 'external')
    os.makedirs(extdir, exist_ok=True)
    for name, url in SOURCES.items():
        try:
            data = fetch_json(url)
        except Exception as err:
            print(f"WARNING: skipping {name}: {url}: {err}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            print(f"WARNING: skipping {name}: {url}: not a JSON object", file=sys.stderr)
            continue
        outfile = os.path.join(extdir, name + '.json')
        with open(outfile, 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        print(f"updated {outfile} from {url}")
