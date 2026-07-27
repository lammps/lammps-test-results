# LAMMPS Test Status Website

Aggregates the results of the automated LAMMPS test runs into a static
website and a rolling GitHub status issue.

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
  be ingested as `data/external/*.json`; the publicly visible Coverity Scan
  analysis metrics are scraped nightly from the project overview page
  (`tools/scrape_coverity.py`).
- The status of the automated manual builds is collected from the
  `status.json` files published with the three manual variants
  (`tools/fetch_docs.py`, see below).

## Notifications

The status issue body is rewritten in place on every update, which does not
notify anybody. A comment is posted only when new failures appear or known
failures are fixed; comments notify issue subscribers. Subscribe to the pinned
status issue to get emails about regressions - and nothing else.

The same applies to the manual builds: a comment goes out when one of the
three variants starts failing or falls behind its branch, and again when it
recovers. Each of those is announced once, not on every update, which is
tracked per manual with a hidden marker in the comment rather than with the
run id used for the test suites.

## Local use

Everything only needs the Python standard library (plus the `gh` CLI for the
scripts that talk to GitHub):

    python3 generator/build_site.py             # data/ -> _site/
    python3 tools/ingest_actions.py --dry-run   # what would be ingested
    python3 tools/fetch_docs.py                 # manual build status
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

## Documentation build status

The three published variants of the manual - `develop`
(<https://docs.lammps.org/latest/>), `release` (<https://docs.lammps.org/>),
and `stable` (<https://docs.lammps.org/stable/>) - each carry a `status.json`
in their document root with the documented commit, the build time, and the
outcome and duration of the html, pdf, and publish steps. `tools/fetch_docs.py`
collects those into `data/external/docs.json`, together with the current head
commit of each branch as queried from GitHub.

The manual is rebuilt hourly but only once per commit hash, so an unchanged
`status.json` normally means there was nothing to do and its age says nothing
about the health of the build machine. Freshness is therefore judged by
comparing the documented commit against the branch head: a newer head commit
shows as *pending* and, once it has gone unbuilt for more than `STALE_HOURS`
(6 h), as *stale*. A `status.json` that cannot be fetched leaves the last
known values in place; if it stays unreachable for longer than the same
interval, the entry is shown as *unknown* rather than as a stale success.

Those rules and the `docs.json` layout live in `tools/docsdata.py` (the
counterpart of `tools/rundata.py`), so the website and the status issue judge
a build the same way and report the same state.
