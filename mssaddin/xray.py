"""X-Ray: a stack of sections from a start height to an end height along
the picked plane's normal, the rule the stlToSolid web app's X-Ray uses.

numpy only (no adsk), so the desktop tests can run it. Heights are in mm
along the unit normal `n`, measured from the model origin (p . n).
"""
import numpy as np

from mssengine.section_fit import STACK_EDGE_MM, stack_extrudes, stack_offsets

# a triangle this close to square-on to the slice direction is a flat face
FLAT_COS = 0.999
# a cut exactly on a face misses the material on one side of it (the
# cutter counts a vertex on the plane as above it), so a flat pick is
# moved this far into the material, against the face's outward normal
INSIDE_MM = 0.01


def pick_height(V, F, point_mm, normal):
    """Where a plane across `normal` goes for a click at `point_mm` on the
    mesh (V mm, F faces): (height mm, flat). The triangle under the click
    is found; a triangle flat across the direction gives its own height,
    INSIDE_MM into the material; any other gives the clicked point's
    height. On an edge between a flat face and a wall the flat face wins."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    p = np.asarray(point_mm, float)
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    N = np.cross(B - A, C - A)
    L = np.linalg.norm(N, axis=1)
    ok = L > 1e-12
    N[ok] /= L[ok, None]
    dist = np.abs(np.einsum('ij,ij->i', p - A, N))
    # barycentric coordinates of the click projected onto each triangle
    e0, e1, w = B - A, C - A, p - A
    d00 = np.einsum('ij,ij->i', e0, e0)
    d01 = np.einsum('ij,ij->i', e0, e1)
    d11 = np.einsum('ij,ij->i', e1, e1)
    d20 = np.einsum('ij,ij->i', w, e0)
    d21 = np.einsum('ij,ij->i', w, e1)
    den = d00 * d11 - d01 * d01
    good = ok & (np.abs(den) > 1e-18)
    v = np.where(good, (d11 * d20 - d01 * d21) / np.where(good, den, 1), -1)
    u = np.where(good, (d00 * d21 - d01 * d20) / np.where(good, den, 1), -1)
    inside = good & (v >= -1e-6) & (u >= -1e-6) & (u + v <= 1 + 1e-6)
    cos = N @ n
    cand = np.flatnonzero(inside)
    if len(cand):
        best = dist[cand].min()
        near = cand[dist[cand] <= best + 1e-6]
        flat_near = near[np.abs(cos[near]) > FLAT_COS]
        t = int(flat_near[0]) if len(flat_near) else int(near[np.argmin(dist[near])])
    else:
        # the click is off every triangle (a display mesh that differs a
        # little): take the nearest triangle centre
        t = int(np.argmin(np.linalg.norm((A + B + C) / 3.0 - p, axis=1)))
    c = float(cos[t])
    if abs(c) > FLAT_COS:
        h = float(np.mean([A[t] @ n, B[t] @ n, C[t] @ n]))
        return h - np.sign(c) * INSIDE_MM, True
    return float(p @ n), False


def xray_plan(V, origin_mm, normal, h_start, h_end, spacing_mm):
    """The stack from height `h_start` to `h_end` every `spacing_mm` (mm
    along `normal`; swapped if reversed), clamped just inside the mesh.
    Returns {'offsets_mm': each plane's offset from the picked plane
    (along its normal), 'extrude_mm': each slab's length towards the next
    plane (None = drawn only), 'heights_mm': the planes' heights, 'lo',
    'hi': the mesh's extent}."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    d = np.asarray(V, float) @ n
    lo, hi = float(d.min()), float(d.max())
    c, half = (lo + hi) / 2.0, (hi - lo) / 2.0
    offs = stack_offsets(h_start - c, h_end - c, spacing_mm, half)
    ats = [c + o for o in offs]
    base = float(np.asarray(origin_mm, float) @ n)
    return {'offsets_mm': [a - base for a in ats],
            'extrude_mm': stack_extrudes(ats, spacing_mm, hi - STACK_EDGE_MM),
            'heights_mm': ats, 'lo': lo, 'hi': hi}
