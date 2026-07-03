#!/usr/bin/env python3
'''
Generate the static LAMMPS test status website from the archived run data.

Reads:  data/<suite>/<runid>/run.json   (see tools/rundata.py for the layout)
        data/external/*.json            (optional summaries, e.g. coverage)
Writes: _site/index.html                (dashboard)
        _site/runs/<suite-slug>/<runid>.html  (per-run detail pages)
        _site/api/summary.json          (machine readable snapshot)

Only the Python standard library is required.

Usage:  python3 generator/build_site.py [--datadir data] [--outdir _site]
'''

from argparse import ArgumentParser
import datetime
import html
import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import rundata

# ---------------------------------------------------------------- styling

# palette: fixed status colors plus chart chrome, selected for light and dark
CSS = '''
:root {
  --page:      #f9f9f7; --surface:   #fcfcfb; --ink:      #0b0b0b;
  --ink-2:     #52514e; --muted:     #898781; --grid:     #e1e0d9;
  --border:    rgba(11,11,11,0.10);
  --good:      #0ca30c; --warning:   #fab219; --serious:  #ec835a;
  --critical:  #d03b3b; --accent:    #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page:    #0d0d0d; --surface:   #1a1a19; --ink:      #ffffff;
    --ink-2:   #c3c2b7; --muted:     #898781; --grid:     #2c2c2a;
    --border:  rgba(255,255,255,0.10);
    --accent:  #3987e5;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 15px; line-height: 1.45;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header.page {
  padding: 1.2rem 2rem 1rem; border-bottom: 1px solid var(--grid);
  background: var(--surface);
}
header.page h1 { margin: 0 0 0.15rem; font-size: 1.35rem; }
header.page .sub { color: var(--ink-2); font-size: 0.9rem; }
main { max-width: 1080px; margin: 0 auto; padding: 1.2rem 2rem 3rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 0.8rem; }
h2:first-child { margin-top: 0.8rem; }
.cards { display: flex; flex-wrap: wrap; gap: 1rem; }
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem 1.2rem; flex: 1 1 300px; min-width: 300px;
}
.card h3 { margin: 0 0 0.4rem; font-size: 1rem; }
.card .meta { color: var(--muted); font-size: 0.8rem; margin-top: 0.5rem; }
.tiles { display: flex; gap: 1.2rem; flex-wrap: wrap; margin: 0.5rem 0; }
.tile .num { font-size: 1.45rem; font-weight: 600; }
.tile .lbl { color: var(--ink-2); font-size: 0.75rem; }
.status { white-space: nowrap; }
.status .ico { font-weight: 700; }
.st-passed  .ico { color: var(--good); }
.st-failed  .ico { color: var(--critical); }
.st-error   .ico { color: var(--serious); }
.st-skipped .ico { color: var(--muted); }
.delta-bad  { color: var(--critical); font-weight: 600; }
.delta-good { color: var(--good); font-weight: 600; }
table { border-collapse: collapse; width: 100%; background: var(--surface);
        border: 1px solid var(--border); border-radius: 8px; }
th, td { text-align: left; padding: 0.4rem 0.7rem; border-top: 1px solid var(--grid);
         vertical-align: top; }
thead th { border-top: none; color: var(--ink-2); font-size: 0.8rem;
           font-weight: 600; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
td .msg { color: var(--ink-2); font-size: 0.85rem; }
tr.hidden { display: none; }
.filters { margin: 0.8rem 0; display: flex; gap: 0.5rem; flex-wrap: wrap;
           align-items: center; }
.filters button {
  background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  border-radius: 6px; padding: 0.25rem 0.7rem; cursor: pointer; font-size: 0.85rem;
}
.filters button.active { border-color: var(--accent); color: var(--accent);
                         font-weight: 600; }
.filters input {
  background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  border-radius: 6px; padding: 0.25rem 0.6rem; font-size: 0.85rem; min-width: 14rem;
}
.spark { display: block; margin-top: 0.4rem; }
.spark polyline { fill: none; stroke: var(--accent); stroke-width: 2; }
.spark circle { fill: var(--accent); }
.props { font-size: 0.85rem; color: var(--ink-2); }
.props td { padding: 0.15rem 0.7rem 0.15rem 0; border: none; }
footer { max-width: 1080px; margin: 0 auto; padding: 0 2rem 2rem;
         color: var(--muted); font-size: 0.8rem; }
'''

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
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="page">
<h1><a href="{root}index.html" style="color:inherit">LAMMPS Test Status</a></h1>
<div class="sub">{esc(title)}</div>
</header>
<main>
{body}
</main>
<footer>Generated {now} &middot; <a href="https://github.com/lammps/lammps">lammps/lammps</a></footer>
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
    out = '<div class="tiles">'
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
    svg = (f'<svg class="spark" width="{width}" height="{height}" role="img" '
           f'aria-label="broken tests trend">')
    svg += f'<polyline points="{poly}"/>'
    for x, y, runid, n in pts:
        svg += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3">'
                f'<title>{esc(runid)}: {n} broken</title></circle>')
    return svg + '</svg><div class="meta">broken tests, last '\
           f'{len(history)} runs</div>'

