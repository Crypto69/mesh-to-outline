#!/usr/bin/env python3
"""Build the zips for a GitHub release of Mesh to Outline.

    python3 tools/build_release.py [--sign IDENTITY]

Writes dist/MeshToOutline-<version>-mac.zip (arm64 + Intel numpy) and
dist/MeshToOutline-<version>-windows.zip. Each holds one MeshSectionSketch/
folder, ready to copy into Fusion's AddIns folder, beside LICENSE, README.md
and THIRD-PARTY-NOTICES.txt. The folder carries its own LICENSE too, since it
is the part that gets copied around.

--sign signs every Mac binary with a Developer ID. Without that, plus
notarising the zip (the release workflow does both), macOS blocks numpy in a
browser download. An unsigned build only works from a local copy.

The Marketplace upload is tools/build_bundle.py, a different layout under
different terms. Standard library only.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_bundle as bb  # noqa: E402

TARGETS = {'mac': bb.PLATFORMS['mac'], 'windows': bb.PLATFORMS['win']}
ROOT_FILES = ('LICENSE', 'README.md')
FOLDER_FILES = (bb.ENTRY + '.py', bb.ENTRY + '.manifest', 'LICENSE')
DEV_PATHS = ('/Volumes/', '/Users/')


def fail(msg):
    sys.exit('build_release: ' + msg)


def check_tree(top):
    """Last checks over what actually landed in the zip's folder."""
    for base, dirs, files in os.walk(top):
        for d in dirs:
            if d in bb.SKIP_DIRS:
                fail('stray folder: ' + os.path.join(base, d))
        for f in files:
            p = os.path.join(base, f)
            if f == '.DS_Store' or f.endswith(bb.SKIP_EXT):
                fail('stray file: ' + p)
            if f.endswith(('.py', '.manifest', '.md', '.txt', '.html')):
                with open(p, encoding='utf-8', errors='replace') as fh:
                    text = fh.read()
                # numpy's own files are not ours to judge
                if not os.path.relpath(p, top).startswith('lib' + os.sep):
                    for word in DEV_PATHS:
                        if word in text:
                            fail('{} in {}'.format(word, p))


def sign_binaries(top, identity):
    count = 0
    for base, _, files in os.walk(top):
        for f in files:
            if f.endswith(('.so', '.dylib')):
                subprocess.run(['codesign', '--force', '--options', 'runtime', '--timestamp',
                                '--sign', identity, os.path.join(base, f)],
                               check=True, stdout=subprocess.DEVNULL)
                count += 1
    print('signed {} binaries'.format(count))


def build_one(target, plats, version, identity=None):
    bb.check_libs(plats)
    with tempfile.TemporaryDirectory() as tmp:
        folder = os.path.join(tmp, bb.ENTRY)
        os.makedirs(folder)
        for name in FOLDER_FILES:
            shutil.copy2(os.path.join(bb.ROOT, name), folder)
        for pkg in bb.PACKAGES:
            bb.copy_tree(os.path.join(bb.ROOT, pkg), os.path.join(folder, pkg))
        for plat in plats:
            # numpy's own test suites are never imported at run time
            bb.copy_tree(os.path.join(bb.ROOT, 'lib', plat), os.path.join(folder, 'lib', plat),
                         skip_dirs=('tests',))
        check_tree(folder)
        if identity and target == 'mac':
            sign_binaries(folder, identity)
        for name in ROOT_FILES:
            shutil.copy2(os.path.join(bb.ROOT, name), tmp)
        with open(os.path.join(tmp, 'THIRD-PARTY-NOTICES.txt'), 'w', encoding='utf-8') as fh:
            fh.write(bb.third_party_notices(plats))

        path = os.path.join(bb.DIST, 'MeshToOutline-{}-{}.zip'.format(version, target))
        if os.path.exists(path):
            os.remove(path)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for base, dirs, files in os.walk(tmp):
                dirs.sort()
                for f in sorted(files):
                    p = os.path.join(base, f)
                    z.write(p, os.path.relpath(p, tmp).replace(os.sep, '/'))
    print('{} ({:.1f} MB)'.format(path, os.path.getsize(path) / 1e6))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sign', metavar='IDENTITY', help='Developer ID to sign the Mac binaries with')
    a = ap.parse_args(argv)
    version = bb.read_manifest()['version']
    os.makedirs(bb.DIST, exist_ok=True)
    return [build_one(t, p, version, a.sign) for t, p in TARGETS.items()]


if __name__ == '__main__':
    main()
