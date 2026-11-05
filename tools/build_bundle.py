#!/usr/bin/env python3
"""Build the Autodesk Marketplace upload for Mesh to Outline.

    python3 tools/build_bundle.py [--os mac|win|both] [--support-email ADDR]

Writes dist/MeshToOutline.bundle/ and dist/MeshToOutline-<version>-<os>.zip
(the zip holds the .bundle folder; that is what the submission form takes).
Layout follows Autodesk's own Fusion bundles: PackageContents.xml at the
root, the add-in (.py + .manifest + packages + lib) inside Contents/.

Refuses to build when:
- a macOS lib folder holds numpy/.dylibs (GNU libraries: the store rejects
  GPL/LGPL; re-vendor with tools/vendor_numpy.py, which takes the clean
  macosx_14_0 wheels);
- a needed lib/<platform> folder is missing;
- anything compiled for one Python version (*.pyc) would ship.

--os defaults to mac: Windows has not been run in Fusion yet, and its numpy
(OpenBLAS DLL) carries the GCC runtime library, which is waiting on
Autodesk's answer. Standard library only.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import uuid
import xml.dom.minidom
import zipfile
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # MeshSectionSketch/
DIST = os.path.join(ROOT, 'dist')
STORE = os.path.join(ROOT, 'store')
ENTRY = 'MeshSectionSketch'
BUNDLE = 'MeshToOutline.bundle'
APP_NAME = 'Mesh to Outline'
APP_NAMESPACE = 'com.autodesk.exchange.Fusion360.MeshToOutline'
COMPANY = 'stlToSolid'
COMPANY_URL = 'https://stltosolid.com'
HELP_URL = 'https://stltosolid.com/help/mesh-to-outline'
PRIVACY_URL = 'https://stltosolid.com/privacy'
DESCRIPTION = ('Cut a mesh body with a plane and draw the section as a sketch of '
               'lines, arcs, circles and splines; stacks, X-Ray and extruded slices.')
PACKAGES = ('mssaddin', 'mssengine')
PLATFORMS = {'mac': ('macos_arm64', 'macos_x86_64'), 'win': ('win_amd64',)}
PC_OS = {'mac': 'macOS', 'win': 'win64', 'both': 'win64|macOS'}
SKIP_DIRS = {'__pycache__', '.DS_Store'}
SKIP_EXT = ('.pyc', '.pyo')


def fail(msg):
    sys.exit('build_bundle: ' + msg)


def read_manifest():
    with open(os.path.join(ROOT, ENTRY + '.manifest'), encoding='utf-8') as fh:
        return json.load(fh)


def platforms(os_choice):
    if os_choice == 'both':
        return PLATFORMS['mac'] + PLATFORMS['win']
    return PLATFORMS[os_choice]


def check_libs(plats):
    for plat in plats:
        lib = os.path.join(ROOT, 'lib', plat)
        if not os.path.isdir(os.path.join(lib, 'numpy')):
            fail('{} has no numpy; run: python3 tools/vendor_numpy.py --platform {}'.format(lib, plat))
        dylibs = os.path.join(lib, 'numpy', '.dylibs')
        if os.path.isdir(dylibs):
            fail('{} holds {}: GNU libraries the store rejects. Re-vendor with '
                 'tools/vendor_numpy.py --platform {} (macosx_14_0 wheel).'.format(
                     dylibs, sorted(os.listdir(dylibs)), plat))


def copy_tree(src, dst, skip_dirs=()):
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in skip_dirs]
        rel = os.path.relpath(base, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            if f in SKIP_DIRS or f.endswith(SKIP_EXT):
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(dst, rel, f))


def fill(template, values):
    with open(template, encoding='utf-8') as fh:
        text = fh.read()
    for k, v in values.items():
        text = text.replace('{{' + k + '}}', v)
    if '{{' in text:
        fail('unfilled placeholder in ' + template)
    return text


def third_party_notices(plats):
    """numpy's own licence files, once per platform build that ships (they
    differ: the Windows wheel lists its OpenBLAS DLL)."""
    out = ['Mesh to Outline includes the following third-party software.', '']
    for plat in plats:
        lib = os.path.join(ROOT, 'lib', plat)
        info = [d for d in os.listdir(lib) if d.startswith('numpy-') and d.endswith('.dist-info')]
        if len(info) != 1:
            fail('expected one numpy dist-info in ' + lib)
        lic_root = os.path.join(lib, info[0], 'licenses')
        out += ['=' * 78, '{} ({}) — lib/{}'.format(info[0][:-len('.dist-info')], 'BSD-3-Clause and others', plat),
                '=' * 78, '']
        if plat.startswith('macos'):
            out += ['Note: this macOS build links Apple\'s Accelerate framework and ships',
                    'no bundled libraries; the sections of numpy\'s LICENSE.txt about',
                    'numpy/.dylibs (OpenBLAS, libgfortran, libgcc, libquadmath) describe',
                    'files that are NOT present in this build.', '']
        for base, _, files in os.walk(lic_root):
            for f in sorted(files):
                p = os.path.join(base, f)
                out += ['--- ' + os.path.relpath(p, lic_root).replace(os.sep, '/') + ' ---']
                with open(p, encoding='utf-8', errors='replace') as fh:
                    out.append(fh.read().rstrip())
                out.append('')
    return '\n'.join(out) + '\n'


def package_contents(version, upgrade, product, os_attr):
    rr = ('<RuntimeRequirements OS={} Platform="Fusion360" SeriesMin="" '
          'SupportPath="./Contents/Documentation"/>').format(quoteattr(os_attr))
    xml_text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ApplicationPackage SchemaVersion="1.0" ProductType="Application" AutodeskProduct="Fusion360"'
        ' Name={name} Description={desc} AppVersion={ver} Author={author}'
        ' AppNameSpace={ns} HelpFile="./Contents/Documentation/MeshToOutline.html"'
        ' OnlineDocumentation={help} Icon="./Contents/resources/icon32.png"'
        ' ProductCode={pc} UpgradeCode={uc}>\n'
        '  <CompanyDetails Name={company} Url={url} Email={email} Phone=" "/>\n'
        '  {rr}\n'
        '  <Components>\n'
        '    {rr}\n'
        '    <ComponentEntry AppName={name} Version={ver} ModuleName="./Contents/{entry}.manifest"'
        ' AppDescription={desc}/>\n'
        '  </Components>\n'
        '</ApplicationPackage>\n').format(
            name=quoteattr(APP_NAME), desc=quoteattr(DESCRIPTION), ver=quoteattr(version),
            author=quoteattr(COMPANY), ns=quoteattr(APP_NAMESPACE), help=quoteattr(HELP_URL),
            pc=quoteattr('{' + product + '}'), uc=quoteattr('{' + upgrade + '}'),
            company=quoteattr(COMPANY), url=quoteattr(COMPANY_URL), email=quoteattr(ARGS.support_email),
            rr=rr, entry=ENTRY)
    xml.dom.minidom.parseString(xml_text.encode('utf-8'))       # must parse
    return xml_text


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build(os_choice):
    man = read_manifest()
    version = man['version']
    upgrade = man['id'].upper()
    # a new ProductCode per version, but the same one when the same version
    # is rebuilt (so a re-upload of an unchanged version is byte-stable)
    product = str(uuid.uuid5(uuid.UUID(upgrade), version)).upper()
    plats = platforms(os_choice)
    check_libs(plats)

    bundle = os.path.join(DIST, BUNDLE)
    if os.path.exists(bundle):
        shutil.rmtree(bundle)
    contents = os.path.join(bundle, 'Contents')
    os.makedirs(contents)

    shutil.copy2(os.path.join(ROOT, ENTRY + '.py'), contents)
    man_store = dict(man, editEnabled=False, runOnStartup=True, author=COMPANY,
                     description={'': DESCRIPTION})
    with open(os.path.join(contents, ENTRY + '.manifest'), 'w', encoding='utf-8') as fh:
        json.dump(man_store, fh, indent=4)
        fh.write('\n')
    for pkg in PACKAGES:
        copy_tree(os.path.join(ROOT, pkg), os.path.join(contents, pkg))
    for plat in plats:
        # numpy's own test suites are never imported at run time
        copy_tree(os.path.join(ROOT, 'lib', plat), os.path.join(contents, 'lib', plat),
                  skip_dirs=('tests',))
    res = os.path.join(contents, 'resources')
    os.makedirs(res)
    shutil.copy2(os.path.join(STORE, 'icon32.png'), res)

    docs = os.path.join(contents, 'Documentation')
    os.makedirs(docs)
    values = {'VERSION': version, 'SUPPORT_EMAIL': ARGS.support_email,
              'PRIVACY_URL': PRIVACY_URL, 'DATE': datetime.date.today().isoformat()}
    for name in ('MeshToOutline.html', 'privacy.html'):
        with open(os.path.join(docs, name), 'w', encoding='utf-8') as fh:
            fh.write(fill(os.path.join(STORE, name), values))
    with open(os.path.join(docs, 'THIRD-PARTY-NOTICES.txt'), 'w', encoding='utf-8') as fh:
        fh.write(third_party_notices(plats))
    with open(os.path.join(docs, 'LICENSE.txt'), 'w', encoding='utf-8') as fh:
        fh.write('Mesh to Outline is licensed to you under the Autodesk Standard End User\n'
                 'Agreement for Autodesk Marketplace apps: https://apps.autodesk.com/en/Public/EULA\n'
                 'Third-party components: see THIRD-PARTY-NOTICES.txt.\n')

    with open(os.path.join(bundle, 'PackageContents.xml'), 'w', encoding='utf-8') as fh:
        fh.write(package_contents(version, upgrade, product, PC_OS[os_choice]))

    # last checks over what actually landed in the bundle
    binaries = []
    for base, dirs, files in os.walk(bundle):
        for d in dirs:
            if d in SKIP_DIRS:
                fail('stray folder in bundle: ' + os.path.join(base, d))
        for f in files:
            p = os.path.join(base, f)
            if f.endswith(SKIP_EXT) or f == '.DS_Store':
                fail('stray file in bundle: ' + p)
            if f.endswith(('.so', '.pyd', '.dll', '.dylib')):
                binaries.append(p)

    zip_path = os.path.join(DIST, 'MeshToOutline-{}-{}.zip'.format(version, os_choice))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, _, files in os.walk(bundle):
            for f in sorted(files):
                p = os.path.join(base, f)
                z.write(p, os.path.relpath(p, DIST))

    # the §5.2(b) record: every binary that ships, by hash
    record = os.path.join(DIST, 'MeshToOutline-{}-{}-binaries.txt'.format(version, os_choice))
    with open(record, 'w', encoding='utf-8') as fh:
        fh.write('# sha256 of every binary in {} (scan these before upload)\n'.format(
            os.path.basename(zip_path)))
        for p in sorted(binaries):
            fh.write('{}  {}\n'.format(sha256(p), os.path.relpath(p, bundle).replace(os.sep, '/')))
        fh.write('{}  {}\n'.format(sha256(zip_path), os.path.basename(zip_path)))

    size = os.path.getsize(zip_path) / 1e6
    print('bundle  {}'.format(bundle))
    print('zip     {} ({:.1f} MB)'.format(zip_path, size))
    print('version {}  UpgradeCode {{{}}}  ProductCode {{{}}}  OS {}'.format(
        version, upgrade, product, PC_OS[os_choice]))
    print('binaries recorded in {} ({} files)'.format(record, len(binaries)))
    if 'win_amd64' in plats:
        print('NOTE: Windows numpy embeds the GCC runtime (GPL-3 + GCC exception) in its '
              'OpenBLAS DLL. Wait for Autodesk\'s answer before uploading this build.')
    return zip_path


def main(argv=None):
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--os', choices=('mac', 'win', 'both'), default='mac')
    ap.add_argument('--support-email', default='support@stltosolid.com')
    ARGS = ap.parse_args(argv)
    os.makedirs(DIST, exist_ok=True)
    return build(ARGS.os)


ARGS = None

if __name__ == '__main__':
    main()
