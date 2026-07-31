#!/usr/bin/env python3
'''
Fetch the monthly GitHub activity of the members of a team (by default the
"core" team of the LAMMPS organization) in lammps/lammps and store it as
data/external/team.json for the dashboard.

Seven counts are collected per member and calendar month, from four sources.
None of them is a single "activity" endpoint - GitHub has none - so each is
swept separately and bucketed here:

  commits             commits on the default branch, from the GraphQL commit
                      history of the branch ref, attributed to the linked
                      GitHub account and dated by their commit date
  issues, prs         issues and pull requests opened, from a GraphQL search
                      per month, dated by creation
  merges              pull requests merged *by* the member, from a second
                      search per month.  there is no "merged-by:" search
                      qualifier, so the merged pull requests of the month are
                      listed and their mergedBy is counted
  reviews, approvals  pull request reviews submitted, and the subset of them
                      that approved, from the reviewer's contributions
                      collection (the only date-indexed source for reviews)
  comments            issue and pull request conversation comments plus
                      inline review comments, from the two repository-wide
                      comment endpoints.  the summary body of a review is not
                      counted here; it is counted as a review above

Every count is bucketed by the UTC month of the event itself rather than
taken from an aggregate endpoint.  That matters: the totals of the GraphQL
contributions collection are bucketed by day in the *contributor's own
profile timezone*, which shifts commits across a month boundary for anyone
not on UTC (for a US/Eastern member, a month queried as UTC came out 39
commits short on one boundary day alone).  Only the review contributions are
read from that collection, and even there the individual submission
timestamps are re-bucketed here, with the query windows padded by a day so
that the snapping cannot drop one.

A full sweep is about 150 requests, well inside both the 5000/h REST budget
and the GraphQL one; the REST search API and its 30/min limit are avoided
throughout in favour of GraphQL search.  Reading team membership needs a
token with the read:org scope - without it nothing else here is worth
collecting, so that is the first thing checked.

As in fetch_activity.py, an incomplete sweep is not published: a failure of
any source prints a warning and leaves an existing file untouched, so the
dashboard keeps showing the last complete set of counts rather than a set in
which some members silently dropped to zero.

Usage: python3 tools/fetch_team.py [--repo lammps/lammps] [--org lammps]
                                   [--team core] [--months 13]
                                   [--output data/external/team.json]
'''

from argparse import ArgumentParser
import datetime
import json
import subprocess
import sys
import time

# the counts collected per member and month, in the order they are reported
METRICS = ('commits', 'prs', 'issues', 'reviews', 'approvals', 'merges',
           'comments')

# how many of them can also be given for the repository as a whole: the
# comment and commit sweeps and the searches see every author, the review
# contributions are per member and cannot be totalled this way
REPO_METRICS = ('commits', 'prs', 'issues', 'merges', 'comments')

ATTEMPTS = 3

def run(cmd, attempts=ATTEMPTS):
    '''run a gh command and return its stdout, retrying transient failures.
       secondary rate limits and 5xx replies are the expected ones here and
       are worth waiting out; a bad query would only fail again, but it costs
       two retries to find that out and the sweep is not time critical'''
    error = None
    for attempt in range(attempts):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return result.stdout
        error = result.stderr.strip() or f'exit status {result.returncode}'
        if attempt + 1 < attempts:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{' '.join(cmd[:3])} failed: {error}")

def gh_rest(path, **params):
    '''one page of a REST endpoint'''
    cmd = ['gh', 'api', '-X', 'GET', path]
    for key, value in params.items():
        cmd += ['-f', f'{key}={value}']
    return json.loads(run(cmd))

def gh_graphql(query):
    '''run a GraphQL query and return its data, raising on the errors GitHub
       reports in the body (where they arrive with a 200 status)'''
    reply = json.loads(run(['gh', 'api', 'graphql', '-f', f'query={query}']))
    if reply.get('errors'):
        raise RuntimeError('; '.join(err.get('message', str(err))
                                     for err in reply['errors'])[:300])
    return reply['data']

