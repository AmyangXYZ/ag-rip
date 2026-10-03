#!/usr/bin/env python3
r"""DLC fidelity checks V1 (completeness) and V2 (conversion), docs/dlc-fidelity-plan.md.

    python agtools/dlc_verify.py                       every AG_dlc_play/<dlc>, every take
    python agtools/dlc_verify.py AG_dlc_play/102202-塞勒涅-沉醉旖旎 [--take touch2]
    python agtools/dlc_verify.py --v1 | --v2           one check only
    python agtools/dlc_verify.py --out issues.md       the issue list (default AG_dlc_play/verify_issues.md)
    python agtools/dlc_verify.py <dlc> --v2 --zips DIR  V2 against a verification build's zips

V1, completeness (Unity project vs recording). For each take, the timeline is read the
way the game plays it (P08Main HeroUITimelineBrain, herouitimeline.lua PlayAction):
  - every track and clip, with the class it runs: a Unity package class, a game class
    ported in agtools/unity/dlc (or charfx), or an AssetRipper STUB (fields only: in
    the reference project it does nothing - a track that is not even a TrackAsset)
  - every output track's binding by the game's rules: the brain for HeroUITimelineBrain
    tracks, the component under "@path" for Animator / CharacterEffect / ... tracks,
    else the prefab's own binding (m_SceneBindings)
  - control clips' exposed references (agref_<id>) against the director's table
  - signals/markers, material curves whose property hash is still unresolved
  - game scripts on the sequence's and the model's objects that are stubs here
From that it predicts, per clip frame, which renderers of the sequence are drawn
(prefab active flags, control clips (activation), activation tracks, m_IsActive /
m_Enabled curves of the animation tracks) and compares with the recording's "on"
flags (web/data/<take>.frames.jsonl, line k = clip frame k + lead). An expected
renderer the recording lacks, or one drawn on other frames than predicted, is an issue.

V2, conversion (recording vs reze zip, reze/<dlc>-<take>.zip):
  - every renderer the recording draws during the take is accounted for: the stage
    (stage.json), a prop (by group), an effect (by class label) or deliberately left
    out (the character - the user's model is drawn instead)
  - every exported texture round-trips: decoded RGBA equal to the recording's source
    (stage textures/*.webp, props tex/*.png)
  - effect windows against the recording's alive particles, prop visibility against
    the recording's on frames, keyed uniforms against the recorded property blocks,
    all on the clip clock
Writes one issue list grouped by pipeline stage (reconstruction, recording,
conversion, playback).
"""
from __future__ import annotations

import argparse
import functools
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PLAY = os.path.join(ROOT, "AG_dlc_play")
HOTFIX = os.path.join(ROOT, "AG_cache", "re", "hotfix")
PORTS = [os.path.join(HERE, "unity", "dlc"), os.path.join(HERE, "unity", "charfx")]
UNITY_BUILTIN = r"C:\Program Files\Unity\Hub\Editor\6000.6.1f1\Editor\Data\Resources\PackageManager\BuiltInPackages"
RATE = 30
STAGES = ("reconstruction", "recording", "conversion", "playback")

RENDERERS = {"23": "mesh", "137": "skinned", "199": "particles", "96": "trail", "120": "line"}
CLASS_IDS = {"1": "GameObject", "23": "MeshRenderer", "137": "SkinnedMeshRenderer", "199": "ParticleSystemRenderer",
             "96": "TrailRenderer", "120": "LineRenderer", "198": "ParticleSystem", "95": "Animator", "4": "Transform"}
# the binding types of Unity's own tracks
PACKAGE_BINDING = {"AnimationTrack": "Animator", "ActivationTrack": "GameObject", "ControlTrack": None,
                   "AudioTrack": "AudioSource", "SignalTrack": "SignalReceiver", "GroupTrack": None,
                   "PlayableTrack": None, "CinemachineTrack": "CinemachineBrain", "MarkerTrack": None}
# the types HeroUITimelineBrain.BindPlayableDirector binds (the rest keep the prefab's binding)
BRAIN_BINDS = {"Animator", "CharacterEffect", "CharacterEffectOverrider", "CriLipsExPlayer", "SceneSetting"}


class Issues:
    def __init__(self):
        self.items = []     # (stage, dlc, take, kind, text)

    def add(self, stage, dlc, take, kind, text):
        assert stage in STAGES, stage
        self.items.append((stage, dlc, take, kind, text))

    def markdown(self):
        out = ["# DLC fidelity issues (agtools/dlc_verify.py)", ""]
        out.append(f"{len(self.items)} issue(s). Clip frames at {RATE}/s; a window is [first, last] frame.")
        out.append("")
        for stage in STAGES:
            rows = [i for i in self.items if i[0] == stage]
            out.append(f"## {stage.capitalize()} ({len(rows)})")
            out.append("")
            if not rows:
                out += ["none", ""]
                continue
            kinds = defaultdict(list)
            for _, dlc, take, kind, text in rows:
                kinds[kind].append((dlc, take, text))
            for kind, lst in kinds.items():
                out.append(f"### {kind} ({len(lst)})")
                out.append("")
                for dlc, take, text in lst:
                    where = f"{dlc}" + (f" {take}" if take else "")
                    out.append(f"- **{where}**: {text}")
                out.append("")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------- Unity YAML
DOC = re.compile(r"--- !u!(\d+) &(-?\d+)[^\n]*\n(.*?)(?=\n--- |\Z)", re.S)


@functools.lru_cache(maxsize=256)
def docs(path):
    """{fileID: (classID, body)} of a Unity YAML file."""
    text = open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    return {m.group(2): (m.group(1), m.group(3)) for m in DOC.finditer(text)}


def fget(body, key, default=None):
    m = re.search(rf"^\s*{re.escape(key)}: ?(.*)$", body, re.M)
    return m.group(1).strip() if m else default


def ref(s):
    """'{fileID: 1, guid: abc, type: 2}' -> (fileID, guid or None)."""
    if not s:
        return None
    m = re.search(r"fileID: (-?\d+)(?:, guid: (\w+))?", s)
    return (m.group(1), m.group(2)) if m else None


def ref_list(body, key):
    """The {fileID, guid} entries of a YAML list field."""
    m = re.search(rf"^(\s*){re.escape(key)}:\s*\n((?:\1[- ] .*\n?)*)", body + "\n", re.M)
    if not m:
        return []
    return [ref(l) for l in m.group(2).splitlines() if "fileID" in l]


