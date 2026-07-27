# LAMMPS Test Status Website

Aggregates the results of the automated LAMMPS test runs into a static
website and a rolling GitHub status issue.

## How it works

- GitHub Actions workflows in [lammps/lammps](https://github.com/lammps/lammps)
  upload JUnit XML test results as artifacts for post-merge runs on the
  `develop` branch (regression tests: merged `run.json` + JUnit XML; unit
  tests: one `junit-<config>` artifact per platform/configuration).
- The full regression tests are no longer run in GitHub Actions but on a
  dedicated machine with a much more complete LAMMPS configuration, in a
  `serial` and a `parallel` (4 MPI tasks) configuration, and published on
  download.lammps.org (`tools/fetch_regression.py`, see below).
- The [update workflow](.github/workflows/update.yml) in this repository
  ingests new artifacts (`tools/ingest_actions.py`) and the latest published
  regression results (`tools/fetch_regression.py`), archives one `run.json`
  per run under `data/<suite>/<runid>/`, rebuilds the website
  (`generator/build_site.py`), deploys it to GitHub Pages, and updates the
  rolling status issue (`tools/update_issue.py`). It runs twice a day.
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
    python3 tools/fetch_regression.py --dry-run # latest regression results
    python3 tools/fetch_docs.py                 # manual build status
    python3 tools/update_issue.py --repo <owner/repo> --site-url <url> --dry-run

`run.json` files can also be produced manually from local test runs with
`tools/regression-tests/merge_results.py` (regression tests) in lammps/lammps
or `tools/junit_to_json.py` (any JUnit XML file, e.g. from
`ctest --output-junit`).

## Data layout

    data/<suite>/<runid>/run.json

`<suite>` is `quick-regression`, `full-regression/<config>`, or
`unit-tests/<config>`: a suite that is run in more than one configuration
keeps them in subdirectories and appears once per configuration. `<runid>` is
`<ISO timestamp>_<short sha>` and sorts chronologically. The `run.json` format
is documented in `tools/rundata.py`.

## Full regression tests

`tools/fetch_regression.py` archives the results published as
<https://download.lammps.org/coverage/serial.json> and the corresponding
`parallel.json` (the `-summary.md` and `-regression.xml` files next to them
show the same data and are not ingested, since the JSON is a superset of
both). Only the most recent run is published, so a run that is not picked up
before the next one replaces it is lost; the runs are gated by changes in the
monitored branch, though, so unchanged results simply stay in place. Since the
published files are rewritten even when no new test run happened, ingestion
deduplicates on the generation time and commit recorded in the file rather
than on its modification time.

The commit and the branch are read from the `commit` and `branch` metadata
fields, and recovered from the `git_info` property where those are missing;
that property is also the source of the git describe string kept as
`version`. The website and the status issue read the commit as `sha`.

The run id is stamped with the `generated` time where that carries a time
zone, and with the publication time from the `Last-Modified` header where it
does not: a `generated` field without a zone is in the local time of the test
machine and cannot be compared with the UTC stamps of the runs ingested from
GitHub Actions.

### Tests that run out of time

A test that hits the time limit of the test harness is reported as an error
like any other, with a message ending in `timeout (<n>s expired)`. Whether it
expires depends on the limit in force (which differs between the serial and
the parallel configuration), on how many tests run beside it, and on the
machine - so it says nothing about the code. Those runs are classified as
`timeout` (`rundata.status_of()`), counted apart from the errors, and left
out of the broken count that drives the trend, the *last all OK* run, and the
notification comments. They are not swept under the carpet: they have their
own tile, filter, and column, a run-to-run comparison lists them as *newly
out of time*, and a test that starts hanging because of a code change shows
up there. The limit itself is read back from the messages
(`rundata.time_limits()`), since the run data does not record it.

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
