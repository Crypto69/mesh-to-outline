"""The "Mesh to Outline" command: dialog, plane cut + curve fit, and
the sketch drawn on the main thread.

Everything runs on Fusion's main thread: a cut plus fit of a 300k-triangle
mesh takes a second or two, so there is no worker thread. A stack of
sections shows a progress dialog and pumps adsk.doEvents() between cuts so
Cancel stays live.

Handler objects are kept in a module-level list: Fusion holds them only by
weak reference and a handler that is garbage collected stops firing.
"""
import os
import sys
import time
import traceback

import adsk.core
import adsk.fusion

CMD_ID = 'MeshSectionSketch_cmd'
CMD_NAME = 'Mesh to Outline'
CMD_TOOLTIP = ('Cut a mesh body with a plane and draw the section as a sketch of lines, '
               'arcs, circles and fitted splines (Create Mesh Section Sketch + Fit Curves '
               'to Mesh Section, in one go).')
# Fusion's Mesh tab: Modify panel, right after Convert Mesh; older ids kept
# as fallbacks, Utilities > Add-Ins as the last resort.
MESH_MODIFY_PANEL_IDS = ('ParaMeshModifyPanel', 'MeshModifyPanel')
MESH_CONVERT_CMD_ID = 'ParaMeshConvertCommand'
FALLBACK_PANEL_ID = 'SolidScriptsAddinsPanel'
REQUIRED_PY = (3, 14)
MIN_NUMPY_MAJOR = 2
HELP_URL = 'https://stltosolid.com/help/mesh-to-outline'
PRIVACY_URL = 'https://stltosolid.com/privacy'
PRIVACY_NOTE = ('Runs only on this computer: collects no data and sends nothing '
                'over the internet.')
MAX_STACK = 400
# the Action dropdown: one sketch, N sketches from the plane, or the
# X-Ray (a stack between a picked start and end, the web app's tool)
MODE_ONE, MODE_STACK, MODE_XRAY = 0, 1, 2
MODE_INPUTS = {
    MODE_ONE: ('plane',),
    MODE_STACK: ('plane', 'spacing', 'count', 'direction', 'extrude', 'rails'),
    MODE_XRAY: ('xray_start', 'xray_end', 'spacing', 'extrude', 'rails', 'xray_info'),
}
ALL_MODE_INPUTS = ('plane', 'xray_start', 'xray_end', 'spacing', 'count', 'direction', 'extrude', 'rails',
                   'xray_info')
# a Fusion loft through hundreds of profiles is impractical
LOFT_MAX = 60
MODE_PICKS = ('plane', 'xray_start', 'xray_end')

# icons: Fusion only shows a command as a panel button when it has them
RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'outline')

_app = None
_ui = None
_handlers = []
_state = {'control': None, 'panel_id': None, 'cmd_def': None}
# mesh arrays by body token, kept while one dialog is open: the X-Ray's
# info line and its mesh picks need them on every input change
_mesh_cache = {}


# ---------------------------------------------------------------- helpers

def _log(text):
    try:
        _app.log('MeshSectionSketch: ' + text)
    except Exception:
        pass


def _msg(text):
    try:
        _ui.messageBox(text, CMD_NAME)
    except Exception:
        pass


def _fail(title):
    _msg('{}:\n{}'.format(title, traceback.format_exc()))


def _numpy_major(np_mod):
    try:
        return int(str(np_mod.__version__).split('.')[0])
    except Exception:
        return 0


def _about_html():
    """The dialog's last line: what the add-in does with data (nothing),
    plus the help page and the privacy policy. The store requires the
    privacy policy to be reachable from inside the add-in; a store build
    carries both pages in Contents/Documentation, a dev checkout links to
    the website."""
    docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'Documentation')

    def link(name, url):
        path = os.path.join(docs, name)
        if os.path.isfile(path):
            # as_uri() percent-encodes the space in 'Application Support'
            import pathlib
            url = pathlib.Path(path).as_uri()
        return url

    return ('{} <a href="{}">Help</a> &middot; <a href="{}">Privacy policy</a>'.format(
        PRIVACY_NOTE, link('MeshToOutline.html', HELP_URL),
        link('privacy.html', PRIVACY_URL)))


def _platform_hint():
    try:
        import MeshSectionSketch as entry
        return entry.platform_dir(), entry.lib_dir()
    except Exception:
        return None, None


def _preflight():
    """The refusals that need no dialog input. Returns an error text or None.

    The message is for an end user, who can only update or reinstall; the
    developer detail (which folder, which command fixes it) goes to the
    Text Commands log."""
    if tuple(sys.version_info[:2]) != REQUIRED_PY:
        _log('Python {}.{} but numpy was vendored for {}.{}: re-vendor with '
             'tools/vendor_numpy.py and update REQUIRED_PY'.format(
                 sys.version_info[0], sys.version_info[1], *REQUIRED_PY))
        return ('Autodesk Fusion has updated its Python to {}.{}.\n\n'
                'This version of Mesh to Outline was built for Python {}.{}. '
                'Please install the latest version of Mesh to Outline.'.format(
                    sys.version_info[0], sys.version_info[1], *REQUIRED_PY))
    try:
        import numpy
    except Exception as e:
        plat, lib = _platform_hint()
        _log('numpy could not be imported ({}); expected in {}; fill with '
             'tools/vendor_numpy.py --platform {}'.format(
                 e, lib or 'lib/<platform>', plat or '<platform>'))
        return ('Mesh to Outline is incomplete: a part it needs (numpy) is missing '
                'or does not match this computer.\n\n'
                'Please install Mesh to Outline again.')
    # Fusion runs every add-in in one Python: if another add-in imported an
    # old numpy first, that is the one we get
    if _numpy_major(numpy) < MIN_NUMPY_MAJOR:
        _log('numpy {} from {} is too old'.format(
            getattr(numpy, '__version__', '?'), getattr(numpy, '__file__', '?')))
        return ('Another add-in has loaded an old copy of numpy ({}), which Mesh to '
                'Outline cannot use.\n\nTurn that add-in off in Utilities > Add-Ins, '
                'restart Autodesk Fusion, and try again.'.format(
                    getattr(numpy, '__version__', '?')))
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if design is None:
        return 'Open a design first (the active document is not a Fusion design).'
    return None


