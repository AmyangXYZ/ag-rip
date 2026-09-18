#!/usr/bin/env python3
r"""
A skin's victory-sequence camera -> camera FBX + JSON next to the character's win clip.

    python ag.py wincam 109503 109501 104701
    python agtools/export_win_camera.py 109503 --char-fbx AG_fbx_anim/1095/109503@bat_win.fbx

The victory pose (comeffect/storytimeline/win/<skin>_win_uitpose) is a Timeline:
an AnimationTrack plays the character's win clip on the character (bound at runtime,
standing at the timeline root), another plays "<cid>_Cam_Win/Take 001" on the camera
rig (<skin>_Cam_Win, whose child carries a CinemachineVirtualCamera), plus a Story
depth-of-field track and voice tracks. Everything starts at 0; the clips run ~5 s.

Output (AG_fbx_anim/<cid>/):
  <skin>@win_camera.json   per frame (30 fps) the camera's world position/rotation in
                           Unity space (left-handed, Y-up, metres; the character's root
                           at the origin), vertical FOV, near/far, the DOF node values
  <skin>@win_camera.fbx    the same camera animated in the character FBX's space: the
                           Unity->Blender mapping is fitted on the rig's rest pose, so
                           it lines up with <skin>@bat_win.fbx (or the --char-fbx given)
  <skin>@win_camera_preview/  a few Workbench frames through that camera (checking)

Run with system Python; the FBX step re-launches itself inside Blender.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "AG_cache", "wincam")
ANIM_OUT = os.path.join(ROOT, "AG_fbx_anim")
SAMPLE = os.path.join(ROOT, "AG_sample")
FPS = 30


# ------------------------------------------------------------------ rip + parse
def rip(skin: str) -> str:
    """AssetRipper export of the win timeline and its direct dependencies (cached)."""
    sys.path.insert(0, HERE)
    import assetripper, bundle_deps, stage_unity
    out = os.path.join(CACHE, skin)
    if os.path.isdir(os.path.join(out, "ExportedProject")):
        return out
    root = os.path.join("comeffect", "storytimeline", "win", f"{skin}_win_uitpose.ys")
    if not os.path.isfile(os.path.join(bundle_deps.ROOT, root)):
        sys.exit(f"error: no win timeline for {skin} ({root})")
    index = bundle_deps.load_index()
    deps = {index.get(c.lower()) or index.get(c) for c in bundle_deps.externals(os.path.join(bundle_deps.ROOT, root))}
    bundles = [root] + sorted(d for d in deps if d and "shader" not in d.lower())
    staged = os.path.join(CACHE, "_stage")
    stage_unity.link_into(bundles, staged)
    with assetripper.Server(os.path.join(CACHE, "_assetripper.log")) as ar:
        ar.export(staged, out, ShaderExportMode="Dummy")
    shutil.rmtree(staged, ignore_errors=True)
    return out


def _docs(path: str) -> dict[str, tuple[str, str]]:
    s = open(path, encoding="utf-8", errors="replace").read()
    parts = re.split(r"\n--- !u!(\d+) &(-?\d+)", s)
    return {parts[i + 1]: (parts[i], parts[i + 2]) for i in range(1, len(parts), 3)}


def _field(body: str, key: str) -> str | None:
    m = re.search(r"\n\s*" + key + r": (.*)", body)
    return m.group(1).strip() if m else None


def _vec(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"[xyzw]: ([-\d.eE+]+)", text)]


def parse(skin: str, project: str) -> dict:
    sys.path.insert(0, HERE)
    from unity_yaml import evaluate, parse_anim, parse_prefab
    assets = os.path.join(project, "ExportedProject", "Assets")
    guid_file = {}
    for meta in glob.glob(os.path.join(assets, "**", "*.meta"), recursive=True):
        m = re.search(r"guid: (\w+)", open(meta, encoding="utf-8", errors="replace").read())
        if m:
            guid_file[m.group(1)] = meta[:-5]
    prefab_path = next(p for p in glob.glob(os.path.join(assets, "**", f"{skin}_win_uitpose.prefab"), recursive=True))
    docs = _docs(prefab_path)
    names = {f: _field(b, "m_Name") for f, (c, b) in docs.items() if c == "1"}

    # PlayableDirector: track guid -> bound object (an Animator)
    director = next(b for c, b in docs.values() if c == "320")
    bindings = dict(re.findall(r"key: \{fileID: \d+, guid: (\w+), type: 2\}\s*\n\s*value: \{fileID: (-?\d+)\}", director))
    camera_track = camera_clip = None
    for guid, target in bindings.items():
        track = guid_file.get(guid, "")
        if "camera" not in os.path.basename(track).lower() or target == "0":
            continue
        body = open(track, encoding="utf-8", errors="replace").read()
        asset = re.search(r"m_Asset: \{fileID: \d+, guid: (\w+)", body)
        clip_asset = open(guid_file[asset.group(1)], encoding="utf-8", errors="replace").read()
        clip_guid = re.search(r"m_Clip: \{fileID: \d+, guid: (\w+)", clip_asset).group(1)
        camera_track, camera_clip = (track, target), guid_file[clip_guid]
        start = float(_field(body, "m_Start") or 0)
        break
    if not camera_clip:
        sys.exit(f"error: {skin}: no bound camera animation track")
    animator_go = _field(docs[camera_track[1]][1], "m_GameObject")
    animator_go = re.search(r"-?\d+", animator_go).group(0)

    # hierarchy (Unity paths from the timeline root) and the animator's own path
    nodes = parse_prefab(prefab_path)
    anim_name = names[animator_go]
    anim_node = next(n for n in nodes.values() if n.name == anim_name and n.parent is not None and n.parent.parent is None)
    lens = next(b for c, b in docs.values() if c == "114" and "m_Lens:" in b)
    fov = float(re.search(r"FieldOfView: ([-\d.eE]+)", lens).group(1))
    near = float(re.search(r"NearClipPlane: ([-\d.eE]+)", lens).group(1))
    far = float(re.search(r"FarClipPlane: ([-\d.eE]+)", lens).group(1))
    vcam_go = re.search(r"m_GameObject: \{fileID: (-?\d+)", lens).group(1)

    clip = parse_anim(camera_clip)
    cam_path = next(p for p in clip.paths)            # the animated child of the rig
    cam_node = nodes.get(f"{anim_node.path}/{cam_path}")

    def mat(pos, rot, scale=(1, 1, 1)):
        x, y, z, w = rot
        r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
             [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
             [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
        return [[r[i][0] * scale[0], r[i][1] * scale[1], r[i][2] * scale[2], pos[i]] for i in range(3)] + [[0, 0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

    def world_of(node):
        m = mat(node.pos, node.rot, node.scale)
        return mul(world_of(node.parent), m) if node.parent is not None else m

    rig_world = world_of(anim_node)
    n = int(round(clip.stop_time * FPS)) + 1
    frames = []
    for f in range(n):
        t = min(f / FPS, clip.stop_time)
        p = evaluate(clip.position[cam_path], t, 3) if cam_path in clip.position else cam_node.pos
        q = evaluate(clip.rotation[cam_path], t, 4) if cam_path in clip.rotation else cam_node.rot
        ql = math.sqrt(sum(c * c for c in q)) or 1.0
        q = tuple(c / ql for c in q)
        w = mul(rig_world, mat(p, q))
        # world rotation back to a quaternion
        r = [row[:3] for row in w[:3]]
        tr = r[0][0] + r[1][1] + r[2][2]
        if tr > 0:
            s = math.sqrt(tr + 1) * 2
            qw, qx, qy, qz = s / 4, (r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s
        elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
            s = math.sqrt(1 + r[0][0] - r[1][1] - r[2][2]) * 2
            qw, qx, qy, qz = (r[2][1] - r[1][2]) / s, s / 4, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s
        elif r[1][1] > r[2][2]:
            s = math.sqrt(1 + r[1][1] - r[0][0] - r[2][2]) * 2
            qw, qx, qy, qz = (r[0][2] - r[2][0]) / s, (r[0][1] + r[1][0]) / s, s / 4, (r[1][2] + r[2][1]) / s
        else:
            s = math.sqrt(1 + r[2][2] - r[0][0] - r[1][1]) * 2
            qw, qx, qy, qz = (r[1][0] - r[0][1]) / s, (r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, s / 4
        frames.append({"t": round(t, 6), "position": [round(w[i][3], 6) for i in range(3)],
                       "rotation": [round(c, 7) for c in (qx, qy, qz, qw)]})

    dof = {}
    for track in glob.glob(os.path.join(assets, "MonoBehaviour", "StoryCameraDepthOfFieldNode*.asset")):
        body = open(track, encoding="utf-8", errors="replace").read()
        dof[os.path.basename(track)] = {k: v for k, v in re.findall(r"\n  (\w+): ([^\n{]+)", body)
                                        if not k.startswith("m_")}
    return {"skin": skin, "fps": FPS, "duration": clip.stop_time, "timeline_start": start,
            "fov_vertical_deg": fov, "near": near, "far": far,
            "space": "Unity world: left-handed, Y up, metres; the timeline root (character root) at the origin",
            "camera_rig": anim_node.path, "camera_node": f"{anim_node.path}/{cam_path}",
            "clip": os.path.relpath(camera_clip, project), "frames": frames,
            "depth_of_field_nodes": dof}


# ------------------------------------------------------------------ Blender
def char_fbx(skin: str, cid: str) -> str | None:
    folder = os.path.join(ANIM_OUT, cid)
    for pat in (f"{skin}@bat_win.fbx", f"{skin}@*win*.fbx", f"{cid}@bat_win.fbx", f"{cid}@*win_0.fbx", f"{cid}@*win*.fbx"):
        hits = sorted(h for h in glob.glob(os.path.join(folder, pat)) if "vice" not in os.path.basename(h))
        if hits:
            return hits[0]
    return None


def rig_prefab(skin: str, cid: str) -> str | None:
    base = os.path.join(SAMPLE, cid, "ExportedProject", "Assets")
    for stem in (f"{skin}_tpose", f"{cid}00_tpose", f"{cid}_tpose"):
        hits = glob.glob(os.path.join(base, "**", f"{stem}.prefab"), recursive=True)
        if hits:
            return hits[0]
    return None


def blender_job(job: dict) -> None:
    sys.path.insert(0, HERE)
    from export_anim_fbx import find_blender
    payload = os.path.join(CACHE, f"{job['skin']}_job.json")
    json.dump(job, open(payload, "w"))
    r = subprocess.run([find_blender(), "-b", "--factory-startup", "--python", os.path.abspath(__file__),
                        "--", "--blender", payload], capture_output=True, text=True)
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith("WINCAM") or "Error" in line or "Traceback" in line:
            print("   ", line)


def run_in_blender(payload: str) -> None:
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector
    sys.path.insert(0, HERE)
    from unity_yaml import parse_prefab
    job = json.load(open(payload))
    cam_data = json.load(open(job["json"]))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=job["char_fbx"], automatic_bone_orientation=False, ignore_leaf_bones=False)
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")

    # Unity world rest positions (character root at the origin) vs Blender rest heads
    nodes = parse_prefab(job["prefab"])

    def uworld(node):
        x, y, z, w = node.rot
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        m = np.eye(4); m[:3, :3] = r * np.array(node.scale); m[:3, 3] = node.pos
        return (uworld(node.parent) @ m) if node.parent is not None else m

    by_name = {}
    for node in nodes.values():
        if node.parent is not None:
            by_name.setdefault(node.name, node)
    U, B = [], []
    for bone in arm.data.bones:
        node = by_name.get(bone.name)
        if node is None:
            continue
        U.append(uworld(node)[:3, 3])
        B.append(np.array(arm.matrix_world @ bone.head_local))
    U, B = np.array(U), np.array(B)
    X = np.hstack([U, np.ones((len(U), 1))])
    sol, *_ = np.linalg.lstsq(X, B, rcond=None)
    A, t = sol[:3].T, sol[3]
    resid = np.abs(X @ sol - B).max()
    sv = np.linalg.svd(A, compute_uv=False)
    print(f"WINCAM fit: {len(U)} bones, max residual {resid:.2e}, scale {sv.mean():.5f} (spread {sv.max() - sv.min():.1e})")

    scene = bpy.context.scene
    scene.render.fps = cam_data["fps"]
    cam = bpy.data.objects.new(f"{job['skin']}_WinCamera", bpy.data.cameras.new(f"{job['skin']}_WinCamera"))
    scene.collection.objects.link(cam)
    cam.data.sensor_fit = "VERTICAL"
    cam.data.angle_y = math.radians(cam_data["fov_vertical_deg"])
    s = float(sv.mean())
    cam.data.clip_start = cam_data["near"] * s
    cam.data.clip_end = cam_data["far"] * s
    for f, fr in enumerate(cam_data["frames"]):
        p = np.array(fr["position"])
        x, y, z, w = fr["rotation"]
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        fwd = A @ r[:, 2]; up = A @ r[:, 1]
        zb = -fwd / np.linalg.norm(fwd)
        yb = up - zb * np.dot(up, zb); yb /= np.linalg.norm(yb)
        xb = np.cross(yb, zb)
        m = Matrix.Identity(4)
        for i in range(3):
            m[i][0], m[i][1], m[i][2] = xb[i], yb[i], zb[i]
            m[i][3] = (A @ p + t)[i]
        cam.matrix_world = m
        cam.keyframe_insert("location", frame=f + 1)
        cam.keyframe_insert("rotation_euler", frame=f + 1)
    for fc in cam.animation_data.action.fcurves:
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
    scene.frame_start, scene.frame_end = 1, len(cam_data["frames"])
    scene.camera = cam

    # preview through the camera (Workbench), character animated
    os.makedirs(job["preview"], exist_ok=True)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    n = len(cam_data["frames"])
    for f in (1, n // 4, n // 2, 3 * n // 4, n):
        scene.frame_set(f)
        scene.render.filepath = os.path.join(job["preview"], f"frame_{f:03d}.png")
        bpy.ops.render.render(write_still=True)

    for o in bpy.data.objects:
        o.select_set(o == cam)
    bpy.context.view_layer.objects.active = cam
    bpy.ops.export_scene.fbx(filepath=job["fbx"], use_selection=True, object_types={"CAMERA"},
                             bake_anim=True, bake_anim_use_all_actions=False, add_leaf_bones=False)
    print(f"WINCAM wrote {job['fbx']}")


# ------------------------------------------------------------------ main
def main() -> int:
    if "--blender" in sys.argv:
        run_in_blender(sys.argv[sys.argv.index("--blender") + 1])
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skins", nargs="+", help="skin ids, e.g. 109503 104701")
    ap.add_argument("--char-fbx", help="character win FBX to align to (default: found in AG_fbx_anim/<cid>)")
    ap.add_argument("--no-fbx", action="store_true", help="JSON only")
    args = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)
    for skin in args.skins:
        cid = skin[:4]
        data = parse(skin, rip(skin))
        out_dir = os.path.join(ANIM_OUT, cid)
        os.makedirs(out_dir, exist_ok=True)
        js = os.path.join(out_dir, f"{skin}@win_camera.json")
        json.dump(data, open(js, "w"), indent=1)
        print(f"{skin}: {len(data['frames'])} frames, fov {data['fov_vertical_deg']}, rig {data['camera_node']} -> {js}")
        if args.no_fbx:
            continue
        fbx = args.char_fbx or char_fbx(skin, cid)
        prefab = rig_prefab(skin, cid)
        if not fbx or not prefab:
            print(f"   no character win FBX / rig prefab for {skin} (fbx {fbx}, prefab {prefab}) - JSON only")
            continue
        print(f"   aligning to {os.path.relpath(fbx, ROOT)} (rest pose from {os.path.basename(prefab)})")
        blender_job({"skin": skin, "json": js, "char_fbx": fbx, "prefab": prefab,
                     "fbx": os.path.join(out_dir, f"{skin}@win_camera.fbx"),
                     "preview": os.path.join(out_dir, f"{skin}@win_camera_preview")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
