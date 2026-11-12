"""The X-Ray mode end to end through the command's dialog handler and
_run, against a stand-in Fusion: a mesh click and a construction plane
become the stack's start and end, the sketches land on planes offset as
xray_plan says, and each slab is extruded to the next slice. The old One
and Stack modes still behave as before."""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import trimesh

from . import conftest
from .fake_adsk import Coll, RecordingSketch



def P(x, y, z):
    return SimpleNamespace(x=float(x), y=float(y), z=float(z))


def plane_geom(origin_cm, normal):
    return SimpleNamespace(origin=P(*origin_cm), normal=P(*normal))


class FakeMesh:
    def __init__(self, V, F):
        self.displayMesh = SimpleNamespace(nodeCoordinatesAsDouble=(np.asarray(V) / 10.0).ravel().tolist(),
                                           nodeIndices=np.asarray(F).ravel().tolist())
        self.entityToken, self.name, self.assemblyContext, self.isLightBulbOn = 'mesh1', 'bracket', None, True


class Body:
    n = 0

    def __init__(self):
        Body.n += 1
        self.entityToken, self.isValid = 'body%d' % Body.n, True


class Fusion:
    """Root component + UI that record what the command builds."""
    def __init__(self):
        self.planes, self.sketches, self.extrudes, self.messages = [], [], [], []
        fus = self

        class CPlanes:
            def createInput(self):
                return SimpleNamespace(setByOffset=lambda pl, v: setattr(self, '_off', (pl, v[1])))

            def add(self, inp):
                pl, off = self._off
                g = pl.geometry
                n = np.array([g.normal.x, g.normal.y, g.normal.z])
                o = np.array([g.origin.x, g.origin.y, g.origin.z]) + n * off
                new = SimpleNamespace(name='', geometry=plane_geom(o, n), offset=off)
                fus.planes.append(new)
                return new

        class Sketches:
            def add(self, plane):
                sk = RecordingSketch()
                sk.plane, sk.name, sk.isComputeDeferred = plane, '', False
                sk.profiles = Coll([SimpleNamespace(areaProperties=lambda: SimpleNamespace(area=1.0))])
                fus.sketches.append(sk)
                return sk

        class Extrudes:
            def createInput(self, prof, op):
                inp = SimpleNamespace(op=op, participantBodies=None)
                inp.setDistanceExtent = lambda sym, v: setattr(inp, 'dist', v[1])
                return inp

            def add(self, inp):
                fus.extrudes.append(inp.dist)
                return SimpleNamespace(bodies=Coll([Body()]))

        self.root = SimpleNamespace(constructionPlanes=CPlanes(), sketches=Sketches(),
                                    features=SimpleNamespace(extrudeFeatures=Extrudes()))
        self.app = SimpleNamespace(activeProduct=SimpleNamespace(rootComponent=self.root),
                                   log=lambda t: None)
        self.ui = SimpleNamespace(messageBox=lambda t, title=None: fus.messages.append(t),
                                  createProgressDialog=lambda: SimpleNamespace(
                                      show=lambda *a: None, hide=lambda: None, wasCancelled=False))


class Sel:
    def __init__(self, *picks):
        self.picks = list(picks)
        self.limits = (1, 1)

    def setSelectionLimits(self, lo, hi):
        self.limits = (lo, hi)

    def clearSelection(self):
        self.picks = []

    @property
    def selectionCount(self):
        return len(self.picks)

    def selection(self, i):
        return self.picks[i]


