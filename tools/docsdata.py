#!/usr/bin/env python3
'''
Status of the automated LAMMPS manual builds: the data layout written by
tools/fetch_docs.py and the rules for judging a build from it.  Shared by
the website generator and the status issue updater so that both apply the
same policy and report the same state.

data/external/docs.json:

    {
      "fetched": <ISO timestamp of the last collection run>,
      "url":     <landing page of the manual>,
      "branches": [
        {
          "branch":  "develop" | "release" | "stable",
          "url":     <document root of this manual variant>,
          "commit":  <LAMMPS commit the published manual was built from>,
          "version": <LAMMPS version string, git describe style>,
          "built":   <ISO timestamp of that build>,
          "steps":   {<step>: {"status": <status>, "seconds": <duration>}},
          "head":    {"commit": <current branch head>, "date": <its date>},
          "checked": <ISO timestamp of the last successful read>,
          "error":       <why the status file could not be read, if so>,
          "error_seen":  <when that read was attempted>
        }, ...
      ]
    }

"head" is absent when the branch head could not be determined, and the
freshness check below is then skipped rather than run against a possibly
outdated commit.  "error" is present when the status file could not be
fetched; the other fields then still hold the last known values.

The manual is rebuilt hourly but only once per commit hash, so an unchanged
status file usually means there was nothing to do and its age says nothing
about the health of the build machine.  Freshness is therefore judged by
comparing the documented commit against the head of the branch it tracks:

    passed   all build steps went through and the published manual is
             current with the branch it tracks
    failed   a build step reported a problem
    pending  the branch has moved on and the new commit has been waiting
             for less than STALE_HOURS
    stale    the branch has moved on and the new commit has been waiting
             for at least that long, so the build was skipped or failed
             without saying so
    unknown  no status file has been retrieved yet, or the last one is
             older than STALE_HOURS because the file is unreachable
'''

import datetime
import json
import os

STALE_HOURS = 6
STEPS = ('html', 'pdf', 'publish')
STEP_LABELS = {'publish': 'sync'}
# states worth notifying subscribers about, as opposed to merely displaying
NOTIFY = ('failed', 'stale')

def load(datadir):
    '''the collected status, or None when it has never been fetched'''
    path = os.path.join(datadir, 'external', 'docs.json')
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('branches') else None

def parse_iso(stamp):
    '''parse an ISO 8601 timestamp (with or without a trailing Z) as UTC;
    returns None for anything unparsable, so callers can fall back'''
    if not stamp:
        return None
    try:
        when = datetime.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when

def fmt_utc(stamp):
    '''an ISO timestamp as a readable absolute UTC time, or an empty string'''
    when = parse_iso(stamp)
    return when.strftime('%Y-%m-%d %H:%M UTC') if when else ''

def seconds(step):
    '''duration of a build step; the status file is written elsewhere, so a
    missing or non-numeric value must not take the whole update down'''
    value = (step or {}).get('seconds', 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return round(value)

def step_order(steps):
    '''the known build steps in build order, followed by any unknown ones,
    so a step added on the build machine still shows up'''
    return ([name for name in STEPS if name in steps]
            + [name for name in steps if name not in STEPS])

def total_seconds(entry):
    steps = entry.get('steps') or {}
    return sum(seconds(steps[name]) for name in step_order(steps))

def timing(entry):
    '''per-step durations as plain text, e.g. "html 26 s, pdf 117 s"'''
    steps = entry.get('steps') or {}
    return ', '.join(f'{STEP_LABELS.get(name, name)} {seconds(steps[name])} s'
                     for name in step_order(steps))

def documents(entry):
    '''short "<version> @ <commit>" description of what the published manual
    contains; the git-describe suffix repeats the hash and is dropped'''
    parts = []
    if entry.get('version'):
        parts.append(str(entry['version']).split('-g')[0])
    if entry.get('commit'):
        parts.append(str(entry['commit'])[:10])
    return parts

def state(entry, now=None):
    '''classify one documentation build.  returns a dict with the status, a
    plain-text detail, and an optional timestamp the caller renders in its
    own format (a relative age on the website, absolute in the issue).

    a build step that reported a problem is always the headline; an
    otherwise clean build is then checked for freshness'''
    now = now or datetime.datetime.now(datetime.timezone.utc)
    steps = entry.get('steps') or {}
    if not steps and not entry.get('built'):
        return {'status': 'unknown', 'text': 'no status file retrieved yet',
                'stamp': ''}

    broken = [name for name in step_order(steps)
              if (steps[name] or {}).get('status') not in ('passed', 'skipped')]
    if broken:
        text = ', '.join(
            f'{STEP_LABELS.get(name, name)} '
            f'{(steps[name] or {}).get("status", "unknown")}' for name in broken)
        return {'status': 'failed', 'text': text, 'stamp': ''}

    # a status file that has been unreachable for a while (webserver down,
    # DNS trouble) leaves us without any current information; a known
    # failure above is still reported, a clean but aging one is not
    if entry.get('error'):
        checked = parse_iso(entry.get('checked'))
        if not checked or (now - checked).total_seconds() >= STALE_HOURS * 3600:
            return {'status': 'unknown', 'text': 'status file unreachable',
                    'stamp': entry.get('checked', '')}

    head = str((entry.get('head') or {}).get('commit') or '')
    if head and entry.get('commit') and head != entry['commit']:
        # the branch moved on; date the lag from the head commit, falling
        # back to the last build if the commit date is unknown
        stamp = (entry.get('head') or {}).get('date') or entry.get('built') or ''
        since = parse_iso(stamp)
        if since and (now - since).total_seconds() >= STALE_HOURS * 3600:
            return {'status': 'stale', 'text': f'{head[:10]} unbuilt since',
                    'stamp': stamp}
        return {'status': 'pending', 'text': f'{head[:10]} queued', 'stamp': ''}
    return {'status': 'passed', 'text': '', 'stamp': ''}