class Project:
    """An exported DLC Unity project: guid -> asset, script guid -> class."""

    def __init__(self, dlc_dir):
        self.dlc = dlc_dir
        self.proj = os.path.join(dlc_dir, "unity", "ExportedProject")
        self.assets = os.path.join(self.proj, "Assets")
        self.spec = json.load(open(os.path.join(self.proj, "ag_dlc.json"), encoding="utf-8"))
        self.guid = {}
        self.script = {}        # guid -> (class, status, path): status port / stub / package / project
        for dp, _, fns in os.walk(self.assets):
            for fn in fns:
                if not fn.endswith(".meta"):
                    continue
                p = os.path.join(dp, fn)
                g = _meta_guid(p)
                if not g:
                    continue
                self.guid[g] = p[:-5]
                if fn.endswith(".cs.meta"):
                    src = p[:-5]
                    head = open(src, encoding="utf-8-sig", errors="replace").read(600)
                    status = "stub" if "Fields only" in head else ("port" if "Ported from" in head else "project")
                    self.script[g] = (fn[:-8], status, src)
        for root in (os.path.join(self.proj, "Library", "PackageCache"), UNITY_BUILTIN):
            if not os.path.isdir(root):
                continue
            for dp, _, fns in os.walk(root):
                if "timeline" not in dp and "cinemachine" not in dp:
                    continue
                for fn in fns:
                    if fn.endswith(".cs.meta"):
                        g = _meta_guid(os.path.join(dp, fn))
                        if g and g not in self.script:
                            self.script[g] = (fn[:-8], "package", os.path.join(dp, fn[:-5]))

    def path(self, rel):
        return os.path.join(self.proj, rel.replace("/", os.sep))

    def script_of(self, body):
        r = ref(fget(body, "m_Script"))
        if not r or not r[1]:
            return ("?", "missing", None)
        return self.script.get(r[1], (f"guid:{r[1]}", "missing", None))

    def resolve(self, r, here):
        """A reference -> (file, fileID) or None."""
        if not r or r[0] == "0":
            return None
        if r[1]:
            f = self.guid.get(r[1])
            return (f, r[0]) if f else None
        return (here, r[0])


def _meta_guid(p):
    try:
        m = re.search(r"guid: (\w{32})", open(p, encoding="utf-8", errors="replace").read(300))
    except OSError:
        return None
    return m.group(1) if m else None


@functools.lru_cache(maxsize=None)
def game_binding_types():
    """class -> binding type name, from the decompiled game sources and the ports."""
    out = {}
    roots = [os.path.join(HOTFIX, d) for d in ("P08Timeline_src", "P08Main_src", "BattleSimulatorRender_src")] + PORTS
    for root in roots:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if not fn.endswith(".cs"):
                    continue
                text = open(os.path.join(dp, fn), encoding="utf-8-sig", errors="replace").read()
                for m in re.finditer(r"\[TrackBindingType\(typeof\((\w+)\)[^\]]*\]\s*(?:\[[^\]]*\]\s*)*public\s+(?:sealed\s+)?class\s+(\w+)", text):
                    out[m.group(2)] = m.group(1)
    return out


@functools.lru_cache(maxsize=None)
@functools.lru_cache(maxsize=None)
def _game_classes():
    out = {}
    for d in ("P08Timeline_src", "P08Main_src", "BattleSimulatorRender_src"):
        for dp, _, fns in os.walk(os.path.join(HOTFIX, d)):
            for fn in fns:
                if fn.endswith(".cs"):
                    p = os.path.join(dp, fn)
                    for c in re.findall(r"^\s*(?:public |internal )?(?:sealed |abstract )?class (\w+)",
                                        open(p, encoding="utf-8-sig", errors="replace").read(), re.M):
                        out.setdefault(c, p)
    return out


def game_source(cls):
    """The decompiled game source of a class (hot-update DLLs), or None."""
    return _game_classes().get(cls)


def runs_methods(src):
    """The Unity messages / playable callbacks a game class implements."""
    if not src:
        return []
    text = open(src, encoding="utf-8-sig", errors="replace").read()
    names = ("Update", "LateUpdate", "FixedUpdate", "OnEnable", "Start", "Awake", "OnWillRenderObject",
             "ProcessFrame", "OnBehaviourPlay", "CreatePlayable", "CreateTrackMixer", "PrepareFrame")
    return [n for n in names if re.search(rf"\b(?:void|Playable)\s+{n}\s*\(", text)]


# ---------------------------------------------------------------- hierarchy
class Hierarchy:
    """GameObjects of a prefab / scene file: names, paths from the root, active flags,
    components."""

    def __init__(self, path):
        self.file = path
        d = docs(path)
        self.go, self.parent, self.comp_go = {}, {}, {}
        t2g = {}
        for fid, (c, b) in d.items():
            if c == "1":
                self.go[fid] = {"name": fget(b, "m_Name", ""), "active": fget(b, "m_IsActive", "1") == "1",
                                "components": [r[0] for r in ref_list(b, "m_Component")]}
            elif c in ("4", "224"):
                t2g[fid] = ref(fget(b, "m_GameObject"))[0]
        for fid, (c, b) in d.items():
            if c in ("4", "224"):
                g = t2g[fid]
                fr = ref(fget(b, "m_Father"))
                self.parent[g] = t2g.get(fr[0]) if fr and fr[0] != "0" else None
        for g, info in self.go.items():
            for cfid in info["components"]:
                self.comp_go[cfid] = g
        self.nested = any(c == "1001" for c, _ in d.values())

    @functools.lru_cache(maxsize=None)
    def path(self, g):
        p, cur = [], g
        while cur:
            p.append(self.go[cur]["name"])
            cur = self.parent.get(cur)
        return "/".join(reversed(p))

    def roots(self):
        return [g for g in self.go if self.parent.get(g) is None]

    def rel(self, g):
        """Path below the root ('' for the root)."""
        return self.path(g).split("/", 1)[1] if "/" in self.path(g) else ""

    def find(self, relpath):
        """Transform.Find from the (single) root."""
        for g in self.go:
            if self.rel(g) == relpath:
                return g
        return None

    def ancestors(self, g):
        out, cur = [], g
        while cur:
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def subtree(self, g):
        return [x for x in self.go if g in self.ancestors(x)]


# ---------------------------------------------------------------- timeline
class Track:
    def __init__(self, name, cls, status, muted, clips, children, infinite, markers, key, binding_type):
        self.name, self.cls, self.status, self.muted = name, cls, status, muted
        self.clips, self.children, self.infinite, self.markers = clips, children, infinite, markers
        self.key, self.binding_type = key, binding_type