def months_back(count, today):
    '''the last <count> calendar months ending with the current one, as
       (label, start, end) with UTC bounds and end exclusive'''
    year, month = today.year, today.month
    out = []
    for _ in range(count):
        nxt = (year + (month == 12), month % 12 + 1)
        out.append((f'{year:04d}-{month:02d}',
                    f'{year:04d}-{month:02d}-01T00:00:00Z',
                    f'{nxt[0]:04d}-{nxt[1]:02d}-01T00:00:00Z'))
        year, month = (year - (month == 1), (month - 2) % 12 + 1)
    return list(reversed(out))

def bucket(counts, login, stamp, metric, months):
    '''count one event for one login in the month its UTC timestamp falls in.
       the sweeps see the whole repository, so most of what they report is by
       someone outside the team and is dropped here, as is an event outside
       the window or one by an author GitHub cannot resolve to an account (a
       commit with an unlinked e-mail address)'''
    if not login or not stamp or login not in counts:
        return
    key = str(stamp)[:7]
    if key in months:
        counts[login][metric][key] = counts[login][metric].get(key, 0) + 1

# --------------------------------------------------------------- sources

def team_members(org, team):
    '''the members of the team, with the display name shown on the page.
       needs read:org; a token without it gets a 404 here rather than an
       empty list, which the caller reports as the fetch failure it is'''
    members = []
    page = 1
    while True:
        rows = gh_rest(f'/orgs/{org}/teams/{team}/members',
                       per_page=100, page=page)
        members += rows
        if len(rows) < 100:
            break
        page += 1
    out = []
    for member in sorted(members, key=lambda m: m['login'].lower()):
        entry = {'login': member['login'], 'url': member.get('html_url', ''),
                 'avatar': member.get('avatar_url', '')}
        # the members endpoint reports no display name, so ask for it
        try:
            entry['name'] = gh_rest(f"/users/{member['login']}").get('name') or ''
        except (RuntimeError, ValueError, KeyError):
            entry['name'] = ''
        out.append(entry)
    return out

def sweep_commits(repo, start, end, counts, totals, months):
    '''every commit reachable from the default branch in the window, paged
       through the GraphQL history of the branch ref.  this is the same set
       the contribution graph counts, but with the individual commit dates
       kept so that the months can be UTC ones'''
    owner, name = repo.split('/')
    cursor, branch = 'null', ''
    while True:
        data = gh_graphql(f'''
query {{
  repository(owner: "{owner}", name: "{name}") {{
    defaultBranchRef {{
      name
      target {{
        ... on Commit {{
          history(since: "{start}", until: "{end}", first: 100, after: {cursor}) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{ committedDate author {{ user {{ login }} }} }}
          }}
        }}
      }}
    }}
  }}
}}''')
        ref = data['repository']['defaultBranchRef']
        branch = ref['name']
        history = ref['target']['history']
        for node in history['nodes']:
            login = ((node.get('author') or {}).get('user') or {}).get('login', '')
            bucket(counts, login, node['committedDate'], 'commits', months)
            key = node['committedDate'][:7]
            if key in months:
                totals['commits'][key] = totals['commits'].get(key, 0) + 1
        if not history['pageInfo']['hasNextPage']:
            return branch
        cursor = json.dumps(history['pageInfo']['endCursor'])

