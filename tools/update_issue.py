#!/usr/bin/env python3
'''
Maintain the rolling "test status" issue.

The issue body is rewritten on every update with the current status snapshot;
editing the body does NOT notify anybody. A comment is added ONLY when the
latest run of a suite has new failures or fixed tests relative to the previous
run AND that change has not been announced in an earlier comment; comments DO
notify issue subscribers. This way anybody who wants email notifications
about regressions subscribes to this one issue and gets no nightly noise
otherwise.

The issue is identified by the "test-status" label (created if missing).
Requires the "gh" CLI with permission to write issues in the target repo.

Usage: python3 tools/update_issue.py --repo <owner/repo> --site-url <url>
                                     [--datadir data] [--dry-run]
'''

from argparse import ArgumentParser
import datetime
import json
import os
import subprocess
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import rundata

LABEL = 'test-status'
TITLE = 'Automated test status (updated nightly)'
# everything below this marker in the issue body is rewritten by this script;
# any hand-written text above it is preserved
MARKER = '<!-- test-status -->'

def gh(args_list, check=True):
    result = subprocess.run(['gh'] + args_list, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args_list)} failed: {result.stderr.strip()}")
    return result.stdout

def md_list(keys, maxlen=25):
    lines = [f"- `{key}`" for key in sorted(keys)[:maxlen]]
    if len(keys) > maxlen:
        lines.append(f"- ... and {len(keys) - maxlen} more")
    return '\n'.join(lines) + '\n'

def suite_title(suite):
    if suite.startswith('unit-tests/'):
        return 'Unit Tests: ' + suite.split('/', 1)[1]
    return suite.replace('-', ' ').title()

def collect(datadir):
    '''gather the latest status and diff for every suite'''
    snapshot = []
    for suite in rundata.list_suites(datadir):
        runs = rundata.list_runs(datadir, suite)
        latest = rundata.load_run(datadir, suite, runs[-1])
        entry = {'suite': suite, 'runid': runs[-1],
                 'counts': latest['metadata']['counts'],
                 'sha': latest['metadata'].get('sha', ''),
                 'run_url': latest['metadata'].get('run_url', ''),
                 'diff': None}
        if len(runs) > 1:
            previous = rundata.load_run(datadir, suite, runs[-2])
            entry['diff'] = rundata.compare_runs(previous, latest)
        snapshot.append(entry)
    return snapshot

def build_body(snapshot, site_url):
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    body = (f"{MARKER}\nCurrent status of the automated LAMMPS test runs. Full details on the"
            f" [test status website]({site_url}).\n\n"
            f"This status table is updated in place (no notifications); a comment is posted"
            f" only when new failures appear or failures are fixed - subscribe to this"
            f" issue to be notified about regressions.\n\n")
    body += "| Suite | Tests | Passed | Failed | Errors | Skipped | Changes |\n"
    body += "|---|---:|---:|---:|---:|---:|---|\n"
    for entry in snapshot:
        counts = entry['counts']
        broken = counts['failed'] + counts['error']
        icon = ':white_check_mark:' if broken == 0 else ':x:'
        changes = ''
        if entry['diff']:
            parts = []
            if entry['diff']['new_failures']:
                parts.append(f"**+{len(entry['diff']['new_failures'])} new**")
            if entry['diff']['fixed']:
                parts.append(f"{len(entry['diff']['fixed'])} fixed")
            changes = ', '.join(parts)
        body += (f"| {icon} {suite_title(entry['suite'])} | {counts['tests']} |"
                 f" {counts['passed']} | {counts['failed']} | {counts['error']} |"
                 f" {counts['skipped']} | {changes} |\n")
    body += f"\n_Last updated: {now}_\n"
    return body

