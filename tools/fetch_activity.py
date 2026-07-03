#!/usr/bin/env python3
'''
Fetch repository activity statistics for lammps/lammps from the GitHub API
and store them as data/external/activity.json for the dashboard.

Uses the "gh" CLI for authentication (like ingest_actions.py). The weekly
commit statistics endpoint may reply 202 (statistics being computed) on the
first request; it is retried a few times and skipped if still unavailable,
in which case an existing file is left untouched.

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

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch GitHub repository activity statistics")
    parser.add_argument("--repo", default="lammps/lammps", help="Repository")
    parser.add_argument("--output", default="data/external/activity.json",
                        help="Output JSON file")
    args = parser.parse_args()

    try:
        repo = json.loads(gh_api(f"repos/{args.repo}"))
        open_prs = json.loads(gh_api(
            f"search/issues?q=repo:{args.repo}+is:pr+is:open&per_page=1"))['total_count']
        # the repository's open_issues_count includes pull requests
        open_issues = repo['open_issues_count'] - open_prs

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
