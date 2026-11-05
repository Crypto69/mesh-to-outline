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
    core.ValueInput = SimpleNamespace(createByReal=staticmethod(lambda v: ('real', v)))
    core.ObjectCollection = SimpleNamespace(create=staticmethod(Coll))
    for name in ('CommandCreatedEventHandler', 'CommandEventHandler', 'InputChangedEventHandler'):
        setattr(core, name, type(name, (object,), {}))
    core.DropDownStyles = SimpleNamespace(TextListDropDownStyle=0)
    fusion.FeatureOperations = SimpleNamespace(NewBodyFeatureOperation=0, JoinFeatureOperation=1)
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


class RecordingSketch:
    """Records every curve call; modelToSketchSpace is the identity, so
    recorded points are the cm points the add-in would hand to Fusion."""
    def __init__(self):
        self.calls = []
        s = self

        class Lines:
            def addByTwoPoints(self, a, b):
                s.calls.append(('line', a, b))

        class Arcs:
            def addByThreePoints(self, a, m, b):
                s.calls.append(('arc', a, m, b))

        class Circles:
            def addByCenterRadius(self, c, r):
                s.calls.append(('circle', c, r))

        class Splines:
            def add(self, pc):
                s.calls.append(('spline', list(pc)))

        self.sketchCurves = SimpleNamespace(sketchLines=Lines(), sketchArcs=Arcs(),
                                            sketchCircles=Circles(), sketchFittedSplines=Splines())

    def modelToSketchSpace(self, p):
        return p

    def kinds(self):
        return [c[0] for c in self.calls]
