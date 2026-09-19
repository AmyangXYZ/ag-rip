#!/usr/bin/env python3
r"""
A skin's authored camera sequences -> camera FBX + sequenced character FBX + JSON.

    python ag.py cams 109501 104701 109503
    python ag.py cams 109501 --only touch1,win

Which sequences carry an authored camera (Timeline + Cinemachine):
  win         comeffect/storytimeline/win/<skin>_win_uitpose     victory pose
  debut, action1_1, touch1, touch2, ...
              comeffect/uitimeline/charactor/<skin>              DLC home-screen interactions
                                                                 (played in the skin's home stage)
Other home-screen motions (idle, touch1-4, login ...) use the fixed home camera, which
the game places in code.

Each sequence is a PlayableDirector prefab. Its camera rig (<name>_cam: an Animator on
"rotation&position" + "lookat" + VirtualCamera/cm) plays a recorded clip on an
AnimationTrack (usually the track's infinite clip). The camera is evaluated the way
Cinemachine does it: transforms from the clip; a Composer aims at the LookAt target plus
m_TrackedObjectOffset (target space; the composers here have no damping and a centred
screen position, so the aim is exact); m_Lens.Dutch rolls it; m_Lens.FieldOfView is the
vertical FOV. Clip-driven lens/composer fields arrive as CRC32-hashed script attributes
and are resolved by name. The character (bound at runtime, standing at the timeline
root) plays the clips of the "tpose" tracks - excerpts with clip-in offsets, in order.

Output, AG_fbx_anim/<cid>/cameras/<skin>@<sequence>.*:
  .camera.json     per frame (30 fps): camera world position / rotation (Unity space:
                   left-handed, Y up, metres, timeline root at the origin), vertical FOV,
                   Dutch; the character clip schedule; camera-cut blends; props
  .camera.fbx      that camera in the character FBX space (mapping fitted on the rig's
                   rest pose)
  .character.fbx   the character playing the schedule as one clip: the excerpts are
                   merged into a Unity clip and exported by export_anim_fbx itself, so it
                   matches the per-clip exports (rest key, facing; frame 0 = timeline 0)
  DLC interactions additionally (built on the home-screen model <skin>ui_tpose, whose
  Eye / Eyebrow / Mouth / Pupil meshes carry the morphs):
  .character.fbx   + morph animation (standard FBX blend-shape channel curves, real
                   channel names): the facial clips decoded from the bundle (unity_clip.py)
                   + the game's pre-analysed lip sync (zh; --lang zh,ja adds .character.ja.fbx)
  .morphs.zh.json  the same morph curves as data (CRC32 mesh / channel keys, 0..1)
  .wav             one audio track (reze-engine has one slot): the voice cue (debut: greet,
                   touchN: talkN) mixed with the scene music/SFX, from t=0
  _preview/        Workbench frames through the camera (checking)
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
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "AG_cache", "cams")
ANIM_OUT = os.path.join(ROOT, "AG_fbx_anim")
SAMPLE = os.path.join(ROOT, "AG_sample")
FPS = 30

SOURCES = {"win": os.path.join("comeffect", "storytimeline", "win", "{skin}_win_uitpose.ys"),
           "dlc": os.path.join("comeffect", "uitimeline", "charactor", "{skin}.ys")}
# Cinemachine fields a camera clip may animate; clips store them as CRC32(name)
ANIMATED_FIELDS = ["m_Lens.FieldOfView", "m_Lens.Dutch", "m_Lens.NearClipPlane", "m_Lens.FarClipPlane",
                   "m_TrackedObjectOffset.x", "m_TrackedObjectOffset.y", "m_TrackedObjectOffset.z",
                   "m_ScreenX", "m_ScreenY"]
FIELD_BY_CRC = {zlib.crc32(n.encode()): n for n in ANIMATED_FIELDS}


# ------------------------------------------------------------------ rip
def rip(skin: str, kind: str) -> str | None:
    """AssetRipper export of a sequence bundle + its direct dependencies (cached)."""
    sys.path.insert(0, HERE)
    import assetripper, bundle_deps, stage_unity
    root = SOURCES[kind].format(skin=skin)
    if not os.path.isfile(os.path.join(bundle_deps.ROOT, root)):
        return None
    out = os.path.join(CACHE, f"{skin}_{kind}")
    if os.path.isdir(os.path.join(out, "ExportedProject")):
        return out
    index = bundle_deps.load_index()
    deps = {index.get(c.lower()) or index.get(c)
            for c in bundle_deps.externals(os.path.join(bundle_deps.ROOT, root))}
    bundles = [root] + sorted(d for d in deps if d and "shader" not in d.lower())
    staged = os.path.join(CACHE, "_stage")
    stage_unity.link_into(bundles, staged)
    with assetripper.Server(os.path.join(CACHE, "_assetripper.log")) as ar:
        ar.export(staged, out, ShaderExportMode="Dummy")
    shutil.rmtree(staged, ignore_errors=True)
    return out


# ------------------------------------------------------------------ YAML helpers
def _read(path: str) -> str:
    return open(path, encoding="utf-8", errors="replace").read()


def _docs(text: str) -> dict[str, tuple[str, str]]:
    parts = re.split(r"\n--- !u!(\d+) &(-?\d+)", text)
    return {parts[i + 1]: (parts[i], parts[i + 2]) for i in range(1, len(parts), 3)}


def _field(body: str, key: str) -> str | None:
    m = re.search(r"\n\s*" + re.escape(key) + r": (.*)", body)
    return m.group(1).strip() if m else None


def _vec(text: str | None) -> tuple[float, ...]:
    return tuple(float(x) for x in re.findall(r"[xyzw]: ([-\d.eE+]+)", text or ""))


def _ref(text: str | None) -> tuple[str, str | None]:
    """'{fileID: 11400000, guid: abc, type: 2}' -> (fileID, guid)."""
    fid = re.search(r"fileID: (-?\d+)", text or "")
    guid = re.search(r"guid: (\w+)", text or "")
    return (fid.group(1) if fid else "0", guid.group(1) if guid else None)


# ------------------------------------------------------------------ math (Unity conventions)
def q_mat(q):
    x, y, z, w = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def trs(p, q, s=(1, 1, 1)):
    r = q_mat(q)
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], p[i]] for i in range(3)] + [[0, 0, 0, 1]]


def mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def m_quat(r):
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1) * 2
        return ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, s / 4)
    if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1 + r[0][0] - r[1][1] - r[2][2]) * 2
        return (s / 4, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    if r[1][1] > r[2][2]:
        s = math.sqrt(1 + r[1][1] - r[0][0] - r[2][2]) * 2
        return ((r[0][1] + r[1][0]) / s, s / 4, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    s = math.sqrt(1 + r[2][2] - r[0][0] - r[1][1]) * 2
    return ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, s / 4, (r[1][0] - r[0][1]) / s)


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def look_rotation(fwd, up=(0.0, 1.0, 0.0)):
    """Quaternion.LookRotation: z = forward, x = up x z, y = z x x (columns)."""
    z = _norm(fwd)
    x = _norm(_cross(up, z))
    y = _cross(z, x)
    return [[x[i], y[i], z[i]] for i in range(3)]


def roll(r, degrees):
    """r * Quaternion.AngleAxis(degrees, Vector3.forward)."""
    a = math.radians(degrees)
    rz = q_mat((0.0, 0.0, math.sin(a / 2), math.cos(a / 2)))
    return [[sum(r[i][k] * rz[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


# ------------------------------------------------------------------ Cinemachine composer
# CinemachineComposer.MutateCameraState (Cinemachine 2.x), reproduced so damped aims come
# out as the game shows them: the orientation carries over from the previous frame, a
# damped pass pulls the target into the dead zone, an undamped pass keeps it inside the
# soft zone ("hard" rect). With no damping and a zero dead zone it is an exact look-at.
_UP = (0.0, 1.0, 0.0)
ASPECT = 16 / 9
SIM_HZ = 60                               # the game's update rate for the damped sim


def _m(q):
    return q_mat(q)


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mv(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def _tr(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _axis_angle(deg, axis):
    a = _norm(axis)
    h = math.radians(deg) / 2
    return q_mat((a[0] * math.sin(h), a[1] * math.sin(h), a[2] * math.sin(h), math.cos(h)))


def _angle(a, b):
    a, b = _norm(a), _norm(b)
    d = [x - y for x, y in zip(a, b)]
    s_ = [x + y for x, y in zip(a, b)]
    return math.degrees(math.atan2(math.sqrt(_dot(d, d)), math.sqrt(_dot(s_, s_))) * 2)


def _signed_angle(a, b, n):
    c = _cross(_norm(a), _norm(b))
    ang = _angle(a, b)
    return -ang if _dot(c, n) < 0 else ang


def _project_plane(v, n):
    nn = _dot(n, n) or 1.0
    k = _dot(v, n) / nn
    return [v[i] - n[i] * k for i in range(3)]


def _rot_to_target(orient, look_dir):
    """UnityQuaternionExtensions.GetCameraRotationToTarget -> (pitch, yaw) degrees."""
    if _dot(look_dir, look_dir) < 1e-10:
        return [0.0, 0.0]
    inv = _tr(orient)
    up = _mv(inv, _UP)
    d = _mv(inv, look_dir)
    ang_h = 0.0
    dh = _project_plane(d, up)
    if _dot(dh, dh) > 1e-10:
        ch = _project_plane([0.0, 0.0, 1.0], up)
        if _dot(ch, ch) > 1e-10:
            ang_h = _signed_angle(ch, dh, up)
    q = _axis_angle(ang_h, up)
    ang_v = _signed_angle(_mv(q, [0.0, 0.0, 1.0]), d, _mv(q, [1.0, 0.0, 0.0]))
    return [ang_v, ang_h]


def _apply_rot(orient, rot):
    """UnityQuaternionExtensions.ApplyCameraRotation."""
    return _mm(_mm(_axis_angle(rot[1], _UP), orient), _axis_angle(rot[0], [1.0, 0.0, 0.0]))


def _damp(initial, damp_time, dt):
    """Cinemachine Damper.Damp."""
    if damp_time < 1e-4 or abs(initial) < 1e-4:
        return initial
    if dt < 1e-4:
        return 0.0
    k = 4.605170186 / damp_time
    return initial * (1 - math.exp(-k * dt))


class Composer:
    def __init__(self, body: str | None) -> None:
        f = lambda k, d: float(_field(body, k) or d) if body else d
        self.offset = _vec(_field(body, "m_TrackedObjectOffset")) if body else (0.0, 0.0, 0.0)
        self.screen = (f("m_ScreenX", 0.5), f("m_ScreenY", 0.5))
        self.dead = (f("m_DeadZoneWidth", 0), f("m_DeadZoneHeight", 0))
        self.soft = (f("m_SoftZoneWidth", 0.8), f("m_SoftZoneHeight", 0.8))
        self.bias = (f("m_BiasX", 0), f("m_BiasY", 0))
        self.damping = (f("m_HorizontalDamping", 0), f("m_VerticalDamping", 0))
        self.center_on_activate = f("m_CenterOnActivate", 1) != 0
        self.reset()

    def describe(self) -> dict:
        return {"tracked_object_offset": list(self.offset), "screen": list(self.screen),
                "dead_zone": list(self.dead), "soft_zone": list(self.soft), "bias": list(self.bias),
                "damping_h_v": list(self.damping), "center_on_activate": self.center_on_activate}

    def reset(self) -> None:
        self.prev = None                    # (camera pos, look-at point, orientation, screen offset)

    def _bounds(self, rect, fov):
        tv = math.tan(math.radians(fov) / 2)
        th = tv * ASPECT
        a = lambda ndc, t: math.degrees(math.atan(ndc * t))
        (x0, y0, x1, y1) = rect
        return (a(2 * y0 - 1, tv), a(2 * y1 - 1, tv), a(2 * x0 - 1, th), a(2 * x1 - 1, th))

    def _to_bounds(self, orient, rect, point, pos, fov, dt):
        rot = _rot_to_target(orient, [point[k] - pos[k] for k in range(3)])
        ymin, ymax, xmin, xmax = self._bounds(rect, fov)
        rot[0] = rot[0] - ymin if rot[0] < ymin else rot[0] - ymax if rot[0] > ymax else 0.0
        rot[1] = rot[1] - xmin if rot[1] < xmin else rot[1] - xmax if rot[1] > xmax else 0.0
        if dt >= 0 and self.prev is not None:
            rot[0] = _damp(rot[0], self.damping[1], dt)
            rot[1] = _damp(rot[1], self.damping[0], dt)
        return _apply_rot(orient, rot)

    def step(self, pos, raw_orient, point, fov, dt):
        sx, sy = self.screen
        dead = (sx - self.dead[0] / 2, sy - self.dead[1] / 2, sx + self.dead[0] / 2, sy + self.dead[1] / 2)
        bx = self.bias[0] * (self.soft[0] - self.dead[0])
        by = self.bias[1] * (self.soft[1] - self.dead[1])
        hard = (sx - self.soft[0] / 2 + bx, sy - self.soft[1] / 2 + by, sx + self.soft[0] / 2 + bx, sy + self.soft[1] / 2 + by)
        if self.prev is None:
            orient = look_rotation(_mv(raw_orient, [0.0, 0.0, 1.0]), _UP)
            rect = (sx, sy, sx, sy) if self.center_on_activate else dead
            orient = self._to_bounds(orient, rect, point, pos, fov, -1)
        else:
            ppos, ppoint, porient, poff = self.prev
            d = [ppoint[k] - ppos[k] for k in range(3)]
            if _dot(d, d) < 1e-10:
                orient = look_rotation(_mv(porient, [0.0, 0.0, 1.0]), _UP)
            else:
                orient = _apply_rot(look_rotation(d, _UP), [-poff[0], -poff[1]])
            orient = self._to_bounds(orient, dead, point, pos, fov, dt)
            orient = self._to_bounds(orient, hard, point, pos, fov, -1)
        off = _rot_to_target(orient, [point[k] - pos[k] for k in range(3)])
        self.prev = (pos, point, orient, off)
        return orient


# ------------------------------------------------------------------ one sequence
class Project:
    def __init__(self, path: str) -> None:
        self.assets = os.path.join(path, "ExportedProject", "Assets")
        self.guid = {}
        for meta in glob.glob(os.path.join(self.assets, "**", "*.meta"), recursive=True):
            m = re.search(r"guid: (\w+)", _read(meta))
            if m:
                self.guid[m.group(1)] = meta[:-5]

    def script(self, body: str) -> str:
        _, g = _ref(_field(body, "m_Script"))
        return os.path.splitext(os.path.basename(self.guid.get(g, "?")))[0] if g else ""


def collect_tracks(proj: Project, playable: str) -> list[dict]:
    """Every track of a TimelineAsset (group tracks flattened), with clips."""
    text = _read(playable)
    local = _docs(text)
    out = []

    def clip_of(asset_ref):
        _, g = _ref(asset_ref)
        f = proj.guid.get(g or "")
        if not f or not f.endswith(".asset"):
            return None, None
        body = _read(f)
        m = re.search(r"m_Clip: \{fileID: \d+, guid: (\w+)", body)
        anim = proj.guid.get(m.group(1)) if m else None
        return anim, (None if m else f)

    def visit(body: str, guid: str | None) -> None:
        kind = proj.script(body)
        track = {"name": (_field(body, "m_Name") or "").strip("'"), "type": kind, "guid": guid,
                 "muted": _field(body, "m_Muted") == "1", "clips": [], "infinite": None}
        for c in re.split(r"\n  - m_Version", body)[1:]:
            anim, node = clip_of(_field(c, "m_Asset"))
            track["clips"].append({"start": float(_field(c, "m_Start") or 0),
                                   "duration": float(_field(c, "m_Duration") or 0),
                                   "clip_in": float(_field(c, "m_ClipIn") or 0),
                                   "time_scale": float(_field(c, "m_TimeScale") or 1),
                                   "anim": anim, "node": node})
        _, ig = _ref(_field(body, "m_InfiniteClip"))
        if ig and proj.guid.get(ig, "").endswith(".anim"):
            track["infinite"] = proj.guid[ig]
        out.append(track)
        children = body.split("m_Children:")[1].split("m_Clips")[0] if "m_Children:" in body else ""
        for g in re.findall(r"guid: (\w+), type: 2", children):
            f = proj.guid.get(g)
            if f:
                visit(_read(f), g)
        for fid in re.findall(r"- \{fileID: (\d+)\}\s*\n", children):
            if fid in local:
                visit(local[fid][1], None)

    root = local["11400000"][1]
    for fid in re.findall(r"- \{fileID: (\d+)\}", root.split("m_Tracks:")[1].split("m_FixedDuration")[0]):
        if fid in local:
            visit(local[fid][1], None)
    return out


def node_clip(proj: Project, node: str | None) -> str | None:
    """ManualAnimationNode and friends: the AnimationClip they play."""
    if not node:
        return None
    m = re.search(r"(?:clip|m_Clip|animationClip)\w*: \{fileID: \d+, guid: (\w+)", _read(node), re.I)
    return proj.guid.get(m.group(1)) if m else None


def sequence(proj: Project, prefab: str) -> dict | None:
    sys.path.insert(0, HERE)
    from unity_yaml import evaluate, parse_anim
    docs = _docs(_read(prefab))
    go_name = {f: _field(b, "m_Name") for f, (c, b) in docs.items() if c == "1"}
    tf, tf_of_go = {}, {}
    for f, (c, b) in docs.items():
        if c in ("4", "224"):
            go = _ref(_field(b, "m_GameObject"))[0]
            tf[f] = {"go": go, "name": go_name.get(go), "father": _ref(_field(b, "m_Father"))[0],
                     "pos": _vec(_field(b, "m_LocalPosition")), "rot": _vec(_field(b, "m_LocalRotation")),
                     "scale": _vec(_field(b, "m_LocalScale"))}
            tf_of_go[go] = f

    def chain(f):                       # root .. f
        out = []
        while f in tf:
            out.append(f)
            f = tf[f]["father"]
        return out[::-1]

    def rel_path(anc, f):               # Unity animation path of f below anc
        names = [tf[x]["name"] for x in chain(f)]
        i = chain(f).index(anc)
        return "/".join(names[i + 1:])

    director = next((b for c, b in docs.values() if c == "320"), None)
    if director is None:
        return None
    playable = proj.guid.get(_ref(_field(director, "m_PlayableAsset"))[1] or "")
    if not playable:
        return None
    bindings = dict(re.findall(r"key: \{fileID: \d+, guid: (\w+), type: 2\}\s*\n\s*value: \{fileID: (-?\d+)\}", director))
    tracks = collect_tracks(proj, playable)

    vcam = next(((f, b) for f, (c, b) in docs.items() if c == "114" and "m_Lens:" in b and "m_Priority" in b), None)
    if vcam is None:                    # no camera rig: stays on the home camera
        ends = [c["start"] + c["duration"] for t in tracks if not t["muted"] for c in t["clips"]]
        if not ends:
            return None
        duration = max(ends)
        frames = [{"t": round(min(i / FPS, duration), 5)} for i in range(int(round(duration * FPS)) + 1)]
        return _sequence_data(proj, prefab, playable, tracks, duration, frames,
                              {"camera_enabled": False, "camera_rig": None})
    vcam_go = _ref(_field(vcam[1], "m_GameObject"))[0]
    vcam_tf = tf_of_go[vcam_go]
    look_tf = _ref(_field(vcam[1], "m_LookAt"))[0]
    look_tf = look_tf if look_tf in tf else None
    lens = {k: float(re.search(k + r": ([-\d.eE]+)", vcam[1]).group(1))
            for k in ("FieldOfView", "NearClipPlane", "FarClipPlane", "Dutch")}
    vcam_enabled = _field(vcam[1], "m_Enabled") != "0"
    # the live pipeline is the child the vcam names in m_ComponentOwner - a rig can carry a
    # second, unused "cm" (104701 touch1/2: an undamped leftover beside the damped one)
    owner = _ref(_field(vcam[1], "m_ComponentOwner"))[0]
    owner_go = tf[owner]["go"] if owner in tf else None
    composers = [(b, _ref(_field(b, "m_GameObject"))[0]) for c, b in docs.values()
                 if c == "114" and "m_TrackedObjectOffset" in b]
    composer_body = next((b for b, g in composers if g == owner_go), composers[0][0] if composers else None)
    composer = Composer(composer_body)
    offset = composer.offset

    # the camera AnimationTrack: bound to an Animator on the rig above the vcam
    rig_chain = chain(vcam_tf)
    cam_track = anim_tf = None
    for t in tracks:
        target = bindings.get(t["guid"] or "")
        if t["muted"] or not target or target not in docs or docs[target][0] != "95":
            continue
        a_go = _ref(_field(docs[target][1], "m_GameObject"))[0]
        if tf_of_go.get(a_go) in rig_chain:
            cam_track, anim_tf = t, tf_of_go[a_go]
            break
    if cam_track is None:               # static shot (e.g. the action1_1 loop): the prefab pose
        cam_track = {"clips": [], "infinite": None}
        anim_tf = rig_chain[0]
    clips = {p: parse_anim(p) for p in {c["anim"] for c in cam_track["clips"] if c["anim"]} | ({cam_track["infinite"]} - {None})}
    for c in clips.values():            # hashed script attributes -> Cinemachine field names
        for attr in list(c.floats):
            m = re.match(r"script_0x([0-9A-Fa-f]+)", attr)
            if m and int(m.group(1), 16) in FIELD_BY_CRC:
                c.floats[FIELD_BY_CRC[int(m.group(1), 16)]] = c.floats.pop(attr)

    def active(t):
        for c in cam_track["clips"]:
            if c["anim"] and c["start"] <= t < c["start"] + c["duration"]:
                return clips[c["anim"]], c["clip_in"] + (t - c["start"]) * c["time_scale"]
        if cam_track["infinite"]:
            return clips[cam_track["infinite"]], t
        if not clips:
            return None, t
        near = min((c for c in cam_track["clips"] if c["anim"]),
                   key=lambda c: min(abs(t - c["start"]), abs(t - c["start"] - c["duration"])))
        tt = near["clip_in"] + (0 if t < near["start"] else near["duration"] * near["time_scale"])
        return clips[near["anim"]], tt

    static_above = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    for f in chain(anim_tf)[:-1]:
        static_above = mul(static_above, trs(tf[f]["pos"], tf[f]["rot"], tf[f]["scale"]))

    def world(target, clip, lt):
        m = mul(static_above, trs(tf[anim_tf]["pos"], tf[anim_tf]["rot"], tf[anim_tf]["scale"]))
        path_chain = chain(target)
        for f in path_chain[path_chain.index(anim_tf) + 1:]:
            p = rel_path(anim_tf, f)
            pos = evaluate(clip.position[p], lt, 3) if clip and p in clip.position else tf[f]["pos"]
            rot = evaluate(clip.rotation[p], lt, 4) if clip and p in clip.rotation else tf[f]["rot"]
            n = math.sqrt(sum(c * c for c in rot)) or 1.0
            m = mul(m, trs(pos, tuple(c / n for c in rot), tf[f]["scale"]))
        return m

    def static_world(f):
        m = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        for x in chain(f):
            m = mul(m, trs(tf[x]["pos"], tf[x]["rot"], tf[x]["scale"]))
        return m

    # timeline length: the latest clip end on an unmuted track (or the camera clip)
    ends = [c["start"] + c["duration"] for t in tracks if not t["muted"] for c in t["clips"]]
    if cam_track["infinite"]:
        ends.append(clips[cam_track["infinite"]].stop_time)
    duration = max(ends)

    # camera cuts restart the composer (no damping carried across a cut)
    cut_starts = sorted(round(c["start"] * SIM_HZ) for t in tracks
                        if t["type"] == "StoryTimelineCameraCutTypeTrack" and not t["muted"] for c in t["clips"])
    frames = []
    ticks = int(round(duration * SIM_HZ))
    step = SIM_HZ // FPS
    for tick in range(ticks + 1):
        t = min(tick / SIM_HZ, duration)
        if tick in cut_starts and tick:
            composer.reset()
        clip, lt = active(t)
        fl = lambda k, d: evaluate(clip.floats[k], lt, 1)[0] if clip and k in clip.floats else d
        cam = world(vcam_tf, clip, lt)
        pos = [cam[k][3] for k in range(3)]
        dutch = fl("m_Lens.Dutch", lens["Dutch"])
        fov = fl("m_Lens.FieldOfView", lens["FieldOfView"])
        raw = [row[:3] for row in cam[:3]]
        target = None
        if look_tf:
            lw = world(look_tf, clip, lt) if anim_tf in chain(look_tf) else static_world(look_tf)
            off = [fl(f"m_TrackedObjectOffset.{a}", offset[i2]) for i2, a in enumerate("xyz")]
            target = [lw[k][3] + sum(lw[k][j] * off[j] for j in range(3)) for k in range(3)]
            r = composer.step(pos, raw, target, fov, 1.0 / SIM_HZ)
        else:
            r = raw
        if tick % step:
            continue
        r = roll(r, dutch)
        frames.append({"t": round(t, 5), "position": [round(c, 6) for c in pos],
                       "rotation": [round(c, 7) for c in m_quat(r)],
                       "fov": round(fov, 4), "dutch": round(dutch, 4),
                       "look_at": [round(c, 6) for c in target] if target else None})

    held = hard_cuts(frames)
    return _sequence_data(proj, prefab, playable, tracks, duration, frames, {
        "held_frames": held,
        "lens": lens, "composer": composer.describe(), "camera_enabled": vcam_enabled,
        "look_at": tf[look_tf]["name"] if look_tf else None,
        "camera_rig": "/".join(tf[x]["name"] for x in chain(vcam_tf))})


CUT_MOVE = 0.25            # m per frame: a step this long (or 15x the shot's median) is a cut
CUT_TURN = 20.0            # degrees per frame, likewise
CUT_BLUR = 3               # a jump run this short (<= 0.1 s) is one cut, not a camera move


def hard_cuts(frames: list[dict]) -> list[int]:
    """Collapse sub-0.1 s camera whips into clean cuts; returns the frames held.

    Some shot changes are authored as a few keys that fling the camera across the set
    (104701 debut 3.40-3.50 s: down, back, sideways, one axis per key) or leave a stray
    key at an old shot's spot (7.30 s). At 30 fps they are one or two frames of unrelated
    views - flashes. A run of <= CUT_BLUR consecutive cut-sized steps holds the outgoing
    shot and jumps once, on the frame the new shot starts. Longer runs are real moves
    (104701 touch2's 0.17 s push-in) and are kept."""
    pos = [f.get("position") for f in frames]
    if len(frames) < 3 or any(p is None for p in pos):
        return []
    steps = [math.dist(a, b) for a, b in zip(pos, pos[1:])]
    turns = [math.degrees(2 * math.acos(min(1.0, abs(_dot4(a["rotation"], b["rotation"])))))
             for a, b in zip(frames, frames[1:])]
    move = max(CUT_MOVE, 15 * sorted(steps)[len(steps) // 2])
    fast = [s > move or r > CUT_TURN for s, r in zip(steps, turns)]
    held, i = [], 0
    while i < len(fast):
        if not fast[i]:
            i += 1
            continue
        j = i
        while j < len(fast) and fast[j]:
            j += 1
        if 1 < j - i <= CUT_BLUR:                    # frames i+1 .. j-1 are the whip
            for k in range(i + 1, j):
                frames[k] = {**frames[i], "t": frames[k]["t"]}
                held.append(k)
        i = j
    return held


def _dot4(a, b):
    return sum(x * y for x, y in zip(a, b))


def _sequence_data(proj: Project, prefab: str, playable: str, tracks: list[dict], duration: float,
                   frames: list[dict], camera: dict) -> dict:
    """Everything but the camera: character clip schedule, facial clips, audio cues, cuts."""
    schedule = []
    for t in tracks:
        if t["muted"] or "tpose" not in t["name"] or t["name"].count("/") > 1:
            continue
        if t["type"] not in ("AnimationTrack", "ManualAnimatorTrack"):
            continue
        for c in t["clips"]:
            anim = c["anim"] or node_clip(proj, c["node"])
            name = os.path.splitext(os.path.basename(anim))[0] if anim else None
            if name and name.lower().startswith(("facialani", "recorded")):
                continue
            schedule.append({"clip": name, "start": round(c["start"], 5), "duration": round(c["duration"], 5),
                             "clip_in": round(c["clip_in"], 5), "time_scale": c["time_scale"], "anim": anim})
    schedule.sort(key=lambda s: s["start"])
    # facial: "tpose" tracks whose clips drive blend shapes (SkinnedMeshRenderer, typeID 137)
    facial = []
    for t in tracks:
        if t["muted"] or "tpose" not in t["name"] or t["name"].count("/") > 1                 or t["type"] not in ("AnimationTrack", "ManualAnimatorTrack"):
            continue
        entries = [dict(c, anim=c["anim"] or node_clip(proj, c["node"])) for c in t["clips"]]
        entries = [c for c in entries if c["anim"]]
        if t["infinite"]:
            entries.append({"anim": t["infinite"], "start": 0.0, "duration": None, "clip_in": 0.0,
                            "time_scale": 1.0, "infinite": True})
        for c in entries:
            body = _read(c["anim"])
            n137 = body.count("typeID: 137")
            if not n137:
                continue
            stop = float(_field(body.split("m_AnimationClipSettings:")[1], "m_StopTime") or 0)
            facial.append({"clip": _field(body, "m_Name"), "stop": stop, "bindings": n137,
                           "start": c["start"], "duration": c["duration"], "clip_in": c["clip_in"],
                           "time_scale": c["time_scale"], "infinite": bool(c.get("infinite"))})
    cuts = []
    for t in tracks:
        if t["type"] == "StoryTimelineCameraCutTypeTrack" and not t["muted"]:
            for c in t["clips"]:
                body = _read(c["node"]) if c["node"] else ""
                cuts.append({"start": round(c["start"], 5), "duration": round(c["duration"], 5),
                             "blend_style": int(_field(body, "m_Style") or 0),
                             "blend_time": float(_field(body, "m_Time") or 0)})
    props = sorted({t["name"].split("/")[-1] for t in tracks
                    if not t["muted"] and t["name"].count("/") > 1 and t["type"].endswith("AnimationTrack")})
    # audio the timeline itself starts (StoryCriwareTrack): the scene music/SFX, and on some
    # sequences (debut) the voice line; the rest of the voice is started by game code
    audio = []
    for t in tracks:
        if t["type"] == "StoryCriwareTrack" and not t["muted"]:
            for c in t["clips"]:
                body = _read(c["node"]) if c["node"] else ""
                sheet, cue = _field(body, "mCueSheet"), _field(body, "mCueName")
                if sheet and cue:
                    audio.append({"sheet": sheet, "cue": cue, "start": round(c["start"], 5),
                                  "voice": _field(body, "mIsVoice") == "1" or sheet.startswith("vo_")})
    return {"sequence": os.path.splitext(os.path.basename(prefab))[0],
            "timeline": os.path.relpath(playable, proj.assets), "fps": FPS, "duration": round(duration, 5),
            "space": "Unity world: left-handed, Y up, metres; the timeline root (where the character stands) at the origin",
            **camera,
            "character_schedule": schedule, "facial": facial, "camera_cuts": cuts, "animated_props": props,
            "audio_cues": audio, "frames": frames}




# ------------------------------------------------------------------ character: merged clip
def clip_set_job(cid: str, skin: str, kind: str) -> dict | None:
    """The export_anim_fbx job (rig FBX, prefab, stem) of the clip set the sequence's
    clips come from - so the character FBX is built exactly like the per-clip exports
    (same rest key, frame range, basis fit, root fold, FBX settings)."""
    sys.path.insert(0, HERE)
    import export_anim_fbx
    jobs = [j for j in export_anim_fbx.plan(cid, None, None) if j["model"] and j["prefab"]]
    want = [f"{skin}dlc"] if kind == "dlc" else [f"{skin}bat", f"{cid}00bat"]
    for w in want:
        for j in jobs:
            if os.path.basename(j["dir"]).lower() == w:
                return j
    return None


def win_source(job: dict) -> str | None:
    wins = sorted(c for c in job["clips"] if c.lower().startswith("win"))
    return os.path.join(job["dir"], wins[0]) if wins else None


def write_merged_clip(path: str, name: str, schedule: list[dict], duration: float) -> None:
    """Sample the schedule (clip excerpts at timeline offsets) into one Unity clip, one
    key per frame; parse_anim / export_anim_fbx read it like any ripped clip. Timeline
    semantics: outside every clip the nearest clip holds its edge pose."""
    sys.path.insert(0, HERE)
    from unity_yaml import evaluate, parse_anim
    clips = {s["anim"]: parse_anim(s["anim"]) for s in schedule}
    groups = ("rotation", "position", "scale", "euler")
    paths = {g: sorted(set().union(*(getattr(c, g).keys() for c in clips.values()))) for g in groups}
    n = int(round(duration * FPS)) + 1

    def pick(t):
        for s in schedule:
            if s["start"] - 1e-6 <= t < s["start"] + s["duration"] - 1e-6:
                return s, s["clip_in"] + (t - s["start"]) * s["time_scale"]
        before = [s for s in schedule if s["start"] <= t]
        s = before[-1] if before else schedule[0]
        return s, s["clip_in"] + (s["duration"] * s["time_scale"] if before else 0.0)

    keys = {g: {p: [] for p in paths[g]} for g in groups}
    for i in range(n):
        t = i / FPS
        s, lt = pick(t)
        c = clips[s["anim"]]
        lt = min(max(lt, 0.0), c.stop_time)
        for g in groups:
            ncomp = 4 if g == "rotation" else 3
            for p in paths[g]:
                src, lt2 = getattr(c, g).get(p), lt
                if src is None:                 # path missing in this clip: any clip that has it
                    other = next(cc for cc in clips.values() if p in getattr(cc, g))
                    src, lt2 = getattr(other, g)[p], min(lt, other.stop_time)
                keys[g][p].append((t, evaluate(src, lt2, ncomp)))

    section = {"rotation": "m_RotationCurves", "position": "m_PositionCurves",
               "scale": "m_ScaleCurves", "euler": "m_EulerCurves"}

    def vec(v):
        return "{" + ", ".join(f"{a}: {x:.9g}" for a, x in zip("xyzw", v)) + "}"

    out = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:", "--- !u!74 &7400000", "AnimationClip:",
           f"  m_Name: {name}"]
    for g in ("rotation", "euler", "position", "scale"):
        if not paths[g]:
            out.append(f"  {section[g]}: []")
            continue
        out.append(f"  {section[g]}:")
        zero = vec((0.0,) * (4 if g == "rotation" else 3))
        for p in paths[g]:
            out += ["  - curve:", "      serializedVersion: 2", "      m_Curve:"]
            for t, v in keys[g][p]:
                out += ["      - serializedVersion: 3", f"        time: {t:.9g}", f"        value: {vec(v)}",
                        f"        inSlope: {zero}", f"        outSlope: {zero}"]
            out += ["      m_PreInfinity: 2", "      m_PostInfinity: 2", f"    path: {p}"]
    out += ["  m_FloatCurves: []", f"  m_SampleRate: {FPS}", "  m_AnimationClipSettings:",
            "    m_StartTime: 0", f"    m_StopTime: {duration:.9g}", "    m_LoopTime: 0"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def export_characters(cid: str, job: dict, merged_dir: str, outputs: list[tuple[str, str, str | None]]) -> None:
    """Run the character exporter on merged clips: (clip name, output path, morph JSON).
    Morph JSONs become shape-key animation (standard FBX blend-shape channel curves)."""
    sys.path.insert(0, HERE)
    import export_anim_fbx
    tmp_out = os.path.join(CACHE, "_char_out")
    shutil.rmtree(tmp_out, ignore_errors=True)
    j = dict(job, dir=merged_dir, clips=[f"{n}.anim" for n, _, _ in outputs], prefix="",
             morphs={n: m for n, _, m in outputs if m})
    payload = os.path.join(CACHE, "_char_job.json")
    json.dump({"cid": cid, "out": tmp_out, "dry_run": False, "no_fold_root": False,
               "script_dir": HERE, "jobs": [j]}, open(payload, "w"))
    r = subprocess.run([export_anim_fbx.find_blender(), "-b", "-noaudio", "-P",
                        os.path.abspath(export_anim_fbx.__file__), "--", payload],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if re.match(r"\s+\[\d+/\d+\]", line) or "basis fit" in line:
            print("   ", line.strip())
    produced = glob.glob(os.path.join(tmp_out, cid, "*.fbx"))
    for n, dst, _ in outputs:
        tag = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9._-]", "_", n)).strip("_")
        src = next((p for p in produced if os.path.basename(p).endswith(f"@{tag}.fbx")), None)
        if src:
            shutil.move(src, dst)
        else:
            print(f"    ! character export missing for {n}")


# ------------------------------------------------------------------ facial, lips, audio
def voice_cue(skin: str, data: dict, lang: str | None) -> tuple[str, str, float] | None:
    """The sequence's voice line: (sheet, cue, start s). The timeline's own voice cue when it
    has one (debut: greet); otherwise the line the game starts with it, paired by name -
    touchN -> talkN, debut[N] -> greet[N], interact_* / wedding_touch_10N -> <same>_01 when
    that is the only variant (lengths match the sequences)."""
    for a in data.get("audio_cues", []):
        if a["voice"] and a["sheet"].startswith("vo_"):
            return a["sheet"], a["cue"], a["start"]
    sys.path.insert(0, HERE)
    import extract_voice
    sheet = f"vo_sys_{skin}"
    known = set(extract_voice.lips(sheet, lang or "zh"))
    seq = data["sequence"]
    m = re.fullmatch(r"touch(\d+)", seq)
    names = [f"talk{m.group(1)}"] if m else []
    m = re.fullmatch(r"debut(\d*)", seq)
    if m:
        names = [f"greet{m.group(1)}", "greet"] if m.group(1) else ["greet", "greet1"]
    if seq.startswith(("interact_", "wedding_touch_")):
        variants = sorted(k for k in known if k.startswith(f"v_s_{skin}_{seq}_"))
        plain = f"v_s_{skin}_{seq}_01"
        if plain in variants and all(v.startswith(plain) for v in variants):
            variants = [plain]                # _01 beside alternate takes of it (_01_管理员)
        # several different lines (wedding_touch_105: _01.._04) are placed by game code: no guess
        names = [variants[0][len(f"v_s_{skin}_"):]] if len(variants) == 1 else []
    for n in names:
        if f"v_s_{skin}_{n}" in known:
            return sheet, f"v_s_{skin}_{n}", 0.0
    return None
LIP_SHAPES = ("Mouth_a", "Mouth_i", "Mouth_u", "Mouth_e", "Mouth_o")   # CriLipsExPlayer A I U E O (I unused)


def rig_prefab(skin: str, cid: str, ui: bool) -> str | None:
    base = os.path.join(SAMPLE, cid, "ExportedProject", "Assets")
    stems = [f"{skin}ui_tpose", f"{cid}ui_tpose"] if ui else [f"{skin}_tpose", f"{cid}_tpose"]
    for stem in stems:
        hits = glob.glob(os.path.join(base, "**", f"{stem}.prefab"), recursive=True)
        if hits:
            return hits[0]
    return None


def ui_rig(skin: str, cid: str) -> tuple[str | None, str | None]:
    """The home-screen model: the DLC interactions play on it, and its Eye / Eyebrow /
    Mouth / Pupil meshes carry the morphs the facial clips drive."""
    fbx = next(iter(glob.glob(os.path.join(ROOT, "AG_fbx", cid, "**", f"{skin}ui_tpose.fbx"), recursive=True)), None)
    return fbx, rig_prefab(skin, cid, True)


def build_morphs(skin: str, data: dict, lang: str | None, path: str) -> int:
    """Per-frame morph values (0..1) for a sequence: the facial clips (decoded from the
    bundle - AssetRipper's YAML merges the unnamed blend-shape curves) placed on the
    timeline, then lip sync (the voice cue's pre-analysed A/I/U/E/O) over Mouth_a..o
    while the voice plays, as CriLipsExPlayer does after the Animator."""
    sys.path.insert(0, HERE)
    import bundle_deps
    import extract_voice
    import unity_clip
    n = len(data["frames"])
    chans: dict[tuple, list[float]] = {}
    if data.get("facial"):
        # facial curves live in the timeline bundle (109501: FacialAni) or inside the body
        # clips (104701: touch1x ...) in a dependency bundle - search both
        root = SOURCES["dlc"].format(skin=skin)
        index = bundle_deps.load_index()
        bundles = [root] + sorted({index.get(c.lower()) or index.get(c) for c in
                                   bundle_deps.externals(os.path.join(bundle_deps.ROOT, root))} - {None})
        raw = []
        for bnd in bundles:
            if "shader" not in bnd.lower():
                raw += unity_clip.load_clips(os.path.join(bundle_deps.ROOT, bnd), {f["clip"] for f in data["facial"]})
        for f in data["facial"]:
            clip = next((c for c in raw if c.name == f["clip"] and abs(c.stop - c.start - f["stop"]) < 0.05
                         and sum(b["typeID"] == 137 for b in c.bindings) == f["bindings"]), None)
            if clip is None:
                print(f"    ! facial clip {f['clip']} ({f['stop']:.2f}s) not found in the bundle")
                continue
            for bi, b in enumerate(clip.bindings):
                if b["typeID"] != 137:
                    continue
                vals = chans.setdefault((b["path"], b["attribute"]), [0.0] * n)
                for i in range(n):
                    t = i / FPS
                    if f["infinite"]:
                        lt = t
                    elif f["start"] <= t < f["start"] + f["duration"]:
                        lt = f["clip_in"] + (t - f["start"]) * f["time_scale"]
                    else:
                        continue
                    vals[i] = clip.value(bi, min(max(lt, 0.0), clip.stop - clip.start))[0] / 100.0
    vc = voice_cue(skin, data, lang) if lang else None
    lip = extract_voice.lips(vc[0], lang).get(vc[1]) if vc else None
    lip_start = vc[2] if vc else 0.0
    if lip:
        # CriLipsExPlayer.LateUpdate sets weight = raw/10 on the mouth's A/I/U/E/O channels -
        # but I is -1 (the constructor default, and the field is not serialized), so the game
        # never applies the "i" vowel: only a/u/e/o. The data is 30 fps (length_ms / frames
        # = 33.3); the game indexes it by playback ms / 33, which runs the mouth 1% fast -
        # 0.15-0.2 s ahead of the voice by the end of a 30 s line, closing before the words
        # do. Sampled at the true 30 fps it tracks the voice from start to end.
        mouth = zlib.crc32(b"Mouth")
        aiueo = lip["aiueo"]
        for k, shape in enumerate(LIP_SHAPES):
            if k == 1:
                continue
            vals = chans.setdefault((mouth, zlib.crc32(shape.encode())), [0.0] * n)
            for i in range(n):
                ms = (i / FPS - lip_start) * 1000
                j = int(ms * lip["fps"] / 1000 + 1e-6)
                if ms >= 0 and j < len(aiueo):
                    vals[i] = aiueo[j][k]
    spec = {"fps": FPS, "frames": n, "lips": f"{vc[1]} @{lip_start:g}s ({lang})" if lip else None,
            "note": "mesh / shape are CRC32 of the SkinnedMeshRenderer path and the blend-shape "
                    "channel name (e.g. CRC32('Mouth'), CRC32('Mouth_a')); values 0..1",
            "channels": [{"mesh": m, "shape": s, "values": [round(v, 4) for v in vals]}
                         for (m, s), vals in chans.items()]}
    json.dump(spec, open(path, "w"))
    return len(chans)


def mix_wavs(paths: list, out: str) -> float:
    """Sum WAVs - paths, or (path, start seconds) - into one 16-bit stereo WAV at the highest rate,
    resampling the others (polyphase); scaled down only if the sum would clip.
    Returns the peak before scaling."""
    import wave
    from fractions import Fraction
    import numpy as np
    tracks = []
    for item in paths:
        f, start = (item, 0.0) if isinstance(item, str) else item
        w = wave.open(f)
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, w.getnchannels())
        a = a.astype(np.float64) / 32768
        if a.shape[1] == 1:
            a = np.repeat(a, 2, axis=1)
        tracks.append((a[:, :2], w.getframerate(), start))
    rate = max(r for _, r, _ in tracks)
    mixed = []
    for a, r, start in tracks:
        if r != rate:
            from scipy.signal import resample_poly
            q = Fraction(rate, r)
            a = resample_poly(a, q.numerator, q.denominator, axis=0)
        pad = int(round(start * rate))
        mixed.append((a, pad))
    m = np.zeros((max(len(a) + pad for a, pad in mixed), 2))
    for a, pad in mixed:
        m[pad:pad + len(a)] += a
    peak = float(np.abs(m).max()) if len(m) else 0.0
    gain = min(1.0, 0.98 / peak) if peak else 1.0
    w = wave.open(out, "wb")
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes((m * gain * 32767).round().astype("<i2").tobytes())
    w.close()
    return peak


def export_audio(skin: str, data: dict, out_dir: str, stem: str, lang: str) -> str | None:
    """One WAV per sequence (reze-engine plays a single audio track): every cue the timeline
    starts (scene music/SFX, a voice line) plus the voice line the game starts with it,
    mixed at their start times."""
    sys.path.insert(0, HERE)
    import extract_voice
    parts, labels = [], []
    for a in data.get("audio_cues", []):
        if a["voice"]:
            continue
        w = extract_voice.decode(a["sheet"], None).get(a["cue"])
        if w:
            parts.append((w, a["start"]))
            labels.append(a["cue"])
    if not data.get("audio_cues"):
        w = extract_voice.decode(f"ui_scene_{skin}", None).get(f"ui_scene_{skin}_{data['sequence']}")
        if w:
            parts.append((w, 0.0))
            labels.append(os.path.basename(w)[:-4])
    vc = voice_cue(skin, data, lang)
    if vc:
        w = extract_voice.decode(vc[0], lang).get(vc[1])
        if w:
            parts.append((w, vc[2]))
            labels.append(f"{vc[1]} [{lang}]")
    if not parts:
        return None
    out = os.path.join(out_dir, f"{stem}.wav")
    mix_wavs(parts, out)
    return " + ".join(labels) + f" -> {os.path.basename(out)}"


# ------------------------------------------------------------------ camera FBX (Blender)
def blender(payload: dict) -> None:
    sys.path.insert(0, HERE)
    from export_anim_fbx import find_blender
    path = os.path.join(CACHE, "_cam_job.json")
    json.dump(payload, open(path, "w"))
    r = subprocess.run([find_blender(), "-b", "-noaudio", "--factory-startup", "--python",
                        os.path.abspath(__file__), "--", "--blender", path], capture_output=True, text=True)
    for line in (r.stdout + r.stderr).splitlines():
        if line.startswith("CAMS") or "Traceback" in line or "Error:" in line:
            print("   ", line)


def run_in_blender(payload: str) -> None:
    import bpy
    import numpy as np
    from mathutils import Matrix
    sys.path.insert(0, HERE)
    from unity_yaml import parse_prefab
    job = json.load(open(payload))

    def import_fbx(path, anim=True):
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=path, use_anim=anim, automatic_bone_orientation=False,
                                 ignore_leaf_bones=False)
        return [o for o in bpy.data.objects if o not in before]

    # the rig exactly as export_anim_fbx imports it; Unity -> Blender fitted on its rest
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm = next(o for o in import_fbx(job["model"], anim=False) if o.type == "ARMATURE")
    nodes = parse_prefab(job["prefab"])

    def uworld(node):
        m = np.eye(4)
        m[:3, :3] = np.array(q_mat(node.rot)) * np.array(node.scale)
        m[:3, 3] = node.pos
        return (uworld(node.parent) @ m) if node.parent is not None else m

    by_name = {}
    for node in nodes.values():
        if node.parent is not None:
            by_name.setdefault(node.name, node)
    U, B = [], []
    for bone in arm.data.bones:
        node = by_name.get(bone.name)
        if node is not None:
            U.append(uworld(node)[:3, 3])
            B.append(np.array(arm.matrix_world @ bone.head_local))
    U, B = np.array(U), np.array(B)
    X = np.hstack([U, np.ones((len(U), 1))])
    sol, *_ = np.linalg.lstsq(X, B, rcond=None)
    A, t = sol[:3].T, sol[3]
    sv = np.linalg.svd(A, compute_uv=False)
    print(f"CAMS fit on {os.path.basename(job['model'])}: {len(U)} bones, "
          f"max residual {np.abs(X @ sol - B).max():.1e}, scale {sv.mean():.5f}")
    scene = bpy.context.scene

    for seq in job["sequences"]:
        data = json.load(open(seq["json"]))
        n = len(data["frames"])
        scene.render.fps = data["fps"]
        cam = bpy.data.objects.new(seq["stem"], bpy.data.cameras.new(seq["stem"]))
        scene.collection.objects.link(cam)
        cam.data.sensor_fit = "VERTICAL"
        cam.data.clip_start = data["lens"]["NearClipPlane"] * float(sv.mean())
        cam.data.clip_end = data["lens"]["FarClipPlane"] * float(sv.mean())
        for f, fr in enumerate(data["frames"]):         # frame f = timeline t * fps, as the clips
            r = np.array(q_mat(fr["rotation"]))
            fwd, up = A @ r[:, 2], A @ r[:, 1]
            zb = -fwd / np.linalg.norm(fwd)
            yb = up - zb * np.dot(up, zb)
            yb /= np.linalg.norm(yb)
            xb = np.cross(yb, zb)
            loc = A @ np.array(fr["position"]) + t
            m = Matrix.Identity(4)
            for i in range(3):
                m[i][0], m[i][1], m[i][2], m[i][3] = xb[i], yb[i], zb[i], loc[i]
            cam.matrix_world = m
            cam.data.angle_y = math.radians(fr["fov"])
            cam.keyframe_insert("location", frame=f)
            cam.keyframe_insert("rotation_euler", frame=f)
            cam.data.keyframe_insert("lens", frame=f)
        for db in (cam, cam.data):
            for fc in db.animation_data.action.fcurves:
                for k in fc.keyframe_points:
                    k.interpolation = "LINEAR"
        scene.frame_start, scene.frame_end = 0, n - 1
        for o in bpy.data.objects:
            o.select_set(o is cam)
        # export_anim_fbx.export's settings, so camera and character share one space
        bpy.ops.export_scene.fbx(filepath=seq["camera_fbx"], use_selection=True, apply_unit_scale=True,
                                 apply_scale_options="FBX_SCALE_NONE", object_types={"CAMERA"},
                                 add_leaf_bones=False, bake_anim=True, bake_anim_use_all_bones=True,
                                 bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False,
                                 bake_anim_force_startend_keying=True, bake_anim_step=1.0,
                                 bake_anim_simplify_factor=0.0, path_mode="STRIP")
        bpy.data.objects.remove(cam, do_unlink=True)
        print(f"CAMS wrote {os.path.basename(seq['camera_fbx'])} ({n} frames)")

    # what a consumer sees: both final FBXs imported fresh, rendered through the camera
    for seq in job["sequences"]:
        if not os.path.isfile(seq["character_fbx"]):
            continue
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        import_fbx(seq["character_fbx"])
        cam = next(o for o in import_fbx(seq["camera_fbx"]) if o.type == "CAMERA")
        scene.camera = cam
        f0, f1 = (int(x) for x in cam.animation_data.action.frame_range)
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x, scene.render.resolution_y = 640, 360
        os.makedirs(seq["preview"], exist_ok=True)
        for old in glob.glob(os.path.join(seq["preview"], "*.png")):
            os.remove(old)
        for k in range(6):
            f = f0 + (f1 - f0) * k // 5
            scene.frame_set(f)
            scene.render.filepath = os.path.join(seq["preview"], f"frame_{f:04d}.png")
            bpy.ops.render.render(write_still=True)


# ------------------------------------------------------------------ main
def main() -> int:
    if "--blender" in sys.argv:
        run_in_blender(sys.argv[sys.argv.index("--blender") + 1])
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skins", nargs="+", help="skin ids, e.g. 109501 104701")
    ap.add_argument("--only", help="comma list of sequences (win, debut, touch1, ...)")
    ap.add_argument("--no-fbx", action="store_true", help="JSON only")
    ap.add_argument("--lang", default="zh", help="voice / lip-sync languages, comma list; the first is "
                    "character.fbx and the audio, others add character.<lang>.fbx (e.g. zh,ja)")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    os.makedirs(CACHE, exist_ok=True)
    for skin in args.skins:
        cid = skin[:4]
        out_dir = os.path.join(ANIM_OUT, cid, "cameras")
        os.makedirs(out_dir, exist_ok=True)
        for kind in SOURCES:
            project = rip(skin, kind)
            if not project:
                continue
            proj = Project(project)
            job = clip_set_job(cid, skin, kind)
            langs = args.lang.split(",")
            if kind == "dlc" and job:                    # the interactions play on the home-screen model
                ui_fbx, ui_prefab = ui_rig(skin, cid)
                if ui_fbx and ui_prefab:
                    job = dict(job, model=ui_fbx, prefab=ui_prefab, stem=f"{skin}ui_tpose")
            prefabs = sorted(p for p in glob.glob(os.path.join(proj.assets, "**", "*.prefab"), recursive=True)
                             if "\n--- !u!320 " in _read(p))
            merged_dir = os.path.join(CACHE, "_merged", f"{skin}_{kind}")
            shutil.rmtree(merged_dir, ignore_errors=True)
            os.makedirs(merged_dir)
            done = []
            for prefab in prefabs:
                data = sequence(proj, prefab)
                if not data:
                    continue
                name = "win" if kind == "win" else data["sequence"]
                if name.startswith(f"{skin}ui_"):          # 109503ui_wedding_touch_101 -> wedding_touch_101
                    name = name[len(skin) + 3:]
                if only and name not in only:
                    continue
                data["sequence"] = name
                stem = f"{skin}@{name}"
                sched = data["character_schedule"]
                if kind == "win":               # the win clip is an external the rip leaves unresolved
                    src = win_source(job) if job else None
                    sched = [{"clip": os.path.splitext(os.path.basename(src))[0], "start": 0.0,
                              "duration": data["duration"], "clip_in": 0.0, "time_scale": 1.0,
                              "anim": src}] if src else []
                data["character_schedule"] = [{k: v for k, v in s.items() if k != "anim"} for s in sched]
                data["character_fbx"] = f"{stem}.character.fbx"
                js = os.path.join(out_dir, f"{stem}.camera.json")
                json.dump(data, open(js, "w"), indent=1)
                plan = ", ".join(f"{s['clip']}@{s['start']:g}s" for s in sched) or "-"
                print(f"{stem}: {data['duration']:.2f}s, {len(data['camera_cuts'])} cut(s), character: {plan}")
                if sched and all(s["anim"] and os.path.isfile(s["anim"]) for s in sched):
                    write_merged_clip(os.path.join(merged_dir, f"{stem}.anim"), stem, sched, data["duration"])
                if kind == "dlc" and not args.no_fbx:
                    audio = export_audio(skin, data, out_dir, stem, langs[0])
                    if audio:
                        print(f"   audio: {audio}")
                done.append((stem, js))
            if args.no_fbx or not done:
                continue
            if not job:
                print(f"   no clip set / rig for {skin} {kind} - JSON only")
                continue
            outputs = []
            for s_, js_ in done:
                merged = os.path.join(merged_dir, f"{s_}.anim")
                if not os.path.isfile(merged):
                    continue
                data_ = json.load(open(js_))
                if kind != "dlc":
                    outputs.append((s_, os.path.join(out_dir, f"{s_}.character.fbx"), None))
                    continue
                for li, lang in enumerate(langs):  # one character FBX per lip-sync language
                    mj = os.path.join(out_dir, f"{s_}.morphs.{lang}.json")
                    nch = build_morphs(skin, data_, lang, mj)
                    clip = s_ if li == 0 else f"{s_}__{lang}"
                    if li:
                        shutil.copyfile(merged, os.path.join(merged_dir, f"{clip}.anim"))
                    suffix = "" if li == 0 else f".{lang}"
                    outputs.append((clip, os.path.join(out_dir, f"{s_}.character{suffix}.fbx"), mj))
                    print(f"   morphs [{lang}]: {nch} channels -> {os.path.basename(mj)}")
            if outputs:
                export_characters(cid, job, merged_dir, outputs)
            live = []
            for s_, js_ in done:
                if json.load(open(js_)).get("camera_enabled", True):
                    live.append((s_, js_))
                    continue
                # the rig's vcam is disabled: the game stays on its home camera (placed by
                # game code), so the sequence has no camera of its own
                print(f"   {s_}: virtual camera disabled in the timeline - no camera (home camera)")
                stale = os.path.join(out_dir, f"{s_}.camera.fbx")
                if os.path.isfile(stale):
                    os.remove(stale)
            if live:
                blender({"model": job["model"], "prefab": job["prefab"],
                         "sequences": [{"stem": s, "json": js,
                                        "camera_fbx": os.path.join(out_dir, f"{s}.camera.fbx"),
                                        "character_fbx": os.path.join(out_dir, f"{s}.character.fbx"),
                                        "preview": os.path.join(out_dir, f"{s}_preview")} for s, js in live]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
