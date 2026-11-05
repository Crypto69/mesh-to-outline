"""X-Ray mode: a mesh click becomes a plane height (flat faces snap, just
inside the material), and the stack between start and end follows the
web app's rule (section_fit.stack_offsets + stack_extrudes)."""
import numpy as np
import pytest
import trimesh

from . import conftest
from mssaddin import xray

Z = np.array([0.0, 0.0, 1.0])


@pytest.fixture(scope='module')
def bracket():
    m = trimesh.load(conftest.sample('servo_bracket_1.stl'), force='mesh')
    return np.asarray(m.vertices), np.asarray(m.faces), m


def _tri(m, pick):
    idx = np.flatnonzero(pick)
    assert len(idx), 'no such triangle on the bracket'
    return int(idx[0])


def test_flat_up_face_snaps_inside(bracket):
    V, F, m = bracket
    zmin = m.bounds[0, 2]
    nz, cz = m.face_normals[:, 2], m.triangles_center[:, 2]
    # the foot's top face: faces up, 5 mm above the bottom
    t = _tri(m, (nz > 0.999) & (np.abs(cz - zmin - 5.0) < 1e-6))
    h, flat = xray.pick_height(V, F, m.triangles_center[t], Z)
    assert flat
    assert h == pytest.approx(zmin + 5.0 - xray.INSIDE_MM, abs=1e-5)   # float32 STL


def test_flat_down_face_snaps_inside(bracket):
    V, F, m = bracket
    nz = m.face_normals[:, 2]
    t = _tri(m, nz < -0.999)
    h, flat = xray.pick_height(V, F, m.triangles_center[t], Z)
    assert flat
    assert h == pytest.approx(m.triangles_center[t][2] + xray.INSIDE_MM, abs=1e-5)


def test_slanted_face_takes_the_clicked_point(bracket):
    V, F, m = bracket
    nz = m.face_normals[:, 2]
    t = _tri(m, (np.abs(nz) > 0.1) & (np.abs(nz) < 0.9))
    p = m.triangles_center[t]
    h, flat = xray.pick_height(V, F, p, Z)
    assert not flat
    assert h == pytest.approx(p[2], abs=1e-9)


def test_click_off_the_mesh_takes_the_nearest_triangle(bracket):
    V, F, m = bracket
    nz, cz = m.face_normals[:, 2], m.triangles_center[:, 2]
    t = _tri(m, (nz > 0.999) & (np.abs(cz - m.bounds[0, 2] - 5.0) < 1e-6))
    # a hair above the face (a display mesh that differs a little)
    h, flat = xray.pick_height(V, F, m.triangles_center[t] + [0, 0, 1e-4], Z)
    assert flat


def test_plan_matches_the_web_rule(bracket):
    from stl_to_solid.section_fit import stack_offsets, stack_extrudes, STACK_EDGE_MM
    V, F, m = bracket
    lo, hi = m.bounds[:, 2]
    c, half = (lo + hi) / 2, (hi - lo) / 2
    origin = np.array([0.0, 0.0, 2.0])          # any plane across Z
    plan = xray.xray_plan(V, origin, Z, hi - 3.0, lo + 5.0 - 0.01, 0.7)   # reversed on purpose
    web = stack_offsets(hi - 3.0 - c, lo + 5.0 - 0.01 - c, 0.7, half)
    assert plan['heights_mm'] == pytest.approx([c + o for o in web])
    assert plan['offsets_mm'] == pytest.approx([c + o - 2.0 for o in web])
    assert plan['extrude_mm'] == stack_extrudes(plan['heights_mm'], 0.7, hi - STACK_EDGE_MM)
    # ascending, so every slab runs towards +normal
    assert all(b > a for a, b in zip(plan['offsets_mm'], plan['offsets_mm'][1:]))


def test_plan_clamps_inside_the_mesh(bracket):
    V, F, m = bracket
    lo, hi = m.bounds[:, 2]
    plan = xray.xray_plan(V, np.zeros(3), Z, lo - 50, hi + 50, 5.0)
    assert plan['heights_mm'][0] > lo and plan['heights_mm'][-1] < hi
    assert plan['extrude_mm'][-1] is None or plan['extrude_mm'][-1] >= 0.01


def test_single_plane_when_start_is_end(bracket):
    V, F, m = bracket
    plan = xray.xray_plan(V, np.zeros(3), Z, 10.0, 10.0, 0.2)
    assert len(plan['offsets_mm']) == 1 and plan['extrude_mm'] == [None]
