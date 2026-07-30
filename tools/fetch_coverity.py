#!/usr/bin/env python3
'''
Collect what is known about the Coverity Scan analysis of LAMMPS and store it
as data/external/coverity.json for the dashboard. Only the Python standard
library is required.

The file has two halves, collected from two sources that know different
things about the scan:

  metrics  the publicly visible "Analysis Metrics" of the project overview
           page on scan.coverity.com, scraped from the
           <dl class="kpi"><dd><em>value</em></dd><dt>label</dt></dl> pairs
           the page renders them as, plus the analyzed version. This is the
           outcome of an analysis; the page does not say which state of the
           source tree it was run on.
  build    the summary the submitting script (coverity.sh in the
           lammps-analyze repository) publishes next to the reports of the
           other analysis runs: branch, commit, and version of the tree that
           was built for the scan, when that happened, and with which
           compiler on which operating system. This is the input of an
           analysis.

The two are not two views of one run. The build is submitted from here, the
analysis runs on the Coverity servers and can lag the submission
considerably, so the metrics usually describe an earlier submission than the
last one recorded here. Both halves are therefore stored as they come, and
the site generator keeps them apart rather than presenting them as one
report.

A source that cannot be read leaves its half of the file as it was, so the
dashboard keeps showing the last known values of that half.  With neither of
them readable and nothing on file to keep, nothing is written at all.

Usage: python3 tools/fetch_coverity.py [--project lammps-lammps]
                                       [--output data/external/coverity.json]
'''

from argparse import ArgumentParser
import json
import re
import sys
import urllib.request

# the build submitted for scanning, published by the same machine and next to
# the reports of the nightly static analysis, but on a schedule of its own:
# the scan runs twice a week, and only when the monitored branch has changed
BUILD_URL = 'https://download.lammps.org/analysis/coverity.json'
SCHEMA = 1

def scrape(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)'})
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode('utf-8', errors='replace')

    metrics = {}
    for match in re.finditer(
            r'<dd[^>]*>\s*<em>(.*?)</em>\s*</dd>\s*<dt>(.*?)</dt>', page, re.S):
        value = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        label = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        if label and value:
            metrics[label] = value

    version = ''
    match = re.search(r'Version:\s*([0-9a-fA-F]+)', page)
    if match:
        version = match.group(1)
    return metrics, version

def fetch_json(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))

if __name__ == "__main__":
    parser = ArgumentParser(description="Collect Coverity Scan status")
    parser.add_argument("--project", default="lammps-lammps",
                        help="Coverity Scan project name")
    parser.add_argument("--output", default="data/external/coverity.json",
                        help="Output JSON file")
    args = parser.parse_args()

    # what is on file is the fallback for whichever half cannot be read
    try:
        with open(args.output) as f:
            previous = json.load(f)
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}

    url = f"https://scan.coverity.com/projects/{args.project}"
    try:
        metrics, version = scrape(url)
        if not metrics:
            raise RuntimeError('no analysis metrics on the page')
    except Exception as err:
        print(f"WARNING: could not scrape {url}: {err}", file=sys.stderr)
        metrics = previous.get('metrics') or {}
        version = previous.get('version', '')
        date = previous.get('date', '')
    else:
        date = metrics.get('Last Analyzed', '')

    try:
        build = fetch_json(BUILD_URL)
        if not isinstance(build, dict):
            raise RuntimeError('not a JSON object')
        if build.get('schema') != SCHEMA:
            print(f"WARNING: {BUILD_URL}: unexpected schema version "
                  f"{build.get('schema')!r}, reading it anyway", file=sys.stderr)
    except Exception as err:
        print(f"WARNING: could not fetch {BUILD_URL}: {err}", file=sys.stderr)
        build = previous.get('build') or {}

    if not metrics and not build:
        print(f"WARNING: nothing collected, {args.output} left untouched",
              file=sys.stderr)
        sys.exit(0)

    data = {'metrics': metrics, 'version': version, 'date': date, 'url': url}
    if build:
        data['build'] = build
    with open(args.output, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

    if metrics:
        print(f"{args.output}: {', '.join(f'{k}={v}' for k, v in metrics.items())}")
    if build:
        print(f"{args.output}: submitted {build.get('branch', '?')} @ "
              f"{str(build.get('commit', ''))[:10]} on {build.get('built', '?')}")