def build_sections(snapshot):
    '''one markdown section per suite whose latest run has new failures or
       fixed tests; the heading doubles as the dedup key for drop_announced()'''
    sections = []
    for entry in snapshot:
        diff = entry['diff']
        if not diff or not (diff['new_failures'] or diff['fixed']):
            continue
        heading = f"### {suite_title(entry['suite'])} ({entry['runid']})"
        text = heading + "\n"
        if entry['sha']:
            text += f"commit {entry['sha'][:10]}"
            if entry['run_url']:
                text += f" - [workflow run]({entry['run_url']})"
            text += "\n"
        if diff['new_failures']:
            text += f"\n**New failures ({len(diff['new_failures'])}):**\n"
            text += md_list(diff['new_failures'])
        if diff['fixed']:
            text += f"\n**Fixed ({len(diff['fixed'])}):**\n"
            text += md_list(diff['fixed'])
        sections.append({'heading': heading, 'text': text})
    return sections

def drop_announced(sections, repo, number):
    '''drop sections whose heading already appears in a posted comment.
       a suite's diff stays the same until a newer run of it arrives, so
       without this check every scheduled update would re-post it'''
    if not sections:
        return sections
    posted = gh(['api', f'repos/{repo}/issues/{number}/comments',
                 '--paginate', '--jq', '.[].body'])
    return [s for s in sections if s['heading'] not in posted]

def build_comment(sections, site_url):
    return ('\n'.join(s['text'] for s in sections)
            + f"\nFull details on the [test status website]({site_url}).\n")

def find_or_create_issue(repo):
    out = gh(['issue', 'list', '--repo', repo, '--label', LABEL, '--state', 'open',
              '--json', 'number', '--jq', '.[0].number'], check=False).strip()
    if out:
        return int(out)
    # make sure the label exists, then create the issue
    gh(['label', 'create', LABEL, '--repo', repo,
        '--description', 'rolling automated test status issue',
        '--color', '2a78d6'], check=False)
    url = gh(['issue', 'create', '--repo', repo, '--title', TITLE,
              '--label', LABEL, '--body', 'initializing ...']).strip()
    print(f"created issue {url}")
    return int(url.rsplit('/', 1)[1])

if __name__ == "__main__":
    parser = ArgumentParser(description="Update the rolling test status issue")
    parser.add_argument("--repo", required=True, help="Repository for the issue")
    parser.add_argument("--issue", type=int, default=0,
                        help="Issue number (default: find or create by label)")
    parser.add_argument("--site-url", required=True, help="URL of the status website")
    parser.add_argument("--datadir", default="data", help="Data directory")
    parser.add_argument("--dry-run", action='store_true', default=False,
                        help="Print body and comment instead of posting")
    args = parser.parse_args()

    snapshot = collect(args.datadir)
    if not snapshot:
        print("no run data, nothing to do")
        sys.exit(0)

    body = build_body(snapshot, args.site_url)
    sections = build_sections(snapshot)

    if args.dry_run:
        print("=== issue body ===")
        print(body)
        print("=== comment (before dedup against posted comments) ===")
        if sections:
            print(build_comment(sections, args.site_url))
        else:
            print("(no comment - no new failures or fixes)")
        sys.exit(0)

    if args.issue:
        number = args.issue
    else:
        number = find_or_create_issue(args.repo)

    # preserve hand-written text above the marker in the existing issue body
    old_body = gh(['issue', 'view', str(number), '--repo', args.repo,
                   '--json', 'body', '--jq', '.body'], check=False)
    preamble = old_body.split(MARKER, 1)[0].rstrip()
    if preamble:
        body = preamble + '\n\n' + body

    gh(['issue', 'edit', str(number), '--repo', args.repo, '--body', body])
    print(f"updated body of issue #{number}")
    sections = drop_announced(sections, args.repo, number)
    if sections:
        gh(['issue', 'comment', str(number), '--repo', args.repo,
            '--body', build_comment(sections, args.site_url)])
        print(f"posted notification comment on issue #{number}")
    else:
        print("no unannounced failures or fixes - no comment posted")