def diff_summary_html(diff, run_url=None):
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

    body = f'<h2>{esc(suite_title(suite))} &mdash; {esc(runid)}</h2>'
    body += tiles_html(counts)

    # metadata table
    body += '<table class="props"><tbody>'
    for key in ('sha', 'branch', 'run_url', 'generated'):
        if meta.get(key):
            value = esc(meta[key])
            if key == 'run_url':
                value = f'<a href="{value}">{value}</a>'
            body += f'<tr><td>{esc(key)}</td><td>{value}</td></tr>'
    for key, value in meta.get('properties', {}).items():
        body += f'<tr><td>{esc(key)}</td><td>{esc(value)}</td></tr>'
    body += '</tbody></table>'

    # comparison with the previous run
    idx = runs.index(runid)
    if idx > 0:
        previous = rundata.load_run(datadir, suite, runs[idx - 1])
        diff = rundata.compare_runs(previous, run)
        body += f'<h2>Changes vs {esc(runs[idx - 1])}</h2>'
        body += f'<p>{diff_summary_html(diff)}</p>'
        for key, label in (('new_failures', 'New failures'), ('fixed', 'Fixed'),
                           ('new_tests', 'New tests'), ('removed_tests', 'Removed tests')):
            if diff[key]:
                items = ''.join(f'<li><code>{esc(t)}</code></li>' for t in diff[key][:50])
                more = (f'<li>... and {len(diff[key]) - 50} more</li>'
                        if len(diff[key]) > 50 else '')
                body += f'<h3>{label} ({len(diff[key])})</h3><ul>{items}{more}</ul>'

    # last-ok information for currently broken tests
    broken = sorted(k for k, v in tests.items() if v['status'] in rundata.BAD)
    lastok = {}
    if broken and idx > 0:
        for test in broken:
            lastok[test] = rundata.last_ok_run(datadir, suite, runs[:idx + 1], test)

    # the full result table with filters
    body += '<h2>All tests</h2>'
    body += '''<div class="filters">
<button data-filter="all" class="active">All</button>
<button data-filter="failed">Failed</button>
<button data-filter="error">Errors</button>
<button data-filter="skipped">Skipped</button>
<button data-filter="passed">Passed</button>
<input type="search" id="q" placeholder="filter by name ...">
</div>'''
    body += ('<table id="results"><thead><tr><th>Status</th><th>Test</th>'
             '<th class="n">Time (s)</th><th>Details</th></tr></thead><tbody>')
    for key in sorted(tests):
        entry = tests[key]
        details = esc(entry['message'])
        if key in lastok and lastok[key]:
            details += f' <span class="meta">(last OK: {esc(lastok[key])})</span>'
        body += (f'<tr data-status="{esc(entry["status"])}">'
                 f'<td>{status_chip(entry["status"])}</td>'
                 f'<td><code>{esc(key)}</code></td>'
                 f'<td class="n">{entry["time"]:.1f}</td>'
                 f'<td><div class="msg">{details}</div></td></tr>')
    body += '</tbody></table>'
    body += '''<script>
(function() {
  var current = 'all';
  var buttons = document.querySelectorAll('.filters button');
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
        f.write(page(f'{suite_title(suite)} - {runid}', body, root='../../'))

def run_link(suite, runid):
    return f'runs/{suite_slug(suite)}/{runid}.html'

def build_index(datadir, outdir, summary):
    body = ''

    # regression suites as cards
    regression = [s for s in summary['suites'] if not s['suite'].startswith('unit-tests/')]
    if regression:
        body += '<h2>Regression tests</h2><div class="cards">'
        for entry in regression:
            counts = entry['counts']
            body += f'<div class="card"><h3><a href="{run_link(entry["suite"], entry["latest"])}">'
            body += f'{esc(suite_title(entry["suite"]))}</a></h3>'
            body += tiles_html(counts)
            if entry.get('diff'):
                body += f'<div>{diff_summary_html(entry["diff"])}</div>'
            body += sparkline(entry['history'])
            meta = []
            if entry.get('sha'):
                meta.append(f'commit {esc(entry["sha"][:10])}')
            meta.append(esc(entry['latest']))
            meta.append(f'{len(entry["history"])} archived run(s)')
            body += f'<div class="meta">{" &middot; ".join(meta)}</div>'
            body += '</div>'
        body += '</div>'

    # unit test matrix as a table
    matrix = [s for s in summary['suites'] if s['suite'].startswith('unit-tests/')]
    if matrix:
        body += '<h2>Unit tests (per platform / configuration)</h2>'
        body += ('<table><thead><tr><th>Configuration</th><th>Status</th>'
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
                     f'<td>{esc(entry["last_all_ok"]) if entry.get("last_all_ok") else "&mdash;"}</td></tr>')
        body += '</tbody></table>'

    # external report summaries (coverage, static analysis)
    external = summary.get('external', {})
    body += '<h2>Other reports</h2><div class="cards">'
    if 'coverage' in external:
        cov = external['coverage']
        body += ('<div class="card"><h3><a href="https://download.lammps.org/coverage/">'
                 'Code coverage</a></h3><div class="tiles">')
        for label in ('line_percent', 'function_percent', 'branch_percent'):
            if label in cov:
                body += (f'<div class="tile"><div class="num">{esc(cov[label])}%</div>'
                         f'<div class="lbl">{esc(label.split("_")[0])}</div></div>')
        body += f'</div><div class="meta">{esc(cov.get("date", ""))}</div></div>'
    else:
        body += ('<div class="card"><h3><a href="https://download.lammps.org/coverage/">'
                 'Code coverage</a></h3><div class="meta">summary not ingested yet;'
                 ' see download.lammps.org/coverage</div></div>')
    if 'analysis' in external:
        ana = external['analysis']
        body += ('<div class="card"><h3><a href="https://download.lammps.org/analysis/">'
                 'Static analysis</a></h3><div class="tiles">')
        for label, num in ana.get('counts', {}).items():
            body += (f'<div class="tile"><div class="num">{num}</div>'
                     f'<div class="lbl">{esc(label)}</div></div>')
        body += f'</div><div class="meta">{esc(ana.get("date", ""))}</div></div>'
    else:
        body += ('<div class="card"><h3><a href="https://download.lammps.org/analysis/">'
                 'Static analysis</a></h3><div class="meta">summary not ingested yet;'
                 ' see download.lammps.org/analysis</div></div>')
    body += '</div>'

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