def read_timeline(pj, playable):
    """The timeline's tracks (a tree) from its .playable."""
    d = docs(playable)
    root = d.get("11400000")
    tracks = []

    def load(loc, parent_muted=False):
        f, fid = loc
        dd = docs(f)
        if fid not in dd:
            return None
        _, b = dd[fid]
        cls, status, _src = pj.script_of(b)
        name = fget(b, "m_Name", "")
        name = name.strip("'\"")
        muted = fget(b, "m_Muted", "0") == "1" or parent_muted
        clips = []
        cm = re.search(r"^  m_Clips:\s*\n(.*?)(?=^  m_\w+:)", b + "\n  m_End:", re.M | re.S)
        if cm:
            for cb in re.split(r"\n  - m_Version", "\n" + cm.group(1))[1:]:
                asset = pj.resolve(ref(fget(cb, "m_Asset")), f)
                clips.append({"start": float(fget(cb, "m_Start", 0)), "duration": float(fget(cb, "m_Duration", 0)),
                              "clipIn": float(fget(cb, "m_ClipIn", 0)), "timeScale": float(fget(cb, "m_TimeScale", 1)),
                              "name": fget(cb, "m_DisplayName", ""), "asset": asset})
        inf = pj.resolve(ref(fget(b, "m_InfiniteClip")), f)
        mk = re.search(r"m_Markers:\s*\n\s*m_Objects:\s*(\[\])?", b)
        markers = 0 if (not mk or mk.group(1)) else len(ref_list(b.split("m_Markers:")[1], "m_Objects"))
        key = (f, fid)
        bt = PACKAGE_BINDING.get(cls, game_binding_types().get(cls))
        children = [load(pj.resolve(r, f), muted) for r in ref_list(b, "m_Children")]
        t = Track(name, cls, status, muted, clips, [c for c in children if c], inf, markers, key, bt)
        tracks.append(t)
        return t

    tops = [load(pj.resolve(r, playable)) for r in ref_list(root[1], "m_Tracks")]
    return [t for t in tops if t], tracks


def clip_asset(pj, loc):
    """(class, status, body, file) of a clip's PlayableAsset."""
    if not loc:
        return None
    f, fid = loc
    d = docs(f)
    if fid not in d:
        return None
    _, b = d[fid]
    cls, status, _ = pj.script_of(b)
    return cls, status, b, f


def anim_curves(path):
    """An .anim's float curves: [(path, classID, attribute, [(time, value)])]."""
    d = docs(path)
    out = []
    for _, (c, b) in d.items():
        if c != "74":
            continue
        fc = re.search(r"^  m_FloatCurves:\s*\n(.*?)(?=^  m_\w+:)", b + "\n  m_End:", re.M | re.S)
        if not fc:
            continue
        for item in re.split(r"\n  - ", "\n" + fc.group(1))[1:]:
            keys = [(float(t), float(v)) for t, v in re.findall(r"time: ([-\d.eE+]+)\s*\n\s*value: ([-\d.eE+]+)", item)]
            out.append((fget(item, "path", ""), fget(item, "classID", ""), fget(item, "attribute", ""), keys))
    return out


def step_eval(keys, t):
    """A switch curve's value at t (constant before the first key)."""
    if not keys:
        return None
    v = keys[0][1]
    for kt, kv in keys:
        if kt <= t + 1e-6:
            v = kv
    return v


# ---------------------------------------------------------------- recording
class Recording:
    def __init__(self, dlc_dir):
        self.data = os.path.join(dlc_dir, "web", "data")
        self.ok = os.path.isfile(os.path.join(self.data, "scene.json"))
        if not self.ok:
            return
        self.scene = json.load(open(os.path.join(self.data, "scene.json"), encoding="utf-8"))
        self.rend = {r["id"]: r for r in self.scene["renderers"]}
        self.by_path = defaultdict(list)
        for r in self.scene["renderers"]:
            self.by_path[r["path"]].append(r["id"])
        self.mats = {m["id"]: m for m in self.scene["materials"]}

    @functools.lru_cache(maxsize=8)
    def take(self, name):
        """(lead, frames count, on[rid] bool array, alive[rid] bool array, mpb[rid] per frame)
        on the clip clock: index = clip frame."""
        path = os.path.join(self.data, f"{name}.frames.jsonl")
        if not os.path.isfile(path):
            return None
        lines = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        lead = round(lines[0]["clipTime"] * RATE) if "clipTime" in lines[0] else 1
        n = len(lines) + lead
        on = defaultdict(lambda: np.zeros(n, bool))
        alive = defaultdict(lambda: np.zeros(n, bool))
        mpb = defaultdict(dict)       # rid -> {frame: mpb dict}
        mats = defaultdict(dict)
        state = {}
        for i, fr in enumerate(lines):
            for rid, rec in fr["r"].items():
                cur = state.setdefault(rid, {})
                if "on" in rec:
                    cur["on"] = rec["on"]
                    if rec.get("on"):
                        cur["mpb"] = rec.get("mpb", {})
                        cur["mats"] = rec.get("mats")
                cur["alive"] = bool(rec.get("particles") or rec.get("trail"))
            for rid, cur in state.items():
                f = i + lead
                if cur.get("on"):
                    on[rid][f] = True
                    if cur.get("alive") and rid in fr["r"]:
                        alive[rid][f] = True
                    if cur.get("mpb"):
                        mpb[rid][f] = cur["mpb"]
                    if cur.get("mats"):
                        mats[rid][f] = cur["mats"]
        # frames before the first line hold its state
        for d in (on, alive):
            for rid, a in d.items():
                a[:lead] = a[lead] if lead < n else False
        return {"lead": lead, "n": n, "on": dict(on), "alive": dict(alive), "mpb": dict(mpb), "mats": dict(mats),
                "clip_end": max((fr.get("clipTime", 0) for fr in lines), default=0)}


def windows(mask):
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i - 1))
            start = None
    return out


def fmt_win(w):
    return "none" if not w else ", ".join(f"[{a}, {b}]" for a, b in w)


