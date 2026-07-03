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
import rundata

ICONS = {'passed': '&#10003;', 'failed': '&#10007;',
         'error': '&#9888;', 'skipped': '&#9675;'}
LABELS = {'passed': 'passed', 'failed': 'failed',
          'error': 'error', 'skipped': 'skipped'}

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

def suite_title(suite):
    if suite.startswith('unit-tests/'):
        return 'Unit Tests: ' + suite.split('/', 1)[1]
    return suite.replace('-', ' ').title()

def tiles_html(counts):
    tiles = [('Tests', counts['tests'], ''), ('Passed', counts['passed'], 'st-passed'),
             ('Failed', counts['failed'], 'st-failed'), ('Errors', counts['error'], 'st-error'),
             ('Skipped', counts['skipped'], 'st-skipped')]
    out = '<div class="d-flex flex-wrap gap-4 my-2">'
    for label, num, cls in tiles:
        out += (f'<div class="tile {cls}"><div class="num">{num}</div>'
                f'<div class="lbl">{label}</div></div>')
    return out + '</div>'

def sparkline(history, width=220, height=36):
    '''tiny SVG trend of the broken (failed+error) count over the recent runs'''
    if len(history) < 2:
        return ''
    history = history[-20:]
    top = max(max(n for _, n in history), 1)
    pts = []
    for i, (runid, n) in enumerate(history):
        x = 6 + i * (width - 12) / (len(history) - 1)
        y = height - 6 - (n / top) * (height - 12)
        pts.append((x, y, runid, n))
    poly = ' '.join(f'{x:.1f},{y:.1f}' for x, y, _, _ in pts)
    svg = (f'<svg class="spark d-block mt-2" width="{width}" height="{height}" role="img" '
           f'aria-label="broken tests trend">')
    svg += f'<polyline points="{poly}"/>'
    for x, y, runid, n in pts:
        svg += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3">'
                f'<title>{esc(runid)}: {n} broken</title></circle>')
    return (svg + '</svg><div class="text-body-secondary small">broken tests, last '
            f'{len(history)} runs</div>')

def diff_summary_html(diff):
    '''one-line rendering of a run-to-run comparison'''
    parts = []
    if diff['new_failures']:
        parts.append(f'<span class="delta-bad">+{len(diff["new_failures"])} new failures</span>')
    if diff['fixed']:
        parts.append(f'<span class="delta-good">{len(diff["fixed"])} fixed</span>')
    if diff['new_tests']:
        parts.append(f'{len(diff["new_tests"])} new tests')
    if diff['removed_tests']:
        parts.append(f'{len(diff["removed_tests"])} removed')
    if not parts:
        return 'no changes vs previous run'
    return ' &middot; '.join(parts) + ' vs previous run'

# ---------------------------------------------------------------- pages

