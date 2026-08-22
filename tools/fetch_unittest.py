#!/usr/bin/env python3
'''
Fetch the JUnit XML of the unit test run published on download.lammps.org and
archive it under data/unit-tests/<config>/<runid>/run.json.

This is the unit test suite as it is run on the dedicated machine that also
produces the code coverage report, in one build and one pass: a native GCC
build of x86_64 Linux with a far more complete package selection than the
GitHub Actions runners compile, which is why it reports several hundred tests
more than any of the configurations ingested from there
(tools/ingest_actions.py). It is published next to the coverage report as
junit.xml, in the "ctest --output-junit" format.

That format records neither the commit nor the branch, stamps the run in the
local time of the test machine, and says nothing about the build beyond the
host name. All of it is taken from the summary.json of the coverage report
instead: it is the second set of data published by this same run, and it
records the commit in full, the branch, a date on the UTC time line, and the
compiler and operating system the tests ran on. Publishing wipes the webroot
and fills it again, though, so the two files are only the two halves of one
run once that has finished; a summary read mid-rsync can still be the one of
the run before, which the commit in the test output catches (see ingest()).

The git describe string is taken from a "version" field of the summary where
it carries one, and otherwise from the output of the tests themselves, where
every LAMMPS run prints a "Git info (<branch> / <describe>)" banner (see
git_info_of() below). That banner is also what carries a run whose summary
could not be fetched: it names the branch and the abbreviated commit as well,
which with the publication time of the JUnit file is enough to archive the run
rather than drop it.

As for the regression results (tools/fetch_regression.py), only the most
recent run is published and the file is rewritten in place even when nothing
was rerun, so ingestion is idempotent on the contents rather than on the file
being new: results whose generation time and commit are already archived are
skipped, results that repeat an archived commit replace the run archived for
it, and results that repeat its every verdict as well are not archived at all.
This script can therefore run from a schedule as often as necessary.

A source that is unreachable or does not deliver a usable JUnit document is
skipped with a warning, leaving the already archived runs untouched.

Usage: python3 tools/fetch_unittest.py [--datadir data] [--dry-run]
                                       [--config linux-x86_64-gcc]
                                       [--url URL] [--summary-url URL]
'''

from argparse import ArgumentParser
import email.utils
import io
import json
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET

URL = 'https://download.lammps.org/coverage/junit.xml'
# the other set of data this run publishes, which records the metadata the
# JUnit format has no place for (tools/fetch_external.py reads the coverage
# numbers of the same file for the dashboard)
SUMMARY_URL = 'https://download.lammps.org/coverage/summary.json'
# the slot this run is archived in. the machine builds x86_64 Linux with GCC,
# which no configuration ingested from GitHub Actions covers: those are the
# BIGBIG, single precision FFT, ARM64, macOS, Windows, and KOKKOS builds
CONFIG = 'linux-x86_64-gcc'
SUITE = 'unit-tests'
# the banner every LAMMPS run prints, quoted in the captured test output
GIT_INFO = re.compile(r'Git info \([^)]*\)')
# fields of the summary that describe the machine and the build rather than
# the run itself, and are kept as properties of it. the names and the wording
# are those the full regression runs report the same two in
SUMMARY_PROPERTIES = ('operating_system', 'compiler')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import junit_to_json
import rundata

def fetch_url(url):
    '''return the raw document and its publication time (or None)'''
    request = urllib.request.Request(
        url, headers={'User-Agent': 'lammps-test-results ingest'})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        modified = response.headers.get('Last-Modified')
    published = None
    if modified:
        try:
            published = email.utils.parsedate_to_datetime(modified)
        except (TypeError, ValueError):
            published = None
    return raw, published

def fetch_summary(url):
    '''the commit, branch, and date the coverage report of this run publishes.

       an empty dict where it cannot be read: the run is then archived from
       what its own test output says, which is less precise but keeps the
       results coming in'''
    try:
        raw, _ = fetch_url(url)
        summary = json.loads(raw.decode('utf-8'))
    except Exception as err:
        print(f"WARNING: {url}: {err}", file=sys.stderr)
        return {}
    if not isinstance(summary, dict):
        print(f"WARNING: {url}: not a JSON object", file=sys.stderr)
        return {}
    return summary

def suite_header(raw):
    '''the name and the timestamp of the (first) test suite of a JUnit
       document; both are attributes of the element itself and are therefore
       not part of what parse_junit() reads'''
    root = ET.parse(io.BytesIO(raw)).getroot()
    suite = root if root.tag == 'testsuite' else root.find('testsuite')
    if suite is None:
        return '', ''
    return suite.get('name', ''), suite.get('timestamp', '')

