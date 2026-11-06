#!/usr/bin/env python3
'''
Shared helpers for the archived status of the nightly package builds: the data
layout written by tools/fetch_nightly.py, and the way the website reads it.

The layout is one file per build:

    data/nightly/<folder>.json

<folder> is the name of the dated folder on download.lammps.org/nightly/ that
holds the packages of that build, "YYYY-MM-DD", so that the files sort
chronologically.  A file is the status file of the build as it was published:

    {"date": <when the build ended, ISO timestamp in UTC>,
     "branch": ..., "commit": ..., "version": <the git describe string>,
     "url": <the page listing the folders>, "folder": <folder>,
     "tasks": {<task>: {"seconds": <time spent on it>, "status": true | false}}}

The branch, commit, and version are those of the LAMMPS checkout the packages
were built from.
'''

import json
import os
import re

import docsdata

NIGHTLYDIR = 'nightly'
FOLDER = re.compile(r'\d{4}-\d{2}-\d{2}')

# the tasks of a build, as (key, label, description), in the order they are
# run (create-all-lammps.sh in the lammps-release repository)
TASKS = (
    ('boot', 'boot', 'start the containers and the macOS virtual machine'),
    ('macos', 'macOS', 'the macOS package with LAMMPS-GUI, for Intel and Apple CPUs'),
    ('linux', 'Linux', 'the Linux package with LAMMPS-GUI'),
    ('static', 'static', 'the statically linked Linux executables'),
    ('flatpak', 'flatpak', 'the LAMMPS-GUI flatpak, the source archive, and the PDF manual'),
    ('windows', 'Windows', 'the Windows installers and plugins'),
    ('shutdown', 'shutdown', 'stop the virtual machine and the containers'),
)
TASK_KEYS = tuple(key for key, _, _ in TASKS)

def usable(status):
    '''what is wrong with a published status file, or an empty string.  the
       folder becomes the name of a file here, and the tasks are what the
       status is for, so neither can be left to chance'''
    if not isinstance(status, dict):
        return 'not a JSON object'
    if not FOLDER.fullmatch(str(status.get('folder', ''))):
        return f'not a dated folder: {status.get("folder")!r}'
    if docsdata.parse_iso(status.get('date')) is None:
        return f'not a date: {status.get("date")!r}'
    tasks = status.get('tasks')
    if (not tasks or not isinstance(tasks, dict)
            or not all(isinstance(task, dict) for task in tasks.values())):
        return 'no tasks'
    return ''

def list_builds(datadir):
    '''the folders of the archived builds, oldest first'''
    path = os.path.join(datadir, NIGHTLYDIR)
    if not os.path.isdir(path):
        return []
    return sorted(name[:-5] for name in os.listdir(path)
                  if name.endswith('.json') and FOLDER.fullmatch(name[:-5]))

def load_builds(datadir, count):
    '''the status of the last "count" builds, oldest first'''
    builds = []
    for folder in list_builds(datadir)[-count:]:
        with open(os.path.join(datadir, NIGHTLYDIR, folder + '.json')) as f:
            builds.append(json.load(f))
    return builds

def seconds(build, task):
    '''the time spent on a task of a build; the status file is written
       elsewhere, so a value that is not a number counts as none'''
    return docsdata.seconds((build.get('tasks') or {}).get(task))

def passed(build, task):
    return (build.get('tasks') or {}).get(task, {}).get('status') is True

def total_seconds(build):
    return sum(seconds(build, task) for task in build.get('tasks') or {})

def task_order(builds):
    '''the tasks of some builds in the order they are run, followed by any
       unknown ones, so a task added on the build machine still shows up'''
    found = [task for build in builds for task in build.get('tasks') or {}]
    return ([task for task in TASK_KEYS if task in found]
            + [task for task in dict.fromkeys(found) if task not in TASK_KEYS])
