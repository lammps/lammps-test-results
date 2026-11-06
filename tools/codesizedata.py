#!/usr/bin/env python3
'''
Shared helpers for the archived size of the code of LAMMPS: the data layout
written by tools/fetch_codesize.py, and the way the website reads it.

The layout is one file per week:

    data/codesize/<week>.json

<week> is the Sunday the week began on, "YYYY-MM-DD", as for the weekly commits
of the activity card, so that the files sort chronologically.  A file is what
codesize.py in the lammps-analyze repository published about the end of that week:

    {"week": <week>, "date": <the time of the commit, ISO timestamp in UTC>,
     "branch": ..., "commit": ...,
     "presets": {"basic": [<package>, ...], "most": [<package>, ...]},
     "lines": {<folder>: <lines of code>}}

A folder is "src" for the top of src/, its name for one below it, and
"lib/<name>" for a library.  They are only put into groups here, with the
presets of the newest week for all of them, so that a change of the groups or of
a preset applies to the whole history without counting anything again.
'''

import datetime
import json
import os
import re

CODESIZEDIR = 'codesize'
WEEK = re.compile(r'\d{4}-\d{2}-\d{2}')

# the folders with a group of their own that are not named by a preset
CORE = ('src', 'fmt', 'nlohmann', 'STUBS')
ACCELERATORS = ('GPU', 'INTEL', 'KOKKOS', 'OPENMP', 'OPT')
LIBRARIES = ('lib/colvars', 'lib/gpu', 'lib/kokkos', 'lib/lepton', 'lib/linalg')
# the legacy build system, which is not part of what gets compiled
BUILD_SYSTEM = ('MAKE', 'DEPEND')

# the groups as (key, label, description), in the order they are stacked
GROUPS = (
    ('core', 'Core', 'the files that are always included: the top of src/,'
     ' and fmt, nlohmann, and STUBS below it'),
    ('basic', 'Basic', 'the packages of the basic preset'),
    ('accel', 'Accel.', 'the accelerator packages: ' + ', '.join(ACCELERATORS)),
    ('most', 'Most', 'the remaining packages of the most preset'),
    ('other', 'Other', 'the packages in neither of those'),
    ('libs', 'Libraries', 'the bundled libraries: '
     + ', '.join(name[4:] for name in LIBRARIES)),
)

def usable(status):
    '''what is wrong with a published status file, or an empty string.  the
       week becomes the name of a file here, and the lines are what the status
       is for, so neither can be left to chance'''
    if not isinstance(status, dict):
        return 'not a JSON object'
    week = str(status.get('week', ''))
    try:
        datetime.date.fromisoformat(week)
    except ValueError:
        week = ''
    if not WEEK.fullmatch(week):
        return f'not a week: {status.get("week")!r}'
    if not isinstance(status.get('presets'), dict):
        return 'no presets'
    lines = status.get('lines')
    if (not lines or not isinstance(lines, dict)
            or not all(isinstance(num, int) for num in lines.values())):
        return 'no lines of code'
    return ''

def list_weeks(datadir):
    '''the archived weeks, oldest first'''
    path = os.path.join(datadir, CODESIZEDIR)
    if not os.path.isdir(path):
        return []
    return sorted(name[:-5] for name in os.listdir(path)
                  if name.endswith('.json') and WEEK.fullmatch(name[:-5]))

def load_weeks(datadir, count):
    '''the last "count" weeks, oldest first'''
    weeks = []
    for week in list_weeks(datadir)[-count:]:
        with open(os.path.join(datadir, CODESIZEDIR, week + '.json')) as f:
            weeks.append(json.load(f))
    return weeks

def group_of(folder, presets):
    '''the group a folder is counted in, or None for one that is in none'''
    if folder in CORE:
        return 'core'
    if folder in BUILD_SYSTEM:
        return None
    if folder.startswith('lib/'):
        return 'libs' if folder in LIBRARIES else None
    if folder in ACCELERATORS:
        return 'accel'
    if folder in presets.get('basic', ()):
        return 'basic'
    return 'most' if folder in presets.get('most', ()) else 'other'

def group_lines(week, presets):
    '''the lines of code of a week per group'''
    groups = {key: 0 for key, _, _ in GROUPS}
    for folder, lines in week.get('lines', {}).items():
        group = group_of(folder, presets)
        if group:
            groups[group] += lines
    return groups
