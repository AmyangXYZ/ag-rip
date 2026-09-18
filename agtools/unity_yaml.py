#!/usr/bin/env python3
"""
Minimal readers for the two AssetRipper YAML files this pipeline needs:
a generic AnimationClip (.anim) and a model prefab (.prefab).

PyYAML is not available inside Blender's bundled Python and a general parser would
be far too slow here anyway - a single clip is ~3 MB of deeply nested scalars and a
character has ~128 of them. AssetRipper's output is machine-generated with a fixed
indentation grammar, so both readers are line-oriented state machines keyed on
indent depth. The grammar was verified against the real files before it was encoded;
anything unexpected raises rather than silently producing a half-parsed clip.
"""

from __future__ import annotations

import math
import re
import zlib

INF = float("inf")

_DOC = re.compile(r"^--- !u!(\d+) &(-?\d+)")
_KEY = re.compile(r"^( *)(- )?([A-Za-z_]\w*):(.*)$")

# Curve blocks we care about. Unity also writes m_EditorCurves / m_EulerEditorCurves,
# which are redundant per-component copies used only by the Unity animation window -
# reading them would double every bone.
TRS_SECTIONS = {
    "m_RotationCurves": "rotation",
    "m_PositionCurves": "position",
    "m_ScaleCurves": "scale",
    "m_EulerCurves": "euler",
    # Humanoid clips carry no transform curves at all - the whole pose is scalar
    # muscle values plus root/IK goals, keyed by `attribute` rather than `path`.
    "m_FloatCurves": "floats",
}


def _f(tok: str) -> float:
    tok = tok.strip()
    if tok == "Infinity":
        return INF
    if tok == "-Infinity":
        return -INF
    if tok == "NaN":
        return math.nan
    return float(tok)


def _vec(text: str) -> tuple[float, ...]:
    """'{x: 1, y: 2, z: 3, w: 4}' -> (1.0, 2.0, 3.0, 4.0), in x/y/z/w order."""
    inner = text.strip().lstrip("{").rstrip("}")
    out = []
    for part in inner.split(","):
        _, _, val = part.partition(":")
        out.append(_f(val))
    return tuple(out)


class Clip:
    """One AnimationClip. Curves are keyed by Unity transform path ('root/Bip001')."""

    __slots__ = ("name", "sample_rate", "start_time", "stop_time", "loop",
                 "rotation", "position", "scale", "euler", "floats")

    def __init__(self) -> None:
        self.name = ""
        self.sample_rate = 30.0
        self.start_time = 0.0
        self.stop_time = 0.0
        self.loop = False
        # path -> [(time, value, in_slope, out_slope), ...]; value/slopes are tuples
        self.rotation: dict[str, list] = {}
        self.position: dict[str, list] = {}
        self.scale: dict[str, list] = {}
        self.euler: dict[str, list] = {}
        # attribute -> keys, values 1-tuples. Root motion (RootT.*/RootQ.*) on generic
        # clips; on humanoid clips this is the entire animation.
        self.floats: dict[str, list] = {}

    @property
    def float_attrs(self) -> list[str]:
        return list(self.floats)

    @property
    def is_humanoid(self) -> bool:
        """Muscle-space clip: no transform curves, driven by named muscle scalars."""
        return not self.paths and any(" " in a or "." in a for a in self.floats)

    @property
    def paths(self) -> set[str]:
        return set(self.rotation) | set(self.position) | set(self.scale) | set(self.euler)

    def __repr__(self) -> str:
        return (f"<Clip {self.name} {self.stop_time:.2f}s @{self.sample_rate:g}fps "
                f"{len(self.paths)} paths>")


