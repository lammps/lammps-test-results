#!/usr/bin/env python3
'''
Maintain the rolling "test status" issue.

The issue body is rewritten on every update with the current status snapshot;
editing the body does NOT notify anybody. A comment is added ONLY when the
latest run of a suite has new failures or fixed tests relative to the previous
run AND that change has not been announced in an earlier comment; comments DO
notify issue subscribers. This way anybody who wants email notifications
about regressions subscribes to this one issue and gets no nightly noise
otherwise. Tests that ran out of time are reported in the body and alongside
the failures of a comment that goes out anyway, but never trigger one on
their own (see tools/rundata.py).

The status of the automated manual builds (tools/fetch_docs.py) is reported
the same way: the body always shows the current state of all three published
variants, and a comment is posted when one of them starts failing or falls
behind its branch, and again when it recovers. Which of the two was announced
last is recorded per manual in a hidden marker in the comment, so a build that
stays broken is announced once rather than every night.

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
import docsdata
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

# the status issue and the website name a suite the same way (tools/rundata.py)
suite_title = rundata.suite_title

def collect(datadir):
    '''gather the latest status and diff for every suite'''
    snapshot = []
    for suite in rundata.list_suites(datadir):
        runs = rundata.list_runs(datadir, suite)
        latest = rundata.load_run(datadir, suite, runs[-1])
        entry = {'suite': suite, 'runid': runs[-1],
                 'counts': rundata.counts(latest),
                 'sha': latest['metadata'].get('sha', ''),
                 'run_url': latest['metadata'].get('run_url', ''),
                 'limits': rundata.time_limits(latest),
                 'diff': None}
        if len(runs) > 1:
            previous = rundata.load_run(datadir, suite, runs[-2])
            entry['diff'] = rundata.compare_runs(previous, latest)
        snapshot.append(entry)
    return snapshot

DOCS_ICONS = {'passed': ':white_check_mark:', 'failed': ':x:',
              'stale': ':warning:', 'pending': ':hourglass_flowing_sand:',
              'unknown': ':grey_question:'}

def docs_status_text(state):
    '''the classified state as one readable phrase'''
    text = state['text']
    if state['stamp']:
        text = f"{text} {docsdata.fmt_utc(state['stamp'])}".strip()
    return text or state['status']

def docs_table(docs, now=None):
    '''current state of all three published manual variants'''
    body = "\n### Documentation builds\n\n"
    body += "| Manual | Status | Documents | Built |\n|---|---|---|---|\n"
    for entry in docs.get('branches', []):
        state = docsdata.state(entry, now)
        icon = DOCS_ICONS.get(state['status'], ':grey_question:')
        name = entry.get('branch', '?')
        if entry.get('url'):
            name = f"[{name}]({entry['url']})"
        contents = ' @ '.join(f"`{part}`" for part in docsdata.documents(entry))
        built = docsdata.fmt_utc(entry.get('built')) or '-'
        body += (f"| {icon} {name} | {docs_status_text(state)} |"
                 f" {contents or '-'} | {built} |\n")
    return body

def build_body(snapshot, docs, site_url):
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    body = (f"{MARKER}\nCurrent status of the automated LAMMPS test runs and manual"
            f" builds. Full details on the [test status website]({site_url}).\n\n"
            f"This status table is updated in place (no notifications); a comment is posted"
            f" only when new failures appear or failures are fixed - subscribe to this"
            f" issue to be notified about regressions.\n\n")
    # the timeout column is carried only where tests ran out of time, which
    # keeps it out of the table on the days the unit test suites are alone
    timeouts = any(entry['counts'].get('timeout') for entry in snapshot)
    column = " Timeouts |" if timeouts else ""
    if snapshot:
        body += (f"| Suite | Tests | Passed | Failed | Errors |{column}"
                 f" Skipped | Changes |\n")
        body += f"|---|---:|---:|---:|---:|{'---:|' if timeouts else ''}---:|---|\n"
    for entry in snapshot:
        counts = entry['counts']
        icon = ':white_check_mark:' if rundata.broken(counts) == 0 else ':x:'
        changes = []
        if entry['diff']:
            if entry['diff']['new_failures']:
                changes.append(f"**+{len(entry['diff']['new_failures'])} new**")
            if entry['diff']['fixed']:
                changes.append(f"{len(entry['diff']['fixed'])} fixed")
            if entry['diff']['new_timeouts']:
                changes.append(f"{len(entry['diff']['new_timeouts'])} newly out of time")
        cell = f" {counts['timeout']} |" if timeouts else ""
        body += (f"| {icon} {suite_title(entry['suite'])} | {counts['tests']} |"
                 f" {counts['passed']} | {counts['failed']} | {counts['error']} |{cell}"
                 f" {counts['skipped']} | {', '.join(changes)} |\n")
    if timeouts:
        body += ("\nTests that ran into the time limit of the test harness are"
                 " counted as timeouts rather than as errors: whether they expire"
                 " depends on the limit in force and on the load of the machine,"
                 " so they are reported but not announced as new failures.\n")
    if docs:
        body += docs_table(docs)
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
        # not worth a notification of its own, but part of the picture in a
        # comment that goes out anyway
        if diff['new_timeouts']:
            limits = ' / '.join(f"{limit} s" for limit in entry['limits'])
            text += (f"\nAnother {len(diff['new_timeouts'])} test(s) newly ran"
                     f" into the time limit of the test harness"
                     f"{f' ({limits})' if limits else ''}.\n")
        sections.append({'heading': heading, 'text': text})
    return sections

def posted_comments(repo, number):
    '''the bodies of all comments on the issue, oldest first'''
    return gh(['api', f'repos/{repo}/issues/{number}/comments',
               '--paginate', '--jq', '.[].body'])

def drop_announced(sections, posted):
    '''drop sections whose heading already appears in a posted comment.
       a suite's diff stays the same until a newer run of it arrives, so
       without this check every scheduled update would re-post it'''
    return [s for s in sections if s['heading'] not in posted]

def docs_marker(branch, announcement):
    '''hidden per-manual record of the last announcement made about it; the
       heading cannot serve as the dedup key here the way it does for a test
       suite, because it carries no run id that would change over time'''
    return f"<!-- docs-status: {branch} {announcement} -->"

def last_docs_announcement(posted, branch):
    '''"broken", "ok", or "" for a manual never announced about; whichever
       marker appears last wins, so the comments must be in posting order'''
    last, position = '', -1
    for announcement in ('broken', 'ok'):
        found = posted.rfind(docs_marker(branch, announcement))
        if found > position:
            last, position = announcement, found
    return last

def docs_sections(docs, posted, now=None):
    '''one markdown section per manual that started failing or falling behind
       since the last announcement, and per manual that recovered since'''
    sections = []
    for entry in (docs or {}).get('branches', []):
        branch = entry.get('branch', '?')
        state = docsdata.state(entry, now)
        announced = last_docs_announcement(posted, branch)
        if state['status'] in docsdata.NOTIFY and announced != 'broken':
            announcement = 'broken'
        elif state['status'] == 'passed' and announced == 'broken':
            announcement = 'ok'
        else:
            # unchanged, or a state (pending, unknown) that says nothing
            # either way and must not clear a pending "broken" announcement
            continue

        name = f"{branch} manual"
        if entry.get('url'):
            name = f"[{name}]({entry['url']})"
        heading = f"### Documentation build: {branch}"
        text = f"{heading}\n{docs_marker(branch, announcement)}\n\n"
        if announcement == 'broken':
            detail = docs_status_text(state)
            text += f"The {name} build is **{state['status']}**"
            text += f" ({detail}).\n" if detail != state['status'] else ".\n"
        else:
            text += f"The {name} builds cleanly again.\n"
        contents = ' @ '.join(f"`{part}`" for part in docsdata.documents(entry))
        if contents:
            text += f"\n- published manual documents {contents}\n"
        built = docsdata.fmt_utc(entry.get('built'))
        if built:
            timing = docsdata.timing(entry)
            text += f"- last build {built}"
            text += f" ({timing})\n" if timing else "\n"
        sections.append({'heading': heading, 'text': text})
    return sections

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
    docs = docsdata.load(args.datadir)
    if not snapshot and not docs:
        print("no run data, nothing to do")
        sys.exit(0)

    body = build_body(snapshot, docs, args.site_url)

    if args.dry_run:
        print("=== issue body ===")
        print(body)
        print("=== comment (before dedup against posted comments) ===")
        # nothing announced yet, so this shows every section that a state
        # change could produce
        sections = build_sections(snapshot) + docs_sections(docs, '')
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
    posted = posted_comments(args.repo, number)
    sections = drop_announced(build_sections(snapshot), posted)
    sections += docs_sections(docs, posted)
    if sections:
        gh(['issue', 'comment', str(number), '--repo', args.repo,
            '--body', build_comment(sections, args.site_url)])
        print(f"posted notification comment on issue #{number}")
    else:
        print("no unannounced failures or fixes - no comment posted")