def stats_line(st, outline=False):
    """The one-line summary the stlToSolid web app shows for a section."""
    parts = []
    if st.get('lines'):
        parts.append('{} lines'.format(st['lines']))
    if st.get('arcs'):
        parts.append('{} arcs'.format(st['arcs']))
    if st.get('circles'):
        parts.append('{} circle{}'.format(st['circles'], '' if st['circles'] == 1 else 's'))
    if st.get('splines'):
        parts.append('{} spline{}'.format(st['splines'], '' if st['splines'] == 1 else 's'))
    head = '{} outline{}'.format(st['loops'], '' if st['loops'] == 1 else 's')
    if st.get('open'):
        head += ', {} open curve{}{}'.format(st['open'], '' if st['open'] == 1 else 's',
                                             ' skipped' if outline else '')
    tail = ''
    if st.get('joins'):
        tail = '; {} gap{} joined (largest {:.2f} mm)'.format(
            st['joins'], '' if st['joins'] == 1 else 's', st['max_gap'])
    if st.get('inner'):
        head += ', {} inner loop{} skipped'.format(st['inner'], '' if st['inner'] == 1 else 's')
    if st.get('trimmed'):
        tail += '; {} sliver{} trimmed'.format(st['trimmed'], '' if st['trimmed'] == 1 else 's')
    if st.get('specks'):
        tail += '; {} speck{} on the outline dropped'.format(st['specks'], '' if st['specks'] == 1 else 's')
    if st.get('tangent') or st.get('sharpened'):
        t = []
        if st.get('tangent'):
            t.append('{} tangent'.format(st['tangent']))
        if st.get('sharpened'):
            t.append('{} sharpened'.format(st['sharpened']))
        tail += '; ' + ', '.join(t)
    if st.get('bridged'):
        tail += '; outline closed across {} mesh gap{} (largest {:.2f} mm)'.format(
            st['bridged'], '' if st['bridged'] == 1 else 's', st.get('bridge_max', 0.0))
    return '{}: {}{}; worst miss {:.3f} mm'.format(head, ', '.join(parts) or 'nothing',
                                                   tail, st['dev_max'])


# ---------------------------------------------------------------- drawing

def draw_prims(sketch, prims, notes=None):
    """Draw one fitted loop or open chain (3-D mm primitives from
    section_fit.lift_prims) into `sketch`: real lines, three-point arcs, a
    circle for a whole-circle loop, fitted splines for spline stretches.
    Nothing here closes a chain: a loop closes because its primitives
    share end points, an open chain stays open. Returns the curve count.

    Fusion refuses some curves ("Some input argument is invalid"): an arc
    so flat its three points are collinear to Fusion (a ~1 m radius on a
    controller's side wall), a zero-length line, a too-small circle. One
    refused curve used to end the whole run; now a refused arc or spline
    is drawn as straight lines through its own points (same ends, so the
    loop stays closed) and a refused line or circle is left out. `notes`
    (a dict) counts them."""
    curves = sketch.sketchCurves
    notes = {} if notes is None else notes

    def P(p):
        return sketch.modelToSketchSpace(
            adsk.core.Point3D.create(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0))

    def lines(pts):
        k = 0
        for a, b in zip(pts, pts[1:]):
            try:
                curves.sketchLines.addByTwoPoints(P(a), P(b))
                k += 1
            except Exception:
                notes['skipped'] = notes.get('skipped', 0) + 1
        return k

    if isinstance(prims, dict):                      # full circle
        try:
            curves.sketchCircles.addByCenterRadius(P(prims['center3']), float(prims['r']) / 10.0)
            return 1
        except Exception:
            notes['skipped'] = notes.get('skipped', 0) + 1
            return 0
    n = 0
    for p in prims:
        if p['type'] == 'line':
            n += lines([p['p0'], p['p1']])
        elif p['type'] == 'arc':
            try:
                curves.sketchArcs.addByThreePoints(P(p['p0']), P(p['mid']), P(p['p1']))
                n += 1
            except Exception:
                notes['arcs_as_lines'] = notes.get('arcs_as_lines', 0) + 1
                n += lines([p['p0'], p['mid'], p['p1']])
        else:
            pc = adsk.core.ObjectCollection.create()
            for q in p['pts']:
                pc.add(P(q))
            try:
                curves.sketchFittedSplines.add(pc)
                n += 1
            except Exception:
                notes['splines_as_lines'] = notes.get('splines_as_lines', 0) + 1
                n += lines(list(p['pts']))
    return n


# (draw_prims_tidy and _nearest_end are copied verbatim from
# stl_to_solid.fusion_export._DRAW_TIDY; desktop_tests/test_parity checks)
def _nearest_end(curve, target):
    """The end sketch point of `curve` nearest `target` (sketch space).
    Fusion keeps an arc counter-clockwise, so a clockwise arc's
    startSketchPoint is where it was asked to end."""
    a, b = curve.startSketchPoint, curve.endSketchPoint
    return a if a.geometry.distanceTo(target) <= b.geometry.distanceTo(target) else b


def draw_prims_tidy(sketch, prims, notes=None):
    """Tidy-corners twin of draw_prims: the curves of one loop or open
    chain are chained through shared sketch points (each curve starts on
    the previous curve's end point, a loop's last curve ends on the first
    curve's start point), so Fusion sees them connected, and every
    junction the section data marks tangent ('tan1' on a curve, 'tan0' on
    the next) gets a tangent constraint. A curve Fusion refuses on shared
    points is drawn from plain points instead, with draw_prims's
    fallbacks. `notes` also counts 'tangent' (constraints added) and
    'tangent_failed'. Returns the curve count."""
    notes = {} if notes is None else notes
    if isinstance(prims, dict) or len(prims) < 2:
        return draw_prims(sketch, prims, notes)
    curves = sketch.sketchCurves

    def P(p):
        return sketch.modelToSketchSpace(
            adsk.core.Point3D.create(p[0] / 10.0, p[1] / 10.0, p[2] / 10.0))

    def ends(p):
        if p['type'] == 'spline':
            return p['pts'][0], p['pts'][-1]
        return p['p0'], p['p1']

    def bump(key):
        notes[key] = notes.get(key, 0) + 1

    a0, z1 = ends(prims[0])[0], ends(prims[-1])[1]
    closed = sum((x - y) ** 2 for x, y in zip(a0, z1)) < 1e-12
    first, prev_end, made, n = None, None, [], 0
    for k, p in enumerate(prims):
        q0, q1 = ends(p)
        start = prev_end if prev_end is not None else P(q0)
        end = first if (closed and k == len(prims) - 1 and first is not None) else P(q1)
        curve, ok = None, True
        try:
            if p['type'] == 'line':
                curve = curves.sketchLines.addByTwoPoints(start, end)
            elif p['type'] == 'arc':
                curve = curves.sketchArcs.addByThreePoints(start, P(p['mid']), end)
            else:
                pc = adsk.core.ObjectCollection.create()
                pc.add(start)
                for q in p['pts'][1:-1]:
                    pc.add(P(q))
                pc.add(end)
                curve = curves.sketchFittedSplines.add(pc)
        except Exception:
            ok = False
        if not ok:
            n += draw_prims(sketch, [p], notes)
        else:
            n += 1
        if curve is None:
            prev_end = None
            made.append((p, None))
            continue
        if k == 0:
            first = _nearest_end(curve, P(q0))
        prev_end = _nearest_end(curve, P(q1))
        made.append((p, curve))
    gc = sketch.geometricConstraints
    pairs = list(zip(made, made[1:]))
    if closed:
        pairs.append((made[-1], made[0]))
    for (pa, ca), (pb, cb) in pairs:
        if not (pa.get('tan1') and pb.get('tan0')):
            continue
        if ca is None or cb is None:
            bump('tangent_failed')
            continue
        try:
            gc.addTangent(ca, cb)
            bump('tangent')
        except Exception:
            bump('tangent_failed')
    return n