def parse_anim(path: str) -> Clip:
    clip = Clip()
    section = None          # current m_* block name
    kind = None             # 'rotation' | 'position' | 'scale' | 'euler' | None
    keys = None             # key list of the curve entry being read
    key = None              # [time, value, in_slope, out_slope] under construction
    in_curve = False        # inside an m_Curve list
    in_settings = False

    def flush_key() -> None:
        nonlocal key
        if key is not None and keys is not None:
            keys.append((key[0], key[1], key[2], key[3]))
        key = None

    def flush_entry(entry_path: str | None) -> None:
        nonlocal keys
        if keys is not None and entry_path is not None and kind is not None:
            getattr(clip, kind)[entry_path] = keys
        keys = None

    pending_path = None

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _KEY.match(line.rstrip("\n"))
            if not m:
                continue
            indent, dash, name, rest = len(m.group(1)), bool(m.group(2)), m.group(3), m.group(4)

            if indent == 2 and not dash:
                # A new top-level field closes whatever curve entry was open.
                flush_key()
                flush_entry(pending_path)
                pending_path, in_curve = None, False
                in_settings = name == "m_AnimationClipSettings"
                section = name
                kind = TRS_SECTIONS.get(name)
                val = rest.strip()
                if name == "m_Name":
                    clip.name = val
                elif name == "m_SampleRate":
                    clip.sample_rate = _f(val) or 30.0
                if val == "[]":
                    section, kind = None, None
                continue

            if in_settings and indent == 4 and not dash:
                val = rest.strip()
                if name == "m_StartTime":
                    clip.start_time = _f(val)
                elif name == "m_StopTime":
                    clip.stop_time = _f(val)
                elif name == "m_LoopTime":
                    clip.loop = val == "1"
                continue

            if kind is None:
                continue

            if indent == 2 and dash and name in ("curve", "serializedVersion"):
                flush_key()
                flush_entry(pending_path)
                pending_path, keys, in_curve = None, [], False
            elif indent == 4 and name == ("attribute" if kind == "floats" else "path"):
                # Float curves are keyed by muscle/attribute name, not transform path.
                pending_path = rest.strip()
            elif indent == 6 and name == "m_Curve":
                in_curve = rest.strip() != "[]"
            elif indent == 6 and dash and in_curve:
                flush_key()
                key = [0.0, (), (), ()]
            elif indent == 8 and key is not None:
                if name == "time":
                    key[0] = _f(rest)
                elif name == "value":
                    key[1] = (_f(rest),) if kind == "floats" else _vec(rest)
                elif name == "inSlope":
                    key[2] = (_f(rest),) if kind == "floats" else _vec(rest)
                elif name == "outSlope":
                    key[3] = (_f(rest),) if kind == "floats" else _vec(rest)
            elif indent <= 6 and not dash and in_curve and name not in (
                    "m_PreInfinity", "m_PostInfinity", "m_RotationOrder"):
                flush_key()
                in_curve = False

    flush_key()
    flush_entry(pending_path)

    if clip.stop_time <= 0:
        # Fall back to the last key time if the clip settings were missing.
        last = 0.0
        for group in (clip.rotation, clip.position, clip.scale, clip.euler,
                      clip.floats):
            for ks in group.values():
                if ks:
                    last = max(last, ks[-1][0])
        clip.stop_time = last
    return clip


_HASHED = re.compile(r"^path_0x([0-9A-Fa-f]{1,8})_")


def resolve_hashed_paths(clip: "Clip", candidates) -> tuple[int, int]:
    """Recover real bone paths for curves AssetRipper could only name by hash.

    Unity stores an animation binding's target as a CRC32 of the transform path, and
    AssetRipper can only turn that back into a name when the matching hierarchy was
    loaded alongside the clip. Character clips that live outside the model's own
    bundle - the home-screen interaction sets - therefore arrive with nearly every
    curve called `path_0x1136AEEC_...`, which binds to nothing: an idle exports with
    7 usable curves (root through the spine to the head) instead of a full body.

    The hash is plain CRC32, so given the rig's own paths it inverts by lookup.
    Returns (resolved, still_unknown).
    """
    table = {}
    for p in candidates:
        table.setdefault(zlib.crc32(p.encode("utf-8")) & 0xFFFFFFFF, p)

    resolved = unknown = 0
    for group in ("rotation", "position", "scale", "euler"):
        curves = getattr(clip, group)
        for name in [k for k in curves if _HASHED.match(k)]:
            real = table.get(int(_HASHED.match(name).group(1), 16))
            if real is None:
                unknown += 1
                continue
            # Keep a real curve already present under the proper name.
            if real in curves:
                curves.pop(name)
            else:
                curves[real] = curves.pop(name)
            resolved += 1
    return resolved, unknown


