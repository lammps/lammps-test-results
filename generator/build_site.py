#!/usr/bin/env python3
'''
Generate the static LAMMPS test status website from the archived run data.

Reads:  data/<suite>/<runid>/run.json   (see tools/rundata.py for the layout)
        data/external/*.json            (optional summaries, e.g. coverage)
        static/                         (vendored Bootstrap, brand CSS, logo)
Writes: _site/index.html                (dashboard)
        _site/runs/<suite-slug>/<runid>.html  (per-run detail pages)
        _site/api/summary.json          (machine readable snapshot)
        _site/static/                   (copy of the static assets)

The page layout and styling follow the design of the LAMMPS website
(www.lammps.org): Bootstrap 5 with the LAMMPS brand palette layered on
top (static/css/lammps-status.css), a dark navbar with the gold accent,
and a light/dark theme toggle. Only the Python standard library is
required.

Usage:  python3 generator/build_site.py [--datadir data] [--outdir _site]
'''

from argparse import ArgumentParser
import datetime
import html
import json
import os
import shutil
import sys

TOPDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.append(os.path.join(TOPDIR, 'tools'))
import docsdata
import rundata

ICONS = {'passed': '&#10003;', 'failed': '&#10007;',
         'error': '&#9888;', 'timeout': '&#9716;', 'skipped': '&#9675;',
         'pending': '&#8635;', 'stale': '&#9888;', 'unknown': '&#8212;'}
LABELS = {'passed': 'passed', 'failed': 'failed',
          'error': 'error', 'timeout': 'timeout', 'skipped': 'skipped',
          'pending': 'pending', 'stale': 'stale', 'unknown': 'unknown'}

def esc(text):
    return html.escape(str(text), quote=True)

def status_chip(status):
    ico = ICONS.get(status, '?')
    return (f'<span class="status st-{esc(status)}"><span class="ico">{ico}</span>'
            f' {esc(LABELS.get(status, status))}</span>')

