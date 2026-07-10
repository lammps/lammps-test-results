#!/usr/bin/env python3
'''
Fetch repository activity statistics for lammps/lammps from the GitHub API
and store them as data/external/activity.json for the dashboard.

Uses the "gh" CLI for authentication (like ingest_actions.py). The weekly
commit statistics endpoint may reply 202 (statistics being computed) on the
first request; it is retried a few times and skipped if still unavailable,
in which case an existing file is left untouched.

open_prs and open_issues are taken from GraphQL totalCount fields, which
report exact repository state. They were previously fetched from the REST
search API (search/issues with is:pr / is:issue), but that endpoint is being
migrated to an issues-only "advanced search", rolled out per token type: the
Actions GITHUB_TOKEN already ignores the is:pr qualifier and returns the
open-issue count for both queries, while personal tokens still get the old
behavior. The two counts are still cross-checked against the repository's
open_issues_count; when the numbers don't reconcile, that is treated like
any other fetch failure below: a warning is printed and the existing file is
left untouched, rather than publishing counts that may be swapped or
otherwise wrong.

Usage: python3 tools/fetch_activity.py [--repo lammps/lammps]
                                       [--output data/external/activity.json]
'''

from argparse import ArgumentParser
import datetime
import json
import subprocess
import sys
import time

def gh_api(path):
    result = subprocess.run(['gh', 'api', path], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return result.stdout

def gh_graphql(query, **fields):
    cmd = ['gh', 'api', 'graphql', '-f', f"query={query}"]
    for key, val in fields.items():
        cmd += ['-F', f"{key}={val}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {result.stderr.strip()}")
    return result.stdout

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch GitHub repository activity statistics")
    parser.add_argument("--repo", default="lammps/lammps", help="Repository")
    parser.add_argument("--output", default="data/external/activity.json",
                        help="Output JSON file")
    args = parser.parse_args()

    try:
        repo = json.loads(gh_api(f"repos/{args.repo}"))
        owner, name = args.repo.split('/')
        counts = json.loads(gh_graphql(
            'query($owner: String!, $name: String!) {'
            ' repository(owner: $owner, name: $name) {'
            ' issues(states: OPEN) { totalCount }'
            ' pullRequests(states: OPEN) { totalCount } } }',
            owner=owner, name=name))['data']['repository']
        open_prs = counts['pullRequests']['totalCount']
        open_issues = counts['issues']['totalCount']
        # the repository's open_issues_count includes pull requests, so the
        # two counts above should add up to it; if not, one of the APIs is
        # in a bad state and neither count can be trusted
        if open_prs + open_issues != repo['open_issues_count']:
            raise RuntimeError(
                f"open_prs ({open_prs}) + open_issues ({open_issues}) != "
                f"open_issues_count ({repo['open_issues_count']}); GitHub "
                "API counts look inconsistent, not publishing")

        # weekly commit counts for the past year; 202 means "still computing"
        weeks = []
        for _ in range(5):
            out = gh_api(f"repos/{args.repo}/stats/commit_activity").strip()
            if out and out != '[]':
                stats = json.loads(out)
                if isinstance(stats, list) and stats:
                    weeks = [[datetime.date.fromtimestamp(w['week']).isoformat(),
                              w['total']] for w in stats]
                    break
            time.sleep(3)
    except Exception as err:
        print(f"WARNING: could not fetch activity for {args.repo}: {err}",
              file=sys.stderr)
        sys.exit(0)

    data = {
        'open_prs': open_prs,
        'open_issues': open_issues,
        'stars': repo.get('stargazers_count', 0),
        'forks': repo.get('forks_count', 0),
        'commits_per_week': weeks,
        'date': datetime.datetime.now().isoformat(timespec='seconds'),
        'url': f"https://github.com/{args.repo}/pulse",
    }
    with open(args.output, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(f"{args.output}: {open_prs} open PRs, {open_issues} open issues,"
          f" {len(weeks)} weeks of commit data")