def last_day(end):
    '''the last day covered by a month whose exclusive end is given.  the
       search date qualifiers are inclusive on both ends, so asking for the
       first of the next month would return that day twice, once in each of
       two neighbouring months'''
    stop = datetime.datetime.strptime(end, '%Y-%m-%dT%H:%M:%SZ')
    return (stop - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

def search(repo, query, fields, label, seen):
    '''page through a GraphQL issue search.  every field has to sit inside an
       inline fragment: a search returns a union of issues and pull requests,
       which have no fields in common to select directly.  the search backend
       caps every query at 1000 results however it is paged, which is why the
       callers ask one month at a time; a month that reaches the cap is
       reported rather than silently truncated.  ids already seen are dropped,
       so that a result landing in two neighbouring queries is counted once'''
    cursor, nodes, total = 'null', [], None
    while True:
        data = gh_graphql(f'''
query {{
  search(query: "repo:{repo} {query}", type: ISSUE, first: 100, after: {cursor}) {{
    issueCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ __typename {fields} }}
  }}
}}''')
        result = data['search']
        total = result['issueCount']
        nodes += result['nodes']
        if not result['pageInfo']['hasNextPage'] or len(nodes) >= 1000:
            break
        cursor = json.dumps(result['pageInfo']['endCursor'])
    if total > 1000:
        print(f"WARNING: {label}: {total} results, only the first "
              f"{len(nodes)} are counted", file=sys.stderr)
    fresh = [node for node in nodes if node.get('id') not in seen]
    seen.update(node.get('id') for node in fresh)
    return fresh

def sweep_opened(repo, month, start, end, counts, totals, months, seen):
    '''issues and pull requests opened in the month.  one search returns
       both, told apart by their type'''
    nodes = search(repo, f'created:{start[:10]}..{last_day(end)}',
                   '... on Issue { id createdAt author { login } }'
                   ' ... on PullRequest { id createdAt author { login } }',
                   f'{month} created', seen)
    for node in nodes:
        login = (node.get('author') or {}).get('login', '')
        metric = 'prs' if node['__typename'] == 'PullRequest' else 'issues'
        bucket(counts, login, node.get('createdAt'), metric, months)
        key = str(node.get('createdAt', ''))[:7]
        if key in months:
            totals[metric][key] = totals[metric].get(key, 0) + 1

def sweep_merged(repo, month, start, end, counts, totals, months, seen):
    '''pull requests merged in the month, counted against whoever merged
       them.  this is the maintainer action, not the authorship of the pull
       request - in a repository where one person presses most of the
       buttons it says so'''
    nodes = search(repo, f'is:pr is:merged merged:{start[:10]}..{last_day(end)}',
                   '... on PullRequest { id mergedAt mergedBy { login } }',
                   f'{month} merged', seen)
    for node in nodes:
        login = (node.get('mergedBy') or {}).get('login', '')
        bucket(counts, login, node.get('mergedAt'), 'merges', months)
        key = str(node.get('mergedAt', ''))[:7]
        if key in months:
            totals['merges'][key] = totals['merges'].get(key, 0) + 1

def sweep_reviews(repo, login, window, counts, months):
    '''the reviews one member submitted, from their contributions collection.
       that collection is the only source indexed by review date, but it
       spans at most a year and snaps its bounds to whole days in the
       member's own timezone, so the caller passes padded sub-windows and the
       submission timestamps are re-bucketed here as UTC'''
    start, end = window
    cursor = 'null'
    while True:
        data = gh_graphql(f'''
query {{
  user(login: "{login}") {{
    contributionsCollection(from: "{start}", to: "{end}") {{
      pullRequestReviewContributions(first: 100, after: {cursor}) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          repository {{ nameWithOwner }}
          pullRequestReview {{ state submittedAt }}
        }}
      }}
    }}
  }}
}}''')
        user = data.get('user')
        if not user:
            raise RuntimeError(f'no such user: {login}')
        block = user['contributionsCollection']['pullRequestReviewContributions']
        for node in block['nodes']:
            if node['repository']['nameWithOwner'] != repo:
                continue
            review = node['pullRequestReview'] or {}
            bucket(counts, login, review.get('submittedAt'), 'reviews', months)
            if review.get('state') == 'APPROVED':
                bucket(counts, login, review.get('submittedAt'), 'approvals',
                       months)
        if not block['pageInfo']['hasNextPage']:
            return
        cursor = json.dumps(block['pageInfo']['endCursor'])

def sweep_comments(repo, start, counts, totals, months):
    '''every comment written in the window, from the two repository-wide
       listings: conversation comments on issues and pull requests, and
       inline comments on the diff of a pull request.  their "since"
       parameter filters on the last edit rather than on creation, so it
       returns a few older comments that were edited inside the window;
       those are dropped by the bucketing, while everything created inside
       it is necessarily returned'''
    for endpoint in ('issues/comments', 'pulls/comments'):
        page = 1
        while True:
            rows = gh_rest(f'/repos/{repo}/{endpoint}', since=start,
                           per_page=100, page=page, sort='created',
                           direction='asc')
            for row in rows:
                login = (row.get('user') or {}).get('login', '')
                bucket(counts, login, row.get('created_at'), 'comments', months)
                key = str(row.get('created_at', ''))[:7]
                if key in months:
                    totals['comments'][key] = totals['comments'].get(key, 0) + 1
            if len(rows) < 100:
                break
            page += 1

# the contributions collection refuses a window spanning more than a year, so
# the reviews are asked for in chunks of at most this many months.  eleven
# leaves room for the padding below however long the months are
REVIEW_CHUNK = 11

def review_windows(window):
    '''the sub-windows the reviews are collected over: consecutive chunks of
       the month list, each padded by a day.  the padding is there because the
       collection snaps its bounds to whole days in the member's own timezone,
       which can pull a bound the wrong way by a few hours; widening it can
       only add reviews outside the months asked for, and those are dropped
       again when they are bucketed'''
    pad = datetime.timedelta(days=1)
    def stamp(when):
        return when.strftime('%Y-%m-%dT%H:%M:%SZ')
    def parse(text):
        return datetime.datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ')
    spans = []
    for first in range(0, len(window), REVIEW_CHUNK):
        chunk = window[first:first + REVIEW_CHUNK]
        spans.append((stamp(parse(chunk[0][1]) - pad),
                      stamp(parse(chunk[-1][2]) + pad)))
    return spans

# ------------------------------------------------------------------ main

def collect(repo, org, team, nmonths, today):
    window = months_back(nmonths, today)
    months = {label for label, _, _ in window}
    start, end = window[0][1], window[-1][2]

    members = team_members(org, team)
    if not members:
        raise RuntimeError(f'team {org}/{team} has no members')
    logins = [member['login'] for member in members]
    counts = {login: {metric: {} for metric in METRICS} for login in logins}
    totals = {metric: {} for metric in REPO_METRICS}

    branch = sweep_commits(repo, start, end, counts, totals, months)
    opened, merged = set(), set()
    for label, first, last in window:
        sweep_opened(repo, label, first, last, counts, totals, months, opened)
        sweep_merged(repo, label, first, last, counts, totals, months, merged)
    sweep_comments(repo, start, counts, totals, months)

    for login in logins:
        for span in review_windows(window):
            sweep_reviews(repo, login, span, counts, months)

    labels = [label for label, _, _ in window]
    for member in members:
        member['counts'] = {metric: [counts[member['login']][metric].get(label, 0)
                                     for label in labels]
                            for metric in METRICS}
    return {
        'repo': repo, 'org': org, 'team': team, 'branch': branch,
        'url': f'https://github.com/orgs/{org}/teams/{team}',
        'months': labels,
        'metrics': list(METRICS),
        'members': members,
        'totals': {metric: [totals[metric].get(label, 0) for label in labels]
                   for metric in REPO_METRICS},
    }

if __name__ == "__main__":
    parser = ArgumentParser(description="Fetch team member activity")
    parser.add_argument("--repo", default="lammps/lammps",
                        help="Repository the activity is counted in")
    parser.add_argument("--org", default="lammps", help="Organization")
    parser.add_argument("--team", default="core", help="Team slug")
    parser.add_argument("--months", type=int, default=13,
                        help="Months to collect, ending with the current one")
    parser.add_argument("--output", default="data/external/team.json",
                        help="Output JSON file")
    args = parser.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        data = collect(args.repo, args.org, args.team, args.months, now)
    except Exception as err:
        print(f"WARNING: could not collect team activity: {err}",
              file=sys.stderr)
        print(f"WARNING: {args.output} left untouched", file=sys.stderr)
        sys.exit(0)

    data['fetched'] = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    with open(args.output, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

    busiest = sorted(data['members'],
                     key=lambda m: -sum(m['counts']['commits']))[:3]
    print(f"{args.output}: {len(data['members'])} member(s) of {args.org}/"
          f"{args.team}, {len(data['months'])} months "
          f"({data['months'][0]}..{data['months'][-1]})")
    print("  most commits: " + ', '.join(
        f"{m['login']} {sum(m['counts']['commits'])}" for m in busiest))
