"""The Autodesk Marketplace rules the add-in itself must keep: store-user
error messages (no developer commands), the numpy-version guard, the
privacy line in the dialog, and the bundle tools/build_bundle.py makes
(layout, PackageContents.xml, no GNU libraries, nothing version-locked)."""
import importlib.util
import json
import os
import sys
import xml.dom.minidom
import zipfile
from types import SimpleNamespace

import pytest

from . import conftest  # noqa: F401  (sys.path)

ADDIN = conftest.ADDIN


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        'mss_' + name, os.path.join(ADDIN, 'tools', name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def command(monkeypatch):
    import adsk.fusion
    from mssaddin import command
    logged = []
    monkeypatch.setattr(command, '_app', SimpleNamespace(
        activeProduct=object(), log=logged.append))
    monkeypatch.setattr(adsk.fusion, 'Design', SimpleNamespace(cast=lambda x: x), raising=False)
    command.logged = logged
    return command


def test_python_bump_message_is_for_store_users(command, monkeypatch):
    monkeypatch.setattr(command, 'REQUIRED_PY', (3, 99))
    text = command._preflight()
    assert 'latest version' in text
    assert 'vendor' not in text and 'REQUIRED_PY' not in text
    # the developer detail still reaches the Text Commands log
    assert any('vendor_numpy' in line for line in command.logged)


def test_old_numpy_from_another_addin_is_refused(command, monkeypatch):
    monkeypatch.setattr(command, 'REQUIRED_PY', tuple(sys.version_info[:2]))
    monkeypatch.setitem(sys.modules, 'numpy', SimpleNamespace(__version__='1.26.4', __file__='x'))
    text = command._preflight()
    assert 'old copy of numpy' in text and '1.26.4' in text


def test_preflight_passes_with_current_numpy(command, monkeypatch):
    monkeypatch.setattr(command, 'REQUIRED_PY', tuple(sys.version_info[:2]))
    assert command._preflight() is None


def test_about_line_links_privacy_policy(command):
    html = command._about_html()
    assert 'collects no data' in html
    assert 'Privacy policy' in html and 'Help' in html
    # a dev checkout has no Documentation folder: the website pages
    assert command.PRIVACY_URL in html and command.HELP_URL in html


def test_about_line_prefers_bundled_pages(command, tmp_path, monkeypatch):
    contents = tmp_path / 'Application Support' / 'Contents'
    (contents / 'mssaddin').mkdir(parents=True)
    (contents / 'Documentation').mkdir()
    for name in ('privacy.html', 'MeshToOutline.html'):
        (contents / 'Documentation' / name).write_text('x')
    monkeypatch.setattr(command, '__file__', str(contents / 'mssaddin' / 'command.py'))
    html = command._about_html()
    assert 'file://' in html and 'Application%20Support' in html
    assert command.PRIVACY_URL not in html


def test_vendor_script_takes_only_clean_mac_wheels():
    vendor = _tool('vendor_numpy')
    for plat in ('macos_arm64', 'macos_x86_64'):
        assert all(t.startswith('macosx_14_0_') for t in vendor.TAGS[plat]), vendor.TAGS[plat]


def test_bundle_refuses_gnu_dylibs(tmp_path, monkeypatch):
    build = _tool('build_bundle')
    dylibs = tmp_path / 'lib' / 'macos_arm64' / 'numpy' / '.dylibs'
    dylibs.mkdir(parents=True)
    (dylibs / 'libquadmath.0.dylib').write_bytes(b'')
    monkeypatch.setattr(build, 'ROOT', str(tmp_path))
    with pytest.raises(SystemExit, match='GNU'):
        build.check_libs(['macos_arm64'])


@pytest.mark.skipif(not all(os.path.isdir(os.path.join(ADDIN, 'lib', p, 'numpy'))
                            for p in ('macos_arm64', 'macos_x86_64')),
                    reason='Mac numpy not vendored (tools/vendor_numpy.py)')
def test_built_bundle(tmp_path, monkeypatch):
    build = _tool('build_bundle')
    monkeypatch.setattr(build, 'DIST', str(tmp_path))
    build.main(['--os', 'mac'])
    with open(os.path.join(ADDIN, 'MeshSectionSketch.manifest'), encoding='utf-8') as fh:
        dev = json.load(fh)
    bundle = tmp_path / 'MeshToOutline.bundle'
    contents = bundle / 'Contents'

    # PackageContents.xml at the root, add-in inside Contents/
    doc = xml.dom.minidom.parse(str(bundle / 'PackageContents.xml'))
    app = doc.documentElement
    assert app.getAttribute('AutodeskProduct') == 'Fusion360'
    assert app.getAttribute('AppVersion') == dev['version']
    assert app.getAttribute('UpgradeCode').strip('{}').lower() == dev['id']
    assert app.getAttribute('ProductCode') != app.getAttribute('UpgradeCode')
    entry = doc.getElementsByTagName('ComponentEntry')[0]
    assert entry.getAttribute('ModuleName') == './Contents/MeshSectionSketch.manifest'
    assert {r.getAttribute('OS') for r in doc.getElementsByTagName('RuntimeRequirements')} == {'macOS'}
    for attr in ('HelpFile', 'Icon'):
        assert (bundle / app.getAttribute(attr)).is_file(), attr

    man = json.loads((contents / 'MeshSectionSketch.manifest').read_text(encoding='utf-8'))
    assert man['version'] == dev['version'] and man['id'] == dev['id']
    assert man['editEnabled'] is False and man['runOnStartup'] is True

    for name in ('MeshSectionSketch.py', 'mssaddin/command.py', 'mssengine/section_fit.py',
                 'Documentation/privacy.html', 'Documentation/MeshToOutline.html',
                 'Documentation/THIRD-PARTY-NOTICES.txt', 'lib/macos_arm64/numpy/__init__.py'):
        assert (contents / name).is_file(), name
    assert not (contents / 'lib' / 'win_amd64').exists()
    for page in ('privacy.html', 'MeshToOutline.html'):
        assert '{{' not in (contents / 'Documentation' / page).read_text(encoding='utf-8')

    # nothing a store user must not get
    for base, dirs, files in os.walk(bundle):
        assert '__pycache__' not in dirs and 'tests' not in dirs, base
        assert not any(f.endswith('.pyc') or f == '.DS_Store' for f in files), base
        assert '.dylibs' not in dirs, base
    for dev_only in ('desktop_tests', 'tools', 'README.md', 'store'):
        assert not (contents / dev_only).exists(), dev_only

    with zipfile.ZipFile(tmp_path / 'MeshToOutline-{}-mac.zip'.format(dev['version'])) as z:
        names = z.namelist()
    assert 'MeshToOutline.bundle/PackageContents.xml' in names
    assert all(n.startswith('MeshToOutline.bundle/') for n in names)


@pytest.mark.skipif(not all(os.path.isdir(os.path.join(ADDIN, 'lib', p, 'numpy'))
                            for p in ('macos_arm64', 'macos_x86_64', 'win_amd64')),
                    reason='numpy not vendored for all platforms (tools/vendor_numpy.py)')
def test_release_zips(tmp_path, monkeypatch):
    release = _tool('build_release')
    monkeypatch.setattr(release.bb, 'DIST', str(tmp_path))
    mac, win = release.main([])
    for path, ships, absent in ((mac, 'macos_arm64', 'win_amd64'), (win, 'win_amd64', 'macos_arm64')):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            notice = z.read('LICENSE').decode('utf-8')
            assert z.read('MeshSectionSketch/LICENSE').decode('utf-8') == notice
        assert 'PolyForm Noncommercial' in notice and 'mesh-to-outline' in notice
        for name in ('README.md', 'THIRD-PARTY-NOTICES.txt', 'MeshSectionSketch/MeshSectionSketch.py',
                     'MeshSectionSketch/MeshSectionSketch.manifest', 'MeshSectionSketch/mssengine/section_fit.py',
                     'MeshSectionSketch/mssaddin/resources/outline/32x32.png',
                     'MeshSectionSketch/lib/{}/numpy/__init__.py'.format(ships)):
            assert name in names, name
        assert not any('/lib/{}/'.format(absent) in n for n in names)
        assert not any('__pycache__' in n or n.endswith(('.pyc', '.DS_Store')) or '/tests/' in n for n in names)
        for dev_only in ('desktop_tests', 'tools', 'store', 'dist'):
            assert not any(n.startswith('MeshSectionSketch/' + dev_only) for n in names), dev_only
        assert {n.split('/')[0] for n in names} == {'MeshSectionSketch', 'LICENSE', 'README.md',
                                                    'THIRD-PARTY-NOTICES.txt'}
