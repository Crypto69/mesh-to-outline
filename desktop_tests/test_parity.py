"""The add-in's engine copy gives exactly what stlToSolid gives."""
import os
import subprocess
import sys

import numpy as np
import pytest
import trimesh

from . import conftest

ADDIN = conftest.ADDIN


def _sheet(xy, z0=-1.0, z1=1.0):
    xy = np.asarray(xy, float)
    n = len(xy)
    V = np.vstack([np.c_[xy, np.full(n, z0)], np.c_[xy, np.full(n, z1)]])
    F = []
    for i in range(n - 1):
        F.append([i, i + 1, n + i + 1])
        F.append([i, n + i + 1, n + i])
    return V, np.asarray(F)


def _same_section(a, b):
    assert a['stats'] == b['stats']
    assert len(a['polylines']) == len(b['polylines'])
    for p, q in zip(a['polylines'], b['polylines']):
        assert np.allclose(p, q)
    assert len(a['open']) == len(b['open'])


def _cases():
    Va, Fa = _sheet([[0, 10], [0, 0], [20, 0], [20, 10]])
    Vb, Fb = _sheet([[10.5, 0], [20, 0]])
    slit_V = np.vstack([_sheet([[0, 0], [10, 0]])[0], Vb])
    slit_F = np.vstack([_sheet([[0, 0], [10, 0]])[1], Fb + 4])
    box = trimesh.creation.box([40, 30, 10])
    ring = trimesh.creation.annulus(r_min=5, r_max=8, height=4)
    return [('U sheet', Va, Fa, [0, 0, 0], 0.0), ('slit', slit_V, slit_F, [0, 0, 0], 1.0),
            ('box', box.vertices, box.faces, [0, 0, 1], 2.5),
            ('annulus', ring.vertices, ring.faces, [0, 0, 0.5], 2.5)]


@pytest.mark.parametrize('name,V,F,origin,join', _cases(), ids=lambda c: c if isinstance(c, str) else '')
def test_synthetic_parity(name, V, F, origin, join):
    from mssengine import section_fit as port
    ref = pytest.importorskip('stl_to_solid.section_fit')
    a = port.section_preview(V, F, origin, [0, 0, 1], tol=0.08, join_mm=join)
    b = ref.section_preview(V, F, origin, [0, 0, 1], tol=0.08, join_mm=join)
    _same_section(a, b)


def test_rc_n2_parity():
    from mssengine import section_fit as port
    ref = pytest.importorskip('stl_to_solid.section_fit')
    m = trimesh.load(conftest.sample('fixed-rc-n2-360.stl'), force='mesh')
    m.apply_scale(0.1)
    z = m.bounding_box.centroid[2] - 26.0
    a = port.section_preview(m.vertices, m.faces, [0, 0, z], [0, 0, 1], tol=0.08, join_mm=2.5)
    b = ref.section_preview(m.vertices, m.faces, [0, 0, z], [0, 0, 1], tol=0.08, join_mm=2.5)
    _same_section(a, b)
    assert a['stats']['lines'] > 50 and a['stats']['arcs'] > 50


@pytest.mark.skipif(not conftest.HAVE_REPO, reason='needs the stlToSolid checkout')
def test_engine_copies_are_current():
    r = subprocess.run([sys.executable, os.path.join(ADDIN, 'tools', 'sync_engine.py'), '--check'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_tidy_parity():
    """Tidy corners + sharpen give exactly what stlToSolid gives."""
    from mssengine import section_fit as port
    ref = pytest.importorskip('stl_to_solid.section_fit')
    for name, V, F, origin, join in _cases():
        a = port.section_preview(V, F, origin, [0, 0, 1], tol=0.08, join_mm=join, tidy=True, sharpen_mm=0.08)
        b = ref.section_preview(V, F, origin, [0, 0, 1], tol=0.08, join_mm=join, tidy=True, sharpen_mm=0.08)
        _same_section(a, b)
    m = trimesh.load(conftest.sample('fixed-rc-n2-360.stl'), force='mesh')
    m.apply_scale(0.1)
    o = [0, 0, m.bounding_box.centroid[2] - 26.0]
    a = port.section_preview(m.vertices, m.faces, o, [0, 0, 1], tol=0.08, tidy=True, sharpen_mm=0.08)
    b = ref.section_preview(m.vertices, m.faces, o, [0, 0, 1], tol=0.08, tidy=True, sharpen_mm=0.08)
    _same_section(a, b)
    assert a['loops'] == b['loops']


def test_draw_prims_tidy_is_the_web_scripts_copy():
    """The add-in's draw_prims_tidy / _nearest_end are the exact text of
    stl_to_solid.fusion_export._DRAW_TIDY (the X-Ray script's copy)."""
    fe = pytest.importorskip('stl_to_solid.fusion_export')
    src = open(os.path.join(ADDIN, 'mssaddin', 'command.py')).read()
    assert fe._DRAW_TIDY.strip('\n') in src