def draw_section(sketch, sec, notes=None, tidy=False):
    """All loops (outer + holes) and open chains of a section_preview
    result; with `tidy` through shared sketch points with tangent
    constraints (draw_prims_tidy). Returns the curve count."""
    draw = draw_prims_tidy if tidy else draw_prims
    n = 0
    for outer, holes in sec['loops']:
        n += draw(sketch, outer, notes)
        for h in holes:
            n += draw(sketch, h, notes)
    for chain in sec.get('open', []):
        n += draw(sketch, chain, notes)
    return n


def _token(body):
    try:
        return body.entityToken
    except Exception:
        return None


def _live(bodies):
    """The bodies still there, each once (a body merged into another goes
    invalid; the same body can be reached through two references)."""
    out, seen = [], set()
    for b in bodies:
        try:
            if not b.isValid:
                continue
        except Exception:
            continue
        t = _token(b)
        if t is not None:
            if t in seen:
                continue
            seen.add(t)
        out.append(b)
    return out


def _track(feat, bodies, seen):
    """Remember every body `feat` left behind. A Join whose profile touches
    none of its participants does not fail: Fusion quietly makes a new
    body. Unless that body becomes a participant of the next slab, every
    slab above it in that column joins nothing too, and one arm of the part
    comes out as a hundred loose slabs."""
    for k in range(feat.bodies.count):
        b = feat.bodies.item(k)
        t = _token(b)
        if t is None or t not in seen:
            if t is not None:
                seen.add(t)
            bodies.append(b)


def _area(pr):
    """A profile's area (cm2), or None when Fusion cannot measure it (a
    zero-size scrap where a leaky outline touches itself)."""
    try:
        return pr.areaProperties().area
    except Exception:
        return None


def _material_profiles(sketch, areas_cm2, rel=0.03):
    """The profiles to extrude. Fusion lists a hole's interior as a
    profile as well as the ring around it; extruding both fills the hole.
    section_preview gives each loop's material area (outer minus its
    holes), so the profiles whose area matches one of those are the
    material. Returns (profiles, matched): with no expected areas, or none
    matching (a fit that went wrong), every profile is taken and matched
    is False so the run can say so."""
    profs = [sketch.profiles.item(i) for i in range(sketch.profiles.count)]
    profs = [pr for pr in profs if _area(pr) is not None] or profs     # drop scraps
    if not areas_cm2:
        return profs, True
    out = []
    for pr in profs:
        a = _area(pr)
        if a is not None and any(abs(a - e) <= rel * max(e, 1e-9) for e in areas_cm2):
            out.append(pr)
    if out:
        return out, True
    return profs, False


def _extrude_slice(root, sketch, dist_cm, bodies, seen, areas_cm2=None):
    """Extrude the material profiles of `sketch` by `dist_cm` (signed,
    along the sketch normal), one profile at a time: joined to the bodies
    made so far (all of them as participants; the one it touches takes
    it), which leaves a new body where it touches none (a joystick tip
    that appears mid-stack starts its own column). Every body a feature
    leaves behind is added to `bodies` (see _track) so the next slab can
    join it. Returns (profiles extruded, areas matched)."""
    if sketch.profiles.count == 0:
        raise RuntimeError('no closed profile to extrude')
    ops = adsk.fusion.FeatureOperations
    ext = root.features.extrudeFeatures
    profs, matched = _material_profiles(sketch, areas_cm2)
    made = 0
    for prof in profs:
        feat = None
        live = _live(bodies)
        if live:
            try:
                inp = ext.createInput(prof, ops.JoinFeatureOperation)
                inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(dist_cm))
                inp.participantBodies = live
                feat = ext.add(inp)
            except Exception:
                feat = None
        if feat is None:
            inp = ext.createInput(prof, ops.NewBodyFeatureOperation)
            inp.setDistanceExtent(False, adsk.core.ValueInput.createByReal(dist_cm))
            feat = ext.add(inp)
        _track(feat, bodies, seen)
        made += 1
    return made, matched


def _rail_plane(root, base, centre_mm, m, np):
    """A construction plane through `centre_mm` with normal `m`, square to
    the picked plane `base` (the stack runs along base's normal, which the
    rail plane contains): a construction line in a sketch on `base`, then a
    plane at 90 degrees to `base` about that line. Built from geometry, so it
    works in parametric designs as well as direct ones."""
    g = adsk.core.Plane.cast(base.geometry)
    nb = np.array([g.normal.x, g.normal.y, g.normal.z], float)
    nb /= np.linalg.norm(nb)
    ob = np.array([g.origin.x, g.origin.y, g.origin.z], float) * 10.0
    c = centre_mm - ((centre_mm - ob) @ nb) * nb             # the centre on the base plane
    d = np.cross(nb, m)
    d /= np.linalg.norm(d)
    sk = root.sketches.add(base)
    sk.name = 'rail axis'

    def P(q):
        return sk.modelToSketchSpace(adsk.core.Point3D.create(q[0] / 10.0, q[1] / 10.0, q[2] / 10.0))
    ln = sk.sketchCurves.sketchLines.addByTwoPoints(P(c - 10.0 * d), P(c + 10.0 * d))
    ln.isConstruction = True
    inp = root.constructionPlanes.createInput()
    inp.setByAngle(ln, adsk.core.ValueInput.createByString('90 deg'), base)
    return root.constructionPlanes.add(inp)


def draw_rails(root, base, rails, notes, np):
    """One construction plane + sketch per rail plane, one fitted spline
    per rail. Returns [(rail, curve)] for the rails drawn."""
    sketches, out = {}, []
    for k, r in enumerate(rails):
        key = tuple(round(c, 6) for c in r['plane']['normal'])
        name = 'rail {}/{}'.format(k + 1, len(rails))
        if key not in sketches:
            try:
                pl = _rail_plane(root, base, np.asarray(r['plane']['origin'], float),
                                 np.asarray(r['plane']['normal'], float), np)
                pl.name = 'rail plane {}'.format(len(sketches) + 1)
                sk = root.sketches.add(pl)
                sk.name = 'rails {}'.format(len(sketches) + 1)
            except Exception:
                why = traceback.format_exc().strip().splitlines()[-1]
                notes.append('the rail plane could not be made ({}); its rails are left out'.format(why))
                sk = None
            sketches[key] = sk
        sk = sketches[key]
        if sk is None:
            continue
        pc = adsk.core.ObjectCollection.create()
        for q in r['pts']:
            pc.add(sk.modelToSketchSpace(adsk.core.Point3D.create(q[0] / 10.0, q[1] / 10.0, q[2] / 10.0)))
        try:
            out.append((r, sk.sketchCurves.sketchFittedSplines.add(pc)))
        except Exception:
            notes.append('{}: Fusion refused the rail'.format(name))
    return out


