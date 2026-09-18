#!/usr/bin/env python3
r"""
Bind AetherGazer AnimationClips onto their rigged FBX and write one animated FBX per clip.

Why this exists
---------------
AssetStudioMod exports the models beautifully (skinned mesh, ~180-bone Biped, textures)
but cannot bind this game's clips - `-m animator --fbx-animation all` on 1044 produces
FBX files that import into Blender with zero armatures, zero meshes and zero actions.
AssetRipper, conversely, recovers every clip as readable generic Unity YAML but cannot
write FBX. This script is the join: AssetStudioMod's rig + AssetRipper's curves.

How it works
------------
The clips store absolute parent-local TRS per bone in Unity's left-handed Y-up frame.
The FBX has passed through two coordinate conversions (AssetStudio's export, Blender's
import), so rather than assume what came out the far end, the basis change C is *solved*:
all 48 signed axis permutations are scored against the prefab's rest pose versus the
armature's rest pose, and the best is used only if its residual is negligible. That fit
doubles as a check that the rig and the clips really belong to the same skeleton.

With C known, each bone's pose is

    matrix_basis = rest_local^-1 @ (C @ local_unity(t) @ C^-1)

for bones with a parent (a whole-armature offset cancels in the parent-relative form),
and the delta form C @ (rest_unity^-1 @ local_unity(t)) @ C^-1 for the root bone, whose
rest matrix is the one place that offset survives.

Usage
-----
  python ag.py anim 1095                       # every clip set for 1095
  python ag.py anim 1095 --clips stand,attack1 # just these
  python ag.py anim 1095 --dry-run             # fit + coverage report, no export
  python ag.py anim 1095 --costume 109500      # one costume only

Needs the rig (ag.py rig) and the prefab (ag.py prefab) first; `ag.py build`
does all three in order. Run with system Python; it re-launches itself inside
Blender.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

def find_blender() -> str:
    """Newest installed Blender. Hardcoding a version silently breaks on upgrade -
    3.6 vanished from under this script when 5.2 replaced it."""
    import glob
    pats = [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                         "Blender Foundation", "Blender *", "blender.exe"),
            os.path.expanduser(os.path.join("~", "Downloads", "blender-*", "blender.exe")),
            os.path.expanduser(os.path.join("~", "Downloads", "blender-*", "*", "blender.exe"))]
    cands = []
    for pat in pats:
        cands += glob.glob(pat)

    def version(path):
        m = re.search(r"[Bb]lender[ -](\d+)\.(\d+)", path)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    # Blender 4.4 replaced Actions with the slotted system: `action.fcurves` and
    # `action.groups` are gone, and this exporter writes curves through both. Prefer
    # the newest release that still has the legacy API, and only fall back to a newer
    # one (which will fail loudly) when nothing else is installed.
    legacy = [c for c in cands if version(c) < (4, 4)]
    return max(legacy or cands, key=version) if cands else ""


BLENDER = find_blender()
ANIM_ROOT = r"C:\AetherGazerStarter\AG_animations\ComChar\ArtResources\Char\Hero"
SAMPLE_ROOT = r"C:\AetherGazerStarter\AG_sample"
FBX_ROOT = r"C:\AetherGazerStarter\AG_fbx"
OUT_ROOT = r"C:\AetherGazerStarter\AG_fbx_anim"

# Clip folders are <char><costume>[suffix]; each maps to the tpose model it animates.
# 'display' (showcase), 'capture' (photo mode) and 'dorm' clips all reuse the battle
# rig - those prefabs are the same skeleton.
SUFFIX_TO_MODEL = {"": "{cid}{cos}_tpose", "ui": "{cid}{cos}ui_tpose",
                   "display": "{cid}{cos}_tpose", "capture": "{cid}{cos}_tpose",
                   "dorm": "{cid}{cos}_tpose"}


# --------------------------------------------------------------------------- launcher

def find_model_fbx(cid: str, stem: str) -> str | None:
    """Locate an AssetStudioMod export named <stem>.fbx anywhere under AG_fbx."""
    for root in (os.path.join(FBX_ROOT, cid), FBX_ROOT):
        if not os.path.isdir(root):
            continue
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.lower() == f"{stem}.fbx".lower():
                    return os.path.join(dp, fn)
    return None


def find_prefab(cid: str, stem: str) -> str | None:
    base = os.path.join(SAMPLE_ROOT, cid, "ExportedProject", "Assets")
    if not os.path.isdir(base):
        return None
    for dp, _, fns in os.walk(base):
        for fn in fns:
            if fn.lower() == f"{stem}.prefab".lower():
                return os.path.join(dp, fn)
    return None


# Sets that share a model stem need their filenames disambiguated or they overwrite.
# 'display', 'capture' and 'dorm' all reuse the battle rig AND ship pose0..pose7, so
# 1022 silently lost 10 clips that way; 'ui' is safe because its stem already differs.
_SHARED_STEM_SUFFIXES = ("display", "capture", "dorm")


def _name_prefix(entry: dict, suffix: str) -> str:
    if entry.get("extra"):
        return f"{entry['context']}_"
    return f"{suffix}_" if suffix in _SHARED_STEM_SUFFIXES else ""


def plan(cid: str, costume_filter: str | None, clip_filter: set[str] | None,
         include_extra: bool = True, clip_regex: str | None = None) -> list[dict]:
    """Group a character's clips into (model fbx, prefab, clip list) jobs."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import clip_sets

    sets = clip_sets.main_sets(cid)
    # Costume DLC poses live outside the hero tree; they animate the same rig.
    # Some characters - NPC/boss models especially - have *only* those, so the
    # hero tree being empty is not on its own an error.
    if include_extra:
        sets += clip_sets.extra_sets(cid)
    if not sets:
        sys.exit(f"error: no clips found for {cid}")

    jobs = []
    for entry in sets:
        folder, full = entry["label"], entry["dir"]
        costume = entry["costume"][len(cid):]
        suffix = entry["suffix"]
        if costume_filter and not (folder == costume_filter
                                   or entry["costume"] == costume_filter
                                   or costume == costume_filter[len(cid):]):
            continue
        if suffix not in SUFFIX_TO_MODEL:
            print(f"  ? {folder}: unrecognised suffix '{suffix}', skipping")
            continue

        clips = list(entry["clips"])
        if clip_filter:
            clips = [c for c in clips if os.path.splitext(c)[0] in clip_filter]
        if clip_regex:
            rx = re.compile(clip_regex, re.I)
            clips = [c for c in clips if rx.fullmatch(os.path.splitext(c)[0])]
        if not clips:
            continue

        stem = SUFFIX_TO_MODEL[suffix].format(cid=cid, cos=costume)
        # 1044's base costume is '00' but its model is plain '1044_tpose'.
        candidates = [stem] + ([stem.replace(f"{cid}00", cid)] if costume == "00" else [])
        model = prefab = None
        for cand in candidates:
            model = model or find_model_fbx(cid, cand)
            prefab = prefab or find_prefab(cid, cand)
            if model and prefab:
                stem = cand
                break
        jobs.append({"folder": folder, "dir": full, "clips": clips, "stem": stem,
                     "model": model, "prefab": prefab,
                     "prefix": _name_prefix(entry, suffix)})
    return jobs