# ---------------------------------------------------------------- V1
def v1_take(pj, rec, take, issues, dlc, report):
    seq = next((s for s in pj.spec["sequences"] if s["name"] == take), None)
    if not seq or not seq["prefab"]:
        issues.add("reconstruction", dlc, take, "take not in the project", "no sequence prefab in ag_dlc.json")
        return
    prefab = pj.path(seq["prefab"])
    H = Hierarchy(prefab)
    if H.nested:
        issues.add("reconstruction", dlc, take, "nested prefab instance", f"{seq['prefab']} holds PrefabInstance documents - not read")
    model = Hierarchy(pj.path(pj.spec["model"]))
    pd_body = next((b for c, b in docs(prefab).values() if c == "320"), None)
    if not pd_body:
        issues.add("reconstruction", dlc, take, "no director", seq["prefab"])
        return
    tl_ref = ref(fget(pd_body, "m_PlayableAsset"))
    playable = pj.guid.get(tl_ref[1]) if tl_ref and tl_ref[1] else None
    if not playable:
        issues.add("reconstruction", dlc, take, "no timeline", f"{seq['prefab']}: m_PlayableAsset unresolved")
        return
    tops, tracks = read_timeline(pj, playable)
    scene_bind = {}
    sb = pd_body.split("m_SceneBindings:")[1] if "m_SceneBindings:" in pd_body else ""
    for m in re.finditer(r"key: (\{[^}]*\})\s*\n\s*value: (\{[^}]*\})", sb):
        k, v = ref(m.group(1)), ref(m.group(2))
        loc = pj.resolve(k, playable)
        scene_bind[loc] = v[0] if v else "0"
    exposed = dict(re.findall(r"- (agref_-?\d+): \{fileID: (-?\d+)", pd_body))
    duration = max((c["start"] + c["duration"] for t in tracks for c in t.clips), default=0.0)
    n = int(round(duration * RATE)) + 1
    tk = rec.take(take) if rec.ok else None
    if tk:
        n = max(n, tk["n"])
    report.append(f"### {dlc} {take}: {len(tracks)} tracks, {sum(len(t.clips) for t in tracks)} clips, {duration:.2f} s")

    # ---- classes, bindings, references
    act_go = defaultdict(lambda: None)        # GameObject -> on[n] forced by control/activation clips
    curves = []                               # (bound GO, curve tuple, timing)
    for t in tracks:
        if t.muted:
            continue
        what = f"track '{t.name}' ({t.cls})"
        if t.status in ("stub", "missing"):
            src = game_source(t.cls)
            does = runs_methods(src)
            issues.add("reconstruction", dlc, take, "track class not ported (does nothing in the reference)",
                       f"{what}, {len(t.clips)} clip(s) " + fmt_clips(t.clips)
                       + (f"; game code {os.path.relpath(src, ROOT)} implements {', '.join(does)}" if src else "; no decompiled game code"))
        if t.markers:
            issues.add("reconstruction", dlc, take, "markers / signals", f"{what}: {t.markers} marker(s) - not played")
        for c in t.clips:
            a = clip_asset(pj, c["asset"])
            if not a:
                issues.add("reconstruction", dlc, take, "clip asset missing", f"{what} clip '{c['name']}'")
                continue
            acls, astatus, ab, af = a
            if astatus in ("stub", "missing") and t.status not in ("stub", "missing"):
                issues.add("reconstruction", dlc, take, "clip class not ported (does nothing in the reference)",
                           f"{what} clip '{c['name']}' ({acls}) at {fmt_clip(c)}")
            for m in re.finditer(r"exposedName: (\S+)", ab):
                name = m.group(1)
                if acls == "IKLookAtNode" and fget(ab, "targetType", "1") == "0":
                    continue    # looks at the main camera: the reference is not read
                if name.startswith("agref_") and exposed.get(name, "0") == "0":
                    issues.add("reconstruction", dlc, take,
                               "exposed reference null in the game data" if name in exposed else
                               "exposed reference missing from the director (null in the game too)",
                               f"{what} clip '{c['name']}' ({acls}) {name} at {fmt_clip(c)}")
                elif not name.startswith("agref_") and name not in ("", "''"):
                    issues.add("reconstruction", dlc, take, "exposed reference not renamed",
                               f"{what} clip '{c['name']}' ({acls}) exposedName {name} (dlc_play.exposed_references)")
            if acls == "ControlPlayableAsset":
                src = exposed.get(fget(ab, "exposedName", ""), "0")
                g = H.comp_go.get(src, src) if src in H.go or src in H.comp_go else None
                if fget(ab, "prefabGameObject", "{fileID: 0}") != "{fileID: 0}":
                    issues.add("reconstruction", dlc, take, "control clip spawns a prefab",
                               f"{what} clip '{c['name']}': prefabGameObject set - check AGDlcRecord sees the instance")
                if g and fget(ab, "active", "1") == "1":
                    on = act_go[g] if act_go[g] is not None else np.zeros(n, bool)
                    a0, a1 = frame_range(c, n)
                    on[a0:a1] = True
                    act_go[g] = on
                if fget(ab, "updateDirector", "0") == "1" and g:
                    for x in H.subtree(g):
                        for cf in H.go[x]["components"]:
                            if docs(prefab).get(cf, ("",))[0] == "320":
                                issues.add("reconstruction", dlc, take, "nested sub-timeline",
                                           f"{what} clip '{c['name']}' drives the director on {H.path(x)} - not enumerated here")
            if acls == "AnimationPlayableAsset":
                clip_loc = pj.resolve(ref(fget(ab, "m_Clip")), af)
                if clip_loc:
                    curves.append((t, clip_loc[0], c))
        if t.infinite:
            curves.append((t, t.infinite[0], None))
        if t.binding_type is None and t.cls not in PACKAGE_BINDING and t.cls not in game_binding_types() \
                and t.status not in ("stub", "missing") and t.cls != "GroupTrack" and not t.children:
            report.append(f"- note: {what}: binding type unknown")
        # the game's binding
        bt = t.binding_type
        if t.children or t.cls == "GroupTrack" or bt is None:
            continue
        named = t.name[:1] in "@#&" and len(t.name) > 1
        if bt == "HeroUITimelineBrain":
            t.bound = ("brain", None)
            continue
        if bt in BRAIN_BINDS and named:
            target = t.name[1:]
            g = model.find(target)
            where = "model" if g else None
            if not g:
                g = H.find(target)
                where = "sequence" if g else None
            if not g:
                issues.add("reconstruction", dlc, take, "binding path not found",
                           f"{what}: '{target}' is under neither the model nor the sequence (the game would try GameObject.Find in the scene)")
            t.bound = (where, g)
            continue
        val = scene_bind.get(t.key, "0")
        if val == "0":
            if bt in BRAIN_BINDS or bt == "GameObject":
                issues.add("reconstruction", dlc, take, "unbound track (unbound in the game too)",
                           f"{what} binding type {bt}: no prefab binding and no '@path' name - " + fmt_clips(t.clips))
            t.bound = (None, None)
            continue
        g = val if val in H.go else H.comp_go.get(val)
        t.bound = ("sequence", g)
        if t.cls == "ActivationTrack" and g:
            on = act_go[g] if act_go[g] is not None else np.zeros(n, bool)
            for c in t.clips:
                a0, a1 = frame_range(c, n)
                on[a0:a1] = True
            act_go[g] = on

    # ---- material curves still hashed, activation / enable curves
    curve_on = defaultdict(lambda: None)      # (GameObject, kind) -> on[n]; kind GameObject or renderer class
    for t, anim, c in curves:
        try:
            cv = anim_curves(anim)
        except OSError:
            continue
        hashed = sorted({a for _, _, a, _ in cv if "path_0x" in a})
        if hashed:
            issues.add("reconstruction", dlc, take, "material curve with an unresolved property hash",
                       f"track '{t.name}' {os.path.basename(anim)}: {', '.join(hashed[:4])}" + (" ..." if len(hashed) > 4 else ""))
        where, base = getattr(t, "bound", (None, None))
        if where != "sequence" or base is None:
            if any(a in ("m_IsActive", "m_Enabled") for _, _, a, _ in cv) and where != "model":
                issues.add("reconstruction", dlc, take, "activation curves on an unbound track",
                           f"track '{t.name}' {os.path.basename(anim)}: m_IsActive/m_Enabled curves bound to nothing")
            continue
        for path, cid, attr, keys in cv:
            if attr not in ("m_IsActive", "m_Enabled"):
                continue
            g = base if path in ("", "0") else _child(H, base, path)
            if g is None:
                if not path.isdigit():
                    issues.add("reconstruction", dlc, take, "curve path not found",
                               f"track '{t.name}' {os.path.basename(anim)}: {path} ({attr}) under {H.path(base)}")
                continue
            arr = np.zeros(n, bool)
            for f in range(n):
                tt = f / RATE
                if c is not None:
                    if not (c["start"] <= tt < c["start"] + c["duration"]):
                        arr[f] = np.nan if False else False
                        continue
                    tt = (tt - c["start"]) * c["timeScale"] + c["clipIn"]
                v = step_eval(keys, tt)
                arr[f] = bool(v is not None and v > 0.5)
            kind = "GameObject" if cid == "1" else cid
            curve_on[(g, kind)] = arr if curve_on[(g, kind)] is None else (curve_on[(g, kind)] | arr)

    # ---- objects a game script spawns and binds at runtime (DynamicTimelineTrackBinding:
    # P08CharBinding.GetInst -> Asset.InstantiateWithoutCache(path), then SceneBindings
    # bind its Animator to tracks)
    for fid, (c, b) in docs(prefab).items():
        if c != "114" or pj.script_of(b)[0] != "DynamicTimelineTrackBinding":
            continue
        for key, path in re.findall(r"- key: (.*)\n\s+path: (.*)", b.split("SceneBindings:")[0]):
            spawned = [p_ for p_ in rec.by_path if key in p_.split("/")] if rec.ok else []
            bound = len(re.findall(rf"- key: {re.escape(key)}\n", b.split("SceneBindings:")[1].split("MixBindings:")[0]))
            if not spawned:
                issues.add("reconstruction", dlc, take, "object spawned by a game script is missing",
                           f"DynamicTimelineTrackBinding on {H.path(H.comp_go.get(fid))} loads '{path.strip()}' (key {key.strip()}) "
                           f"at Awake and binds {bound} track(s) to it - the reference neither spawns nor binds it, the recording has none of it")

    # ---- game scripts on the sequence's / model's objects that are stubs here
    for hier, label in ((H, "sequence"), (model, "model")):
        d = docs(hier.file)
        seen = set()
        for fid, (c, b) in d.items():
            if c != "114":
                continue
            cls, status, _ = pj.script_of(b)
            if status not in ("stub", "missing") or cls in seen:
                continue
            seen.add(cls)
            g = hier.comp_go.get(fid)
            src = game_source(cls)
            does = runs_methods(src)
            per_frame = [m for m in does if m in ("Update", "LateUpdate", "FixedUpdate", "OnWillRenderObject")]
            if label == "model" and not per_frame:
                continue
            issues.add("reconstruction", dlc, take if label == "sequence" else "",
                       "game script not ported" + (" (runs every frame)" if per_frame else (" (setup code only)" if does else "")),
                       f"{cls} on {label} object {hier.path(g) if g else '?'}"
                       + (f" - game code implements {', '.join(does)}" if does else (" - no decompiled game code" if not src else " - data only")))

    for fid, (c, b) in docs(prefab).items():
        if c == "95" and ref(fget(b, "m_Controller")) and ref(fget(b, "m_Controller"))[0] != "0":
            g = H.comp_go.get(fid)
            report.append(f"- note: {take}: {H.path(g) if g else fid} runs its own Animator controller - its curves are not predicted")

    # ---- predicted drawing vs the recording
    if not tk:
        issues.add("recording", dlc, take, "no recording", f"web/data/{take}.frames.jsonl missing")
        return
    seq_root = f"Char/{pj.spec['modelId']}/{take}"
    expected = 0
    for fid, (c, b) in docs(prefab).items():
        if c not in RENDERERS:
            continue
        g = H.comp_go.get(fid)
        if g is None:
            continue
        kind = RENDERERS[c]
        drawn = np.ones(n, bool)
        for a in H.ancestors(g):
            # the sequence's root is switched on by the player (herouitimeline.lua SetActive)
            st = np.full(n, H.go[a]["active"] or H.parent.get(a) is None)
            if act_go[a] is not None:
                st = act_go[a].copy()
            cv = curve_on[(a, "GameObject")]
            if cv is not None:
                st = cv if act_go[a] is None else (st & cv)
            drawn &= st
        en = np.full(n, fget(b, "m_Enabled", "1") == "1")
        if curve_on[(g, c)] is not None:
            en = curve_on[(g, c)]
        drawn &= en
        if not drawn.any():
            continue
        expected += 1
        rel = H.rel(g)
        rpath = f"{seq_root}/{rel}" if rel else seq_root
        rids = rec.by_path.get(rpath)
        exp_w = windows(drawn)
        if not rids:
            stage = "recording"
            issues.add(stage, dlc, take, f"{kind} renderer not recorded",
                       f"{rel} ({CLASS_IDS.get(c, c)}) should draw on {fmt_win(exp_w)} - AGDlcRecord has no such renderer")
            continue
        got = np.zeros(n, bool)
        for rid in rids:
            if rid in tk["on"]:
                got |= tk["on"][rid][:n]
        got_w = windows(got)
        if not got.any():
            issues.add("reconstruction", dlc, take, "renderer never drawn",
                       f"{rel} ({kind}) predicted on {fmt_win(exp_w)}, never on in the recording")
            continue
        # a frame of slack at each edge (Unity applies a clip's activation as it plays)
        extra = got & ~_dilate(drawn, 1)
        miss = drawn & ~_dilate(got, 1)
        if extra.sum() > 0 or miss.sum() > 0:
            issues.add("reconstruction", dlc, take, "drawn on other frames than the timeline says",
                       f"{rel} ({kind}): timeline {fmt_win(exp_w)}, recording {fmt_win(got_w)}")
        if kind in ("particles", "trail", "line"):
            al = np.zeros(n, bool)
            for rid in rids:
                if rid in tk["alive"]:
                    al |= tk["alive"][rid][:n]
            meshless = c == "199" and fget(b, "m_RenderMode", "0") == "4" and fget(b, "m_Mesh", "{fileID: 0}") == "{fileID: 0}"
            if not al.any() and not meshless:
                issues.add("reconstruction", dlc, take, f"{kind} never emits",
                           f"{rel}: on {fmt_win(got_w)} but no geometry in any frame"
                           + (" (emits over distance: does its emitter move?)" if _rate_over_distance(b, docs(prefab), H, g) else ""))
    report.append(f"- {expected} renderers predicted to draw, checked against the recording (lead {tk['lead']})")


