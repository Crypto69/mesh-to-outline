"""MeshSectionSketch — Fusion add-in entry point.

Cut a mesh body with a plane and draw the section as a real sketch: lines,
arcs and circles where they hold the tolerance, fitted splines where they
do not; open chains of a leaky mesh drawn open, hairline gaps joined. It
is Fusion's Create Mesh Section Sketch + Fit Curves to Mesh Section in one
click, using the stlToSolid section engine (numpy only).

run(context) must be fast (Fusion loads every add-in at start-up): it only
puts this folder and lib/<platform> on sys.path and registers the command.
The Python-version and numpy checks happen when the command runs.

Package names are `mssaddin` and `mssengine`: Fusion runs every add-in in
ONE interpreter with one sys.modules, so generic names would collide with
other add-ins. stop() evicts only modules loaded from this folder.
"""
import os
import sys
import traceback

import adsk.core

# realpath, not abspath: the add-in is usually a symlink in Fusion's
# AddIns directory, and lib/<platform> must resolve against the checkout.
_HERE = os.path.dirname(os.path.realpath(__file__))
_PACKAGES = ('mssaddin', 'mssengine')


def platform_dir():
    """Which lib/<platform> folder holds the vendored numpy for this
    machine (see tools/vendor_numpy.py). None on an unsupported platform."""
    if sys.platform == 'darwin':
        machine = os.uname().machine        # 'arm64' or 'x86_64'
        return 'macos_arm64' if machine == 'arm64' else 'macos_x86_64'
    if sys.platform == 'win32':
        return 'win_amd64'
    return None


def lib_dir():
    """Our own lib/<platform>: it holds numpy, or the error message names
    where to put it."""
    p = platform_dir()
    return os.path.join(_HERE, 'lib', p) if p else None


def _our_paths():
    paths = [_HERE]
    lib = lib_dir()
    if lib:
        paths.append(lib)
    return paths


def _setup_path():
    for p in reversed(_our_paths()):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


def _teardown_path():
    for p in _our_paths():
        while p in sys.path:
            sys.path.remove(p)


def _report(title):
    try:
        ui = adsk.core.Application.get().userInterface
        ui.messageBox('{}:\n{}'.format(title, traceback.format_exc()))
    except Exception:
        pass


def run(context):
    try:
        _setup_path()
        import mssaddin.command as command
        command.start(context)
    except Exception:
        _report('MeshSectionSketch failed to start')


def _is_ours(mod):
    try:
        f = getattr(mod, '__file__', None) or ''
        if f:
            return os.path.realpath(f).startswith(_HERE + os.sep)
        for entry in list(getattr(mod, '__path__', []) or []):
            if os.path.realpath(entry).startswith(_HERE + os.sep):
                return True
    except Exception:
        pass
    return False


def stop(context):
    try:
        command = sys.modules.get('mssaddin.command')
        if command is not None:
            command.stop(context)
    except Exception:
        _report('MeshSectionSketch failed to stop cleanly')
    finally:
        for name in list(sys.modules):
            top = name.split('.', 1)[0]
            if top not in _PACKAGES:
                continue
            if _is_ours(sys.modules.get(name)):
                sys.modules.pop(name, None)
        _teardown_path()