def build_run_page(datadir, outdir, suite, runs, runid):
    run = rundata.load_run(datadir, suite, runid)
    meta = run['metadata']
    counts = meta['counts']
    tests = run['tests']

    body = tiles_html(counts)

    # metadata table
    body += '<table class="table table-sm table-borderless w-auto small text-body-secondary mb-4"><tbody>'
    for key in ('sha', 'branch', 'run_url', 'generated'):
        if meta.get(key):
            value = esc(meta[key])
            if key == 'run_url':
                value = f'<a href="{value}">{value}</a>'
            body += f'<tr><td class="pe-3">{esc(key)}</td><td>{value}</td></tr>'
    for key, value in meta.get('properties', {}).items():
        body += f'<tr><td class="pe-3">{esc(key)}</td><td>{esc(value)}</td></tr>'
    body += '</tbody></table>'

    # comparison with the previous run
    idx = runs.index(runid)
    if idx > 0:
        previous = rundata.load_run(datadir, suite, runs[idx - 1])
        diff = rundata.compare_runs(previous, run)
        body += f'<h2 class="h5 mt-4">Changes vs {esc(runs[idx - 1])}</h2>'
        body += f'<p>{diff_summary_html(diff)}</p>'
        for key, label in (('new_failures', 'New failures'), ('fixed', 'Fixed'),
                           ('new_tests', 'New tests'), ('removed_tests', 'Removed tests')):
            if diff[key]:
                items = ''.join(f'<li><code>{esc(t)}</code></li>' for t in diff[key][:50])
                more = (f'<li>... and {len(diff[key]) - 50} more</li>'
                        if len(diff[key]) > 50 else '')
                body += (f'<h3 class="h6">{label} ({len(diff[key])})</h3>'
                         f'<ul>{items}{more}</ul>')

    # last-ok information for currently broken tests
    broken = sorted(k for k, v in tests.items() if v['status'] in rundata.BAD)
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
        details = esc(entry['message'])
        if key in lastok and lastok[key]:
            details += (f' <span class="text-body-secondary">'
                        f'(last OK: {esc(lastok[key])})</span>')
        body += (f'<tr data-status="{esc(entry["status"])}">'
                 f'<td>{status_chip(entry["status"])}</td>'
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
    with open(outfile, 'w') as f:
        f.write(page(f'{suite_title(suite)} &mdash; {runid}', body, root='../../'))

def run_link(suite, runid):
    return f'runs/{suite_slug(suite)}/{runid}.html'

def build_index(datadir, outdir, summary):
    body = ''

    # regression suites as cards
    regression = [s for s in summary['suites'] if not s['suite'].startswith('unit-tests/')]
    if regression:
        body += '<h2 class="h5 mt-2">Regression tests</h2><div class="row g-3">'
        for entry in regression:
            counts = entry['counts']
            body += '<div class="col-md-6 col-xl-4"><div class="card h-100"><div class="card-body">'
            body += (f'<h3 class="h6 card-title">'
                     f'<a href="{run_link(entry["suite"], entry["latest"])}">'
                     f'{esc(suite_title(entry["suite"]))}</a></h3>')
            body += tiles_html(counts)
            if entry.get('diff'):
                body += f'<div>{diff_summary_html(entry["diff"])}</div>'
            body += sparkline(entry['history'])
            meta = []
            if entry.get('sha'):
                meta.append(f'commit {esc(entry["sha"][:10])}')
            meta.append(esc(entry['latest']))
            meta.append(f'{len(entry["history"])} archived run(s)')
            body += (f'<div class="text-body-secondary small mt-2">'
                     f'{" &middot; ".join(meta)}</div>')
            body += '</div></div></div>'
        body += '</div>'

    # unit test matrix as a table
    matrix = [s for s in summary['suites'] if s['suite'].startswith('unit-tests/')]
    if matrix:
        body += '<h2 class="h5 mt-4">Unit tests (per platform / configuration)</h2>'
        body += ('<div class="table-responsive"><table class="table table-striped '
                 'table-hover align-middle">'
                 '<thead><tr><th>Configuration</th><th>Status</th>'
                 '<th class="n">Tests</th><th class="n">Passed</th><th class="n">Failed</th>'
                 '<th class="n">Errors</th><th class="n">Skipped</th>'
                 '<th>Commit</th><th>Latest run</th><th>Last all-OK</th></tr></thead><tbody>')
        for entry in matrix:
            counts = entry['counts']
            broken = counts['failed'] + counts['error']
            status = status_chip('passed' if broken == 0 else 'failed')
            config = entry['suite'].split('/', 1)[1]
            sha = esc(entry.get('sha', '')[:10]) if entry.get('sha') else '&mdash;'
            body += (f'<tr><td><a href="{run_link(entry["suite"], entry["latest"])}">'
                     f'{esc(config)}</a></td>'
                     f'<td>{status}</td>'
                     f'<td class="n">{counts["tests"]}</td>'
                     f'<td class="n">{counts["passed"]}</td>'
                     f'<td class="n">{counts["failed"]}</td>'
                     f'<td class="n">{counts["error"]}</td>'
                     f'<td class="n">{counts["skipped"]}</td>'
                     f'<td>{sha}</td>'
                     f'<td>{esc(entry["latest"])}</td>'
                     f'<td>{esc(entry["last_all_ok"]) if entry.get("last_all_ok") else "&mdash;"}'
                     f'</td></tr>')
        body += '</tbody></table></div>'

    # external report summaries (coverage, static analysis)
    external = summary.get('external', {})
    body += '<h2 class="h5 mt-4">Other reports</h2><div class="row g-3">'
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
        body += (f'</div><div class="text-body-secondary small">'
                 f'{esc(cov.get("date", ""))}</div>')
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
        body += (f'</div><div class="text-body-secondary small">'
                 f'{esc(ana.get("date", ""))}</div>')
    else:
        body += ('<div class="text-body-secondary small">summary not ingested yet;'
                 ' see download.lammps.org/analysis</div>')
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

    for suite in rundata.list_suites(args.datadir):
        runs = rundata.list_runs(args.datadir, suite)
        history = []
        last_all_ok = None
        for runid in runs:
            run = rundata.load_run(args.datadir, suite, runid)
            counts = run['metadata']['counts']
            history.append((runid, counts['failed'] + counts['error']))
            if counts['failed'] + counts['error'] == 0:
                last_all_ok = runid
            build_run_page(args.datadir, args.outdir, suite, runs, runid)
        latest = rundata.load_run(args.datadir, suite, runs[-1])
        entry = {
            'suite': suite,
            'latest': runs[-1],
            'counts': latest['metadata']['counts'],
            'sha': latest['metadata'].get('sha', ''),
            'history': history,
            'last_all_ok': last_all_ok,
        }
        if len(runs) > 1:
            previous = rundata.load_run(args.datadir, suite, runs[-2])
            entry['diff'] = rundata.compare_runs(previous, latest)
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
    build_index(args.datadir, args.outdir, summary)
    # machine readable snapshot (also used for gating nightly runs upstream)
    api = {'generated': summary['generated'],
           'suites': [{k: v for k, v in s.items() if k != 'history'}
                      for s in summary['suites']]}
    for entry in api['suites']:
        if 'diff' in entry:
            entry['diff'] = {k: len(v) for k, v in entry['diff'].items()}
    with open(os.path.join(args.outdir, 'api', 'summary.json'), 'w') as f:
        json.dump(api, f, indent=2)
        f.write('\n')

    nsuites = len(summary['suites'])
    print(f"generated site for {nsuites} suite(s) in {args.outdir}/")
