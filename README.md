# LAMMPS Test Status Website

Aggregates the results of the automated LAMMPS test runs into a static
website and a rolling GitHub status issue.

**This is a prototype** for what may become the official LAMMPS test status
site; layout, data sources, and hosting location are still under discussion.

## How it works

- GitHub Actions workflows in [lammps/lammps](https://github.com/lammps/lammps)
  upload JUnit XML test results as artifacts for post-merge runs on the
  `develop` branch (regression tests: merged `run.json` + JUnit XML; unit
  tests: one `junit-<config>` artifact per platform/configuration).
- The nightly [update workflow](.github/workflows/update.yml) in this
  repository ingests new artifacts (`tools/ingest_actions.py`), archives one
  `run.json` per run under `data/<suite>/<runid>/`, rebuilds the website
  (`generator/build_site.py`), deploys it to GitHub Pages, and updates the
  rolling status issue (`tools/update_issue.py`).
- Summaries of the server-side reports (code coverage, static analysis) can
  be ingested as `data/external/*.json`.

## Notifications

The status issue body is rewritten in place on every update, which does not
notify anybody. A comment is posted only when new failures appear or known
failures are fixed; comments notify issue subscribers. Subscribe to the pinned
status issue to get emails about regressions - and nothing else.

## Local use

Everything only needs the Python standard library (plus the `gh` CLI for the
scripts that talk to GitHub):

    python3 generator/build_site.py             # data/ -> _site/
    python3 tools/ingest_actions.py --dry-run   # what would be ingested
    python3 tools/update_issue.py --repo <owner/repo> --site-url <url> --dry-run

`run.json` files can also be produced manually from local test runs with
`tools/regression-tests/merge_results.py` (regression tests) in lammps/lammps
or `tools/junit_to_json.py` (any JUnit XML file, e.g. from
`ctest --output-junit`).

## Data layout

    data/<suite>/<runid>/run.json

`<suite>` is `full-regression`, `quick-regression`, or `unit-tests/<config>`;
`<runid>` is `<ISO timestamp>_<short sha>` and sorts chronologically. The
`run.json` format is documented in `tools/rundata.py`.