@pytest.fixture
def cmd(monkeypatch):
    import adsk.core
    import adsk.fusion
    from mssaddin import command
    ident = SimpleNamespace(cast=lambda x: x)
    for name in ('SelectionCommandInput', 'ValueCommandInput', 'BoolValueCommandInput',
                 'DropDownCommandInput', 'IntegerSpinnerCommandInput', 'TextBoxCommandInput'):
        monkeypatch.setattr(adsk.core, name, ident, raising=False)
    monkeypatch.setattr(adsk.core, 'Plane', SimpleNamespace(
        cast=lambda g: g if hasattr(g, 'normal') else None), raising=False)
    monkeypatch.setattr(adsk.fusion, 'MeshBody', SimpleNamespace(
        cast=lambda e: e if isinstance(e, FakeMesh) else None), raising=False)
    monkeypatch.setattr(adsk.fusion, 'Design', ident, raising=False)
    fus = Fusion()
    monkeypatch.setattr(command, '_app', fus.app)
    monkeypatch.setattr(command, '_ui', fus.ui)
    monkeypatch.setattr(command, 'REQUIRED_PY', tuple(sys.version_info[:2]))
    command._mesh_cache.clear()
    return command, fus


@pytest.fixture(scope='module')
def bracket():
    m = trimesh.load(conftest.sample('servo_bracket_1.stl'), force='mesh')
    return m


def _inputs(mesh, mode, start=None, end=None, spacing_mm=0.7, count=3, extrude=True, plane=True,
            tidy=False):
    xy = SimpleNamespace(name='XY', geometry=plane_geom((0, 0, 0), (0, 0, 1)))
    items = {
        'mesh_body': Sel(SimpleNamespace(entity=mesh)),
        'plane': Sel(*([SimpleNamespace(entity=xy)] if plane else [])),
        'tolerance': SimpleNamespace(value=0.008), 'join': SimpleNamespace(value=0.25),
        'trim': SimpleNamespace(value=0.0), 'outline': SimpleNamespace(value=True),
        'tidy': SimpleNamespace(value=tidy), 'sharpen': SimpleNamespace(value=0.008, isVisible=tidy),
        'mode': SimpleNamespace(selectedItem=SimpleNamespace(index=mode)),
        'spacing': SimpleNamespace(value=spacing_mm / 10.0), 'count': SimpleNamespace(value=count),
        'direction': SimpleNamespace(selectedItem=SimpleNamespace(index=0)),
        'extrude': SimpleNamespace(value=extrude), 'hide_mesh': SimpleNamespace(value=False),
        'xray_start': Sel(*([start] if start else [])), 'xray_end': Sel(*([end] if end else [])),
        'xray_info': SimpleNamespace(text='', isVisible=False),
    }
    for k in ('spacing', 'count', 'direction', 'extrude', 'xray_start', 'xray_end'):
        setattr(items[k], 'isVisible', False)
    return SimpleNamespace(itemById=items.get, items=items)


def _foot_click(m, mesh):
    zmin = m.bounds[0, 2]
    t = np.flatnonzero((m.face_normals[:, 2] > 0.999)
                       & (np.abs(m.triangles_center[:, 2] - zmin - 5.0) < 1e-6))[0]
    return SimpleNamespace(entity=mesh, point=P(*(m.triangles_center[t] / 10.0)))


def _mode_change(command, inputs):
    command._InputChanged().notify(SimpleNamespace(input=SimpleNamespace(id='mode'), inputs=inputs))


def test_xray_mesh_click_to_plane(cmd, bracket):
    command, fus = cmd
    from mssaddin import xray
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    lo, hi = m.bounds[:, 2]
    end_plane = SimpleNamespace(name='top', geometry=plane_geom((0, 0, (hi - 3.0) / 10.0), (0, 0, -1)))
    inputs = _inputs(mesh, command.MODE_XRAY, start=_foot_click(m, mesh),
                     end=SimpleNamespace(entity=end_plane), plane=False)
    # the dialog: picking the mode shows the X-Ray inputs and the info line
    _mode_change(command, inputs)
    assert inputs.items['xray_start'].isVisible and not inputs.items['count'].isVisible
    # the slices run parallel to the End plane, the one plane picked
    plan = xray.xray_plan(m.vertices, [0, 0, hi - 3.0], [0, 0, -1],
                          -(lo + 5.0 - xray.INSIDE_MM), -(hi - 3.0), 0.7)
    n = len(plan['offsets_mm'])
    assert inputs.items['xray_info'].text.startswith('{} slices from'.format(n))
    assert 'off top (start: flat face, end: plane)' in inputs.items['xray_info'].text

    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    assert len(fus.sketches) == n, fus.messages
    # every sketch on a plane offset from the End plane as the plan says
    # (cm); a slice at offset 0 sits on the End plane itself
    offs = [getattr(sk.plane, 'offset', 0.0) for sk in fus.sketches]
    assert offs == pytest.approx([o / 10.0 for o in plan['offsets_mm']])
    assert fus.sketches[0].name.startswith('xray top') and fus.sketches[-1].name.endswith('({}/{})'.format(n, n))
    # every slab to the next slice; the end plane, a hair after the last
    # spacing multiple or on the face, as stack_extrudes says
    want = [e / 10.0 for e in plan['extrude_mm'] if e]
    assert fus.extrudes == pytest.approx(want)
    assert 'X-Ray from' in fus.messages[-1] and 'extruded to the next slice' in fus.messages[-1]