def _child(H, base, path):
    want = H.path(base) + "/" + path
    for g in H.subtree(base):
        if H.path(g) == want:
            return g
    return None


def _dilate(a, k):
    out = a.copy()
    for s in range(1, k + 1):
        out[s:] |= a[:-s]
        out[:-s] |= a[s:]
    return out


def _rate_over_distance(_rb, d, H, g):
    for cf in H.go[g]["components"]:
        c, b = d.get(cf, ("", ""))
        if c == "198":
            m = re.search(r"rateOverDistance:\s*\n\s+serializedVersion: 2\s*\n\s+minMaxState: \d\s*\n\s+scalar: ([-\d.eE]+)", b)
            return bool(m and float(m.group(1)) > 0)
    return False


def frame_range(c, n):
    a0 = int(round(c["start"] * RATE))
    a1 = int(round((c["start"] + c["duration"]) * RATE))
    return max(0, a0), min(n, a1)


def fmt_clip(c):
    a0 = int(round(c["start"] * RATE))
    a1 = int(round((c["start"] + c["duration"]) * RATE)) - 1
    extra = ""
    if c["clipIn"]:
        extra += f", clipIn {c['clipIn']:.3f}"
    if abs(c["timeScale"] - 1) > 1e-6:
        extra += f", timeScale {c['timeScale']:.3f}"
    return f"[{a0}, {a1}]{extra}"


