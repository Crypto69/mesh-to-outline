"""The button finds the Mesh tab's Modify panel even when Fusion renames
it, and only falls back to Utilities > Add-Ins when there is none."""
from types import SimpleNamespace

from . import conftest  # noqa: F401  (sys.path)


class Controls:
    def __init__(self, ids):
        self.ids, self.added = list(ids), []

    def itemById(self, i):
        return SimpleNamespace(id=i) if i in self.ids else None

    def addCommand(self, cmd_def, after=None, before=True):
        if after is not None and after not in self.ids:
            raise RuntimeError('no such control')
        c = SimpleNamespace(cmd=cmd_def, after=after, isPromoted=False, isPromotedByDefault=False)
        self.added.append(c)
        return c


class Panels:
    def __init__(self, panels):
        self.panels = panels

    @property
    def count(self):
        return len(self.panels)

    def item(self, i):
        return self.panels[i]

    def itemById(self, pid):
        return next((p for p in self.panels if p.id == pid), None)


def _panel(pid, controls=()):
    return SimpleNamespace(id=pid, controls=Controls(controls))


def _place(monkeypatch, panels):
    from mssaddin import command
    logs = []
    monkeypatch.setattr(command, '_ui', SimpleNamespace(allToolbarPanels=Panels(panels)))
    monkeypatch.setattr(command, '_app', SimpleNamespace(log=logs.append))
    control, where = command._place_control('cmd')
    return control, where, logs


def test_renamed_panel_found_by_convert_mesh(monkeypatch):
    panels = [_panel('SolidCreatePanel', ['ExtrudeCommand']),
              _panel('MeshPrepareModifyPanelV2', ['ParaMeshConvertCommand']),
              _panel('SolidScriptsAddinsPanel')]
    control, where, _ = _place(monkeypatch, panels)
    assert where.startswith('MeshPrepareModifyPanelV2') and control.after == 'ParaMeshConvertCommand'
    assert control.isPromoted and control.isPromotedByDefault


def test_renamed_panel_found_by_name(monkeypatch):
    panels = [_panel('NewMeshModifyPanel', ['SomethingElse']), _panel('SolidScriptsAddinsPanel')]
    control, where, _ = _place(monkeypatch, panels)
    assert where == 'NewMeshModifyPanel' and control.isPromoted


def test_fallback_logs_the_mesh_panels(monkeypatch):
    panels = [_panel('MeshCreatePanel'), _panel('SolidScriptsAddinsPanel')]
    control, where, logs = _place(monkeypatch, panels)
    assert where == 'SolidScriptsAddinsPanel'
    assert any('MeshCreatePanel' in line for line in logs)
