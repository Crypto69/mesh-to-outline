"""A stub `adsk` package: just enough for mssaddin.command to import and
for draw_prims to be exercised against a recording sketch."""
import sys
import types
from types import SimpleNamespace


def _xyz(x, y, z):
    return (x, y, z)


class Coll(list):
    def add(self, x):
        self.append(x)

    @property
    def count(self):
        return len(self)

    def item(self, i):
        return self[i]


def _build():
    adsk = types.ModuleType('adsk')
    core = types.ModuleType('adsk.core')
    fusion = types.ModuleType('adsk.fusion')
    core.Point3D = SimpleNamespace(create=staticmethod(_xyz))
    core.Vector3D = SimpleNamespace(create=staticmethod(_xyz))
    core.ValueInput = SimpleNamespace(createByReal=staticmethod(lambda v: ('real', v)),
                                      createByString=staticmethod(lambda v: ('string', v)))
    core.ObjectCollection = SimpleNamespace(create=staticmethod(Coll))
    for name in ('CommandCreatedEventHandler', 'CommandEventHandler', 'InputChangedEventHandler'):
        setattr(core, name, type(name, (object,), {}))
    core.DropDownStyles = SimpleNamespace(TextListDropDownStyle=0)
    fusion.FeatureOperations = SimpleNamespace(NewBodyFeatureOperation=0, JoinFeatureOperation=1,
                                               CutFeatureOperation=2)
    adsk.core, adsk.fusion = core, fusion
    adsk.doEvents = lambda: None
    return adsk, core, fusion


def install():
    if 'adsk' in sys.modules and getattr(sys.modules['adsk'], '_fake', False):
        return sys.modules['adsk']
    adsk, core, fusion = _build()
    adsk._fake = True
    sys.modules['adsk'] = adsk
    sys.modules['adsk.core'] = core
    sys.modules['adsk.fusion'] = fusion
    return adsk


class Pt(tuple):
    """A sketch-space point with Point3D's distanceTo."""
    def distanceTo(self, o):
        return sum((x - y) ** 2 for x, y in zip(self, o)) ** 0.5


class SketchPoint:
    def __init__(self, g):
        self.geometry = Pt(g)


def _geom(p):
    return p.geometry if isinstance(p, SketchPoint) else Pt(p)


class Curve:
    """What a curve call hands back: its two end sketch points (a point
    passed in is reused, so curves drawn through it share it). Fusion keeps
    arcs counter-clockwise, so a clockwise arc's ends come back swapped."""
    def __init__(self, kind, a, b, mid=None):
        self.kind = kind
        sp = lambda p: p if isinstance(p, SketchPoint) else SketchPoint(p)
        self.startSketchPoint, self.endSketchPoint = sp(a), sp(b)
        if mid is not None:
            A, M, B = _geom(a), Pt(mid), _geom(b)
            if (M[0] - A[0]) * (B[1] - M[1]) - (M[1] - A[1]) * (B[0] - M[0]) < 0:
                self.startSketchPoint, self.endSketchPoint = self.endSketchPoint, self.startSketchPoint


class RecordingSketch:
    """Records every curve call; modelToSketchSpace is the identity, so
    recorded points are the cm points the add-in would hand to Fusion
    (a SketchPoint where a curve was started on another's end). Curves come
    back as Curve objects; tangent constraints are recorded in `tangents`."""
    def __init__(self):
        self.calls = []
        self.tangents = []
        self.curves = []
        s = self

        def made(c):
            s.curves.append(c)
            return c

        class Lines:
            def addByTwoPoints(self, a, b):
                s.calls.append(('line', a, b))
                return made(Curve('line', a, b))

        class Arcs:
            def addByThreePoints(self, a, m, b):
                s.calls.append(('arc', a, m, b))
                return made(Curve('arc', a, b, mid=m))

        class Circles:
            def addByCenterRadius(self, c, r):
                s.calls.append(('circle', c, r))
                return made(Curve('circle', c, c))

        class Splines:
            def add(self, pc):
                s.calls.append(('spline', list(pc)))
                return made(Curve('spline', pc[0], pc[-1]))

        class Constraints:
            def addTangent(self, a, b):
                s.tangents.append((a, b))

        self.sketchCurves = SimpleNamespace(sketchLines=Lines(), sketchArcs=Arcs(),
                                            sketchCircles=Circles(), sketchFittedSplines=Splines())
        self.geometricConstraints = Constraints()

    def modelToSketchSpace(self, p):
        return p

    def kinds(self):
        return [c[0] for c in self.calls]