def fmt_clips(clips):
    if not clips:
        return "(no clips)"
    return "clips " + ", ".join(f"'{c['name']}' {fmt_clip(c)}" for c in clips[:6]) + (" ..." if len(clips) > 6 else "")


# ---------------------------------------------------------------- V2
def ascii_name(s, limit):
    return (re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "p")[:limit]


def class_label(path):
    parts = [re.sub(r"\s*\(\d+\)$", "", p) for p in path.split("/")]
    return "/".join(parts[-2:])


def is_cast(r, mats, root):
    import stage_native
    return stage_native.is_cast(r, mats, root)


def decode(b, name):
    from PIL import Image
    im = Image.open(io.BytesIO(b))
    im.load()
    return np.asarray(im.convert("RGBA")), im.mode


def v2_take(dlc_dir, rec, take, issues, dlc, report, checked_stage, zips=None):
    name = os.path.basename(dlc_dir)
    zpath = os.path.join(zips or os.path.join(dlc_dir, "reze"), f"{name}-{take}.zip")
    if not os.path.isfile(zpath):
        issues.add("conversion", dlc, take, "no zip", os.path.relpath(zpath, ROOT))
        return
    tk = rec.take(take)
    if not tk:
        return
    zrec_time = os.path.getmtime(os.path.join(rec.data, f"{take}.frames.jsonl"))
    stale = os.path.getmtime(zpath) < zrec_time
    if stale:
        issues.add("conversion", dlc, take, "zip older than the recording",
                   f"{os.path.basename(zpath)} predates web/data/{take}.frames.jsonl: its prop textures are not compared "
                   f"(ids belong to the older recording); windows are compared against the newer recording")
    z = zipfile.ZipFile(zpath)
    names = set(z.namelist())
    doc = json.loads(z.read("scene.json"))
    models = doc.get("assets", {}).get("models", [])
    props = {}
    for m in models:
        pn = m["model"].split("/")[1] if m["model"].startswith("props/") else None
        if pn:
            props[pn] = m
    effects = doc.get("settings", {}).get("background", {}).get("effects", [])
    eff_by = defaultdict(list)
    for e in effects:
        eff_by[e["source"]["name"].split(" (")[0]].append(e)
    eff_groups = defaultdict(list)          # name without the primes -> effects
    for e in effects:
        eff_groups[e["source"]["name"].rstrip("'")].append(e)
    rig = {}
    lj = "props/particle_emitters/particle_emitters.lights.json"
    if lj in names:
        for p in json.loads(z.read(lj))["particles"]:
            rig[p["class"]["name"]] = p["class"]
    stage = None
    sj = next((n for n in names if re.match(r"stage/[^/]+\.json$", n)), None)
    if sj:
        stage = json.loads(z.read(sj))
    stage_paths = {r["path"] for r in (stage or {}).get("renderers", [])}
    mats = rec.mats
    root = None
    try:
        import stage_native
        root = stage_native.cast_root(rec.scene, mats)
    except Exception:  # noqa: BLE001
        pass
    seq_names = [s for s in json.load(open(os.path.join(rec.data, "index.json"), encoding="utf-8")).get("sequences", [])]
    seq_names = [s["name"] if isinstance(s, dict) else s for s in seq_names]
    n = tk["n"]
    accounted = defaultdict(int)
    for rid, on in tk["on"].items():
        if not on.any():
            continue
        r = rec.rend[rid]
        p = r["path"]
        parts = p.split("/")
        if parts[0] == "Char" and len(parts) > 2 and parts[2] in seq_names and parts[2] != take:
            continue   # another take's object (left active) - not this take's
        if r["kind"] == "skinned" and is_cast(r, mats, root) or (r["kind"] != "particles" and is_cast(r, mats, root)):
            accounted["the character (user's model)"] += 1
            continue
        if not any(r.get("materials") or []):
            accounted["no material (draws nothing)"] += 1
            continue
        if r["kind"] == "particles":
            label = class_label(p)
            es = eff_by.get(label)
            if not es:
                # a mesh particle becomes a prop named after it
                if r.get("mesh") and any(k.startswith(ascii_name(parts[-1], 64)) for k in props):
                    accounted["mesh particle prop"] += 1
                    continue
                if not tk["alive"].get(rid, np.zeros(1, bool)).any():
                    accounted["particles never alive"] += 1
                    continue
                # systems of one spec and material are one effect under its first
                # emitter's label (X340's 53 candle flames)
                mnames = {rec.mats[m_]["name"] for m_ in r["materials"] if m_ and m_ in rec.mats}
                if any(e["source"]["name"].rstrip("'").endswith(f"({mn})") for e in effects for mn in mnames):
                    accounted["effect (merged class)"] += 1
                    continue
                shader = ", ".join(sorted({rec.mats[m_]["shader"] for m_ in r["materials"] if m_ and m_ in rec.mats}))
                issues.add("conversion", dlc, take, "particle system dropped",
                           f"{p} ({shader}): drawn {fmt_win(windows(on))}, alive {fmt_win(windows(tk['alive'].get(rid, np.zeros(n, bool))))}, "
                           f"no effect of its label or material in the zip")
                continue
            accounted["effect"] += 1
            continue
        if r["kind"] in ("trail", "line"):
            group = parts[3] if len(parts) > 4 and parts[2] == take else parts[-1]
            if not any(k.startswith(ascii_name(group, 64)) for k in props):
                issues.add("conversion", dlc, take, f"{r['kind']} dropped",
                           f"{p}: drawn {fmt_win(windows(on))} ({int(tk['alive'].get(rid, np.zeros(1)).sum())} frames with geometry), no prop for it")
            else:
                accounted["ribbon prop"] += 1
            continue
        if parts[0] != "Char":
            if p in stage_paths:
                accounted["stage"] += 1
            else:
                issues.add("conversion", dlc, take, "stage renderer dropped", f"{p} ({r['kind']})")
            continue
        group = parts[3] if len(parts) > 4 and parts[2] == take else parts[-1]
        cands = [k for k in props if k == ascii_name(group, 64) or re.fullmatch(re.escape(ascii_name(group, 64)) + r"_\d+", k)]
        if not cands:
            issues.add("conversion", dlc, take, "prop dropped", f"{p} ({r['kind']}): drawn {fmt_win(windows(on))}, no prop '{ascii_name(group, 64)}'")
            continue
        accounted["prop"] += 1
    report.append(f"- V2 {take}: " + ", ".join(f"{v} {k}" for k, v in sorted(accounted.items())))

    # ---- effect windows on the clip clock (classes sharing a label and material,
    # told apart by primes, are compared as one: the renderers cannot be told apart)
    for nm, group in eff_groups.items():
        cls = rig.get(group[0]["source"]["name"])
        label, mat = nm.split(" (")[0], nm.split(" (", 1)[1].rstrip(")") if " (" in nm else ""
        rids = [rid for rid in tk["on"] if class_label(rec.rend[rid]["path"]) == label and rec.rend[rid]["kind"] == "particles"
                and _in_take(rec.rend[rid]["path"], take, seq_names)
                and (not mat or any(rec.mats.get(m_, {}).get("name") == mat for m_ in rec.rend[rid]["materials"] if m_))]
        looping = cls and cls["spec"].get("looping")
        rec_mask = np.zeros(n, bool)
        for rid in rids:
            rec_mask |= (tk["on"][rid] if looping else tk["alive"].get(rid, np.zeros(n, bool)))[:n]
        win = None if any(e.get("window") is None for e in group) else [w for e in group for w in e["window"]]
        zmask = np.zeros(n, bool)
        if win is None:
            zmask[:] = True
        else:
            for w in win:
                zmask[w["start"]:w["end"] + 1] = True
        if win is None and not rec_mask.all():
            issues.add("conversion", dlc, take, "effect without a window (plays the whole take)",
                       f"'{nm}': no window in the zip, so it plays from frame 0; the recording has it {'on' if looping else 'alive'} "
                       f"{fmt_win(windows(rec_mask))}" + (" - never, so it should not be in the zip at all" if not rec_mask.any() else ""))
            continue
        d_early = np.nonzero(zmask & ~_dilate(rec_mask, 1))[0]
        d_late = np.nonzero(rec_mask & ~_dilate(zmask, 1))[0]
        if len(d_early) or len(d_late):
            issues.add("conversion", dlc, take, "effect window off the clip clock",
                       f"'{nm}': zip {fmt_win(windows(zmask))}, recording {fmt_win(windows(rec_mask))}")
        if cls and cls["spec"].get("emission", {}).get("on") and not cls["spec"]["emission"].get("bursts") \
                and (cls["spec"]["emission"].get("rate") or {}).get("hi", 0) == 0:
            issues.add("playback", dlc, take, "effect with nothing to emit in the spec",
                       f"'{nm}': emission rate 0, no bursts in the zip's spec (the game emits it another way, e.g. rate over distance) - "
                       f"the app falls back to its recycling pool")

    # ---- prop visibility on the clip clock
    for pn, m in props.items():
        if pn == "particle_emitters":
            continue
        vis = m.get("visibility")
        if vis is None:
            continue
        zmask = np.zeros(n, bool)
        for w in vis:
            zmask[w["start"]:w["end"] + 1] = True
        group_rids = [rid for rid in tk["on"] if _prop_of(rec.rend[rid], take, seq_names) == pn.rstrip("0123456789").rstrip("_")
                      or _prop_of(rec.rend[rid], take, seq_names) == pn]
        if not group_rids:
            continue
        rm = np.zeros(n, bool)
        for rid in group_rids:
            rm |= tk["on"][rid][:n]
        if (zmask & ~_dilate(rm, 1)).any() or (rm & ~_dilate(zmask, 1)).any():
            if len(group_rids) == 1 or (zmask & ~_dilate(rm, 1)).any():
                issues.add("conversion", dlc, take, "prop visibility off the clip clock",
                           f"prop '{pn}': zip {fmt_win(windows(zmask))}, recording (its group) {fmt_win(windows(rm))}")

    # ---- keyed uniforms against the recorded property blocks
    for pn in props:
        lp = f"props/{pn}/look.json"
        if lp not in names:
            continue
        look = json.loads(z.read(lp))
        for spec in look.get("materials", []):
            for uname, keys in (spec.get("uniforms") or {}).items():
                rids = [rid for rid in tk["mpb"] if any(rec.mats.get(mid, {}).get("name") == spec["game"]
                                                        for mid in rec.rend[rid]["materials"] if mid)]
                best = _key_offset(keys, rids, tk, uname)
                if best is not None and best[0] != 0:
                    issues.add("conversion", dlc, take, "uniform keys off the clip clock",
                               f"prop '{pn}' {spec['game']} {uname}: the keys match the recorded property block best "
                               f"{best[0]:+d} frame(s) away (error {best[1]:.3g} there vs {best[2]:.3g} in place)")

    # ---- textures round-trip
    if not checked_stage.get(dlc) and stage:
        checked_stage[dlc] = True
        _check_textures(z, names, rec, stage, issues, dlc, report)
    for pn in ([] if stale else props):
        for nm_ in names:
            m = re.match(rf"props/{re.escape(pn)}/tex/(t\d+)\.png$", nm_)
            if m:
                _texture_pair(z, nm_, rec, m.group(1), issues, dlc, take)