# (_loft_with_rails, _hole_profile, _cut_holes and _holes_note are copied
# verbatim from the stlToSolid X-Ray script's runtime,
# fusion_export._XRAY_RAILS_RUNTIME; desktop_tests/test_parity checks)
def _loft_with_rails(root, drawn, rail_curves):
    """Loft one new body through the largest material profile of each
    section sketch, guided by `rail_curves`. Without the rails if Fusion
    refuses them. Returns (how, feature): how = 'with N rails' / 'without
    rails (why)'; raises when Fusion refuses the loft both ways."""
    profs = []
    for sk, sec in drawn:
        areas = [a / 100.0 for a in (sec.get('areas_mm2') or [])]
        cand, _ = _material_profiles(sk, areas)
        if not cand:
            raise RuntimeError('%s has no closed profile' % sk.name)
        profs.append(max(cand, key=lambda p: _area(p) or 0.0))
    lofts = root.features.loftFeatures

    def attempt(rails):
        li = lofts.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        for p in profs:
            li.loftSections.add(p)
        for c in rails:
            li.centerLineOrRails.addRail(c)
        li.isSolid = True
        return lofts.add(li)

    why = None
    if rail_curves:
        try:
            return 'with %d rail(s)' % len(rail_curves), attempt(rail_curves)
        except Exception:
            why = traceback.format_exc().strip().splitlines()[-1]
    feat = attempt([])
    return ('without rails (%s)' % why if why else 'without rails'), feat


def _hole_profile(sk, area_cm2, c_cm):
    """The profile of `sk` that is the inside of the hole with area
    `area_cm2` and centroid `c_cm` (a Point3D in model space): area within
    5 %, centroid within a quarter of the hole's radius. None if none."""
    best, bd = None, None
    for i in range(sk.profiles.count):
        p = sk.profiles.item(i)
        a = _area(p)
        if a is None or abs(a - area_cm2) > 0.05 * area_cm2:
            continue
        try:
            c = p.areaProperties().centroid
        except Exception:
            continue
        # the centroid may come back in sketch space: take the nearer reading
        d = min(c.distanceTo(c_cm), sk.sketchToModelSpace(c).distanceTo(c_cm))
        if bd is None or d < bd:
            best, bd = p, d
    if best is None or bd > 0.25 * (area_cm2 / 3.14159265) ** 0.5 + 0.005:
        return None
    return best


def _cut_holes(root, sketch_at, body, chains):
    """Cut each hole chain (rails.hole_chains: [[section index, area mm2,
    centroid mm], ...]) out of `body` with a loft through the hole's
    profiles in the sketches `sketch_at` maps section indexes to. Returns
    (holes cut, [why each other one was not])."""
    lofts = root.features.loftFeatures
    made, missed = 0, []
    for n, chain in enumerate(chains):
        profs = []
        for k, area, c in chain:
            sk = sketch_at.get(k)
            if sk is None:
                continue
            p = _hole_profile(sk, area / 100.0, adsk.core.Point3D.create(c[0] / 10.0, c[1] / 10.0, c[2] / 10.0))
            if p is not None:
                profs.append(p)
        if len(profs) < 2:
            missed.append('hole %d: its outline was not found in two slices' % (n + 1))
            continue
        try:
            li = lofts.createInput(adsk.fusion.FeatureOperations.CutFeatureOperation)
            for p in profs:
                li.loftSections.add(p)
            li.isSolid = True
            if body is not None:
                li.participantBodies = [body]
            lofts.add(li)
            made += 1
        except Exception:
            missed.append('hole %d: %s' % (n + 1, traceback.format_exc().strip().splitlines()[-1]))
    return made, missed


def _holes_note(made, missed):
    s = 'Holes: %d cut into the loft' % made
    if missed:
        s += '; %d not cut (%s%s)' % (len(missed), '; '.join(missed[:3]), ' ...' if len(missed) > 3 else '')
    return s


def _rails_and_loft(root, base, normal, rails, drawn, loft, np, holes=None, sketch_at=None):
    """After the slices: draw the rails, then (loft) the loft, then cut
    `holes` (rails.hole_chains over the slices, indexed like `sketch_at`)
    out of it. Returns the lines for the closing message."""
    notes = []
    curves = draw_rails(root, base, rails, notes, np)
    done = [c for r, c in curves if r['complete']]
    part = [k for k, r in enumerate(rails) if not r['complete']]
    line = 'Rails: {} drawn, {} touching every slice'.format(len(curves), len(done))
    if part:
        line += '; not used by a loft: ' + '; '.join(
            'rail {}/{} ({})'.format(k + 1, len(rails), rails[k]['note']) for k in part[:3]) + \
            (' ...' if len(part) > 3 else '')
    notes.insert(0, line)
    if not loft:
        return notes
    if len(drawn) < 2:
        notes.append('Loft skipped: it needs at least two sketches')
    elif len(drawn) > LOFT_MAX:
        notes.append('Loft skipped: {} slices is more than {}; use a bigger spacing for the loft'.format(
            len(drawn), LOFT_MAX))
    elif any(not sec['loops'] for sk, sec in drawn):
        notes.append('Loft skipped: a slice has no closed outline')
    else:
        try:
            how, feat = _loft_with_rails(root, drawn, done)
            notes.insert(0, 'Loft: one new body ' + how)
        except Exception:
            why = traceback.format_exc().strip().splitlines()[-1]
            notes.insert(0, 'WARNING: Fusion refused the loft ({}); the sketches and rails are kept'.format(why))
            return notes
        if holes:
            body = None
            try:
                if feat is not None and feat.bodies.count:
                    body = feat.bodies.item(0)
            except Exception:
                pass
            notes.insert(1, _holes_note(*_cut_holes(root, sketch_at or {}, body, holes)))
    return notes


def mesh_arrays(mesh_body, np):
    """(vertices mm, faces) of a MeshBody in root-component space."""
    # displayMesh is a TriangleMesh (nodeIndices); mesh is a PolygonMesh in
    # current Fusion builds (triangleNodeIndices + quadNodeIndices). Take
    # the triangulated one, fall back to the polygon mesh's triangles.
    tri = None
    try:
        tri = mesh_body.displayMesh
    except Exception:
        tri = None
    if tri is not None and hasattr(tri, 'nodeIndices'):
        coords = np.asarray(tri.nodeCoordinatesAsDouble, dtype=float).reshape(-1, 3)   # cm
        faces = np.asarray(tri.nodeIndices, dtype=np.int64).reshape(-1, 3)
    else:
        poly = mesh_body.mesh
        coords = np.asarray(poly.nodeCoordinatesAsDouble, dtype=float).reshape(-1, 3)
        idx = getattr(poly, 'triangleNodeIndices', None) or getattr(poly, 'nodeIndices', None)
        faces = np.asarray(idx, dtype=np.int64).reshape(-1, 3)
    occ = None
    try:
        occ = mesh_body.assemblyContext
    except Exception:
        occ = None
    if occ is not None:
        m = np.asarray(occ.transform.asArray(), dtype=float).reshape(4, 4)
        coords = coords @ m[:3, :3].T + m[:3, 3]
    return coords * 10.0, faces


def _cached_arrays(mesh_body, np):
    t = _token(mesh_body)
    if t is not None and t in _mesh_cache:
        return _mesh_cache[t]
    arr = mesh_arrays(mesh_body, np)
    if t is not None:
        _mesh_cache[t] = arr
    return arr


def _plane_of(entity, np):
    """(origin mm, unit normal) of a construction plane or flat face, or None."""
    try:
        geom = adsk.core.Plane.cast(entity.geometry)
    except Exception:
        geom = None
    if geom is None:
        return None
    n = np.array([geom.normal.x, geom.normal.y, geom.normal.z], float)
    return np.array([geom.origin.x, geom.origin.y, geom.origin.z], float) * 10.0, n / np.linalg.norm(n)


