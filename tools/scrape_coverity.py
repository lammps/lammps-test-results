#!/usr/bin/env python3
'''
Scrape the publicly visible "Analysis Metrics" summary from the Coverity Scan
project overview page and store it as data/external/coverity.json for the
dashboard. Only the Python standard library is required.

The page renders the metrics as <dl class="kpi"><dd><em>value</em></dd>
<dt>label</dt></dl> pairs; we collect all of them plus the analyzed version.
If the page cannot be fetched or parsed, the existing JSON file is left
untouched so the dashboard keeps showing the last known values.

Usage: python3 tools/scrape_coverity.py [--project lammps-lammps]
                                        [--output data/external/coverity.json]
'''

from argparse import ArgumentParser
import json
import re
import sys
import urllib.request

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

if __name__ == "__main__":
    parser = ArgumentParser(description="Scrape Coverity Scan analysis metrics")
    parser.add_argument("--project", default="lammps-lammps",
                        help="Coverity Scan project name")
    parser.add_argument("--output", default="data/external/coverity.json",
                        help="Output JSON file")
    args = parser.parse_args()

    url = f"https://scan.coverity.com/projects/{args.project}"
    try:
        metrics, version = scrape(url)
    except Exception as err:
        print(f"WARNING: could not fetch {url}: {err}", file=sys.stderr)
        sys.exit(0)
    if not metrics:
        print(f"WARNING: no analysis metrics found on {url}", file=sys.stderr)
        sys.exit(0)

    data = {
        'metrics': metrics,
        'version': version,
        'date': metrics.get('Last Analyzed', ''),
        'url': url,
    }
    with open(args.output, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"{args.output}: {', '.join(f'{k}={v}' for k, v in metrics.items())}")