def _in_take(path, take, seq_names):
    parts = path.split("/")
    return not (parts[0] == "Char" and len(parts) > 2 and parts[2] in seq_names and parts[2] != take)


def _prop_of(r, take, seq_names):
    parts = r["path"].split("/")
    if parts[0] != "Char" or r["kind"] == "particles":
        return None
    group = parts[3] if len(parts) > 4 and parts[2] == take else parts[-1]
    return ascii_name(group, 64)


def _key_offset(keys, rids, tk, uname):
    """(offset with the least error, its error, the error at 0) of a keyed uniform's
    values against the recorded property block values, or None."""
    rec_vals = {}
    for rid in rids:
        for f, block in tk["mpb"][rid].items():
            v = block.get(uname) if isinstance(block, dict) else None
            if v is None and isinstance(block, dict) and uname[:-2] in block and uname[-2:] in (".r", ".g", ".b", ".a", ".x", ".y", ".z", ".w"):
                vv = block[uname[:-2]]
                v = vv["rgba".index(uname[-1]) if uname[-1] in "rgba" else "xyzw".index(uname[-1])] if isinstance(vv, list) else None
            if isinstance(v, (int, float)) or isinstance(v, list):
                rec_vals.setdefault(f, np.atleast_1d(np.array(v, dtype=float)))
    if not rec_vals or not keys:
        return None

    def err(off):
        e, k = 0.0, 0
        for f, v in keys:
            g = f + off
            if g in rec_vals:
                a = np.atleast_1d(np.array(v, dtype=float))
                b = rec_vals[g]
                if a.shape == b.shape:
                    e = max(e, float(np.abs(a - b).max()))
                    k += 1
        return e if k else None
    scored = [(o, err(o)) for o in range(-3, 4)]
    scored = [(o, e) for o, e in scored if e is not None]
    if not scored:
        return None
    e0 = dict(scored).get(0)
    o, e = min(scored, key=lambda x: (x[1], abs(x[0])))
    if e0 is None or e0 <= e + 1e-6:
        return (0, e0 or 0.0, e0 or 0.0)
    return (o, e, e0)