def pick_height(selection, normal, np):
    """Height (mm along `normal`) of an X-Ray start or end pick, and a few
    words on how it was found. A click on a mesh snaps to a flat face
    across the direction (just inside it) or takes the clicked point; a
    construction plane or flat face must be parallel to the slice
    direction. Raises ValueError with a sentence for the user."""
    from mssaddin import xray
    ent = selection.entity
    mb = adsk.fusion.MeshBody.cast(ent)
    if mb is not None:
        pt = selection.point
        V, F = _cached_arrays(mb, np)
        h, flat = xray.pick_height(V, F, np.array([pt.x, pt.y, pt.z]) * 10.0, normal)
        return h, 'flat face' if flat else 'clicked point'
    pl = _plane_of(ent, np)
    if pl is None:
        raise ValueError('pick a construction plane, a flat face or a spot on the mesh')
    origin, n = pl
    if abs(float(n @ normal)) < xray.FLAT_COS:
        raise ValueError('{} is not parallel to the slice plane'.format(getattr(ent, 'name', None) or 'that plane'))
    return float(origin @ normal), 'plane'


def xray_from_inputs(inputs, np):
    """The X-Ray plan for the dialog's current inputs: (plan, start/end
    notes, the plane the slices are offset from) or raises ValueError with
    what is missing. The slices run parallel to the Start pick, or the End
    pick if Start is a click on the mesh; two clicks on the mesh give no
    direction."""
    from mssaddin import xray
    sel = adsk.core.SelectionCommandInput.cast(inputs.itemById('mesh_body'))
    s0 = adsk.core.SelectionCommandInput.cast(inputs.itemById('xray_start'))
    s1 = adsk.core.SelectionCommandInput.cast(inputs.itemById('xray_end'))
    if sel is None or sel.selectionCount == 0:
        raise ValueError('pick the mesh body')
    if s0 is None or s0.selectionCount == 0 or s1 is None or s1.selectionCount == 0:
        raise ValueError('pick a start and an end (a plane, a flat face or a spot on the mesh)')
    ref = None
    for s in (s0, s1):
        ent = s.selection(0).entity
        if adsk.fusion.MeshBody.cast(ent) is None and _plane_of(ent, np) is not None:
            ref = ent
            break
    if ref is None:
        raise ValueError('both picks are clicks on the mesh: Start or End must be a plane '
                         'or a flat face of a solid, it sets the slice direction')
    mesh_body = adsk.fusion.MeshBody.cast(sel.selection(0).entity)
    pl = _plane_of(ref, np)
    if mesh_body is None or pl is None:
        raise ValueError('pick a mesh body')
    origin_mm, normal = pl
    spacing_mm = adsk.core.ValueCommandInput.cast(inputs.itemById('spacing')).value * 10.0
    if spacing_mm <= 0:
        raise ValueError('the spacing must be positive')
    h0, how0 = pick_height(s0.selection(0), normal, np)
    h1, how1 = pick_height(s1.selection(0), normal, np)
    V, F = _cached_arrays(mesh_body, np)
    if len(F) == 0:
        raise ValueError('the mesh has no triangles')
    plan = xray.xray_plan(V, origin_mm, normal, h0, h1, spacing_mm)
    return plan, (how0, how1), ref


def _xray_info(inputs):
    """Refresh the X-Ray line: how many slices, between which offsets."""
    box = adsk.core.TextBoxCommandInput.cast(inputs.itemById('xray_info'))
    if box is None:
        return
    try:
        import numpy as np
        plan, hows, ref = xray_from_inputs(inputs, np)
        offs = plan['offsets_mm']
        n = len(offs)
        text = '{} slice{} from {:+.2f} to {:+.2f} mm off {} (start: {}, end: {})'.format(
            n, '' if n == 1 else 's', offs[0], offs[-1], getattr(ref, 'name', None) or 'the plane', *hows)
        if n > MAX_STACK:
            text += ' - over the limit of {}: raise the spacing'.format(MAX_STACK)
    except ValueError as e:
        text = str(e)
    except Exception as e:
        text = 'could not work out the stack: {}'.format(e)
    box.text = text


# ---------------------------------------------------------------- handlers

class _CommandCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            cmd.isRepeatable = False
            inputs = cmd.commandInputs
            md = inputs.addDropDownCommandInput('mode', 'Action',
                                                adsk.core.DropDownStyles.TextListDropDownStyle)
            md.listItems.add('Single slice', True)
            md.listItems.add('Stack (count + direction)', False)
            md.listItems.add('X-Ray (start \u2192 end)', False)
            md.tooltip = ('Single slice and Stack cut on a plane. X-Ray: pick where the stack '
                          'starts and ends (a plane, a flat face or a spot on the mesh)')
            sel = inputs.addSelectionInput('mesh_body', 'Mesh body', 'The mesh body to cut')
            sel.addSelectionFilter('MeshBodies')
            sel.setSelectionLimits(1, 1)
            pl = inputs.addSelectionInput('plane', 'Plane',
                                          'A construction plane or a flat face to cut on')
            pl.addSelectionFilter('ConstructionPlanes')
            pl.addSelectionFilter('PlanarFaces')
            pl.setSelectionLimits(1, 1)
            for iid, label, tip in (('xray_start', 'Start', 'Where the stack starts: a plane, a flat face or a spot on the mesh'),
                                    ('xray_end', 'End', 'Where the stack ends: a plane, a flat face or a spot on the mesh')):
                xs = inputs.addSelectionInput(iid, label, tip)
                xs.addSelectionFilter('ConstructionPlanes')
                xs.addSelectionFilter('PlanarFaces')
                xs.addSelectionFilter('MeshBodies')
                xs.setSelectionLimits(0, 1)
                xs.isVisible = False
            # value inputs are in internal units (cm)
            sp = inputs.addValueInput('spacing', 'Spacing', 'mm',
                                      adsk.core.ValueInput.createByReal(0.02))
            sp.isVisible = False
            ct = inputs.addIntegerSpinnerCommandInput('count', 'Sections', 2, MAX_STACK, 1, 10)
            ct.isVisible = False
            dd = inputs.addDropDownCommandInput('direction', 'Direction',
                                                adsk.core.DropDownStyles.TextListDropDownStyle)
            dd.listItems.add('Along the plane normal (+)', True)
            dd.listItems.add('Against the plane normal (-)', False)
            dd.listItems.add('Both sides', False)
            dd.isVisible = False
            info = inputs.addTextBoxCommandInput('xray_info', '', '', 2, True)
            info.isVisible = False
            inputs.addValueInput('tolerance', 'Fit tolerance', 'mm',
                                 adsk.core.ValueInput.createByReal(0.008))
            inputs.addValueInput('join', 'Join gaps up to', 'mm',
                                 adsk.core.ValueInput.createByReal(0.25))
            inputs.addValueInput('trim', 'Trim slivers up to', 'mm',
                                 adsk.core.ValueInput.createByReal(0.0))
            inputs.addBoolValueInput('outline', 'Outline only', True, '', False)
            td = inputs.addBoolValueInput('tidy', 'Tidy corners', True, '', False)
            td.tooltip = ('Fillets meet their walls tangent and corners meet exactly; the curves '
                          'share their end points and the fillets get tangent constraints')
            sh = inputs.addValueInput('sharpen', 'Sharpen rounds below', 'mm',
                                      adsk.core.ValueInput.createByReal(0.008))
            sh.tooltip = ('A round between two walls that stays within this of their corner is '
                          'drawn as the sharp corner (0 keeps every round)')
            sh.isVisible = False
            ex = inputs.addBoolValueInput('extrude', 'Extrude each slice', True, '', False)
            ex.isVisible = False
            rl = inputs.addBoolValueInput('rails', 'Add 2 rails (90\u00b0 sections)', True, '', False)
            rl.tooltip = ('Two cuts along the stack through the mesh centre, drawn as splines that '
                          'touch every slice\'s outline exactly (what a Loft needs)')
            rl.isVisible = False
            lf = inputs.addBoolValueInput('loft', 'Loft profiles + rails (new body)', True, '', False)
            lf.tooltip = ('After the sketches, loft one new body through the slices along the rails '
                          '(best effort; instead of Extrude each slice)')
            lf.isVisible = False
            lh = inputs.addBoolValueInput('loft_holes', 'Cut holes into the loft', True, '', True)
            lh.tooltip = ('Fusion\'s Loft fills holes: every hole that runs through two or more slices '
                          'is cut out of the lofted body by a loft of its own')
            lh.isVisible = False
            # the mesh picked last time may be gone or changed
            _mesh_cache.clear()
            inputs.addBoolValueInput('hide_mesh', 'Hide mesh after', True, '', False)
            inputs.addTextBoxCommandInput('about', '', _about_html(), 2, True)
            on_change = _InputChanged()
            cmd.inputChanged.add(on_change)
            _handlers.append(on_change)
            on_execute = _Execute()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)
        except Exception:
            _fail('Mesh to Outline: could not build the dialog')


