# LAMMPS Test Status Website

Aggregates the results of the automated LAMMPS test runs into a static
website and a rolling GitHub status issue.

## How it works

- GitHub Actions workflows in [lammps/lammps](https://github.com/lammps/lammps)
  upload JUnit XML test results as artifacts for post-merge runs on the
  `develop` branch (regression tests: merged `run.json` + JUnit XML; unit
  tests: one `junit-<config>` artifact per platform/configuration).
- The full regression tests are no longer run in GitHub Actions but on a
  dedicated machine with a much more complete LAMMPS configuration, and
  published on download.lammps.org (`tools/fetch_regression.py`, see below).
  The same input decks are run in four configurations: `serial` (one MPI
  task), `parallel` (4 MPI tasks), `openmp` (2 MPI tasks with 2 OpenMP
  threads each, through the OPENMP package), and `kokkos` (the same through
  KOKKOS/OpenMP).
- The same machine runs the unit tests in its native GCC build of x86_64
  Linux and publishes them as a JUnit XML file next to the coverage report
  (`tools/fetch_unittest.py`, see below). That build has a far more complete
  package selection than the GitHub Actions runners compile, so it covers
  several hundred tests more than any of the configurations ingested from
  there.
- The [update workflow](.github/workflows/update.yml) in this repository
  ingests new artifacts (`tools/ingest_actions.py`) and the latest published
  regression and unit test results (`tools/fetch_regression.py`,
  `tools/fetch_unittest.py`), archives one `run.json`
  per run under `data/<suite>/<runid>/`, rebuilds the website
  (`generator/build_site.py`), deploys it to GitHub Pages, and updates the
  rolling status issue (`tools/update_issue.py`). It runs twice a day.
- Summaries of the server-side reports (code coverage, static analysis) can
  be ingested as `data/external/*.json`; the state of the Coverity Scan is
  collected from two sources, the analysis metrics of the project overview
  page and the summary of the build that was submitted for scanning
  (`tools/fetch_coverity.py`, see below).
- The status of the automated manual builds is collected from the
  `status.json` files published with the three manual variants
  (`tools/fetch_docs.py`, see below), including the words the spellchecker
  flagged in the development version.

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
`parallel.json`, `openmp.json`, and `kokkos.json` (the `-summary.md` and
`-regression.xml` files next to them show the same data and are not ingested,
since the JSON is a superset of both). The file name is what identifies the
configuration: the `config_file` property does not, three of the four share
`config.yaml`, and only the title of the run spells the difference out - which
is why the status issue carries it alongside the suite name where it says more
than the name does (`rundata.config_label()`).

That title is a shorthand, though (`MPI+OpenMP`, `KOKKOS/OpenMP`), and it says
nothing about the decomposition. The website therefore does not repeat it on
the dashboard cards but spells out what each configuration runs on the run
page and on the comparison page (`rundata.CONFIG_DETAILS`), which is also
where the order the configurations are listed in comes from
(`rundata.CONFIG_ORDER`: serial, parallel, openmp, kokkos - by what each adds
to the one before, not alphabetically).

Only the most recent run is published, so a run that is not picked up
before the next one replaces it is lost; the runs are gated by changes in the
monitored branch, though, so unchanged results simply stay in place. Since the
published files are rewritten even when no new test run happened, ingestion
deduplicates on the generation time and commit recorded in the file rather
than on its modification time.

Beyond that, the archive keeps one run per commit, since every archived run is
a bar of the trend on the dashboard. The test machine only runs when the
monitored branch has changed, so a commit that is published twice was run
again while the test scripts themselves were being worked on: those results
replace the run archived for that commit
(`rundata.archived_with_commit()`, the new run is written before the old one
is removed), and where they repeat its every verdict as well they are not
archived at all - that is a re-publication rather than a run.

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
expires depends on the limit in force (180 s serial, 60 s for the others), on
how many tests run beside it, and on the machine - so it says nothing about
the code. Those runs are classified as
`timeout` (`rundata.status_of()`), counted apart from the errors, and left
out of the broken count that drives the *last all OK* run and the
notification comments. They are not swept under the carpet: they have their
own tile, their own band in the trend bars of a card, their own
filter and column, a run-to-run comparison lists them as *newly out of
time*, and a test that starts hanging because of a code change shows up
there. The limit itself is read back from the messages
(`rundata.time_limits()`), since the run data does not record it.

### What a dashboard card shows

The numbers of the latest run as tiles, the last `TREND_RUNS` (25) archived
runs as one stacked bar each, what changed since the run before, and which
branch, commit and time the numbers are of. A bar is as tall as the number of
tests of that run and is stacked from the baseline up in the order failed,
errors, timed out, skipped, passed: the outcomes worth watching sit on the
baseline, where a change in one of them changes the height of that band
rather than shifting everything above it, and the tests that passed float on
top, so the top edge of a bar stays the number of tests. The bars keep their
pitch while the archive fills, with the newest run at the right edge, and
they carry the color each outcome has everywhere on the site - which is what
makes the tiles above them the legend of the chart.

### Reading a regression result

The example inputs were not written to be tests, so a plain pass/fail count is
misleading and the run pages group the results the way
`tools/regression-tests/REPORTING.md` in lammps/lammps describes:

- **Needs a fix in the examples tree** - every test whose `attention` field
  names a problem with the input script itself, grouped by kind
  (`rundata.attention_groups()`). This is a work list against the repository,
  not against the code, and it is set independently of the verdict, so a test
  that passes can carry one. It is also the majority of what the regression
  suites report: reference log files that match no input, inputs that run a
  production number of steps, and inputs whose initial velocities depend on
  the number of MPI processes.
- **Worth investigating** - the remaining failures, sorted by how early the
  run deviates from its reference log (`rundata.divergence()`). A classical MD
  trajectory is chaotic, so a difference that first appears after a thousand
  steps says nothing about the code, while one that is there in the very first
  thermo output cannot be rounding. The late ones are folded away.
- **Not really tested** - the statuses that are not verdicts (no reference log
  file, needs a multi-partition run, package not installed, ...), counted per
  kind, since each implies different work.

`compare.html` puts the configurations of one commit side by side. It is
reached from the run pages of that commit rather than from the dashboard,
since it says nothing about a run of any other commit. A test is
only counted there where every configuration reaches a verdict on it: inputs
that need a fix and inputs that ran out of time are left out, because most of
the former cannot match a reference log file that was written with a different
number of MPI processes, and they bury everything else.

Because a timeout is the *absence* of a verdict rather than one, a comparison
against a run in which a test timed out falls back to the most recent run
before it that did judge that test (`rundata.compare_runs()` reads older runs
lazily, only as far as it needs them). Otherwise a test that keeps failing but
flaps through a timeout would be announced as a new failure every time it came
back - which is exactly what the archived parallel runs did on 2026-07-27.

## Published unit test run

`tools/fetch_unittest.py` archives
<https://download.lammps.org/coverage/junit.xml> under
`unit-tests/linux-x86_64-gcc`. This is the unit test suite run in the same
pass as the coverage report, in the machine's native GCC build of x86_64
Linux: a package selection far more complete than the GitHub Actions runners
compile, and the only configuration in the matrix that is not ingested from
there. It is published in the `ctest --output-junit` format, which the
converter for the GitHub Actions artifacts already reads
(`tools/junit_to_json.py`).

Publication works as for the regression results - a single file rewritten in
place - so the same rules apply, and they are implemented by the same helpers
in `tools/rundata.py`: deduplication on the generation time and commit
recorded in the file, and one run per commit.

What the JUnit format does *not* carry is the commit and the branch; it stamps
the run in the local time of the test machine, which cannot be compared with
the UTC stamps of the other suites, and it says nothing about the build beyond
the host name. All of that comes from
<https://download.lammps.org/coverage/summary.json> instead - the second set
of data published by this same run, already fetched for the coverage numbers
on the dashboard (`tools/fetch_external.py`). It records the commit in full,
the branch, the date of the run as UTC (which is what the run id is stamped
with), and the compiler and operating system, kept as properties of the run
under the names the full regression runs report the same two in
(`fetch_unittest.SUMMARY_PROPERTIES`).

The two files are the two halves of one run only once it has finished
publishing: a run wipes the webroot and fills it again, so both are absent for
the length of that (which is why all four regression results can read as 404
for a while), and a summary fetched in between can still be the one of the run
before. The abbreviated commit in the test output is of the binary those very
tests ran, so a summary that disagrees with it is not the other half of this
run: that read is dropped with a warning and retried on the next poll, since
what is published stays in place until the next run replaces it.

The git describe string kept as `version` is read from a `version` field of
the summary where it carries one, and otherwise from the output of the tests
themselves: `lmp -h` prints a `Git info (<branch> / <describe>)` banner, and
the JUnit file quotes what each test printed (`fetch_unittest.git_info_of()`).
That banner also names the branch and the abbreviated commit, which is what
carries a run whose summary could not be fetched - with the `Last-Modified`
header of the JUnit file for a stamp, so that a fetch that fails on the
summary alone still archives the run instead of dropping it.

That fallback is thin, and deliberately not relied on for anything else:
`ctest` cuts the output it quotes off at 1024 bytes per test (719 of the 991
tests of the run archived on 2026-07-28 are truncated), so the banner survives
only because it is printed near the top of the help text. The compiler and the
operating system, printed at the end of the same help text, do not survive at
all - which is why the summary is the only source for those two.

The name of the `ctest` suite (e.g. `Linux-g++-15`) is kept as a property
beside them: it names the compiler in short, and it is the only such record
for a run archived before the summary reported one.

## Documentation build status

The three published variants of the manual - `develop`
(<https://docs.lammps.org/latest/>), `release` (<https://docs.lammps.org/>),
and `stable` (<https://docs.lammps.org/stable/>) - each carry a `status.json`
in their document root with the documented commit, the build time, and the
outcome and duration of the build steps (html, pdf, publish, and, on
`develop`, spelling; see below). `tools/fetch_docs.py` collects those into
`data/external/docs.json`, together with the current head commit of each
branch as queried from GitHub.

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

### Spellchecker

The build of the development version also runs the spellchecker (`make
spelling` in the `doc` directory) and lists every word it did not recognize in
`status.json`, one line of `<file>:<line>: (<word>)  <context>` per hit, with
their number in the `spelling` build step. The other two manuals do not run
it: a typo can only be fixed on `develop`.

The list is carried into `docs.json` as it stands and rendered on a page of
its own, `spelling.html`, which links each hit to the documentation source on
GitHub at the commit that was built. The dashboard card and the status issue
only say how many words were flagged and link to that page.

A flagged word is not necessarily a misspelling - technical terms, author
names, and syntax the checker cannot know belong in
`doc/utils/sphinx-config/false_positives.txt` in the LAMMPS repository - so
the number is reported but does not enter the verdict on the build. What the
`spelling` step reports is whether the checker ran, the same as for the html,
pdf, and publish steps.

## Coverity Scan

`tools/fetch_coverity.py` collects `data/external/coverity.json` from the two
ends of the scan, which know different things about it:

- **metrics** - the publicly visible *Analysis Metrics* of
  <https://scan.coverity.com/projects/lammps-lammps>, scraped from the project
  overview page: outstanding, newly detected and fixed defects, defect
  density, lines analyzed, and the day of the last analysis. This is the
  outcome of an analysis, and the page does not say which state of the source
  tree it was run on.
- **build** - <https://download.lammps.org/analysis/coverity.json>, written by
  the script that builds and submits LAMMPS for scanning (`coverity.sh` in the
  lammps-analyze repository, run twice a week and only when the monitored
  branch has changed). It records the branch, commit, and version that were
  built, when, and with which compiler on which operating system - the input
  of an analysis, and none of it visible on the Coverity side.

The two halves are not two views of one run and are not merged into one: the
build is submitted from the LAMMPS side, the analysis runs on the Coverity
servers and can be delayed considerably, so the metrics usually describe an
earlier submission than the last one recorded. The dashboard card states them
as two things, and where the last submission postdates the day of the last
analysis - which is as precisely as the project page dates it - it is marked
*analysis pending* rather than compared against numbers it cannot be part of.

Whichever half cannot be read keeps the values it had, so a Coverity page that
refuses the scrape does not take the recorded submission down with it, and an
unreachable summary file does not blank out the metrics.