def test_xray_refuses_a_plane_that_is_not_parallel(cmd, bracket):
    command, fus = cmd
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    xy = SimpleNamespace(name='XY', geometry=plane_geom((0, 0, 0), (0, 0, 1)))
    side = SimpleNamespace(name='YZ', geometry=plane_geom((0, 0, 0), (1, 0, 0)))
    inputs = _inputs(mesh, command.MODE_XRAY, start=SimpleNamespace(entity=xy),
                     end=SimpleNamespace(entity=side), plane=False)
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    assert not fus.sketches
    assert 'YZ is not parallel' in fus.messages[-1]


def test_xray_needs_start_and_end(cmd, bracket):
    command, fus = cmd
    m = bracket
    inputs = _inputs(FakeMesh(m.vertices, m.faces), command.MODE_XRAY)
    command._InputChanged().notify(SimpleNamespace(input=SimpleNamespace(id='mode'), inputs=inputs))
    assert 'pick a start and an end' in inputs.items['xray_info'].text
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    assert not fus.sketches and 'pick a start and an end' in fus.messages[-1]


def test_one_and_stack_modes_unchanged(cmd, bracket):
    command, fus = cmd
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    # XY sits inside the bracket (it spans z from below 0 to above it)
    assert m.bounds[0, 2] < 0 < m.bounds[1, 2]
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(
        commandInputs=_inputs(mesh, command.MODE_ONE))))
    assert len(fus.sketches) == 1 and not fus.planes and not fus.extrudes
    fus.sketches.clear()
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(
        commandInputs=_inputs(mesh, command.MODE_STACK, count=3, spacing_mm=2.0))))
    assert [p.offset for p in fus.planes] == pytest.approx([0.2, 0.4])
    assert len(fus.sketches) == 3
    assert fus.extrudes == pytest.approx([0.2, 0.2, 0.2])


def test_xray_runs_parallel_to_the_start_plane(cmd, bracket):
    """Two planes picked: the slices run parallel to the Start plane,
    offset from it."""
    command, fus = cmd
    from mssaddin import xray
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    lo, hi = m.bounds[:, 2]
    a, b = lo + 4.0, hi - 4.0
    start = SimpleNamespace(name='left', geometry=plane_geom((0, 0, a / 10.0), (0, 0, 1)))
    end = SimpleNamespace(name='right', geometry=plane_geom((0, 0, b / 10.0), (0, 0, 1)))
    inputs = _inputs(mesh, command.MODE_XRAY, start=SimpleNamespace(entity=start),
                     end=SimpleNamespace(entity=end), spacing_mm=8.0, plane=False)
    command._InputChanged().notify(SimpleNamespace(input=SimpleNamespace(id='mode'), inputs=inputs))
    assert inputs.items['xray_info'].text.startswith('5 slices from +0.00 to +25.00 mm')
    assert 'off left' in inputs.items['xray_info'].text
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    plan = xray.xray_plan(m.vertices, [0, 0, a], [0, 0, 1], a, b, 8.0)
    assert len(fus.sketches) == 5, fus.messages
    # the first sketch sits on the start plane itself, the rest offset from it
    assert fus.sketches[0].plane is start
    assert [p.offset for p in fus.planes] == pytest.approx([o / 10.0 for o in plan['offsets_mm'][1:]])
    assert fus.sketches[0].name.startswith('xray left')