class _InputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.inputs
            iid = args.input.id
            md = adsk.core.DropDownCommandInput.cast(inputs.itemById('mode'))
            mode = md.selectedItem.index if md is not None and md.selectedItem else MODE_ONE
            if iid == 'mode':
                show = MODE_INPUTS.get(mode, ())
                for k in ALL_MODE_INPUTS:
                    inputs.itemById(k).isVisible = k in show
                # a hidden pick lets go of its entity: Fusion would not take
                # the same plane again in the box that is showing now. Only
                # the picks on show are required (OK stays greyed without).
                for k in MODE_PICKS:
                    pick = adsk.core.SelectionCommandInput.cast(inputs.itemById(k))
                    if k not in show:
                        pick.clearSelection()
                    pick.setSelectionLimits(1 if k in show else 0, 1)
            if iid in ('mode', 'rails', 'loft'):
                rl = adsk.core.BoolValueCommandInput.cast(inputs.itemById('rails'))
                lf = adsk.core.BoolValueCommandInput.cast(inputs.itemById('loft'))
                inputs.itemById('loft').isVisible = mode != MODE_ONE and bool(rl.value)
                lh = inputs.itemById('loft_holes')
                if lh is not None:
                    lh.isVisible = mode != MODE_ONE and bool(rl.value) and bool(lf.value)
            if iid == 'tidy':
                td = adsk.core.BoolValueCommandInput.cast(inputs.itemById('tidy'))
                inputs.itemById('sharpen').isVisible = bool(td.value)
            if mode == MODE_XRAY and iid in ('mode', 'mesh_body', 'xray_start',
                                             'xray_end', 'spacing'):
                _xray_info(inputs)
        except Exception:
            _log('input handler error: ' + traceback.format_exc())