def launch() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cid", help="character id, e.g. 1044")
    ap.add_argument("--costume", help="restrict to one clip folder, e.g. 104400")
    ap.add_argument("--clips", help="comma-separated clip names")
    ap.add_argument("--clip-regex", help="only clips whose whole name matches, e.g. "
                    "'(stand|relax|idle|touch).*' (case-insensitive)")
    ap.add_argument("--out", default=OUT_ROOT)
    ap.add_argument("--dry-run", action="store_true",
                    help="solve the basis change and report coverage, export nothing")
    ap.add_argument("--no-extra", action="store_true",
                    help="only the character's own clip folders; skip costume DLC "
                         "pose sets found elsewhere in the ripped tree")
    ap.add_argument("--no-fold-root", action="store_true",
                    help="keep the game's own layout: body travel on the armature "
                         "object and body rotation on the Biped root bone. Faithful, "
                         "but retargeters that key off the hip discard both.")
    ap.add_argument("--blender", default=BLENDER)
    args = ap.parse_args()

    if not os.path.isfile(args.blender):
        sys.exit(f"error: Blender not found at {args.blender}")

    clip_filter = set(args.clips.split(",")) if args.clips else None
    jobs = plan(args.cid, args.costume, clip_filter, include_extra=not args.no_extra,
                clip_regex=args.clip_regex)
    if not jobs:
        sys.exit("error: nothing to do")

    print(f"character {args.cid}: {len(jobs)} clip set(s)")
    for j in jobs:
        print(f"  {j['folder']:<16} {len(j['clips']):>3} clips -> {j['stem']}")
        if not j["model"]:
            print(f"      ! no rig FBX found; run export_fbx.py {args.cid} first")
        if not j["prefab"]:
            print(f"      ! no prefab found; run extract_character.py {args.cid} first")
    jobs = [j for j in jobs if j["model"] and j["prefab"]]
    if not jobs:
        sys.exit("error: no clip set has both a rig and a prefab")

    import json
    payload = os.path.join(os.environ.get("TEMP", "."), f"ag_anim_{args.cid}.json")
    with open(payload, "w", encoding="utf-8") as fh:
        json.dump({"cid": args.cid, "out": args.out, "dry_run": args.dry_run,
                   "no_fold_root": args.no_fold_root,
                   "script_dir": os.path.dirname(os.path.abspath(__file__)),
                   "jobs": jobs}, fh)

    t0 = time.time()
    result_path = payload + ".result"
    if os.path.exists(result_path):
        os.remove(result_path)
    proc = subprocess.run([args.blender, "-b", "-noaudio", "-P",
                           os.path.abspath(__file__), "--", payload])
    print(f"\n{(time.time() - t0) / 60:.1f} min total")

    if os.path.isfile(result_path):
        with open(result_path, encoding="utf-8") as fh:
            result = json.load(fh)
        os.remove(result_path)
        os.remove(payload)
        if proc.returncode != 0:
            print(f"(Blender exited {proc.returncode} after finishing - it can crash "
                  f"on teardown; every clip was written, so this is not a failure)")
        return 1 if result["fail"] else 0

    print(f"error: Blender exited {proc.returncode} without completing the run",
          file=sys.stderr)
    return proc.returncode or 1


