#!/usr/bin/env python3
"""Copy the engine files from the public stlToSolid checkout into
mssengine/, verbatim, and check they import nothing but numpy.

    python3 tools/sync_engine.py            # copy
    python3 tools/sync_engine.py --check    # only verify the copies are current
    python3 tools/sync_engine.py --repo PATH   # checkout not at ../stlToSolid

The add-in never edits these files: fixes go into stlToSolid and are
synced here, so the desktop parity tests compare like with like.
"""
import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # MeshSectionSketch/
REPO = os.path.join(os.path.dirname(ROOT), 'stlToSolid')   # the public checkout, next to this repo
FILES = ('profile_fit.py', 'section_fit.py', 'rails.py')
FORBIDDEN = re.compile(r'^\s*(import|from)\s+(trimesh|shapely|scipy|cadquery|OCC|OCP|networkx)\b', re.M)


def check_imports(path):
    text = open(path).read()
    m = FORBIDDEN.search(text)
    if m:
        sys.exit('{}: forbidden import "{}" (the add-in has numpy only)'.format(path, m.group(0).strip()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='verify only; exit 1 when a copy is stale')
    ap.add_argument('--repo', default=REPO, help='the stlToSolid checkout (default ../stlToSolid)')
    a = ap.parse_args(argv)
    stale = []
    for name in FILES:
        src = os.path.join(a.repo, 'stl_to_solid', name)
        dst = os.path.join(ROOT, 'mssengine', name)
        if not os.path.exists(src):
            sys.exit('missing ' + src)
        check_imports(src)
        same = os.path.exists(dst) and open(src, 'rb').read() == open(dst, 'rb').read()
        if a.check:
            if not same:
                stale.append(name)
            continue
        if not same:
            shutil.copyfile(src, dst)
            print('copied ' + name)
        else:
            print('up to date ' + name)
    if stale:
        sys.exit('stale: {} (run tools/sync_engine.py)'.format(', '.join(stale)))
    if a.check:
        print('engine copies are current')


if __name__ == '__main__':
    main()