class _Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            self._run(args)
        except Exception:
            _fail('Mesh to Outline failed')

    def _run(self, args):
        err = _preflight()
        if err:
            _msg(err)
            return
        import numpy as np
        try:
            from mssengine import section_fit
        except Exception:
            _fail('The section engine could not be imported')
            return

        inputs = args.command.commandInputs
        sel = adsk.core.SelectionCommandInput.cast(inputs.itemById('mesh_body'))
        psel = adsk.core.SelectionCommandInput.cast(inputs.itemById('plane'))
        md = adsk.core.DropDownCommandInput.cast(inputs.itemById('mode'))
        mode = md.selectedItem.index if md is not None and md.selectedItem else MODE_ONE
        has_plane = psel is not None and psel.selectionCount > 0
        if sel is None or sel.selectionCount == 0 or (mode != MODE_XRAY and not has_plane):
            _msg('Select a mesh body and a plane (or flat face).')
            return
        tol_mm = adsk.core.ValueCommandInput.cast(inputs.itemById('tolerance')).value * 10.0
        join_mm = adsk.core.ValueCommandInput.cast(inputs.itemById('join')).value * 10.0
        trim_mm = adsk.core.ValueCommandInput.cast(inputs.itemById('trim')).value * 10.0
        outline = adsk.core.BoolValueCommandInput.cast(inputs.itemById('outline')).value
        tidy = bool(adsk.core.BoolValueCommandInput.cast(inputs.itemById('tidy')).value)
        sharpen_mm = adsk.core.ValueCommandInput.cast(inputs.itemById('sharpen')).value * 10.0
        sharpen_mm = min(max(sharpen_mm, 0.0), 1.0) if tidy else 0.0
        spacing_cm = adsk.core.ValueCommandInput.cast(inputs.itemById('spacing')).value
        count = adsk.core.IntegerSpinnerCommandInput.cast(inputs.itemById('count')).value
        dd = adsk.core.DropDownCommandInput.cast(inputs.itemById('direction'))
        direction = dd.selectedItem.index if dd is not None and dd.selectedItem else 0
        hide_mesh = adsk.core.BoolValueCommandInput.cast(inputs.itemById('hide_mesh')).value
        extrude = adsk.core.BoolValueCommandInput.cast(inputs.itemById('extrude')).value
        want_rails = bool(adsk.core.BoolValueCommandInput.cast(inputs.itemById('rails')).value)
        want_loft = bool(adsk.core.BoolValueCommandInput.cast(inputs.itemById('loft')).value)
        lh_in = inputs.itemById('loft_holes')
        want_holes = bool(adsk.core.BoolValueCommandInput.cast(lh_in).value) if lh_in is not None else True
        if tol_mm <= 0:
            _msg('The fit tolerance must be positive.')
            return
        if join_mm < 0:
            join_mm = 0.0
        if trim_mm < 0:
            trim_mm = 0.0
        if mode == MODE_ONE:
            count, spacing_cm, extrude = 1, 0.0, False
        elif spacing_cm <= 0:
            _msg('The spacing must be positive.')
            return
        # each sketch's offset from the picked plane (cm) and how far it is
        # extruded along the plane normal (cm, None = drawn only)
        xray_note = None
        if mode == MODE_XRAY:
            try:
                plan, hows, plane_ent = xray_from_inputs(inputs, np)
            except ValueError as e:
                _msg('X-Ray: {}.'.format(e))
                return
            offs_cm = [o / 10.0 for o in plan['offsets_mm']]
            count = len(offs_cm)
            if count > MAX_STACK:
                _msg('X-Ray: {} slices is over the limit of {}; raise the spacing.'.format(count, MAX_STACK))
                return
            exts_cm = [e / 10.0 if e else None for e in plan['extrude_mm']]
            xray_note = 'X-Ray from {:+.2f} to {:+.2f} mm (start: {}, end: {})'.format(
                plan['offsets_mm'][0], plan['offsets_mm'][-1], *hows)
        else:
            # +, -, or both sides of the picked plane
            if direction == 1:
                steps = [-k for k in range(count)]
            elif direction == 2:
                half = count // 2
                steps = list(range(-half, count - half))
            else:
                steps = list(range(count))
            offs_cm = [k * spacing_cm for k in steps]
            ext_sign = -1.0 if direction == 1 else 1.0
            exts_cm = [ext_sign * spacing_cm] * count
        rails = want_rails and mode != MODE_ONE and count > 1
        loft = rails and want_loft
        if loft:
            extrude = False                  # one or the other
        if not extrude:
            exts_cm = [None] * count

        mesh_body = adsk.fusion.MeshBody.cast(sel.selection(0).entity)
        if mesh_body is None:
            _msg('The selection is not a mesh body.')
            return
        if mode != MODE_XRAY:
            plane_ent = psel.selection(0).entity
        geom = adsk.core.Plane.cast(plane_ent.geometry)
        if geom is None:
            _msg('The selected entity is not planar.')
            return
        o = geom.origin
        nv = geom.normal
        normal = np.array([nv.x, nv.y, nv.z], float)
        normal /= np.linalg.norm(normal)
        origin_cm = np.array([o.x, o.y, o.z], float)

        design = adsk.fusion.Design.cast(_app.activeProduct)
        root = design.rootComponent
        V, F = mesh_arrays(mesh_body, np)
        if len(F) == 0:
            _msg('{}: the mesh has no triangles.'.format(mesh_body.name))
            return
        plane_name = getattr(plane_ent, 'name', None) or 'plane'
        _log('{}: {} triangles, plane {} at ({:.3f}, {:.3f}, {:.3f}) cm normal ({:.3f}, {:.3f}, {:.3f}), '
             'tol {:.3f} mm, join {:.2f} mm, {} section(s) every {:.2f} mm, {:+.2f}..{:+.2f} mm'.format(
                 mesh_body.name, len(F), plane_name, *origin_cm, *normal, tol_mm, join_mm,
                 count, spacing_cm * 10.0, offs_cm[0] * 10.0, offs_cm[-1] * 10.0))

        pd = None
        if count > 1:
            try:
                pd = _ui.createProgressDialog()
                pd.isCancelButtonShown = True
                pd.show(CMD_NAME, 'Section 1 of {}'.format(count), 0, count)
            except Exception:
                pd = None
        t0 = time.time()
        lines, made, empty, cancelled = [], 0, 0, False
        # extrude each slice towards the next one, joined into one body: a
        # stepped solid that Fusion builds without fail, where Loft between
        # two complex profiles folds over
        ext_bodies, ext_seen, n_ext, unmatched = [], set(), 0, 0
        drawn = {}                       # curves Fusion refused, redrawn as lines
        failed = []                      # sketches whose slab could not be extruded
        slice_stats = []                 # (offset label, stats) per cut, for the open-outline warning
        pre, rail_list, drawn_secs, rail_notes = None, [], [], []
        lifted, sketch_at = [None] * len(offs_cm), {}     # per slice index: for the loft's holes
        try:
            if rails:
                # every slice first, then the rails (they add their crossings
                # to the slices' spline fit points), then the drawing
                pre = []
                for i, off_cm in enumerate(offs_cm):
                    if pd is not None:
                        pd.message = 'Tracing {} of {}'.format(i + 1, count)
                        pd.progressValue = i
                        adsk.doEvents()
                        if pd.wasCancelled:
                            cancelled = True
                            break
                    origin_mm = (origin_cm + normal * off_cm) * 10.0
                    pre.append(section_fit.section_preview_2d(
                        V, F, origin_mm, normal, tol=tol_mm, join_mm=join_mm, closed_only=outline,
                        trim_mm=trim_mm, tidy=tidy, sharpen_mm=sharpen_mm))
                if not cancelled:
                    from mssengine import rails as rails_mod
                    centre_mm = (V.min(axis=0) + V.max(axis=0)) / 2.0
                    rail_list = rails_mod.trace_rails(
                        V, F, normal, centre_mm, [s for s in pre if s['loops']], tol=tol_mm,
                        labels=['{}/{}'.format(i + 1, count) for i, s in enumerate(pre) if s['loops']])
            for i, off_cm in enumerate(offs_cm):
                if cancelled:
                    break
                if pd is not None:
                    pd.message = 'Section {} of {}'.format(i + 1, count)
                    pd.progressValue = i
                    adsk.doEvents()
                    if pd.wasCancelled:
                        cancelled = True
                        break
                origin_mm = (origin_cm + normal * off_cm) * 10.0
                if pre is not None:
                    sec = section_fit.lift_section(pre[i])
                else:
                    sec = section_fit.section_preview(V, F, origin_mm, normal, tol=tol_mm, join_mm=join_mm,
                                                      closed_only=outline, trim_mm=trim_mm, tidy=tidy,
                                                      sharpen_mm=sharpen_mm)
                slice_stats.append(('{:+.2f} mm'.format(off_cm * 10.0), sec['stats']))
                if not sec['loops'] and not sec['open']:
                    empty += 1
                    continue
                kind = 'xray' if mode == MODE_XRAY else 'section'
                if abs(off_cm) < 1e-9:
                    plane = plane_ent
                else:
                    inp = root.constructionPlanes.createInput()
                    inp.setByOffset(plane_ent, adsk.core.ValueInput.createByReal(off_cm))
                    plane = root.constructionPlanes.add(inp)
                    plane.name = '{} plane {:+.2f} mm'.format(kind, off_cm * 10.0)
                sk = root.sketches.add(plane)
                if mode == MODE_XRAY:
                    sk.name = 'xray {} {:+.2f} mm ({}/{})'.format(plane_name, off_cm * 10.0, i + 1, count)
                else:
                    sk.name = 'section {} {:+.2f} mm'.format(plane_name, off_cm * 10.0)
                sk.isComputeDeferred = True
                try:
                    keys = ('arcs_as_lines', 'splines_as_lines', 'skipped')
                    before = {k: drawn.get(k, 0) for k in keys}
                    n = draw_section(sk, sec, drawn, tidy=tidy)
                    if any(drawn.get(k, 0) != before[k] for k in keys):
                        _log('{}: Fusion refused {} arc(s), {} spline(s): drawn as lines; {} '
                             'tiny curve(s) left out'.format(
                                 sk.name, drawn.get('arcs_as_lines', 0) - before.get('arcs_as_lines', 0),
                                 drawn.get('splines_as_lines', 0) - before.get('splines_as_lines', 0),
                                 drawn.get('skipped', 0) - before.get('skipped', 0)))
                finally:
                    sk.isComputeDeferred = False
                made += 1
                drawn_secs.append((sk, sec))
                lifted[i], sketch_at[i] = sec, sk
                if exts_cm[i] is not None:
                    try:
                        areas = [a / 100.0 for a in sec.get('areas_mm2', [])]
                        _, ok = _extrude_slice(root, sk, exts_cm[i], ext_bodies,
                                               ext_seen, areas)
                        n_ext += 1
                        if not ok:
                            unmatched += 1
                    except Exception:
                        why = traceback.format_exc().strip().splitlines()[-1]
                        _log('{}: extrude failed: {}'.format(sk.name, why))
                        lines.append('{}: extrude failed: {}'.format(sk.name, why))
                        failed.append(sk.name)
                line = '{}: {} ({} curves)'.format(sk.name, stats_line(sec['stats'], outline), n)
                lines.append(line)
                _log(line)
        finally:
            if pd is not None:
                try:
                    pd.hide()
                except Exception:
                    pass
        if rails and not cancelled:
            chains = []
            if loft and want_holes:
                from mssengine import rails as rails_mod
                chains = rails_mod.hole_chains(lifted)
            rail_notes = _rails_and_loft(root, plane_ent, normal, rail_list, drawn_secs, loft, np,
                                         holes=chains, sketch_at=sketch_at)
        if made and hide_mesh:
            try:
                mesh_body.isLightBulbOn = False
            except Exception:
                pass
        head = '{} sketch{} in {:.1f} s'.format(made, '' if made == 1 else 'es', time.time() - t0)
        if extrude:
            live = _live(ext_bodies)
            how_far = 'to the next slice' if mode == MODE_XRAY else '{:.2f} mm'.format(spacing_cm * 10.0)
            head += ', {} of them extruded {} into {} {}'.format(
                n_ext, how_far, len(live), 'body' if len(live) == 1 else 'bodies')
            if unmatched:
                head += ' ({} slice{} with no profile matching the expected area: every profile ' \
                        'extruded there, holes filled)'.format(unmatched, '' if unmatched == 1 else 's')
        if empty:
            head += ', {} empty cut{} skipped'.format(empty, '' if empty == 1 else 's')
        open_note = section_fit.open_outline_note(slice_stats, join_mm)
        if open_note:
            head = 'WARNING: ' + open_note + '\n\n' + head
        if failed:
            # said up top: a missing slab is a gap in the solid, easy to miss
            # in the per-slice list below
            head = 'WARNING: {} slab{} could not be extruded (gaps in the solid): {}{}\n\n'.format(
                len(failed), '' if len(failed) == 1 else 's', ', '.join(failed[:6]),
                ', ...' if len(failed) > 6 else '') + head
        fixed = drawn.get('arcs_as_lines', 0) + drawn.get('splines_as_lines', 0)
        if fixed:
            head += '\n{} near-straight arc{}/spline{} Fusion refused were drawn as short lines ' \
                    'through the same points'.format(fixed, '' if fixed == 1 else 's', '' if fixed == 1 else 's')
        if drawn.get('skipped'):
            head += '\n{} tiny curve{} Fusion refused left out'.format(drawn['skipped'], '' if drawn['skipped'] == 1 else 's')
        if tidy:
            head += '\nTidy corners: {} tangent constraint{} added'.format(
                drawn.get('tangent', 0), '' if drawn.get('tangent', 0) == 1 else 's')
            if drawn.get('tangent_failed'):
                head += ', {} could not be added'.format(drawn['tangent_failed'])
        for note in rail_notes:
            if note.startswith('WARNING'):
                head = note + '\n\n' + head
            else:
                head += '\n' + note
        if xray_note:
            head += '\n' + xray_note
        if cancelled:
            head = 'cancelled: ' + head
        if len(lines) > 40:
            lines = lines[:40] + ['... ({} more in the Text Commands palette)'.format(len(lines) - 40)]
        _msg(head + '\n\n' + '\n'.join(lines))