def _texture_pair(z, zname, rec, tid, issues, dlc, take):
    t = next((t for t in rec.scene["textures"] if t["id"] == tid), None)
    if not t:
        issues.add("conversion", dlc, take, "texture without a source", zname)
        return
    src = os.path.join(rec.data, "textures", t["files"][0])
    if not src.endswith(".png"):
        return
    a, amode = decode(z.read(zname), zname)
    b, bmode = decode(open(src, "rb").read(), src)
    if a.shape != b.shape:
        issues.add("conversion", dlc, take, "texture changed size", f"{zname}: {a.shape} vs source {t['name']} {b.shape}")
        return
    d = np.abs(a.astype(int) - b.astype(int))
    if d.max():
        ch = [c for i, c in enumerate("RGBA") if d[..., i].max()]
        issues.add("conversion", dlc, take, "texture not lossless",
                   f"{zname} ({t['name']}): max |diff| {int(d.max())} in {''.join(ch)}, "
                   f"{float((d.max(axis=2) > 0).mean()) * 100:.1f}% of texels differ")


def _check_textures(z, names, rec, stage, issues, dlc, report):
    """Stage textures against their sources, summed up per kind of difference:
    colour (sRGB) pictures in RGB, data pictures in RGB, and alpha."""
    n, bad = 0, 0
    worst = defaultdict(list)       # kind -> [(max diff, % texels, file, name)]
    src_by = {t["id"]: t for t in rec.scene["textures"]}
    for t in stage.get("textures", []):
        for f in t.get("files", []):
            zn = f"stage/textures/{f}"
            if zn not in names:
                issues.add("conversion", dlc, "", "stage texture missing", zn)
                continue
            s = src_by.get(t["id"])
            if not s or s["name"] != t["name"]:
                s = next((x for x in rec.scene["textures"] if x["name"] == t["name"] and x["width"] == t["width"]), None)
            if not s or not s["files"][0].endswith(".png"):
                continue
            n += 1
            a, _ = decode(z.read(zn), zn)
            b, _ = decode(open(os.path.join(rec.data, "textures", s["files"][0]), "rb").read(), s["files"][0])
            if a.shape != b.shape:
                bad += 1
                issues.add("conversion", dlc, "", "texture changed size", f"{zn} ({t['name']}): {a.shape} vs {b.shape}")
                continue
            d = np.abs(a.astype(int) - b.astype(int))
            if d.max():
                bad += 1
                rgb, al = d[..., :3].max(), d[..., 3].max()
                bare = b[..., 3] == 0
                rgb_diff = d[..., :3].max(axis=2) > 0
                if rgb and bare.any() and (rgb_diff <= bare).all() and not a[..., :3][bare].any():
                    worst["RGB zeroed under alpha 0 (WebP written without exact)"].append(
                        (int(rgb), float(rgb_diff.mean()) * 100, f, t["name"]))
                elif rgb:
                    worst["colour (sRGB) RGB" if t.get("srgb") else "data (linear) RGB"].append(
                        (int(rgb), float((d[..., :3].max(axis=2) > 0).mean()) * 100, f, t["name"]))
                if al:
                    worst["alpha"].append((int(al), float((d[..., 3] > 0).mean()) * 100, f, t["name"]))
    for kind, lst in worst.items():
        lst.sort(reverse=True)
        top = "; ".join(f"{f} {nm} max {m} ({p:.0f}% texels)" for m, p, f, nm in lst[:4])
        issues.add("conversion", dlc, "", f"stage texture not lossless: {kind}",
                   f"{len(lst)} of {n} stage textures differ from the recording in {kind} (stage_native writes "
                   + ("sRGB pictures as lossy WebP q90" if kind.startswith("colour") else
                      "every picture with exact=True now - a zip from before that" if kind.startswith("RGB zeroed") else
                      "these lossless - a bug") + f"); worst: {top}")
    report.append(f"- stage textures: {n} compared, {bad} differ")


# ---------------------------------------------------------------- game code
def game_code_notes(issues):
    """What the game's Lua does around a DLC take besides the timeline."""
    base = os.path.join(HOTFIX, "lua_dlc")
    hits = []
    for dp, _, fns in os.walk(base):
        for fn in fns:
            text = open(os.path.join(dp, fn), encoding="utf-8-sig", errors="replace").read()
            for m in re.finditer(r"(SetSceneLightEffect|SetSceneWeather|PlayEffect)\(", text):
                hits.append((fn, m.group(1)))
    for fn, what in sorted(set(hits)):
        if what == "PlayEffect" and "audio" in fn:
            continue
        issues.add("reconstruction", "all", "", "set by game Lua, not by the timeline",
                   f"{what} in {fn} (AG_cache/re/hotfix/lua_dlc) - not reproduced by AGDlcPlayer")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dlcs", nargs="*", help="AG_dlc_play/<dlc> folders (default: all)")
    ap.add_argument("--take", nargs="*")
    ap.add_argument("--v1", action="store_true")
    ap.add_argument("--v2", action="store_true")
    ap.add_argument("--out", default=os.path.join(PLAY, "verify_issues.md"))
    ap.add_argument("--zips", help="V2: the folder holding <dlc>-<take>.zip (default <dlc>/reze) - a verification build")
    a = ap.parse_args()
    both = not a.v1 and not a.v2
    dlcs = [os.path.normpath(d) for d in a.dlcs] or sorted(
        os.path.join(PLAY, d) for d in os.listdir(PLAY) if os.path.isfile(os.path.join(PLAY, d, "dlc.json")))
    issues = Issues()
    report = []
    checked_stage = {}
    if a.v1 or both:
        game_code_notes(issues)
    for d in dlcs:
        dlc = os.path.basename(d)
        print(f"== {dlc}", flush=True)
        rec = Recording(d)
        if not rec.ok:
            issues.add("recording", dlc, "", "no recording", "web/data/scene.json missing")
        pj = Project(d) if (a.v1 or both) else None
        takes = a.take or [s["name"] for s in json.load(open(os.path.join(d, "unity", "ExportedProject", "ag_dlc.json"),
                                                             encoding="utf-8"))["sequences"]]
        for take in takes:
            print(f"   {take}", flush=True)
            if pj:
                v1_take(pj, rec, take, issues, dlc, report)
            if (a.v2 or both) and rec.ok:
                v2_take(d, rec, take, issues, dlc, report, checked_stage, a.zips)
    md = issues.markdown() + "\n## Run notes\n\n" + "\n".join(report) + "\n"
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md)
    by = defaultdict(int)
    for i in issues.items:
        by[i[0]] += 1
    print(f"{len(issues.items)} issue(s): " + ", ".join(f"{s} {by[s]}" for s in STAGES) + f" -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