def evaluate(keys: list, t: float, ncomp: int) -> tuple[float, ...]:
    """Sample a Unity AnimationCurve tuple-channel at time t (cubic Hermite)."""
    if not keys:
        return (0.0,) * ncomp
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]

    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= t:
            lo = mid
        else:
            hi = mid

    t0, v0, _, s0 = keys[lo]
    t1, v1, s1, _ = keys[hi]
    dt = t1 - t0
    if dt <= 0:
        return v1

    u = (t - t0) / dt
    uu = u * u
    uuu = uu * u
    h00 = 2 * uuu - 3 * uu + 1
    h10 = uuu - 2 * uu + u
    h01 = -2 * uuu + 3 * uu
    h11 = uuu - uu

    out = []
    for i in range(ncomp):
        a, b = v0[i], v1[i]
        m0 = s0[i] if i < len(s0) else 0.0
        m1 = s1[i] if i < len(s1) else 0.0
        if math.isinf(m0) or math.isinf(m1):
            out.append(a)            # constant/stepped segment
        else:
            out.append(h00 * a + h10 * m0 * dt + h01 * b + h11 * m1 * dt)
    return tuple(out)


class Node:
    __slots__ = ("name", "path", "parent", "children", "pos", "rot", "scale")

    def __init__(self, name: str) -> None:
        self.name = name
        self.path = ""
        self.parent: Node | None = None
        self.children: list[Node] = []
        self.pos = (0.0, 0.0, 0.0)
        self.rot = (0.0, 0.0, 0.0, 1.0)      # x, y, z, w
        self.scale = (1.0, 1.0, 1.0)

    def __repr__(self) -> str:
        return f"<Node {self.path or self.name}>"


def parse_prefab(path: str) -> dict[str, Node]:
    """Transform hierarchy of a model prefab, keyed by Unity path.

    The root GameObject itself gets path '' - Unity's animation paths are relative
    to the object the Animator sits on, so 'root/Bip001' means root's child Bip001.
    """
    go_names: dict[int, str] = {}
    go_of: dict[int, int] = {}                # transform fileID -> gameObject fileID
    father: dict[int, int] = {}
    local: dict[int, tuple] = {}

    cls = fid = None
    cur_go_name = None
    cur = {}

    def commit() -> None:
        if cls == 1 and fid is not None and cur_go_name is not None:
            go_names[fid] = cur_go_name
        elif cls == 4 and fid is not None:
            go_of[fid] = cur.get("go", 0)
            father[fid] = cur.get("father", 0)
            local[fid] = (cur.get("pos", (0.0, 0.0, 0.0)),
                          cur.get("rot", (0.0, 0.0, 0.0, 1.0)),
                          cur.get("scale", (1.0, 1.0, 1.0)))

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            d = _DOC.match(line)
            if d:
                commit()
                cls, fid = int(d.group(1)), int(d.group(2))
                cur_go_name, cur = None, {}
                continue
            if cls not in (1, 4):
                continue
            m = _KEY.match(line.rstrip("\n"))
            if not m or m.group(1) != "  " or m.group(2):
                continue
            name, rest = m.group(3), m.group(4).strip()
            if cls == 1:
                if name == "m_Name":
                    cur_go_name = rest
            elif name == "m_GameObject":
                cur["go"] = int(rest.split(":")[1].rstrip("}"))
            elif name == "m_Father":
                cur["father"] = int(rest.split(":")[1].rstrip("}"))
            elif name == "m_LocalPosition":
                cur["pos"] = _vec(rest)
            elif name == "m_LocalRotation":
                cur["rot"] = _vec(rest)
            elif name == "m_LocalScale":
                cur["scale"] = _vec(rest)
    commit()

    nodes: dict[int, Node] = {}
    for tid, gid in go_of.items():
        n = Node(go_names.get(gid, f"<{tid}>"))
        n.pos, n.rot, n.scale = local[tid]
        nodes[tid] = n
    for tid, n in nodes.items():
        p = father.get(tid, 0)
        if p and p in nodes:
            n.parent = nodes[p]
            nodes[p].children.append(n)

    by_path: dict[str, Node] = {}
    for n in nodes.values():
        parts = []
        walk = n
        while walk.parent is not None:
            parts.append(walk.name)
            walk = walk.parent
        n.path = "/".join(reversed(parts))
        by_path[n.path] = n
    return by_path