# ------------------------------------------------------- start / stop

def _all_panels():
    panels = _ui.allToolbarPanels
    out = []
    for i in range(panels.count):
        try:
            out.append(panels.item(i))
        except Exception:
            pass
    return out


def _mesh_modify_panels():
    """The Mesh tab's Modify panel, best guess first: the known ids, then
    any panel that holds Convert Mesh, then any panel whose id says mesh
    and modify. Fusion renames panels between builds (2705.1.30 lost the
    button to Utilities > Add-Ins), so the ids alone are not enough."""
    panels = _ui.allToolbarPanels
    out, seen = [], set()

    def add(panel):
        if panel is not None and panel.id not in seen:
            seen.add(panel.id)
            out.append(panel)

    for pid in MESH_MODIFY_PANEL_IDS:
        try:
            add(panels.itemById(pid))
        except Exception:
            pass
    every = _all_panels()
    for panel in every:
        try:
            if panel.controls.itemById(MESH_CONVERT_CMD_ID) is not None:
                add(panel)
        except Exception:
            pass
    for panel in every:
        pid = (panel.id or '').lower()
        if 'mesh' in pid and 'modify' in pid:
            add(panel)
    return out


def _place_control(cmd_def):
    """Mesh tab > Modify, right after Convert Mesh; else Utilities > Add-Ins
    (and the mesh panel ids this Fusion has go to the Text Commands
    palette, so the next fix knows what to look for)."""
    for panel in _mesh_modify_panels():
        pid = panel.id
        try:
            control = panel.controls.addCommand(cmd_def, MESH_CONVERT_CMD_ID, False)
            where = '{} (after {})'.format(pid, MESH_CONVERT_CMD_ID)
        except Exception:
            control = None
        if control is None:
            try:
                control = panel.controls.addCommand(cmd_def)
                where = pid
            except Exception:
                control = None
        if control is not None:
            try:
                control.isPromoted = True
                control.isPromotedByDefault = True
            except Exception:
                pass
            return control, where
    mesh_ids = [p.id for p in _all_panels() if 'mesh' in (p.id or '').lower()]
    _log('no Mesh > Modify panel found; mesh panels in this Fusion: {}'.format(
        ', '.join(mesh_ids) or 'none'))
    panel = _ui.allToolbarPanels.itemById(FALLBACK_PANEL_ID)
    if panel is None:
        raise RuntimeError('no toolbar panel found for the command')
    return panel.controls.addCommand(cmd_def), FALLBACK_PANEL_ID


def start(context):
    global _app, _ui
    _app = adsk.core.Application.get()
    _ui = _app.userInterface
    try:
        old = _ui.commandDefinitions.itemById(CMD_ID)
        if old is not None:
            old.deleteMe()
        cmd_def = _ui.commandDefinitions.addButtonDefinition(
            CMD_ID, CMD_NAME, CMD_TOOLTIP, RESOURCES if os.path.isdir(RESOURCES) else '')
        on_created = _CommandCreated()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)
        _state['cmd_def'] = cmd_def
        control, where = _place_control(cmd_def)
        _state['control'] = control
        _state['panel_id'] = where
        _log('command placed in panel ' + where)
    except Exception:
        _fail('MeshSectionSketch could not register its command')


def stop(context):
    control = _state.pop('control', None)
    if control is not None:
        try:
            control.deleteMe()
        except Exception:
            pass
    cmd_def = _state.pop('cmd_def', None)
    if cmd_def is None and _ui is not None:
        cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def is not None:
        try:
            cmd_def.deleteMe()
        except Exception:
            pass
    _handlers.clear()
