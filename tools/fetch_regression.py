#!/usr/bin/env python3
'''
Fetch the results of the full regression test runs from download.lammps.org
and archive them under data/full-regression/<config>/<runid>/run.json.

The runs are no longer done in GitHub Actions but on a dedicated machine with
a much more complete LAMMPS configuration, in one configuration per published
file: "serial" (one MPI task), "parallel" (four MPI tasks), "openmp" (two MPI
tasks with two OpenMP threads each, through the OPENMP package), and "kokkos"
(the same through KOKKOS/OpenMP). Each publishes the same run.json format
this repository archives (see tools/rundata.py), next to a markdown summary
and a JUnit XML file of the same data, which are not ingested since the JSON
is a superset of both.

Only the results of the most recent run are published, so a run that is not
picked up before the next one is overwritten is lost. The runs themselves are
gated by changes in the monitored branch, however, so the same results stay in
place for as long as nothing new is merged. Ingestion is therefore idempotent
on the contents rather than on the file being new: results whose generation
time and commit are already archived are skipped, and this script can run from
a schedule as often as necessary.

Beyond that, the archive keeps one run per commit, since every archived run
is a point of the trend on the dashboard. Results that repeat a commit
already archived replace the run archived for it (the last publication of a
commit is the one run with the test scripts as they ended up), and results
that repeat its every verdict as well are not archived at all - that is a
re-publication rather than a run.

The commit and the branch are taken from the "commit" and "branch" metadata
fields; where those are absent they are recovered from the "git_info"
property ("Git info (<branch> / <describe>)"), which is also the only source
of the git describe string stored as "version". The website and the status
issue read the commit as "sha".

The run id is stamped with the run's generation time where that carries a
time zone, and with the publication time (the Last-Modified header, which is
UTC) where it does not: a "generated" field without a zone is in the local
time of the machine that ran the tests, which cannot be compared with the
UTC timestamps of the runs ingested from GitHub Actions.

A source that is unreachable or does not deliver a usable run.json is skipped
with a warning, leaving the already archived runs untouched.

Usage: python3 tools/fetch_regression.py [--datadir data] [--dry-run]
                                         [--config serial] [--url-base URL]
'''

from argparse import ArgumentParser
import email.utils
import json
import os
import shutil
import sys
import urllib.request

URL_BASE = 'https://download.lammps.org/coverage/'
# one published file per configuration the inputs are run in: serial, 4 MPI
# tasks, 2 MPI tasks with 2 OpenMP threads each through the OPENMP package,
# and the same through KOKKOS/OpenMP.  the file name is what identifies the
# configuration: the "config_file" property does not, three of them share
# config.yaml, and only the title of the run spells the difference out
CONFIGS = ('serial', 'parallel', 'openmp', 'kokkos')
SUITE = 'full-regression'
# published results that must not be archived, as (configuration, generation
# time) pairs.  a misconfigured run is only noticed after it has been
# published, and it stays published until the next run replaces it; listing
# it here keeps it out of the archive in the meantime.  a generation time
# identifies one publication and no other, so an entry cannot reject the run
# that replaces it, and it can be dropped once that has been archived
DISCARDED = set()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import rundata

def fetch_json(url):
    '''return the parsed document and its publication time (or None)'''
    request = urllib.request.Request(
        url, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode('utf-8'))
        modified = response.headers.get('Last-Modified')
    published = None
    if modified:
        try:
            published = email.utils.parsedate_to_datetime(modified)
        except (TypeError, ValueError):
            published = None
    return data, published

def ingest(url, datadir, config, dry_run=False):
    '''archive one configuration; returns 1 if a new run was written'''
    suite = f'{SUITE}/{config}'
    try:
        data, published = fetch_json(url)
    except Exception as err:
        print(f"WARNING: skipping {suite}: {url}: {err}", file=sys.stderr)
        return 0

    tests = data.get('tests') if isinstance(data, dict) else None
    meta = data.get('metadata') if isinstance(data, dict) else None
    if not isinstance(meta, dict) or not isinstance(tests, dict) or not tests:
        print(f"WARNING: skipping {suite}: {url}: not a run.json document",
              file=sys.stderr)
        return 0

    generated = meta.get('generated', '')
    if (config, generated) in DISCARDED:
        print(f"{suite}: skipping the discarded results of {generated}")
        return 0
    # the git_info property is the fallback for results published before the
    # commit and the branch were reported as metadata in their own right
    branch, version, sha = rundata.parse_git_info(
        meta.get('properties', {}).get('git_info', ''))
    sha = meta.get('commit') or sha
    branch = meta.get('branch') or branch
    seen = rundata.already_archived(datadir, suite, generated, sha)
    if seen:
        print(f"{suite}: already archived as {seen}")
        return 0
    # one run per commit: results that repeat a commit replace the run
    # archived for it, and are not archived at all where they repeat its
    # every verdict as well - that is a re-publication and not a run
    superseded = rundata.archived_with_commit(datadir, suite, sha)
    if superseded and not rundata.verdicts_moved(
            datadir, suite, superseded[-1], tests):
        print(f"{suite}: unchanged since {superseded[-1]}")
        return 0

    runid = rundata.run_id_string(published, generated, sha)
    rundir = os.path.join(datadir, suite, runid)
    if os.path.isdir(rundir):
        print(f"{suite}: {runid} already archived")
        return 0

    counts = meta.get('counts', {})
    if counts.get('tests') != len(tests):
        print(f"NOTE: {suite}/{runid}: counts cover {counts.get('tests')} of"
              f" {len(tests)} tests", file=sys.stderr)
    meta['branch'] = branch
    meta['version'] = version
    meta['sha'] = sha
    meta['source_url'] = url

    if dry_run:
        print(f"would ingest {suite}/{runid}: {counts.get('tests')} tests,"
              f" {counts.get('failed')} failed, {counts.get('error')} errors")
        for old in superseded:
            print(f"would replace {suite}/{old}: same commit {sha[:10]}")
        return 1

    os.makedirs(rundir, exist_ok=True)
    with open(os.path.join(rundir, 'run.json'), 'w') as f:
        json.dump({'metadata': meta, 'tests': tests}, f, indent=2)
        f.write('\n')
    print(f"ingested {suite}/{runid} from {url}")
    # after the new run is on disk, never before: a failure in between leaves
    # the commit archived twice, which the next ingest cleans up, rather than
    # not at all
    for old in superseded:
        shutil.rmtree(os.path.join(datadir, suite, old))
        print(f"{suite}: replaced {old}, same commit {sha[:10]}")
    if superseded:
        # a run that is gone must not be answered for out of the read cache
        rundata.load_run.cache_clear()
    return 1

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch published regression test results")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--url-base", default=URL_BASE,
                        help="Directory URL the results are published in")
    parser.add_argument("--config", action='append', choices=CONFIGS,
                        help="Only fetch this configuration (repeatable)")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be ingested")
    args = parser.parse_args()

    total = 0
    for config in (args.config or CONFIGS):
        total += ingest(args.url_base + config + '.json', args.datadir,
                        config, args.dry_run)
    print(f"ingested {total} new regression run(s) from {args.url_base}")
