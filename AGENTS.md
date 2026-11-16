# Instructions for AI Coding Agents

This repository builds the LAMMPS testing dashboard (https://dashboard.lammps.org) and
the rolling status issue from the results of the automated test runs.  `README.md`
describes every data source, every card of the site, and the notifications: read the
section for the part you change, and update it in the same change.

## Layout

- `tools/fetch_*.py`, `tools/ingest_actions.py`: collect results (GitHub Actions
  artifacts, the REST API of https://ci.lammps.org, files published on
  download.lammps.org) into `data/<suite>/<runid>/run.json` and related files.
- `tools/*data.py`: the data formats, shared by the collectors and the generator.
- `generator/build_site.py`: builds the static site from `data/` into `_site/`.
- `tools/update_issue.py`: rewrites the status issue and posts the comments.
- `.github/workflows/update.yml`: runs all of it once a day and commits the new data.

## Rules

- **Python standard library only**, plus the `gh` command for the scripts that talk
  to GitHub.  No new dependencies.
- **`data/` is written by the update workflow.**  Do not edit archived results by
  hand, and run `git pull` before working: the workflow commits every day.  `_site/`
  is generated and ignored by git.
- **The dashboard is a summary and an entry point.**  Detailed reports (coverage,
  static analysis) stay on download.lammps.org and are linked, not reproduced.  Only
  the latest results matter: failures are fixed on the current state, so results
  that were replaced before a poll are not recovered.
- **Builder and workflow names are shared with other repositories.**  The tables
  `UNITTEST_BUILDERS` (`tools/buildbotdata.py`) and `CI_BADGES_GITHUB` /
  `CI_BADGES_BUILDBOT` (`generator/build_site.py`) must match the builders of
  ci.lammps.org and the workflows of lammps/lammps; the website repository has a
  matching table (`src/data/ci.yaml`).
- **A comment on the status issue notifies its subscribers.**  Comments go out only
  when the set of failures changes; keep it that way, and never post while testing.
- No tokens or other secrets in files, commit messages, or output.  No AI attribution
  lines (`Co-Authored-By:` and the like) in commit messages.  Commit or push only
  when the maintainer asks.

## Testing a change

- Run a collector with `--dry-run` first: it shows what would be archived without
  writing anything.
- Build the site into a scratch folder and look at the pages:
  `python3 generator/build_site.py --datadir data --outdir <scratch folder>`.
- For `tools/update_issue.py` use `--dry-run`, which prints the issue body and the
  comments instead of posting them.
- Test new logic with made-up `run.json` files in a scratch copy of `data/`, not by
  changing the archive.