def git_info_of(raw):
    '''the "Git info (...)" banner of the binary under test, from the output
       the JUnit file quotes for the tests.

       only "lmp -h" prints the banner, so it appears exactly once in the
       document, in the output of the test that runs it - and only because it
       is printed near the top of that output: ctest cuts what it quotes off
       at 1024 bytes per test, which is also why the compiler and the
       operating system, printed at the end of the same help text, cannot be
       recovered from here at all'''
    found = GIT_INFO.search(raw.decode('utf-8', errors='replace'))
    return found.group(0) if found else ''

def ingest(url, summary_url, datadir, config, dry_run=False):
    '''archive the published unit test run; returns 1 if it was new'''
    suite = f'{SUITE}/{config}'
    try:
        raw, published = fetch_url(url)
    except Exception as err:
        print(f"WARNING: skipping {suite}: {url}: {err}", file=sys.stderr)
        return 0

    try:
        name, timestamp = suite_header(raw)
        # parse_junit() takes anything ElementTree parses, a file object
        # included, so the document is not written out just to be read back
        properties, tests = junit_to_json.parse_junit(io.BytesIO(raw))
    except ET.ParseError as err:
        print(f"WARNING: skipping {suite}: {url}: {err}", file=sys.stderr)
        return 0
    if not tests:
        print(f"WARNING: skipping {suite}: {url}: no test cases", file=sys.stderr)
        return 0

    # what the banner says is the fallback for a summary that could not be
    # read, and the source of the git describe string for as long as the
    # summary is published without a "version" field of its own
    branch, version, sha = rundata.parse_git_info(git_info_of(raw))
    summary = fetch_summary(summary_url)
    # publishing wipes the webroot and fills it again, so the two files can be
    # read a moment apart and be of different runs. the abbreviated commit of
    # the banner is of the binary these very tests ran and settles it: a
    # summary that disagrees is not the other half of this run, and the read is
    # simply retried on the next poll - what is published stays in place until
    # the next run replaces it
    commit = str(summary.get('commit', ''))
    if commit and sha and not commit.startswith(sha):
        print(f"WARNING: skipping {suite}: {summary_url} is of commit"
              f" {commit[:10]} but the tests ran {sha}; retrying on the"
              f" next poll", file=sys.stderr)
        return 0
    sha = commit or sha
    branch = summary.get('branch') or branch
    version = summary.get('version') or version
    # the timestamp of the JUnit file is the local time of the test machine
    # and cannot be compared with the UTC stamps of the other suites, so it
    # only stands in where the summary carries no usable date
    generated = timestamp
    if rundata.utc_stamp(summary.get('date')):
        generated = summary['date']
    if not sha:
        print(f"NOTE: {suite}: no commit in {summary_url} or the test output",
              file=sys.stderr)
    seen = rundata.already_archived(datadir, suite, generated, sha)
    if seen:
        print(f"{suite}: already archived as {seen}")
        return 0
    # one run per commit, as for the regression results: a repeated commit
    # replaces the run archived for it, and one that repeats its every verdict
    # as well is a re-publication rather than a run
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

    counts = rundata.metadata_counts(tests)
    # the JUnit document knows nothing about the machine or the build beyond
    # the host name; the summary describes both, and the name of the ctest
    # suite (e.g. "Linux-g++-15") names the compiler in short
    for key in SUMMARY_PROPERTIES:
        if summary.get(key):
            properties.setdefault(key, summary[key])
    if name:
        properties.setdefault('testsuite', name)
    meta = {'title': f'Unit Tests {config}',
            'generated': generated,
            'properties': properties,
            'counts': counts,
            'branch': branch,
            'version': version,
            'sha': sha,
            'source_url': url}

    if dry_run:
        print(f"would ingest {suite}/{runid}: {counts['tests']} tests,"
              f" {counts['failed']} failed, {counts['error']} errors")
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
    parser = ArgumentParser(description="Fetch the published unit test results")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--url", default=URL, help="URL of the JUnit XML file")
    parser.add_argument("--summary-url", default=SUMMARY_URL,
                        help="URL of the summary.json published with it")
    parser.add_argument("--config", default=CONFIG,
                        help="Configuration the results are archived under")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Only report what would be ingested")
    args = parser.parse_args()

    total = ingest(args.url, args.summary_url, args.datadir, args.config,
                   args.dry_run)
    print(f"ingested {total} new unit test run(s) from {args.url}")
