#!/usr/bin/env python3
'''
Convert a JUnit XML file (e.g. from "ctest --output-junit" or from the LAMMPS
regression test tooling) into the run.json format used by this repository
(same format as written by tools/regression-tests/merge_results.py in
lammps/lammps).

Extra metadata (git sha, branch, workflow run URL, config name) can be passed
as --meta key=value arguments.

Example:
    python3 tools/junit_to_json.py --title "Unit Tests linux-x86_64" \
            --meta sha=abc123 --meta branch=develop \
            junit-linux-x86_64.xml data/unit-tests/linux-x86_64/<runid>/run.json
'''

from argparse import ArgumentParser
import datetime
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import rundata

def parse_junit(source):
    '''parse a JUnit XML file into (properties, tests) dicts; handles both a
       <testsuite> root and a <testsuites> wrapper, and the "status" attribute
       convention used by ctest --output-junit. "source" is anything
       ElementTree parses: a file name, or a file object holding a document
       that was fetched rather than downloaded to disk'''
    root = ET.parse(source).getroot()
    if root.tag == 'testsuites':
        suites = root.findall('testsuite')
    elif root.tag == 'testsuite':
        suites = [root]
    else:
        raise ET.ParseError(f"unexpected root element <{root.tag}>")

    properties = {}
    tests = {}
    for suite in suites:
        if suite.get('hostname'):
            properties.setdefault('hostname', suite.get('hostname'))
        for prop in suite.findall('./properties/property'):
            properties.setdefault(prop.get('name', ''), prop.get('value', ''))
        for case in suite.findall('testcase'):
            classname = case.get('classname', '') or ''
            name = case.get('name', '') or ''
            # avoid "name/name" keys when classname just repeats the name (ctest)
            if classname == name:
                classname = ''
            key = classname + '/' + name if classname else name
            try:
                time = float(case.get('time', 0.0))
            except ValueError:
                time = 0.0
            entry = {'status': 'passed', 'time': time, 'message': ''}
            for tag in ('failure', 'error', 'skipped'):
                elem = case.find(tag)
                if elem is not None:
                    entry['status'] = 'failed' if tag == 'failure' else tag
                    entry['message'] = elem.get('message', '')
                    # a run that completed but could not be checked: JUnit
                    # has no element for it, so the regression test harness
                    # writes it as skipped with a status attribute that
                    # tells the two apart (tools/regression-tests/REPORTING.md
                    # in lammps/lammps)
                    if tag == 'skipped' and case.get('status') == 'runtest':
                        entry['status'] = 'runtest'
                    if elem.text and elem.text.strip():
                        entry['details'] = elem.text.strip()
                    break
            else:
                # ctest encodes the outcome in a "status" attribute
                status = case.get('status', '')
                if status == 'fail':
                    entry['status'] = 'failed'
                elif status in ('disabled', 'notrun', 'skipped'):
                    entry['status'] = 'skipped'
            # the regression test harness annotates a test case with what
            # needs attention in the input and where the run deviates from
            # its reference (tools/regression-tests/REPORTING.md), which
            # merge_results.py carries into a run.json under these names
            if case.get('attention'):
                entry['attention'] = case.get('attention')
            if case.get('diverged'):
                entry['diverged'] = int(case.get('diverged'))
                entry['diverged_row'] = int(case.get('diverged-row', 0))
                if case.get('diverged-at') is not None:
                    entry['diverged_at'] = int(case.get('diverged-at'))
            tests[key] = entry
    return properties, tests

def run_json(properties, tests, title, generated, **metadata):
    '''the run.json document for the tests parsed from a JUnit file, with the
       counts the regression test harness records in its metadata and any
       further metadata the caller knows (sha, branch, run_url, ...)'''
    meta = {'title': title, 'generated': generated, 'properties': properties,
            'counts': rundata.metadata_counts(tests)}
    meta.update(metadata)
    return {'metadata': meta, 'tests': tests}

if __name__ == "__main__":
    parser = ArgumentParser(description="Convert a JUnit XML file to run.json")
    parser.add_argument("xmlfile", help="JUnit XML input file")
    parser.add_argument("jsonfile", help="run.json output file")
    parser.add_argument("--title", default="Test Run", help="Title of the test run")
    parser.add_argument("--meta", action='append', default=[],
                        help="Additional metadata as key=value (repeatable)")
    args = parser.parse_args()

    try:
        properties, tests = parse_junit(args.xmlfile)
    except ET.ParseError as err:
        print(f"ERROR: cannot parse {args.xmlfile}: {err}", file=sys.stderr)
        sys.exit(1)

    metadata = {}
    for item in args.meta:
        key, _, value = item.partition('=')
        metadata[key] = value
    data = run_json(properties, tests, args.title,
                    datetime.datetime.now().isoformat(timespec='seconds'), **metadata)
    counts = data['metadata']['counts']

    os.makedirs(os.path.dirname(os.path.abspath(args.jsonfile)), exist_ok=True)
    with open(args.jsonfile, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"{args.jsonfile}: {counts['tests']} tests, {counts['passed']} passed,"
          f" {counts['failed']} failed, {counts['error']} errors,"
          f" {counts['runtest']} runtest, {counts['skipped']} skipped")