def page(title, body, root=''):
    '''wrap page content in the site chrome (navbar, footer, theme toggle)'''
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} &middot; LAMMPS Test Status</title>
<script>
  (function () {{
    try {{
      var t = localStorage.getItem('theme') ||
              (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      document.documentElement.setAttribute('data-bs-theme', t);
    }} catch (e) {{}}
  }})();
</script>
<link rel="shortcut icon" href="{root}static/favicon.ico">
<link rel="apple-touch-icon" href="{root}static/apple-touch-icon.png">
<link href="{root}static/vendor/bootstrap/bootstrap.min.css" rel="stylesheet">
<link href="{root}static/css/lammps-status.css" rel="stylesheet">
</head>
<body>
<header>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark">
  <div class="container-fluid px-md-4">
    <a class="navbar-brand py-0" href="{root}index.html">
      <img src="{root}static/images/lammps-logo.png" alt="LAMMPS" height="32"
           onerror="this.replaceWith(document.createTextNode('LAMMPS'))">
    </a>
    <span class="navbar-text text-white me-auto">Test Status</span>
    <ul class="navbar-nav flex-row gap-3 me-3">
      <li class="nav-item"><a class="nav-link" href="{root}index.html">Dashboard</a></li>
      <li class="nav-item"><a class="nav-link" href="https://www.lammps.org/">lammps.org</a></li>
      <li class="nav-item"><a class="nav-link" href="https://docs.lammps.org/">Docs</a></li>
      <li class="nav-item"><a class="nav-link" href="https://github.com/lammps/lammps">GitHub</a></li>
    </ul>
    <button id="theme-toggle" type="button" class="theme-toggle"
            aria-label="Toggle dark mode" title="Toggle dark mode">&#9790;</button>
  </div>
</nav>
</header>
<main class="py-4">
<div class="container-fluid px-md-4">
<h1 class="h4 mb-3">{esc(title)}</h1>
{body}
</div>
</main>
<footer class="border-top py-4 text-body-secondary">
  <div class="container-fluid px-md-4 d-flex flex-wrap justify-content-between gap-2 small">
    <div>Aggregated results of the automated LAMMPS test runs.</div>
    <div>Last updated: {now} &middot;
      <a href="https://github.com/lammps/lammps-test-results">site source</a></div>
  </div>
</footer>
<script src="{root}static/vendor/bootstrap/bootstrap.bundle.min.js"></script>
<script>
  // absolute timestamps marked <time class="rel"> become relative ages when
  // the page is viewed, which a page rebuilt only once a day cannot bake in
  (function () {{
    function age(seconds) {{
      var minutes = seconds / 60;
      if (minutes < 1) return 'just now';
      if (minutes < 90) return Math.round(minutes) + ' min ago';
      if (minutes < 2880) return Math.round(minutes / 60) + ' h ago';
      return Math.round(minutes / 1440) + ' d ago';
    }}
    document.querySelectorAll('time.rel[datetime]').forEach(function (el) {{
      var when = Date.parse(el.getAttribute('datetime'));
      if (!isNaN(when)) el.textContent = age((Date.now() - when) / 1000);
    }});
  }})();
</script>
<script>
  (function () {{
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    function sync() {{
      var dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
      btn.innerHTML = dark ? '&#9728;' : '&#9790;';
    }}
    btn.addEventListener('click', function () {{
      var dark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
      var next = dark ? 'light' : 'dark';
      document.documentElement.setAttribute('data-bs-theme', next);
      try {{ localStorage.setItem('theme', next); }} catch (e) {{}}
      sync();
    }});
    sync();
  }})();
</script>
</body>
</html>
'''

def suite_slug(suite):
    return suite.replace('/', '-')

# the website and the status issue name a suite the same way (tools/rundata.py)
suite_title = rundata.suite_title

def time_tag(stamp):
    '''an absolute UTC timestamp that the browser turns into a relative age
    ("3 h ago") when the page is viewed; the pages are static and rebuilt
    only once a day, so a relative age baked in here would be wrong by the
    time anybody reads it.  the absolute time remains as the tooltip and as
    the fallback without JavaScript'''
    when = docsdata.parse_iso(stamp)
    if when is None:
        return ''
    text = esc(when.strftime('%Y-%m-%d %H:%M UTC'))
    return (f'<time class="rel" datetime="{esc(when.isoformat())}" '
            f'title="{text}">{text}</time>')

def runid_parts(runid):
    '''split a run id like 2026-07-14T00-44-31Z_4e2bce0464 into a readable
    "YYYY-MM-DD hh:mm" timestamp and the commit hash; manually ingested runs
    may lack the trailing Z. if the id does not follow either pattern, it is
    returned verbatim with an empty hash'''
    stamp, _, sha = runid.partition('_')
    for fmt in ('%Y-%m-%dT%H-%M-%SZ', '%Y-%m-%dT%H-%M-%S'):
        try:
            when = datetime.datetime.strptime(stamp, fmt)
            return when.strftime('%Y-%m-%d %H:%M'), sha
        except ValueError:
            pass
    return runid, ''

def tiles_html(counts):
    tiles = [('Tests', counts['tests'], ''), ('Passed', counts['passed'], 'st-passed'),
             ('Failed', counts['failed'], 'st-failed'), ('Errors', counts['error'], 'st-error'),
             ('Skipped', counts['skipped'], 'st-skipped')]
    # only where tests ran out of time, which the unit test suites never do
    if counts.get('timeout'):
        tiles.insert(4, ('Timeouts', counts['timeout'], 'st-timeout'))
    # the gap is what decides whether all six tiles fit on one line in a
    # card of a three-column dashboard; they wrap at gap-4
    out = '<div class="d-flex flex-wrap gap-3 my-2">'
    for label, num, cls in tiles:
        out += (f'<div class="tile {cls}"><div class="num">{num}</div>'
                f'<div class="lbl">{label}</div></div>')
    return out + '</div>'

def limits_note(limits):
    '''how the time limit that produced the timeouts of a run is named'''
    if not limits:
        return ''
    return ' / '.join(f'{limit} s' for limit in limits) + ' limit'

# how many runs a card shows: the test machine publishes at most once every
# 24 h, so this is about a month of history, and 25 bars over the width of a
# card come out as wide as the weekly commit bars of the activity card.  the
# workflow polls twice a day, but a poll that finds the same results again
# archives nothing (tools/fetch_regression.py), so a bar is a run and not a
# poll
TREND_RUNS = 25

# the segments of a bar, stacked from the bottom up.  the outcomes worth
# watching sit on the baseline, where a change in one of them is a change in
# the height of that band rather than a shift of everything above it; the
# tests that passed float on top, so that the top edge of a bar stays the
# number of tests of that run
BAR_SEGMENTS = (('failed', 'failed'), ('error', 'errors'), ('timeout', 'timed out'),
                ('skipped', 'skipped'), ('passed', 'passed'))

def bar_total(counts):
    '''the tests of a run that reached one of the states a bar is made of'''
    return sum(counts.get(key, 0) for key, _ in BAR_SEGMENTS)

def history_bars(history, width=234, height=96):
    '''how the outcomes of a suite are distributed over its last TREND_RUNS
       runs: one stacked bar per run, the whole bar the number of tests of
       that run, so that both the distribution and the way it moves can be
       read off it.  the bars keep their pitch while the archive fills, with
       the newest run at the right edge - three runs spread over the width of
       a card would read as a trend over a fortnight'''
    history = history[-TREND_RUNS:]
    if not history:
        return ''
    top = max(max(bar_total(counts) for _, counts in history), 1)
    barw = width // TREND_RUNS
    svg = (f'<svg class="trend d-block mt-2" width="100%" height="{height}" '
           f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
           f'aria-label="test outcomes of the last {len(history)} runs">')
    for i, (runid, counts) in enumerate(history):
        x = (TREND_RUNS - len(history) + i) * barw
        when, sha = runid_parts(runid)
        detail = ', '.join(f'{counts[key]} {name}' for key, name in BAR_SEGMENTS
                           if counts.get(key))
        svg += (f'<g><title>{esc(when)} {esc(sha)}: {bar_total(counts)} tests'
                f' ({esc(detail)})</title>')
        # stacked from the baseline up, one pixel of margin above and below
        y = height - 1
        for key, _ in BAR_SEGMENTS:
            num = counts.get(key, 0)
            if not num:
                continue
            # a handful of tests out of a thousand is under a pixel tall and
            # would disappear; it is drawn as a sliver instead
            barh = max(num / top * (height - 2), 0.75)
            y -= barh
            svg += (f'<rect class="st-{key}" x="{x}" y="{y:.2f}" '
                    f'width="{barw - 2}" height="{barh:.2f}"/>')
        svg += '</g>'
    svg += f'<line x1="0" y1="{height - 0.5}" x2="{width}" y2="{height - 0.5}"/></svg>'
    return svg + (f'<div class="text-body-secondary small">test outcomes, last '
                  f'{len(history)} run(s)</div>')

def delta_html(diff):
    '''what changed since the previous run, as the one line a card has room
       for: the detail behind it is on the run page the card links to.  both
       numbers are always shown, so that the line reads the same on every
       card, with the color carried only by the one that is not zero'''
    new, fixed = len(diff['new_failures']), len(diff['fixed'])
    bad = 'delta-bad' if new else 'delta-zero'
    good = 'delta-good' if fixed else 'delta-zero'
    return (f'<div class="mt-2"><span class="{bad}">+{new} failed</span> '
            f'<span class="{good}">-{fixed} fixed</span> '
            f'<span class="text-body-secondary">since last run</span></div>')

def diff_summary_html(diff):
    '''one-line rendering of a run-to-run comparison'''
    parts = []
    if diff['new_failures']:
        parts.append(f'<span class="delta-bad">+{len(diff["new_failures"])} new failures</span>')
    if diff['fixed']:
        parts.append(f'<span class="delta-good">{len(diff["fixed"])} fixed</span>')
    if diff.get('new_timeouts'):
        parts.append(f'{len(diff["new_timeouts"])} newly out of time')
    if diff['new_tests']:
        parts.append(f'{len(diff["new_tests"])} new tests')
    if diff['removed_tests']:
        parts.append(f'{len(diff["removed_tests"])} removed')
    if not parts:
        return 'no changes vs previous run'
    return ' &middot; '.join(parts) + ' vs previous run'

def utc_stamp(stamp):
    '''a timestamp of a report as "YYYY-MM-DD hh:mm UTC".  the Coverity page
       publishes a day rather than a time ("Jul 27, 2026"), which is
       normalized to the same order without inventing a time of day; anything
       else is passed through as it came'''
    when = docsdata.parse_iso(stamp)
    if when is not None:
        return when.strftime('%Y-%m-%d %H:%M UTC')
    try:
        return datetime.datetime.strptime(str(stamp), '%b %d, %Y').strftime('%Y-%m-%d')
    except ValueError:
        return str(stamp)

def card_footer(branch, commit, when):
    '''the line every card that reports on a commit ends on: which branch and
       commit the result is of, and when it was produced.  one shape for all
       of them, so that the cards can be read against one another - the parts
       a report does not record are left out rather than replaced'''
    ident = ' @ '.join(part for part in (str(branch), str(commit)[:10]) if part)
    parts = [esc(part) for part in (ident, when) if part]
    if not parts:
        return ''
    return (f'<div class="text-body-secondary small mt-2">'
            f'{" &middot; ".join(parts)}</div>')

# live GitHub Actions status badges, mirroring data/ci.yaml on the LAMMPS
# website (update both when a workflow file is renamed)
CI_REPO = 'lammps/lammps'
CI_BRANCH = 'develop'
CI_BADGES = (
    ('Linux', 'unittest-linux.yml'),
    ('Windows', 'compile-msvc.yml'),
    ('macOS', 'unittest-macos.yml'),
    ('Linux ARM64', 'unittest-arm64.yml'),
    ('Linux single-FFT', 'unittest-single.yml'),
    ('KOKKOS OpenMP', 'kokkos-regression.yaml'),
    ('Style check', 'style-check.yml'),
    ('C++23', 'check-cpp23.yml'),
    ('GNU make', 'check-gnu-make.yml'),
    ('No VLA', 'check-vla.yml'),
    ('CodeQL', 'codeql-analysis.yml'),
)

def ci_badges_html():
    '''row of live workflow status badges served by GitHub; they reflect
       the latest post-merge run at view time, complementing the archived
       results below'''
    out = '<div class="d-flex flex-wrap gap-2 my-2">'
    for label, file in CI_BADGES:
        out += (f'<a href="https://github.com/{CI_REPO}/actions/workflows/{file}'
                f'?query=branch%3A{CI_BRANCH}" target="_blank" rel="noopener" '
                f'title="{esc(label)} - {CI_BRANCH} branch">'
                f'<img src="https://github.com/{CI_REPO}/actions/workflows/{file}'
                f'/badge.svg?branch={CI_BRANCH}" alt="{esc(label)} build status" '
                f'height="20" loading="lazy"></a>')
    return out + '</div>'

def activity_card(activity):
    '''dashboard card with repository activity: open work tiles and a
       bar chart of weekly commit counts (last half year)'''
    out = '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
    out += (f'<h3 class="h6 card-title"><a href="{esc(activity.get("url", ""))}">'
            'Repository activity</a></h3>')
    out += '<div class="d-flex flex-wrap gap-4 my-2">'
    for label, key in (('open PRs', 'open_prs'), ('open issues', 'open_issues'),
                       ('stars', 'stars'), ('forks', 'forks')):
        if key in activity:
            out += (f'<div class="tile"><div class="num">{activity[key]}</div>'
                    f'<div class="lbl">{esc(label)}</div></div>')
    out += '</div>'

    weeks = activity.get('commits_per_week', [])[-26:]
    if len(weeks) > 1:
        # the bars keep their proportions in a coordinate system of their own
        # and are stretched to whatever width the card has; the height is in
        # pixels, so the baseline stays one pixel thick
        width, height = 234, 96
        top = max(max(n for _, n in weeks), 1)
        barw = width // len(weeks)
        svg = (f'<svg class="actbar d-block mt-2" width="100%" height="{height}" '
               f'viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
               f'role="img" aria-label="commits per week">')
        for i, (day, n) in enumerate(weeks):
            barh = round((n / top) * (height - 10))
            svg += (f'<rect x="{i * barw}" y="{height - 2 - barh}" '
                    f'width="{barw - 2}" height="{max(barh, 1)}" rx="1">'
                    f'<title>week of {esc(day)}: {n} commits</title></rect>')
        svg += (f'<line x1="0" y1="{height - 1}" x2="{width}" y2="{height - 1}"/>')
        svg += '</svg>'
        out += svg
        out += (f'<div class="text-body-secondary small">commits per week, last '
                f'{len(weeks)} weeks</div>')
    return out + '</div></div></div>'

DOCS_URL = 'https://docs.lammps.org/'

def docs_detail(state):
    '''the detail of a classified build as markup: its plain text plus, where
    the state carries one, the timestamp it refers to'''
    parts = [esc(state['text'])] if state['text'] else []
    if state['stamp']:
        parts.append(time_tag(state['stamp']))
    return ' '.join(parts)

def docs_card(docs):
    '''dashboard card with the status of the automated manual builds for the
       three published variants (develop, release, and stable branch)'''
    now = datetime.datetime.now(datetime.timezone.utc)
    out = '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
    out += (f'<h3 class="h6 card-title"><a href="{esc(docs.get("url", DOCS_URL))}">'
            'Documentation builds</a></h3>')
    out += ('<table class="table table-sm table-borderless docs mb-1">'
            '<tbody>')
    notes = []
    for entry in docs.get('branches', []):
        branch = entry.get('branch', '?')
        state = docsdata.state(entry, now)

        # left column: which manual, and what it currently documents
        ident = []
        if entry.get('version'):
            # the git-describe suffix repeats the commit hash shown next to it
            version = str(entry['version']).split('-g')[0]
            ident.append(f'<span title="{esc(entry["version"])}">{esc(version)}</span>')
        if entry.get('commit'):
            ident.append(f'<code>{esc(str(entry["commit"])[:10])}</code>')
        out += (f'<tr><td><a href="{esc(entry.get("url", DOCS_URL))}">{esc(branch)}</a>'
                f'<div class="lbl">{" &middot; ".join(ident)}</div></td>')

        # right column: how that build went, and how long ago it ran
        detail = docs_detail(state)
        seconds = docsdata.total_seconds(entry)
        meta = []
        if detail:
            meta.append(detail)
        elif seconds:
            meta.append(f'<span title="{esc(docsdata.timing(entry))}">{seconds} s</span>')
        if entry.get('built'):
            # labelled: a failure detail can carry an age of its own
            meta.append(f'built {time_tag(entry["built"])}')
        out += (f'<td class="text-end">{status_chip(state["status"])}'
                f'<div class="lbl">{" &middot; ".join(meta)}</div></td></tr>')

        if entry.get('error'):
            read = time_tag(entry.get('checked'))
            when = f' (last read {read})' if read else ''
            notes.append(f'{esc(branch)}: status file unreachable{when}')
    out += '</tbody></table>'

    fetched = time_tag(docs.get('fetched'))
    if fetched:
        notes.append(f'checked {fetched}')
    if notes:
        out += (f'<div class="text-body-secondary small">'
                f'{" &middot; ".join(notes)}</div>')
    return out + '</div></div></div>'

# ------------------------------------------------- interpreting a result

def listing_html(items, limit=50):
    '''a capped list of test names, each with a line of detail; the groups
       run to the hundreds and the point is to read the kind, not every
       member of it'''
    out = '<ul class="listing">'
    for key, detail in items[:limit]:
        out += f'<li><code>{esc(key)}</code>'
        if detail:
            out += f'<div class="lbl">{esc(detail)}</div>'
        out += '</li>'
    if len(items) > limit:
        out += f'<li class="text-body-secondary">... and {len(items) - limit} more</li>'
    return out + '</ul>'

def attention_section(run):
    '''the work list against the examples tree: what a developer has to fix
       in the inputs themselves, grouped by kind'''
    groups = rundata.attention_groups(run)
    if not groups:
        return ''
    total = len({key for keys in groups.values() for key in keys})
    body = (f'<h2 class="h5 mt-4">Needs a fix in the examples tree ({total})</h2>'
            '<p class="text-body-secondary small">Problems with the input scripts'
            ' rather than with the code. These stay until somebody edits the example,'
            ' and they are reported independently of the verdict, so a test that'
            ' passes can carry one.</p>')
    for kind, keys in groups.items():
        items = [(key, run['tests'][key].get('attention', '')) for key in keys]
        body += (f'<details class="mb-2"><summary>{esc(kind)} ({len(keys)})</summary>'
                 f'{listing_html(items)}</details>')
    return body

def divergence_detail(entry):
    '''how a failing test deviates from its reference log, in words'''
    if entry.get('diverged_row') == 0:
        return 'differs in the very first thermo output'
    at, row = entry.get('diverged_at'), entry.get('diverged_row')
    if at is None:
        return f'differs at output row {row}; no step column to say when'
    text = f'differs from step {at} on (output row {row})'
    if rundata.sparse_thermo(entry):
        text += ', but the thermo output is too sparse to tell when it started'
    return text

# the divergence classes, in the order they are worth reading, with the
# heading each gets and whether it starts folded away
DIVERGENCE_SECTIONS = (
    ('setup', 'Differs before the trajectory can diverge', False),
    ('early', 'Differs within the first 200 steps', False),
    ('late', 'Differs after 200 to 1000 steps', True),
    ('chaotic', 'Differs only after 1000 steps, consistent with chaos', True),
    ('nosteps', 'No step column in the thermo output', True),
)

def divergence_sections(run):
    '''the failures a developer should look at, sorted by how early the run
       deviates from its reference log.  tests that carry an "attention"
       field are left out: the input cannot match its reference there, which
       explains an early deviation on its own'''
    buckets = {}
    for key, entry in run.get('tests', {}).items():
        if rundata.status_of(entry) not in rundata.BAD or entry.get('attention'):
            continue
        kind = rundata.divergence(entry)
        if kind:
            buckets.setdefault(kind, []).append(key)
    if not buckets:
        return ''
    urgent = len(buckets.get('setup', [])) + len(buckets.get('early', []))
    body = (f'<h2 class="h5 mt-4">Worth investigating ({urgent})</h2>'
            '<p class="text-body-secondary small">A classical MD trajectory is chaotic,'
            ' so a difference that appears late says nothing about the code, while one'
            ' that is there in the first thermo output cannot be rounding. Inputs that'
            ' cannot match their reference log file are listed above instead.</p>')
    for kind, heading, folded in DIVERGENCE_SECTIONS:
        keys = sorted(buckets.get(kind, []))
        if not keys:
            continue
        items = [(key, divergence_detail(run['tests'][key])) for key in keys]
        summary = f'{heading} ({len(keys)})'
        if folded:
            body += (f'<details class="mb-2"><summary>{esc(summary)}</summary>'
                     f'{listing_html(items)}</details>')
        else:
            body += f'<h3 class="h6 mt-3">{esc(summary)}</h3>{listing_html(items)}'
    return body

def not_tested_section(run):
    '''the statuses that are not verdicts, counted per kind: each of them
       means a different piece of work, and folding them into "skipped"
       hides that'''
    tally = {}
    for entry in run.get('tests', {}).values():
        kind = rundata.not_tested_kind(entry)
        if kind:
            tally[kind] = tally.get(kind, 0) + 1
    if not tally:
        return ''
    body = (f'<h2 class="h5 mt-4">Not really tested ({sum(tally.values())})</h2>'
            '<table class="table table-sm w-auto"><tbody>')
    for kind, num in sorted(tally.items(), key=lambda item: -item[1]):
        body += (f'<tr><td class="n pe-3">{num}</td><td>{esc(kind)}</td></tr>')
    return body + '</tbody></table>'

# ---------------------------------------------------------------- pages

def build_run_page(datadir, outdir, suite, runs, runid, compare_sha=''):
    run = rundata.load_run(datadir, suite, runid)
    meta = run['metadata']
    counts = rundata.counts(run)
    tests = run['tests']

    # what this configuration runs, and where the same inputs in the other
    # configurations are: the dashboard cards have room for neither.  the
    # comparison covers one commit ("compare_sha"), so only the runs of that
    # commit link to it - it says nothing about an older run
    overview = []
    detail = rundata.config_detail(suite)
    if detail:
        overview.append(f'Configuration: {esc(detail)}')
    if compare_sha and meta.get('sha') == compare_sha:
        overview.append('<a href="../../compare.html">compared with the other'
                        ' configurations at this commit</a>')
    body = (f'<p class="text-body-secondary">{" &middot; ".join(overview)}</p>'
            if overview else '')
    body += tiles_html(counts)
    if counts.get('timeout'):
        body += (f'<p class="text-body-secondary small">{counts["timeout"]} test(s)'
                 f' hit the time limit of the test harness'
                 f' ({esc(limits_note(rundata.time_limits(run)))}) and are counted'
                 f' apart from the errors: whether they expire depends on the'
                 f' limit in force and on the load of the machine.</p>')

    # metadata table
    body += '<table class="table table-sm table-borderless w-auto small text-body-secondary mb-4"><tbody>'
    for key in ('title', 'sha', 'branch', 'version', 'generated',
                'run_url', 'source_url'):
        if meta.get(key):
            value = esc(meta[key])
            if key.endswith('url'):
                value = f'<a href="{value}">{value}</a>'
            body += f'<tr><td class="pe-3">{esc(key)}</td><td>{value}</td></tr>'
    for key, value in meta.get('properties', {}).items():
        body += f'<tr><td class="pe-3">{esc(key)}</td><td>{esc(value)}</td></tr>'
    body += '</tbody></table>'

    # comparison with the previous run
    idx = runs.index(runid)
    if idx > 0:
        previous = rundata.load_run(datadir, suite, runs[idx - 1])
        # older runs, newest first, read only as far as the comparison needs
        # them to find a verdict for the tests that timed out in "previous"
        earlier = (rundata.load_run(datadir, suite, older)
                   for older in reversed(runs[:idx - 1]))
        diff = rundata.compare_runs(previous, run, earlier)
        body += f'<h2 class="h5 mt-4">Changes vs {esc(runs[idx - 1])}</h2>'
        body += f'<p>{diff_summary_html(diff)}</p>'
        for key, label in (('new_failures', 'New failures'), ('fixed', 'Fixed'),
                           ('new_timeouts', 'Newly out of time'),
                           ('new_tests', 'New tests'), ('removed_tests', 'Removed tests')):
            if diff[key]:
                items = ''.join(f'<li><code>{esc(t)}</code></li>' for t in diff[key][:50])
                more = (f'<li>... and {len(diff[key]) - 50} more</li>'
                        if len(diff[key]) > 50 else '')
                body += (f'<h3 class="h6">{label} ({len(diff[key])})</h3>'
                         f'<ul>{items}{more}</ul>')

    # what the results mean: the work list against the examples tree first,
    # since an input that cannot match its reference explains its own failure
    body += attention_section(run)
    body += divergence_sections(run)
    body += not_tested_section(run)

    # last-ok information for currently broken tests
    broken = sorted(k for k, v in tests.items()
                    if rundata.status_of(v) in rundata.BAD)
    lastok = {}
    if broken and idx > 0:
        for test in broken:
            lastok[test] = rundata.last_ok_run(datadir, suite, runs[:idx + 1], test)

    # the full result table with filters
    body += '<h2 class="h5 mt-4">All tests</h2>'
    body += '''<div class="d-flex flex-wrap gap-2 align-items-center my-3">
<div class="btn-group btn-group-sm" role="group" aria-label="Status filter">
<button type="button" class="btn btn-outline-primary active" data-filter="all">All</button>
<button type="button" class="btn btn-outline-primary" data-filter="failed">Failed</button>
<button type="button" class="btn btn-outline-primary" data-filter="error">Errors</button>
<button type="button" class="btn btn-outline-primary" data-filter="timeout">Timeouts</button>
<button type="button" class="btn btn-outline-primary" data-filter="skipped">Skipped</button>
<button type="button" class="btn btn-outline-primary" data-filter="passed">Passed</button>
</div>
<input type="search" id="q" class="form-control form-control-sm w-auto"
       placeholder="filter by name ...">
</div>'''
    body += ('<div class="table-responsive"><table id="results" '
             'class="table table-striped table-hover align-middle">'
             '<thead><tr><th>Status</th><th>Test</th>'
             '<th class="n">Time (s)</th><th>Details</th></tr></thead><tbody>')
    for key in sorted(tests):
        entry = tests[key]
        status = rundata.status_of(entry)
        details = esc(entry['message'])
        if status in rundata.BAD and rundata.divergence(entry):
            details += (f'<div class="lbl">{esc(divergence_detail(entry))}</div>')
        if entry.get('attention'):
            details += f'<div class="attn">{esc(entry["attention"])}</div>'
        if key in lastok and lastok[key]:
            details += (f' <span class="text-body-secondary">'
                        f'(last OK: {esc(lastok[key])})</span>')
        body += (f'<tr data-status="{esc(status)}">'
                 f'<td>{status_chip(status)}</td>'
                 f'<td><code>{esc(key)}</code></td>'
                 f'<td class="n">{entry["time"]:.1f}</td>'
                 f'<td><div class="msg">{details}</div></td></tr>')
    body += '</tbody></table></div>'
    body += '''<script>
(function() {
  var current = 'all';
  var buttons = document.querySelectorAll('[data-filter]');
  var query = document.getElementById('q');
  function apply() {
    var text = query.value.toLowerCase();
    document.querySelectorAll('#results tbody tr').forEach(function(row) {
      var okStatus = (current === 'all') || (row.dataset.status === current);
      var okText = !text || row.textContent.toLowerCase().indexOf(text) >= 0;
      row.classList.toggle('hidden', !(okStatus && okText));
    });
  }
  buttons.forEach(function(btn) {
    btn.addEventListener('click', function() {
      buttons.forEach(function(other) { other.classList.remove('active'); });
      btn.classList.add('active');
      current = btn.dataset.filter;
      apply();
    });
  });
  query.addEventListener('input', apply);
})();
</script>'''

    outfile = os.path.join(outdir, 'runs', suite_slug(suite), runid + '.html')
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    emdash = '\u2014'
    with open(outfile, 'w') as f:
        f.write(page(f'{suite_title(suite)} {emdash} {runid}', body, root='../../'))

def run_link(suite, runid):
    return f'runs/{suite_slug(suite)}/{runid}.html'

def same_commit(latest):
    '''the largest set of configurations whose latest run is of one commit
       ("latest" maps a configuration to its (runid, run)); comparing runs of
       different commits would report the changes between them as
       disagreements. ties go to the newest set'''
    by_sha = {}
    for name, (runid, run) in latest.items():
        by_sha.setdefault(run['metadata'].get('sha', ''), {})[name] = (runid, run)
    if not by_sha:
        return '', {}
    sha = max(by_sha, key=lambda s: (len(by_sha[s]),
                                     max(runid for runid, _ in by_sha[s].values())))
    return sha, by_sha[sha]

def build_compare_page(outdir, sha, configs):
    '''compare the configurations of the full regression suite at one commit;
       returns what the dashboard needs to link to the page'''
    names = sorted(configs, key=rundata.config_sort_key)
    runs = {name: configs[name][1] for name in names}
    tests = {name: runs[name].get('tests', {}) for name in names}
    comparable, differing = rundata.compare_configs(runs)

    body = ('<p>The same input decks, run in different configurations at commit '
            f'<code>{esc(sha[:10])}</code>. A test counts here only where every'
            ' configuration reaches a verdict on it: an input that needs a fix in the'
            ' examples tree cannot match its reference log file for a reason of its'
            ' own - most of them because that log was written with a different number'
            ' of MPI processes - and an input that ran out of time has no verdict at'
            ' all. Both are left out, or they bury everything else.</p>')
    for label, num in (('Configurations', len(names)), ('Comparable', len(comparable)),
                       ('Disagreeing', len(differing))):
        body += ('<div class="tile d-inline-block me-4">'
                 f'<div class="num">{num}</div><div class="lbl">{esc(label)}</div></div>')

    # what each configuration brings, and how much of it cannot be compared
    body += ('<table class="table table-sm w-auto mt-3"><thead><tr>'
             '<th>Configuration</th><th>Runs on</th>'
             '<th class="n">Tests</th><th class="n">Needs a fix</th>'
             '<th class="n">Timeouts</th><th>Run</th></tr></thead><tbody>')
    for name in names:
        runid, run = configs[name]
        counts = rundata.counts(run)
        attention = sum(1 for entry in tests[name].values() if entry.get('attention'))
        body += (f'<tr><td><a href="{run_link("full-regression/" + name, runid)}">'
                 f'{esc(name)}</a></td>'
                 f'<td>{esc(rundata.config_detail("full-regression/" + name))}</td>'
                 f'<td class="n">{counts["tests"]}</td>'
                 f'<td class="n">{attention}</td>'
                 f'<td class="n">{counts["timeout"]}</td>'
                 f'<td class="lbl">{esc(runid)}</td></tr>')
    body += '</tbody></table>'

    if len(names) > 2:
        body += '<h2 class="h5 mt-4">How the configurations differ pairwise</h2>'
        body += ('<table class="table table-sm w-auto"><tbody>')
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                num = sum(1 for key in comparable
                          if rundata.status_of(tests[first][key])
                          != rundata.status_of(tests[second][key]))
                body += (f'<tr><td class="n pe-3">{num}</td>'
                         f'<td>{esc(first)} vs {esc(second)}</td></tr>')
        body += '</tbody></table>'

    body += f'<h2 class="h5 mt-4">Tests that do not agree ({len(differing)})</h2>'
    if differing:
        body += ('<div class="table-responsive"><table class="table table-sm '
                 'table-striped align-middle"><thead><tr><th>Test</th>'
                 + ''.join(f'<th>{esc(name)}</th>' for name in names)
                 + '</tr></thead><tbody>')
        for key in differing[:300]:
            body += f'<tr><td><code>{esc(key)}</code></td>'
            for name in names:
                body += f'<td>{status_chip(rundata.status_of(tests[name][key]))}</td>'
            body += '</tr>'
        body += '</tbody></table></div>'
        if len(differing) > 300:
            body += (f'<p class="text-body-secondary small">... and'
                     f' {len(differing) - 300} more</p>')
    else:
        body += '<p>None: every comparable test agrees across the configurations.</p>'

    with open(os.path.join(outdir, 'compare.html'), 'w') as f:
        f.write(page('Configuration comparison', body))
    return {'sha': sha, 'comparable': len(comparable), 'differing': len(differing),
            'configs': names}

def build_index(datadir, outdir, summary):
    body = '<h2 class="h5 mt-2">Live build status (post-merge, develop branch)</h2>'
    body += ci_badges_html()

    # unit test matrix as a table
    matrix = [s for s in summary['suites'] if s['suite'].startswith('unit-tests/')]
    if matrix:
        body += '<hr class="my-4">'
        body += '<h2 class="h5">Unit tests (per platform / configuration)</h2>'
        body += ('<div class="table-responsive"><table class="table table-striped '
                 'table-hover align-middle">'
                 '<thead><tr><th>Configuration</th><th>Status</th>'
                 '<th class="n">Tests</th><th class="n">Passed</th><th class="n">Failed</th>'
                 '<th class="n">Errors</th><th class="n">Skipped</th>'
                 '<th>Commit</th><th>Latest run (UTC)</th>'
                 '<th>Last all-OK (UTC)</th></tr></thead><tbody>')
        for entry in matrix:
            counts = entry['counts']
            status = status_chip('passed' if rundata.broken(counts) == 0 else 'failed')
            config = entry['suite'].split('/', 1)[1]
            sha = esc(entry.get('sha', '')[:10]) if entry.get('sha') else '&mdash;'
            # the commit hash embedded in the latest run id already has its
            # own column, so only the timestamp is shown here
            latest = esc(runid_parts(entry['latest'])[0])
            if entry.get('last_all_ok'):
                when, ok_sha = runid_parts(entry['last_all_ok'])
                all_ok = esc(f'{when} / {ok_sha}') if ok_sha else esc(when)
            else:
                all_ok = '&mdash;'
            body += (f'<tr><td><a href="{run_link(entry["suite"], entry["latest"])}">'
                     f'{esc(config)}</a></td>'
                     f'<td>{status}</td>'
                     f'<td class="n">{counts["tests"]}</td>'
                     f'<td class="n">{counts["passed"]}</td>'
                     f'<td class="n">{counts["failed"]}</td>'
                     f'<td class="n">{counts["error"]}</td>'
                     f'<td class="n">{counts["skipped"]}</td>'
                     f'<td>{sha}</td>'
                     f'<td>{latest}</td>'
                     f'<td>{all_ok}</td></tr>')
        body += '</tbody></table></div>'

    # regression suites as cards
    regression = [s for s in summary['suites'] if not s['suite'].startswith('unit-tests/')]
    if regression:
        body += '<hr class="my-4">'
        body += '<div class="row g-3">'
        for entry in regression:
            counts = entry['counts']
            body += '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
            body += (f'<h3 class="h6 card-title">'
                     f'<a href="{run_link(entry["suite"], entry["latest"])}">'
                     f'{esc(suite_title(entry["suite"]))}</a></h3>')
            body += tiles_html(counts)
            body += history_bars(entry['history'])
            if entry.get('diff'):
                body += delta_html(entry['diff'])
            when, run_sha = runid_parts(entry['latest'])
            body += card_footer(entry.get('branch', ''),
                                entry.get('sha', '') or run_sha, f'{when} UTC')
            body += '</div></div></div>'
        if 'activity' in summary.get('external', {}):
            body += activity_card(summary['external']['activity'])
        if 'docs' in summary.get('external', {}):
            body += docs_card(summary['external']['docs'])
        body += '</div>'

    # external report summaries (coverage, static analysis)
    external = summary.get('external', {})
    body += '<div class="row g-3">'
    body += '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
    body += ('<h3 class="h6 card-title"><a href="https://download.lammps.org/coverage/">'
             'Code coverage</a></h3>')
    if 'coverage' in external:
        cov = external['coverage']
        body += '<div class="d-flex flex-wrap gap-4 my-2">'
        for label in ('line_percent', 'function_percent', 'branch_percent'):
            if label in cov:
                body += (f'<div class="tile"><div class="num">{esc(cov[label])}%</div>'
                         f'<div class="lbl">{esc(label.split("_")[0])}</div></div>')
        body += '</div>' + card_footer(cov.get('branch', ''), cov.get('commit', ''),
                                       utc_stamp(cov.get('date', '')))
    else:
        body += ('<div class="text-body-secondary small">summary not ingested yet;'
                 ' see download.lammps.org/coverage</div>')
    body += '</div></div></div>'
    body += '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
    body += ('<h3 class="h6 card-title"><a href="https://download.lammps.org/analysis/">'
             'Static analysis</a></h3>')
    if 'analysis' in external:
        ana = external['analysis']
        body += '<div class="d-flex flex-wrap gap-4 my-2">'
        for label, num in ana.get('counts', {}).items():
            body += (f'<div class="tile"><div class="num">{num}</div>'
                     f'<div class="lbl">{esc(label)}</div></div>')
        body += '</div>' + card_footer(ana.get('branch', ''), ana.get('commit', ''),
                                       utc_stamp(ana.get('date', '')))
    else:
        body += ('<div class="text-body-secondary small">summary not ingested yet;'
                 ' see download.lammps.org/analysis</div>')
    body += '</div></div></div>'
    body += '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
    body += ('<h3 class="h6 card-title">'
             '<a href="https://scan.coverity.com/projects/lammps-lammps">'
             'Coverity Scan</a> '
             '<a href="https://scan.coverity.com/projects/lammps-lammps">'
             '<img alt="Coverity Scan Build Status" height="18" loading="lazy" '
             'src="https://scan.coverity.com/projects/33115/badge.svg"></a></h3>')
    if 'coverity' in external:
        cov = external['coverity']
        metrics = cov.get('metrics', {})
        body += '<div class="d-flex flex-wrap gap-4 my-2">'
        for label in ('Outstanding', 'Newly detected', 'Fixed', 'Defect Density'):
            if label in metrics:
                body += (f'<div class="tile"><div class="num">{esc(metrics[label])}</div>'
                         f'<div class="lbl">{esc(label.lower())}</div></div>')
        body += '</div>'
        if metrics.get('Lines of Code Analyzed'):
            body += (f'<div class="text-body-secondary small">'
                     f'{esc(metrics["Lines of Code Analyzed"])} lines analyzed'
                     f' &middot; {str(cov.get('version',''))[:10]} &middot; {cov.get('date', '')}</div>')
    else:
        body += ('<div class="text-body-secondary small">summary not scraped yet;'
                 ' see scan.coverity.com</div>')
    body += '</div></div></div></div>'

    with open(os.path.join(outdir, 'index.html'), 'w') as f:
        f.write(page('Dashboard', body))

# ---------------------------------------------------------------- main

if __name__ == "__main__":
    parser = ArgumentParser(description="Generate the LAMMPS test status website")
    parser.add_argument("--datadir", default="data", help="Input data directory")
    parser.add_argument("--outdir", default="_site", help="Output directory")
    args = parser.parse_args()

    summary = {'generated': datetime.datetime.now().isoformat(timespec='seconds'),
               'suites': []}

    # latest run per configuration of the full regression suite, for the
    # comparison between them.  which commit that comparison covers has to be
    # known before the run pages are written: the run pages of that commit are
    # what links to it, the dashboard does not
    regression = {}
    for suite in rundata.list_suites(args.datadir):
        if suite.startswith('full-regression/'):
            runs = rundata.list_runs(args.datadir, suite)
            regression[suite.split('/', 1)[1]] = (
                runs[-1], rundata.load_run(args.datadir, suite, runs[-1]))
    compare_sha, compare_group = same_commit(regression)
    if len(compare_group) < 2:
        compare_sha = ''

    for suite in rundata.list_suites(args.datadir):
        runs = rundata.list_runs(args.datadir, suite)
        # the counts of every archived run, for the trend bars of a card
        history = []
        last_all_ok = None
        for runid in runs:
            run = rundata.load_run(args.datadir, suite, runid)
            counts = rundata.counts(run)
            history.append((runid, counts))
            if rundata.broken(counts) == 0:
                last_all_ok = runid
            build_run_page(args.datadir, args.outdir, suite, runs, runid,
                           compare_sha)
        latest = rundata.load_run(args.datadir, suite, runs[-1])
        entry = {
            'suite': suite,
            'latest': runs[-1],
            'counts': rundata.counts(latest),
            'sha': latest['metadata'].get('sha', ''),
            'branch': latest['metadata'].get('branch', ''),
            'label': rundata.config_label(suite, latest['metadata'].get('title', '')),
            'time_limits': rundata.time_limits(latest),
            'attention': sum(1 for entry in latest.get('tests', {}).values()
                             if entry.get('attention')),
            'history': history,
            'last_all_ok': last_all_ok,
        }
        if len(runs) > 1:
            previous = rundata.load_run(args.datadir, suite, runs[-2])
            earlier = (rundata.load_run(args.datadir, suite, older)
                       for older in reversed(runs[:-2]))
            entry['diff'] = rundata.compare_runs(previous, latest, earlier)
        summary['suites'].append(entry)

    # optional external report summaries
    summary['external'] = {}
    extdir = os.path.join(args.datadir, 'external')
    if os.path.isdir(extdir):
        for name in sorted(os.listdir(extdir)):
            if name.endswith('.json'):
                with open(os.path.join(extdir, name)) as f:
                    summary['external'][name[:-5]] = json.load(f)

    # copy the static assets (vendored Bootstrap, brand CSS, logo)
    staticdir = os.path.join(TOPDIR, 'static')
    if os.path.isdir(staticdir):
        shutil.copytree(staticdir, os.path.join(args.outdir, 'static'),
                        dirs_exist_ok=True)

    os.makedirs(os.path.join(args.outdir, 'api'), exist_ok=True)
    if compare_sha:
        summary['compare'] = build_compare_page(args.outdir, compare_sha,
                                                compare_group)
    build_index(args.datadir, args.outdir, summary)
    # machine readable snapshot (also used for gating nightly runs upstream)
    # the trend bars of the dashboard read every archived run; the snapshot
    # reports the latest one, and keeps the history out of it
    api = {'generated': summary['generated'],
           'suites': [{k: v for k, v in s.items() if k != 'history'}
                      for s in summary['suites']]}
    if summary.get('compare'):
        api['compare'] = summary['compare']
    for entry in api['suites']:
        if 'diff' in entry:
            entry['diff'] = {k: len(v) for k, v in entry['diff'].items()}
    with open(os.path.join(args.outdir, 'api', 'summary.json'), 'w') as f:
        json.dump(api, f, indent=2)
        f.write('\n')

    nsuites = len(summary['suites'])
    print(f"Generated site for {nsuites} suite(s) in {args.outdir}/")
