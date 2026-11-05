"""draw_prims turns fitted primitives into the right sketch calls and never
closes an open chain."""
import numpy as np
import pytest

from . import conftest  # noqa: F401
from .fake_adsk import RecordingSketch
from .test_parity import _sheet


def _section(V, F, origin=(0, 0, 0), join=0.0, tol=0.08):
    from mssengine import section_fit
    return section_fit.section_preview(V, F, origin, [0, 0, 1], tol=tol, join_mm=join)


def test_open_u_is_three_lines_with_free_ends():
    from mssaddin.command import draw_section, stats_line
    V, F = _sheet([[0, 10], [0, 0], [20, 0], [20, 10]])
    sec = _section(V, F)
    sk = RecordingSketch()
    assert draw_section(sk, sec) == 3
    assert sk.kinds() == ['line', 'line', 'line']
    ends = [sk.calls[0][1], sk.calls[-1][2]]                 # cm
    assert np.allclose(ends, [[0, 1.0, 0], [2.0, 1.0, 0]])
    assert 'open curve' in stats_line(sec['stats'])


def test_outline_only_skips_open_chains():
    from mssaddin.command import draw_section, stats_line
    from mssengine import section_fit
    V, F = _sheet([[0, 10], [0, 0], [20, 0], [20, 10]])
    sec = section_fit.section_preview(V, F, [0, 0, 0], [0, 0, 1], tol=0.08, closed_only=True)
    sk = RecordingSketch()
    assert draw_section(sk, sec) == 0 and sec['polylines'] == [] and sec['open'] == []
    assert sec['stats']['open'] == 1
    assert 'open curve skipped' in stats_line(sec['stats'], outline=True)


def test_rounded_rect_is_four_lines_four_arcs(tmp_path):
    import trimesh
    from mssaddin.command import draw_section
    pytest.importorskip('cadquery')
    synth = pytest.importorskip('tests.synth')      # from the stlToSolid checkout
    p = synth.export(synth.rounded_rect(), str(tmp_path / 'rr.stl'))
    m = trimesh.load(p, force='mesh')
    sec = _section(m.vertices, m.faces)
    sk = RecordingSketch()
    draw_section(sk, sec)
    kinds = sk.kinds()
    assert kinds.count('line') == 4 and kinds.count('arc') == 4 and 'spline' not in kinds


def test_circle_and_spline_runs():
    import trimesh
    from mssaddin.command import draw_section
    cyl = trimesh.creation.cylinder(radius=6, height=4, sections=96)
    sec = _section(cyl.vertices, cyl.faces)
    sk = RecordingSketch()
    draw_section(sk, sec)
    assert sk.kinds() == ['circle'] and abs(sk.calls[0][2] - 0.6) < 1e-3
    # a rippled ring: too wobbly for lines/arcs -> fitted spline(s)
    t = np.linspace(0, 2 * np.pi, 2000, endpoint=False)
    r = 10 + 0.25 * np.sin(80 * t)
    xy = np.c_[r * np.cos(t), r * np.sin(t)]
    V, F = _sheet(np.vstack([xy, xy[:1]]))
    sec = _section(V, F, join=0.0, tol=0.05)
    sk = RecordingSketch()
    draw_section(sk, sec)
    assert 'spline' in sk.kinds()


def test_refused_arc_is_drawn_as_two_lines_and_the_run_goes_on():
    """Fusion refused a near-straight arc ("Some input argument is
    invalid") on a scanned controller and the whole X-Ray stopped. Now the
    arc becomes two lines through its own three points (same ends, so the
    loop stays closed), a refused line is left out, and both are counted."""
    from mssaddin.command import draw_prims
    sk = RecordingSketch()

    def refuse(*a):
        raise RuntimeError('3 : Some input argument is invalid')
    sk.sketchCurves.sketchArcs.addByThreePoints = refuse
    real_line = sk.sketchCurves.sketchLines.addByTwoPoints

    def line(a, b):
        if a == b:
            raise RuntimeError('3 : Some input argument is invalid')
        real_line(a, b)
    sk.sketchCurves.sketchLines.addByTwoPoints = line
    prims = [{'type': 'line', 'p0': [0, 0, 0], 'p1': [10, 0, 0]},
             {'type': 'arc', 'p0': [10, 0, 0], 'mid': [15, 0.0001, 0], 'p1': [20, 0, 0]},
             {'type': 'line', 'p0': [20, 0, 0], 'p1': [20, 0, 0]},
             {'type': 'line', 'p0': [20, 0, 0], 'p1': [0, 0, 0]}]
    notes = {}
    assert draw_prims(sk, prims, notes) == 4
    assert sk.kinds() == ['line', 'line', 'line', 'line']
    assert sk.calls[1][1:] == ((1.0, 0.0, 0.0), (1.5, 0.00001, 0.0))
    assert sk.calls[2][1:] == ((1.5, 0.00001, 0.0), (2.0, 0.0, 0.0))
    assert notes == {'arcs_as_lines': 1, 'skipped': 1}