def test_xray_two_mesh_clicks_are_refused(cmd, bracket):
    command, fus = cmd
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    click = _foot_click(m, mesh)
    inputs = _inputs(mesh, command.MODE_XRAY, start=click, end=click, plane=False)
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    assert not fus.sketches and 'Start or End must be a plane' in fus.messages[-1]


def test_a_stale_plane_pick_cannot_set_the_xray_direction(cmd, bracket):
    command, _ = cmd
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    lo, hi = m.bounds[:, 2]
    start = SimpleNamespace(name='low', geometry=plane_geom((0, 0, (lo + 4.0) / 10.0), (0, 0, 1)))
    end = SimpleNamespace(name='high', geometry=plane_geom((0, 0, (hi - 4.0) / 10.0), (0, 0, 1)))
    # XY stays in the Plane box here, as no mode change has cleared it
    inputs = _inputs(mesh, command.MODE_XRAY, start=SimpleNamespace(entity=start),
                     end=SimpleNamespace(entity=end))
    assert inputs.items['plane'].selectionCount == 1
    _, _, ref = command.xray_from_inputs(inputs, np)
    assert ref is start


def test_switching_action_shows_and_clears_the_picks(cmd, bracket):
    """X-Ray hides Plane and lets go of its pick, so the same plane can be
    picked as Start; leaving X-Ray does the same to Start and End."""
    command, _ = cmd
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    click = _foot_click(m, mesh)
    inputs = _inputs(mesh, command.MODE_XRAY, plane=True)
    it = inputs.items
    _mode_change(command, inputs)
    assert not it['plane'].isVisible and it['plane'].selectionCount == 0
    assert it['xray_start'].isVisible and it['xray_end'].isVisible
    assert it['plane'].limits == (0, 1)
    assert it['xray_start'].limits == (1, 1) and it['xray_end'].limits == (1, 1)
    assert it['mesh_body'].selectionCount == 1          # the mesh pick stays

    it['xray_start'].picks = [click]
    it['xray_end'].picks = [click]
    it['mode'].selectedItem.index = command.MODE_ONE
    _mode_change(command, inputs)
    assert it['plane'].isVisible
    assert not it['xray_start'].isVisible and not it['xray_end'].isVisible
    assert it['xray_start'].selectionCount == 0 and it['xray_end'].selectionCount == 0
    assert it['plane'].limits == (1, 1)
    assert it['xray_start'].limits == (0, 1) and it['xray_end'].limits == (0, 1)
    assert it['mesh_body'].selectionCount == 1


class _RecordingInputs:
    """commandInputs that remember what was added, in order."""
    def __init__(self):
        self.ids, self.items, self.labels = [], {}, {}

    def __getattr__(self, name):
        def add(iid, label='', *a, **k):
            item = MagicMock(isVisible=True)
            self.ids.append(iid)
            self.items[iid] = item
            self.labels[iid] = label
            return item
        return add


def test_the_action_comes_first_in_the_dialog(cmd, monkeypatch):
    command, fus = cmd
    monkeypatch.setattr(command, '_handlers', [])
    inputs = _RecordingInputs()
    command._CommandCreated().notify(SimpleNamespace(command=SimpleNamespace(
        commandInputs=inputs, isRepeatable=True, inputChanged=MagicMock(), execute=MagicMock())))
    assert not fus.messages
    assert inputs.ids[:5] == ['mode', 'mesh_body', 'plane', 'xray_start', 'xray_end']
    assert inputs.labels['mode'] == 'Action'
    assert inputs.items['mode'].listItems.add.call_args_list[0].args == ('Single slice', True)
    # the dialog opens on Single slice: Mesh body + Plane, no Start or End
    assert inputs.items['plane'].isVisible
    assert not inputs.items['xray_start'].isVisible and not inputs.items['xray_end'].isVisible