# ---------------------------------------------------------------------- blender side

def run_in_blender(payload_path: str) -> None:
    import json
    import bpy
    from mathutils import Matrix, Quaternion, Vector

    with open(payload_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    sys.path.insert(0, cfg["script_dir"])
    from unity_yaml import evaluate, parse_anim, parse_prefab, resolve_hashed_paths

    # Frame holding the rest pose, kept clear of every clip's exported range.
    REST_FRAME = -10

    def signed_permutations():
        """The 48 signed axis permutations - every way a rip can reshuffle a basis."""
        import itertools
        for perm in itertools.permutations(range(3)):
            for signs in itertools.product((1, -1), repeat=3):
                m = Matrix.Identity(3)
                for row in range(3):
                    for col in range(3):
                        m[row][col] = signs[row] * (1 if perm[row] == col else 0)
                yield m

    def unity_matrix(pos, rot, scale) -> Matrix:
        # Unity evaluates a quaternion curve component-by-component through the key
        # tangents and normalizes the result. Skipping the normalize is not a rounding
        # detail: between sparse keys the raw interpolant drifts up to ~25% off unit
        # length, and to_matrix() turns that straight into a uniform scale of |q|^2 -
        # the bone stretches and drags its children with it.
        return Matrix.LocRotScale(Vector(pos),
                                  Quaternion((rot[3], rot[0], rot[1], rot[2])).normalized(),
                                  Vector(scale))

    def bone_path(bone) -> str:
        parts, walk = [], bone
        while walk is not None:
            parts.append(walk.name)
            walk = walk.parent
        return "/".join(reversed(parts))

    def map_bones(arm, prefab):
        """Bone paths keyed the way the clips address them.

        AssetStudio hoists the Unity node the skeleton hangs off ('root') into the
        armature *object*, so armature-relative bone paths are missing that prefix
        while the clips still carry it. Recover the prefix from the prefab by finding
        where the armature's own root bone lives in the Unity hierarchy.
        """
        roots = [b for b in arm.data.bones if b.parent is None]
        prefixes = set()
        for rb in roots:
            for path in prefab:
                if path == rb.name or path.endswith("/" + rb.name):
                    prefixes.add(path[: -len(rb.name)].rstrip("/"))
        prefix = ""
        if prefixes:
            # Shortest wins: a deeper match would be a same-named bone further down.
            prefix = sorted(prefixes, key=len)[0]
        mapped = {}
        for bone in arm.data.bones:
            bp = bone_path(bone)
            mapped[f"{prefix}/{bp}" if prefix else bp] = bone
        return mapped, prefix

    def tolerate_inbetween_shapes():
        """Let rigs with in-between blend shapes import.

        Blender's FBX importer refuses any shape channel with more than one target
        ("FBX in-between Shapes are not currently supported", bug #84111) and aborts
        the whole file, so 1029 and 1043 lost every clip to their facial morphs.
        Nothing here animates shape keys, so drop just those channels and let the
        ordinary ones through. Re-applied per import because read_factory_settings
        can reload the add-on.
        """
        import io_scene_fbx.import_fbx as imp
        orig = imp.blen_read_shapes
        if getattr(orig, "_ag_patched", False):
            return

        def patched(fbx_tmpl, fbx_data, objects, me, scene):
            keep = [d for d in fbx_data if len(d[3]) <= 1]
            return orig(fbx_tmpl, keep, objects, me, scene)

        patched._ag_patched = True
        imp.blen_read_shapes = patched

    def import_rig(fbx_path: str):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        tolerate_inbetween_shapes()
        bpy.ops.import_scene.fbx(filepath=fbx_path, use_anim=False,
                                 automatic_bone_orientation=False,
                                 ignore_leaf_bones=False)
        arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if len(arms) != 1:
            raise RuntimeError(f"expected 1 armature in {fbx_path}, got {len(arms)}")
        return arms[0]

    def solve_basis(bones_by_path, prefab):
        """Score all 48 permutations against rest poses; return (C4, C4inv, report)."""
        pairs = []          # (unity parent-local, blender parent-local)
        for path, bone in bones_by_path.items():
            if bone.parent is None:
                continue
            node = prefab.get(path)
            if node is None:
                continue
            rest_b = bone.parent.matrix_local.inverted() @ bone.matrix_local
            pairs.append((bone.name,
                          unity_matrix(node.pos, node.rot, node.scale), rest_b))
        if len(pairs) < 8:
            raise RuntimeError(f"only {len(pairs)} bones matched the prefab by path - "
                               "rig and clips are probably from different skeletons")

        best, best_err = None, float("inf")
        for p in signed_permutations():
            pinv = p.transposed()
            err = 0.0
            for _, lu, lb in pairs:
                r = p @ lu.to_quaternion().to_matrix() @ pinv
                d = r - lb.to_quaternion().to_matrix()
                err += sum(d[i][j] ** 2 for i in range(3) for j in range(3))
            if err < best_err:
                best, best_err = p, err
        rot_rms = (best_err / len(pairs)) ** 0.5

        # Uniform scale, recovered from translations once the axes line up.
        num = den = 0.0
        for _, lu, lb in pairs:
            pu = best @ lu.to_translation()
            num += pu.dot(lb.to_translation())
            den += pu.dot(pu)
        scale = num / den if den > 1e-12 else 1.0

        c3 = best * scale
        c4 = c3.to_4x4()
        c4inv = c4.inverted()

        # Judge the fit on the bulk of the skeleton, not the single worst bone.
        # Real rigs carry a few mirrored helper bones whose bind frame sits 180 deg
        # from the prefab's; on 109503 three "Fix" bones out of 191 were enough to
        # veto every clip when the verdict came from the maximum. Those bones are
        # posed from the clip's own absolute values anyway, so they cost nothing.
        residuals = []
        for name, lu, lb in pairs:
            d = (c4 @ lu @ c4inv) - lb
            residuals.append((max(abs(d[i][j]) for i in range(4) for j in range(4)), name))
        residuals.sort()
        vals = [r for r, _ in residuals]
        p50 = vals[min(len(vals) - 1, int(len(vals) * 0.5))]
        p90 = vals[min(len(vals) - 1, int(len(vals) * 0.9))]
        outliers = [(r, n) for r, n in residuals if r > 1e-3]
        return c4, c4inv, {"bones": len(pairs), "rot_rms": rot_rms, "scale": scale,
                           "max_residual": vals[-1], "p50_residual": p50,
                           "p90_residual": p90,
                           "outliers": outliers[-6:], "n_outliers": len(outliers),
                           "matrix": [list(r) for r in best]}

    def sample(clip, chain, nframes, fps, to_basis, stats):
        """Bake a Unity transform path to per-frame Blender basis TRS.

        `chain` is a list of (path, node); their local matrices are composed in
        order, which is how a folded hip absorbs the nodes above it.
        """
        curves = [(p, n, clip.rotation.get(p), clip.position.get(p), clip.scale.get(p))
                  for p, n in chain]
        locs, quats, scales = [], [], []
        prev_q = None
        for f in range(nframes):
            t = f / fps
            m = None
            for _, node, rot_k, pos_k, scl_k in curves:
                pos = evaluate(pos_k, t, 3) if pos_k else node.pos
                rot = evaluate(rot_k, t, 4) if rot_k else node.rot
                scl = evaluate(scl_k, t, 3) if scl_k else node.scale
                # Guard the normalize: if this grows large again, bones stretch.
                dev = abs((sum(v * v for v in rot)) ** 0.5 - 1.0)
                if dev > stats["qdev"]:
                    stats["qdev"] = dev
                lu = unity_matrix(pos, rot, scl)
                m = lu if m is None else m @ lu
            loc, q, sc = to_basis(m).decompose()
            # Keep the quaternion on one hemisphere or the bake flips mid-clip.
            if prev_q is not None and q.dot(prev_q) < 0:
                q.negate()
            prev_q = q
            locs.append(loc)
            quats.append(q)
            scales.append(sc)
        return locs, quats, scales

    def write_curves(action, prefix, group, nframes, locs, quats, scales, rest=None):
        """Write baked TRS curves, preceded by one key holding the rest pose.

        The rest key sits at REST_FRAME, outside the exported range. Blender writes
        each bone node's *static* Lcl transform from the pose at the current frame,
        and that static value is what a retargeter reads as the bind pose. Parked on
        a posed frame, every clip ships a different "bind" - an idle exports a
        bent-elbow bind, and any bind-relative retarget then hinges that joint the
        wrong way. Parking on this key makes the embedded bind the real rest.
        """
        rest_loc, rest_quat, rest_scale = rest or ((0.0, 0.0, 0.0),
                                                   (1.0, 0.0, 0.0, 0.0),
                                                   (1.0, 1.0, 1.0))
        for data_path, values, ncomp, rest_v in (
                (f"{prefix}location", locs, 3, rest_loc),
                (f"{prefix}rotation_quaternion", quats, 4, rest_quat),
                (f"{prefix}scale", scales, 3, rest_scale)):
            for idx in range(ncomp):
                fc = action.fcurves.new(data_path, index=idx, action_group=group)
                fc.keyframe_points.add(nframes + 1)
                flat = [float(REST_FRAME), rest_v[idx]]
                for f, v in enumerate(values):
                    flat += [float(f), v[idx]]
                fc.keyframe_points.foreach_set("co", flat)
                fc.keyframe_points.foreach_set("interpolation", [1] * (nframes + 1))
                fc.update()

    def build_action(arm, clip, prefab, bones_by_path, root_path, c4, c4inv, fold):
        fps = max(1, int(round(clip.sample_rate)))
        nframes = max(2, int(round(clip.stop_time * fps)) + 1)

        action = bpy.data.actions.new(clip.name)
        used = skipped = 0
        root_motion = False
        stats = {"qdev": 0.0}

        def animated(p):
            return p in clip.rotation or p in clip.position or p in clip.scale

        def node_matrix(n):
            return unity_matrix(n.pos, n.rot, n.scale)

        class RigRest:
            """A bone's own bind pose, expressed back in Unity node terms.

            Used to hold channels a clip doesn't animate. The prefab is the game's
            rest and is normally identical, but where the two disagree - mirrored
            helper bones whose bind frame sits 180 deg away - the rig's own value is
            the safe one: it leaves the bone at the bind the skinning was authored
            against instead of flipping it.
            """

            __slots__ = ("pos", "rot", "scale")

            def __init__(self, rest_local_b):
                loc, q, sc = (c4inv @ rest_local_b @ c4).decompose()
                self.pos = tuple(loc)
                self.rot = (q.x, q.y, q.z, q.w)
                self.scale = tuple(sc)

        root_bone = next((b for b in bones_by_path.values() if b.parent is None), None)
        root_bone_path = next((p for p, b in bones_by_path.items() if b is root_bone), None)
        obj_node = prefab.get(root_path) if root_path else None
        bip_node = prefab.get(root_bone_path) if root_bone_path else None

        # Where the body's motion goes.
        #
        # This game animates the Unity node the skeleton hangs off (travel) and the
        # armature's own root bone (body rotation, up to ~177 deg), while the hip bone
        # below them stays dead still. Retargeters key off the *hip*: MMD's centre bone
        # comes from "Bip001 Pelvis", and a bare Biped root is deliberately unmapped.
        # Left as-authored, every bit of body translation and rotation is discarded and
        # the legs reach for a body that never moves. Folding both nodes down into the
        # hip puts the body transform on the bone retargeters actually read, which is
        # where UE/Unity rigs carry it anyway.
        fold_on = bool(fold and root_bone is not None and obj_node is not None
                       and bip_node is not None)
        prefix_chain = [(root_path, obj_node), (root_bone_path, bip_node)] if fold_on else []

        if not fold_on and obj_node is not None and animated(root_path):
            # Not folding: the node became the armature *object*, so drive that instead
            # or its travel is simply lost.
            rest_inv = node_matrix(obj_node).inverted()
            basis_rest = arm.matrix_basis.copy()
            arm.rotation_mode = "QUATERNION"
            locs, quats, scales = sample(
                clip, [(root_path, obj_node)], nframes, fps,
                lambda m: basis_rest @ (c4 @ (rest_inv @ m) @ c4inv), stats)
            rl, rq, rs = basis_rest.decompose()
            write_curves(action, "", "Object Transforms", nframes, locs, quats, scales,
                         rest=(rl, (rq.w, rq.x, rq.y, rq.z), rs))
            root_motion = True

        # Compare by name: Blender hands out a fresh RNA wrapper for `bone.parent`,
        # so `is` against the bone from the collection never matches.
        root_name = root_bone.name if root_bone else None
        for path, bone in bones_by_path.items():
            folded = fold_on and bone.parent is not None and bone.parent.name == root_name
            if fold_on and bone.name == root_name:
                continue                      # its transform now lives in its children
            if not (animated(path) or (folded and (animated(root_path)
                                                   or animated(root_bone_path)))):
                continue
            node = prefab.get(path)
            if node is None and (folded or bone.parent is None):
                skipped += 1          # the root chain genuinely needs the prefab
                continue
            if not (folded or bone.parent is None):
                node = RigRest(bone.parent.matrix_local.inverted() @ bone.matrix_local)
            used += 1

            chain = prefix_chain + [(path, node)] if folded else [(path, node)]
            if folded or bone.parent is None:
                # Delta from the Unity rest of the whole chain. The root bone's rest
                # matrix is the one place the armature-level offset survives, so an
                # absolute pose would double-apply it.
                rest = None
                for _, n in chain:
                    rest = node_matrix(n) if rest is None else rest @ node_matrix(n)
                rest_inv = rest.inverted()
                to_basis = lambda m, ri=rest_inv: c4 @ (ri @ m) @ c4inv
            else:
                rest_inv = (bone.parent.matrix_local.inverted()
                            @ bone.matrix_local).inverted()
                to_basis = lambda m, ri=rest_inv: ri @ (c4 @ m @ c4inv)

            locs, quats, scales = sample(clip, chain, nframes, fps, to_basis, stats)
            group = action.groups.new(bone.name)
            write_curves(action, f'pose.bones["{bone.name}"].', group.name,
                         nframes, locs, quats, scales)
            if folded and (animated(root_path) or animated(root_bone_path)):
                root_motion = True

        return action, nframes, fps, used, skipped, root_motion, stats

    def export(arm, action, out_path, fps, nframes):
        scene = bpy.context.scene
        scene.render.fps = fps
        scene.frame_start = 0
        scene.frame_end = nframes - 1
        if arm.animation_data is None:
            arm.animation_data_create()
        arm.animation_data.action = action
        # Park on the rest key so the FBX's static bone transforms - which is what a
        # retargeter reads as the bind pose - are the real rest, not frame 1 of this
        # clip. The exported range still starts at 0, so this frame ships no data.
        scene.frame_set(REST_FRAME)
        bpy.ops.export_scene.fbx(
            filepath=out_path, use_selection=False, apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_NONE", object_types={"ARMATURE", "MESH"},
            use_mesh_modifiers=False, add_leaf_bones=False,
            bake_anim=True, bake_anim_use_all_bones=True,
            bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False,
            bake_anim_force_startend_keying=True, bake_anim_step=1.0,
            bake_anim_simplify_factor=0.0, path_mode="STRIP")

    # ------------------------------------------------------------------ drive the jobs
    total_ok = total_fail = 0
    for job in cfg["jobs"]:
        print(f"\n=== {job['folder']}  rig={os.path.basename(job['model'])}", flush=True)
        # Per-set isolation. A rig Blender's importer chokes on (1075 ships one that
        # raises `KeyError: root Model`) used to abort the whole character: 1075 lost
        # all 181 clips to a single bad file. Skip that set and carry on.
        try:
            arm = import_rig(job["model"])
            prefab = parse_prefab(job["prefab"])
            bones_by_path, root_path = map_bones(arm, prefab)
            print(f"    {len(bones_by_path)} bones under prefab path '{root_path}'")
            # Also guarded: a set whose rig isn't the prefab's skeleton at all
            # (6070: "only 7 bones matched") raises here, and took the character down.
            c4, c4inv, fit = solve_basis(bones_by_path, prefab)
        except Exception as exc:                                      # noqa: BLE001
            print(f"    ! cannot load this set ({type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:90]}); skipping its "
                  f"{len(job['clips'])} clip(s)", flush=True)
            total_fail += len(job["clips"])
            continue
        print(f"    basis fit: {fit['bones']} bones, scale {fit['scale']:.6g}, "
              f"residual p50 {fit['p50_residual']:.2e} / p90 {fit['p90_residual']:.2e}"
              f" / max {fit['max_residual']:.2e}")
        print(f"    C = {fit['matrix']}")
        if fit["n_outliers"]:
            names = ", ".join(f"{n} ({r:.2f})" for r, n in reversed(fit["outliers"]))
            print(f"    {fit['n_outliers']} bone(s) differ from the prefab: {names}")
        # Verdict on the MEDIAN, not p90. The question this guards is "are the rig
        # and the clips the same skeleton at all", and outliers are normal: costume
        # and ui rigs routinely disagree with their prefab on the fingers alone (1083
        # had 20 of 133 bones over 1e-3, every one a finger, while the body agreed at
        # 4.5e-04). p90 turned that into a refusal of 8 good clips. Those bones cost
        # nothing anyway - ordinary bones take their fallback rest from the rig's own
        # bind, not the prefab, so only the basis solve and the root chain read it.
        if fit["p50_residual"] > 1e-3:
            print("    ! rig and prefab disagree across the skeleton; refusing this set")
            total_fail += len(job["clips"])
            continue

        out_dir = os.path.join(cfg["out"], cfg["cid"])
        os.makedirs(out_dir, exist_ok=True)
        tex_src = os.path.dirname(job["model"])
        for fn in os.listdir(tex_src):
            if fn.lower().endswith((".png", ".tga", ".jpg")):
                dst = os.path.join(out_dir, fn)
                if not os.path.exists(dst):
                    shutil.copyfile(os.path.join(tex_src, fn), dst)

        for i, clip_file in enumerate(job["clips"], 1):
            name = os.path.splitext(clip_file)[0]
            clip = parse_anim(os.path.join(job["dir"], clip_file))
            # Clips authored outside the model's bundle name their targets by CRC
            # hash; turn those back into bone paths before anything tries to bind.
            rehashed, still_hashed = resolve_hashed_paths(
                clip, set(bones_by_path) | set(prefab) | {root_path})
            missing = sorted(clip.paths - set(bones_by_path) - {root_path})
            action, nframes, fps, used, skipped, root_motion, stats = build_action(
                arm, clip, prefab, bones_by_path, root_path, c4, c4inv,
                not cfg.get("no_fold_root"))

            note = ("  +root@hip" if cfg.get("no_fold_root") is not True else "  +root") \
                if root_motion else ""
            if rehashed:
                note += f"  [{rehashed} hashed paths resolved]"
            if still_hashed:
                note += f"  [{still_hashed} still hashed]"
            if stats["qdev"] > 0.02:
                note += f"  [q renormalised, peak {stats['qdev'] * 100:.0f}%]"
            if missing:
                note += f"  [{len(missing)} paths not in rig]"
            if skipped:
                note += f"  [{skipped} bones not in prefab]"
            if cfg["dry_run"]:
                print(f"    [{i}/{len(job['clips'])}] {name}: {used} bones, "
                      f"{nframes} frames @{fps}{note}", flush=True)
                bpy.data.actions.remove(action)
                continue

            # Clips outside the hero tree arrive with editor names like "Take 001"
            # and "Recorded (12)"; keep filenames shell-friendly.
            safe = re.sub(r"_+", "_",
                          re.sub(r"[^A-Za-z0-9._-]", "_",
                                 job.get("prefix", "") + name)).strip("_")
            out_path = os.path.join(out_dir,
                                    f"{job['stem'].replace('_tpose', '')}@{safe}.fbx")
            try:
                export(arm, action, out_path, fps, nframes)
                mb = os.path.getsize(out_path) / 1e6
                total_ok += 1
                print(f"    [{i}/{len(job['clips'])}] {name}: {used} bones, "
                      f"{nframes}f @{fps} -> {mb:.2f}MB{note}", flush=True)
            except Exception as exc:
                total_fail += 1
                print(f"    [{i}/{len(job['clips'])}] {name}: EXPORT FAILED {exc}",
                      flush=True)
            finally:
                if arm.animation_data:
                    arm.animation_data.action = None
                bpy.data.actions.remove(action)

    print(f"\n{total_ok} clip(s) exported, {total_fail} failed")

    # Report the outcome through a file rather than the exit code: Blender can
    # segfault during teardown after the work is finished and every clip is written,
    # which would otherwise look like a failed run and abort `ag.py build`.
    with open(payload_path + ".result", "w", encoding="utf-8") as fh:
        json.dump({"ok": total_ok, "fail": total_fail,
                   "dry_run": bool(cfg["dry_run"])}, fh)


if __name__ == "__main__":
    # Blender puts everything after '--' in sys.argv; the launcher never passes one.
    if "--" in sys.argv:
        run_in_blender(sys.argv[sys.argv.index("--") + 1])
    else:
        sys.exit(launch())
