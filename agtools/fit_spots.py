#!/usr/bin/env python3
r"""Fit where a DLC puts its character, for sequences whose camera is in stage space.

    python agtools/fit_spots.py 109502 --grep night      -> AG_fbx_anim/1095/cameras/109502.spots.json

Some DLCs (109502's room, 109503's beach and aisle) author their cameras in the
stage's own coordinates and let game code stand the character at a spot - a
position and a heading that no bundle states. Her clips play at the origin, so
exported as they are the camera films an empty room.

The camera knows where she is: its Composer aims at a look-at target authored to
ride her head, and it films her from the front. So per sequence the fit runs her
head along the merged clip (forward kinematics over the rig's bind pose) and finds
the heading and ground position that
    1. keep the look-at target on her head (position, least squares), and
    2. put the camera in front of her face (heading, among the headings that
       satisfy 1 about equally well - a still pose alone cannot tell them apart).
Sequences that share a spot share one anchor - the median of their fits - so she
does not step between them. A sequence whose own placement track already moves her
(the night debut) is left to it, and its anchor is printed as the check.

The result is read by export_cameras (`<skin>.spots.json` beside the camera JSON):
a sequence with no placement track of its own takes its spot's anchor. A sequence
whose look target jumps further than --spot-radius at a cut changes spot there; each
part is fitted on its own and written as [from t, x, y, z, yaw]. A sequence whose look
target stays within --origin-radius of the origin films her where she stands (character
space, like most skins) and is left out.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_cameras as ec  # noqa: E402
from unity_yaml import evaluate, parse_anim, parse_prefab, resolve_hashed_paths  # noqa: E402

HEAD = "Bip001 Head"


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz])


def qrot(q, v):
    r = qmul(qmul(q, np.array([v[0], v[1], v[2], 0.0])), np.array([-q[0], -q[1], -q[2], q[3]]))
    return r[:3]


def chain_to(nodes, name):
    head = next(n for n in nodes.values() if n.name == name)
    out = []
    n = head
    while n is not None and n.parent is not None:
        out.append(n)
        n = n.parent
    out.reverse()
    paths, acc = [], []
    for n in out:
        acc.append(n.name)
        paths.append("/".join(acc))
    return out, paths


def head_track(clip, chain, paths, times):
    """Head position and facing (object space) at each time."""
    pos, fwd = [], []
    bind_q = np.array([0.0, 0.0, 0.0, 1.0])
    for n in chain:
        bind_q = qmul(bind_q, np.array(n.rot))
    face_local = qrot(np.array([-bind_q[0], -bind_q[1], -bind_q[2], bind_q[3]]), (0.0, 0.0, 1.0))
    for t in times:
        p = np.zeros(3)
        q = np.array([0.0, 0.0, 0.0, 1.0])
        for n, path in zip(chain, paths):
            lp = evaluate(clip.position[path], t, 3) if path in clip.position else n.pos
            lq = evaluate(clip.rotation[path], t, 4) if path in clip.rotation else n.rot
            lq = np.array(lq) / (np.linalg.norm(lq) or 1.0)
            p = p + qrot(q, lp)
            q = qmul(q, lq)
        pos.append(p)
        fwd.append(qrot(q, face_local))
    return np.array(pos), np.array(fwd)


def fit(head, face, look, cam):
    """(x, y, z, yaw deg) placing the head path under the look target, facing the camera."""
    best = []
    for deg in range(0, 360, 2):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])        # Unity yaw about +Y
        h = head @ R.T
        t = (look - h).mean(axis=0)
        resid = np.linalg.norm(h + t - look, axis=1).mean()
        f = face @ R.T
        to_cam = cam - (h + t)
        to_cam /= np.linalg.norm(to_cam, axis=1, keepdims=True)
        facing = float((f * to_cam).sum(axis=1).mean())
        best.append((resid, facing, deg, t))
    floor = min(b[0] for b in best)
    ok = [b for b in best if b[0] <= floor + 0.08]
    resid, facing, deg, t = max(ok, key=lambda b: b[1])
    return {"x": float(t[0]), "y": float(t[1]), "z": float(t[2]), "yaw": float(deg),
            "residual": float(resid), "facing": facing}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skin")
    ap.add_argument("--grep", default="", help="only sequences whose name contains this")
    ap.add_argument("--spot-radius", type=float, default=1.5, help="look targets this close share a spot (m)")
    ap.add_argument("--max-residual", type=float, default=0.4,
                    help="a sequence the look target leaves further than this (m) is a walk, not a spot: left unplaced")
    ap.add_argument("--origin-radius", type=float, default=1.0,
                    help="a sequence whose look target stays this close to the origin (m) is in character space")
    args = ap.parse_args()
    skin, cid = args.skin, args.skin[:4]
    cams = os.path.join(ec.ANIM_OUT, cid, "cameras")
    merged = os.path.join(ec.CACHE, "_merged", f"{skin}_dlc")
    _, prefab = ec.ui_rig(skin, cid)
    nodes = parse_prefab(prefab)
    chain, paths = chain_to(nodes, HEAD)
    all_paths = set()
    for n in nodes.values():
        acc, x = [], n
        while x is not None and x.parent is not None:
            acc.append(x.name)
            x = x.parent
        if acc:
            all_paths.add("/".join(reversed(acc)))

    fits, origin = {}, set()
    for js in sorted(glob.glob(os.path.join(cams, f"{skin}@*.camera.json"))):
        stem = os.path.basename(js)[:-len(".camera.json")]
        seq = stem.split("@", 1)[1]
        if args.grep not in seq:
            continue
        d = json.load(open(js, encoding="utf-8"))
        anim = os.path.join(merged, f"{stem}.anim")
        frames = [f for f in d["frames"] if f.get("look_at")]
        if not frames or not os.path.isfile(anim):
            continue
        # a look target that never leaves the timeline root is filming her where she
        # stands, in character space (128402's home-scene debut / touch1 / touch2 beside
        # its wedding-stage sequences): nothing to place
        if max(math.hypot(f["look_at"][0], f["look_at"][2]) for f in frames) < args.origin_radius:
            print(f"{seq}: look target within {args.origin_radius} m of the origin - character space, left unplaced")
            origin.add(seq)
            continue
        clip = parse_anim(anim)
        resolve_hashed_paths(clip, all_paths)
        # a sequence can move her between spots behind a cut (104903 wedding_touch_102:
        # 6.7 s at one, the rest 5.5 m away) - the look target jumps; fit each part alone
        cuts = [i for i in range(1, len(frames))
                if np.linalg.norm(np.subtract(frames[i]["look_at"], frames[i - 1]["look_at"])) > args.spot_radius]
        parts = [frames[a:b] for a, b in zip([0] + cuts, cuts + [len(frames)])]
        for k, part in enumerate(parts, 1):
            pick = part[:: max(1, len(part) // 40)]
            times = [f["t"] for f in pick]
            head, face = head_track(clip, chain, paths, times)
            if d.get("placement_source"):   # the merged clip already carries a fitted spot: undo it
                for j, t in enumerate(times):
                    (p, q) = d["placement"][min(int(round(t * ec.FPS)), len(d["placement"]) - 1)]
                    inv = np.array([-q[0], -q[1], -q[2], q[3]])
                    head[j] = qrot(inv, head[j] - np.array(p))
                    face[j] = qrot(inv, face[j])
            look = np.array([f["look_at"] for f in pick])
            cam = np.array([f["position"] for f in pick])
            r = fit(head, face, look, cam)
            # she changes spot with the clip change just before the cut (wedding_touch_102: clip
            # at 6.667 s, cut at 6.767 s), so her pose and her place switch together
            t0 = part[0]["t"] if k > 1 else 0.0
            starts = [s["start"] for s in d["character_schedule"] if t0 - 0.5 <= s["start"] <= t0 + 1e-6]
            r["seq"], r["t0"] = seq, (max(starts) if starts and k > 1 else t0)
            own = d.get("placement") if not d.get("placement_source") else None   # not a fit's own earlier guess
            r["own_placement"] = bool(own)
            r["look"] = look.mean(axis=0).tolist()
            r["_head"], r["_look"] = head, look
            r["_anchor"] = own[0] if own else None
            fits[seq if len(parts) == 1 else f"{seq}#{k}"] = r

    # spots: sequences whose look targets sit together
    spots = []
    for seq, r in sorted(fits.items()):
        here = np.array(r["look"])[[0, 2]]
        for sp in spots:
            if np.linalg.norm(np.array(sp["centre"]) - here) < args.spot_radius:
                sp["members"].append(seq)
                break
        else:
            spots.append({"centre": here.tolist(), "members": [seq]})
    def rot(deg):
        a = math.radians(deg)
        c, s_ = math.cos(a), math.sin(a)
        return np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])

    def place(seq, deg):
        r = fits[seq]
        return (r["_look"] - r["_head"] @ rot(deg).T).mean(axis=0)

    # CALIBRATION against a spot the game does place: a sequence with its own constant
    # placement shares its spot with fitted ones, which fixes the two things the fit
    # gets systematically wrong - the heading (shots frame her three-quarters on, so
    # "the camera is in front of her" leans one way) and the look target's offset from
    # her head.
    bias, offset = 0.0, np.zeros(3)
    for sp in spots:
        truth = next((fits[s]["_anchor"] for s in sp["members"] if fits[s]["_anchor"]), None)
        mem = [s for s in sp["members"] if not fits[s]["own_placement"] and fits[s]["residual"] <= args.max_residual]
        if truth and mem:
            q = truth[1]
            t_yaw = math.degrees(2 * math.atan2(q[1], q[3]))
            f_yaw = math.degrees(math.atan2(np.mean([math.sin(math.radians(fits[s]["yaw"])) for s in mem]),
                                            np.mean([math.cos(math.radians(fits[s]["yaw"])) for s in mem])))
            bias = f_yaw - t_yaw
            fitted = np.median([place(s, t_yaw) for s in mem], axis=0)
            offset = rot(-t_yaw) @ (np.array(truth[0]) - fitted)
            print(f"calibrated on {', '.join(mem)}: heading bias {bias:+.0f} deg, look offset {np.round(offset, 2)}")
            break

    out = {}
    for k, sp in enumerate(spots, 1):
        mem = [s for s in sp["members"] if not fits[s]["own_placement"] and fits[s]["residual"] <= args.max_residual]
        if not mem:
            print(f"spot {k}: {', '.join(sp['members'])} - left unplaced (a walk, or placed by its own track)")
            continue
        truth = next((fits[s]["_anchor"] for s in sp["members"] if fits[s]["_anchor"]), None)
        if truth:                                          # the game's own anchor
            q = truth[1]
            yaw = math.degrees(2 * math.atan2(q[1], q[3]))
            xs = np.array(truth[0])
        else:
            yaws = np.array([fits[s]["yaw"] for s in mem])
            yaw = math.degrees(math.atan2(np.sin(np.radians(yaws)).mean(), np.cos(np.radians(yaws)).mean())) - bias
            xs = np.median([place(s, yaw) for s in mem], axis=0) + rot(yaw) @ offset
        print(f"spot {k}: ({xs[0]:.2f}, {xs[1]:.2f}, {xs[2]:.2f}) yaw {yaw:.0f}  from {len(mem)} sequence(s)")
        for s in sp["members"]:
            r = fits[s]
            tag = "  (own placement track)" if r["own_placement"] else ""
            print(f"    {s:34s} fit ({r['x']:.2f}, {r['y']:.2f}, {r['z']:.2f}) yaw {r['yaw']:.0f}"
                  f"  residual {r['residual']:.2f} m  facing {r['facing']:+.2f}{tag}")
            if not r["own_placement"]:       # every sequence at the spot, not only the ones it was fitted on
                anchor = [round(float(xs[0]), 4), round(float(xs[1]), 4), round(float(xs[2]), 4), round(yaw, 2)]
                if s == r["seq"]:
                    out[s] = anchor
                else:                        # a moving sequence: [from t, x, y, z, yaw] per part
                    out.setdefault(r["seq"], []).append([round(r["t0"], 5)] + anchor)
    for v in out.values():
        if isinstance(v[0], list):
            v.sort()
    # sequences not fitted this run (--grep, or a merged clip `cams --only` did not rebuild)
    # keep their spot from the last run
    path = os.path.join(cams, f"{skin}.spots.json")
    fitted = {r["seq"] for r in fits.values()}
    kept = {k: v for k, v in (json.load(open(path)) if os.path.isfile(path) else {}).items() if k not in fitted | origin}
    if kept:
        print(f"kept from the last run: {', '.join(sorted(kept))}")
    json.dump({**kept, **out}, open(path, "w"), indent=1)
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