def test_stack_still_needs_a_plane(cmd, bracket):
    command, fus = cmd
    m = bracket
    inputs = _inputs(FakeMesh(m.vertices, m.faces), command.MODE_STACK, plane=False)
    command._InputChanged().notify(SimpleNamespace(input=SimpleNamespace(id='mode'), inputs=inputs))
    assert inputs.items['plane'].limits == (1, 1)
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    assert not fus.sketches and 'Select a mesh body and a plane' in fus.messages[-1]


def test_xray_warns_when_a_slice_outline_stays_open(cmd, bracket, monkeypatch):
    """A slice whose cut stays open (a mesh hole wider than Join gaps) has
    no profile and no slab; the closing message now says so up top, with
    the Join gaps value that would close it."""
    command, fus = cmd
    from mssengine import section_fit
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    real = section_fit.section_preview
    calls = []

    def leaky(*a, **k):
        sec = real(*a, **k)
        calls.append(1)
        if len(calls) == 2:                       # the second slice: outline open
            sec = dict(sec, loops=[], open=[], polylines=[],
                       stats=dict(sec['stats'], loops=0, open_max=300.0, loop_max=0.0, close_gap=6.4))
        return sec
    monkeypatch.setattr(section_fit, 'section_preview', leaky)
    inputs = _inputs(mesh, command.MODE_STACK, count=3, spacing_mm=2.0)
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=inputs)))
    msg = fus.messages[-1]
    assert msg.startswith('WARNING: the outline did not close in 1 slice (+2.00 mm)')
    assert 'Join gaps 6.5 mm would close it' in msg


def test_tidy_inputs_in_the_dialog(cmd, monkeypatch):
    """Tidy corners is a tick, off by default; Sharpen shows only with it."""
    command, fus = cmd
    monkeypatch.setattr(command, '_handlers', [])
    inputs = _RecordingInputs()
    command._CommandCreated().notify(SimpleNamespace(command=SimpleNamespace(
        commandInputs=inputs, isRepeatable=True, inputChanged=MagicMock(), execute=MagicMock())))
    assert not fus.messages
    assert inputs.labels['tidy'] == 'Tidy corners' and inputs.labels['sharpen'] == 'Sharpen rounds below'
    assert inputs.ids.index('tidy') == inputs.ids.index('outline') + 1
    assert inputs.items['sharpen'].isVisible is False
    m = SimpleNamespace(value=True)
    sh = SimpleNamespace(isVisible=False)
    items = {'tidy': m, 'sharpen': sh, 'mode': SimpleNamespace(selectedItem=SimpleNamespace(index=0))}
    fake = SimpleNamespace(itemById=items.get)
    command._InputChanged().notify(SimpleNamespace(input=SimpleNamespace(id='tidy'), inputs=fake))
    assert sh.isVisible is True
    m.value = False
    command._InputChanged().notify(SimpleNamespace(input=SimpleNamespace(id='tidy'), inputs=fake))
    assert sh.isVisible is False


def test_single_slice_with_tidy_adds_tangents(cmd, bracket):
    """A single slice of the bracket with Tidy corners: the curves share
    their sketch points, the fillets get tangent constraints, and the
    message says how many."""
    command, fus = cmd
    m = bracket
    mesh = FakeMesh(m.vertices, m.faces)
    plain = _inputs(mesh, command.MODE_ONE)
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=plain)))
    assert len(fus.sketches) == 1 and not fus.sketches[0].tangents
    assert 'Tidy corners' not in fus.messages[-1]
    fus.sketches.clear()
    tidy = _inputs(mesh, command.MODE_ONE, tidy=True)
    command._Execute()._run(SimpleNamespace(command=SimpleNamespace(commandInputs=tidy)))
    sk = fus.sketches[0]
    k = len(sk.tangents)
    assert 'Tidy corners: {} tangent constraint'.format(k) in fus.messages[-1], fus.messages[-1]
    # every curve after a loop's first starts on a sketch point made before
    from desktop_tests.fake_adsk import SketchPoint
    assert any(isinstance(c[1], SketchPoint) for c in sk.calls)
