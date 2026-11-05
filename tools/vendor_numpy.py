#!/usr/bin/env python3
"""Vendor numpy for Fusion's embedded CPython 3.14 into MeshSectionSketch/lib/<platform>/.

    python tools/vendor_numpy.py [--platform macos_arm64|macos_x86_64|win_amd64] [--version 2.5.4]

Downloads the cp314 wheel with pip (any Python that has pip will do: pip
resolves the target interpreter from the flags, it does not need to *be*
3.14) and unzips it. lib/ is git-ignored. Standard library only.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # MeshSectionSketch/
LIB = os.path.join(ROOT, 'lib')

PYTHON_VERSION = '3.14'
ABI = 'cp314'
DEFAULT_NUMPY = '2.5.4'

# wheel platform tags per lib folder; pip accepts several --platform flags
# and takes the first wheel that matches any of them.
# macOS: ONLY the macosx_14_0 wheels. They link Apple's Accelerate and carry
# no bundled libraries; the older macosx_11_0 / 10_x wheels ship libgfortran,
# libgcc (GPL-3 + GCC exception) and libquadmath (LGPL-2.1) in numpy/.dylibs,
# and Autodesk's store rejects GPL/LGPL. Fusion needs macOS 15, so 14.0 loads.
TAGS = {
    'macos_arm64': ['macosx_14_0_arm64'],
    'macos_x86_64': ['macosx_14_0_x86_64'],
    'win_amd64': ['win_amd64'],
}


def current_platform():
    if sys.platform == 'darwin':
        return 'macos_arm64' if os.uname().machine == 'arm64' else 'macos_x86_64'
    if sys.platform == 'win32':
        return 'win_amd64'
    return None


def download(version, plat, dest):
    cmd = [sys.executable, '-m', 'pip', 'download', 'numpy==' + version,
           '--only-binary=:all:', '--python-version', PYTHON_VERSION,
           '--implementation', 'cp', '--abi', ABI, '--no-deps', '-d', dest]
    for tag in TAGS[plat]:
        cmd += ['--platform', tag]
    print('$ ' + ' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit('pip download failed (exit {}). If the error says "No module named pip", '
                 'run this with a Python that has pip, e.g. .venv/bin/python.'.format(r.returncode))
    wheels = [f for f in os.listdir(dest) if f.endswith('.whl')]
    if len(wheels) != 1:
        sys.exit('expected one wheel in {}, found {}'.format(dest, wheels))
    return os.path.join(dest, wheels[0])


def install(wheel, plat):
    target = os.path.join(LIB, plat)
    os.makedirs(target, exist_ok=True)
    # remove an older copy first (numpy/ and its dist-info)
    for name in os.listdir(target):
        if name in ('numpy', 'numpy.libs') or name.startswith('numpy-'):
            path = os.path.join(target, name)
            print('removing ' + path)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    print('unzipping {} -> {}'.format(os.path.basename(wheel), target))
    with zipfile.ZipFile(wheel) as z:
        z.extractall(target)
    bundled = os.path.join(target, 'numpy', '.dylibs')
    if os.path.isdir(bundled):
        sys.exit('{} holds {}: this wheel bundles GNU libraries, which the Autodesk '
                 'store rejects. Use a macosx_14_0 wheel.'.format(bundled, os.listdir(bundled)))
    return target


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--platform', choices=sorted(TAGS), default=current_platform(),
                    help='lib/<platform> folder to fill (default: this machine)')
    ap.add_argument('--version', default=DEFAULT_NUMPY, help='numpy version (default %(default)s)')
    args = ap.parse_args(argv)
    if args.platform is None:
        sys.exit('unsupported platform {}; pass --platform'.format(sys.platform))
    with tempfile.TemporaryDirectory() as tmp:
        wheel = download(args.version, args.platform, tmp)
        target = install(wheel, args.platform)
    print('done: numpy {} for {} in {}'.format(args.version, args.platform, target))
    if args.platform == current_platform() and sys.version_info[:2] == (3, 14):
        sys.path.insert(0, target)
        import numpy
        print('import check OK: numpy', numpy.__version__)


if __name__ == '__main__':
    main()
