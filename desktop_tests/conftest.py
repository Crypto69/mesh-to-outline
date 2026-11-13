"""Desktop tests for the MeshSectionSketch add-in (needs pytest, numpy and
trimesh, see DEVELOPING.md):

    .venv/bin/python -m pytest desktop_tests -q

Puts the add-in folder (for `mssengine`, `mssaddin`) on sys.path, and the
stlToSolid checkout next to this repo when there is one (for `stl_to_solid`
and its samples: the parity tests skip without it). Installs the `adsk`
stub before anything imports `mssaddin.command`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ADDIN = os.path.dirname(HERE)
# STLTOSOLID_REPO points the tests at another checkout (a worktree)
REPO = os.environ.get('STLTOSOLID_REPO') or os.path.join(os.path.dirname(ADDIN), 'stlToSolid')
HAVE_REPO = os.path.isdir(os.path.join(REPO, 'stl_to_solid'))
for p in (ADDIN, REPO) if HAVE_REPO else (ADDIN,):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from . import fake_adsk  # noqa: E402

fake_adsk.install()


def sample(name):
    """Path of a stlToSolid sample; skips the test when it is not there."""
    p = os.path.join(REPO, 'samples', name)
    if not os.path.exists(p):
        pytest.skip('sample not present (needs the stlToSolid checkout): ' + name)
    return p
