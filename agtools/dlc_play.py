#!/usr/bin/env python3
r"""One DLC skin -> a standalone Unity project that plays its home-screen sequences.

    python ag.py dlc 107402                 -> AG_dlc_play/107402-英招-效率至上/unity
    python ag.py dlc 107402 --refresh       re-apply shaders/helpers/manifest, no re-export

Everything the game loads for the skin's DLC scene goes into one project, so a
sequence plays there as it does in the game:
  stage       the skin's special scene (SkinSceneActionCfg -> HomeSceneSettingCfg
              prefix), e.g. 107402 -> scene 6010 -> X306a
  character   comchar/char/<skin>ui_* (the tpose model the timelines bind, with its
              game materials)
  props       comchar/prop/*_<skin>
  timelines   comeffect/uitimeline/charactor/<skin> (the timeline roots with their
              fx objects, camera rigs) and the skin's dlc clip folder
Each bundle's full dependency closure is exported by AssetRipper, and then the
same fix-ups as a stage project (stage_unity.py): the game's own decompiled
shaders, game script fields, native textures, AGSimPipeline.

The folder is named <skin>-<character>-<skin name> from the game's config
(SkinCfg.name, HeroCfg.suffix).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assetripper  # noqa: E402
import bundle_deps  # noqa: E402
import game_config  # noqa: E402
import stage_unity  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "AG_dlc_play")
STAGING = r"C:\_agdlc"


def _rows(module: str, key: str) -> list[dict]:
    return [v for v in game_config.consts(f"game/config/{module}") if isinstance(v, dict) and key in v]


def skin_info(skin: str) -> dict:
    """Stage, scene id and names of a DLC skin, from the game's config."""
    sid = int(skin)
    action = next((r for r in _rows("skinsceneactioncfg", "skin_id") if r["skin_id"] == sid), None)
    if not action:
        raise SystemExit(f"error: {skin} has no DLC scene (SkinSceneActionCfg)")
    scene = next(r for r in _rows("homescenesettingcfg", "prefix") if r["id"] == action["special_scene_id"])
    skin_row = next(r for r in _rows("skincfg", "hero") if r["id"] == sid)
    hero = next(r for r in _rows("herocfg", "suffix") if r["id"] == skin_row["hero"])
    return {"skin": skin, "scene_id": action["special_scene_id"], "stage": scene["prefix"],
            "scene_title": scene.get("title", ""), "hero": skin_row["hero"],
            "character": hero["suffix"], "skin_name": skin_row["name"],
            "folder": f"{skin}-{hero['suffix']}-{skin_row['name']}"}


def roots(info: dict, index: dict) -> list[str]:
    """The skin's own bundles plus its stage's scene bundles."""
    skin, stage = info["skin"], info["stage"].lower()
    have = set(index.values())
    out = [os.path.join("comscene", "levels", f"{stage}.ys"),
           os.path.join("comscene", "levels", f"{stage}_dep.ys"),
           os.path.join("comeffect", "uitimeline", "charactor", f"{skin}.ys")]
    for rel in sorted(have):
        low = rel.lower().replace("/", "\\")
        if "$naive" in low:
            continue
        if low.startswith(f"comchar\\char\\{skin}ui_") or (low.startswith("comchar\\prop\\") and skin in low):
            out.append(rel)
        elif low.endswith(f"\\{skin}dlc.ys") and "effectchar" in low:
            out.append(rel)
    return [r for r in dict.fromkeys(os.path.normpath(r) for r in out)
            if os.path.isfile(os.path.join(bundle_deps.ROOT, r))]


# ---------------------------------------------------------------- make it play
DLC_HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unity", "dlc")
# the character's render path, ported from P08.RenderPipeline / Battle.Simulator.Render (see its README)
CHARFX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unity", "charfx")
# game classes installed over their stubs: (source root, assembly folder)
PORTS = ((DLC_HELPERS, "P08.Timeline"), (DLC_HELPERS, "P08.Main"),
         (CHARFX, "P08.RenderPipeline"), (CHARFX, "Battle.Simulator.Render"))
# Unity 6.6 builds Cinemachine and Timeline in (6.6.0). Its Cinemachine is the 3.x line, which
# still runs the 2.x components the game data uses (Runtime/Deprecated: CinemachineVirtualCamera,
# FreeLook, Composer, ...) under their old serialized names.
PACKAGES = {"com.unity.cinemachine": "6.6.0", "com.unity.timeline": "6.6.0", "com.unity.ugui": "2.0.0"}
# AssetRipper writes stubs for the package assemblies; the real packages replace them
# (UnityEngine.UI: a skin whose bundles carry a UGUI component, 128402, gets a stub
# assembly of that name, which collides with com.unity.ugui's and stops compilation)
PACKAGE_STUBS = {"Cinemachine": "com.unity.cinemachine", "Unity.Timeline": "com.unity.timeline",
                 "UnityEngine.UI": "com.unity.ugui"}
ASSET_EXT = (".asset", ".prefab", ".playable", ".unity", ".controller", ".signal")
AUDIO = os.path.join(ROOT, "AG_dlc_scene")


def _guid(meta: str) -> str | None:
    m = re.search(r"guid: (\w+)", open(meta, encoding="utf-8", errors="replace").read(400))
    return m.group(1) if m else None


def _classes(cs: str) -> list[str]:
    return re.findall(r"^\s*(?:public |internal )?(?:sealed |abstract )?(?:class|enum|struct) (\w+)",
                      open(cs, encoding="utf-8-sig", errors="replace").read(), re.M)


def swap_package_stubs(proj: str) -> dict[str, str]:
    """Delete the AssetRipper stubs of the Cinemachine and Timeline assemblies, add the
    real packages. Returns stub guid -> class name, for rewrite_script_refs."""
    scripts = os.path.join(proj, "Assets", "Scripts")
    stub_guids = {}
    for folder in PACKAGE_STUBS:
        d = os.path.join(scripts, folder)
        for dp, _, fns in os.walk(d):
            for fn in fns:
                if fn.endswith(".cs.meta"):
                    g = _guid(os.path.join(dp, fn))
                    if g:
                        stub_guids[g] = fn[:-8]
        shutil.rmtree(d, ignore_errors=True)
        if os.path.isfile(d + ".meta"):
            os.remove(d + ".meta")
    man = os.path.join(proj, "Packages", "manifest.json")
    data = json.load(open(man, encoding="utf-8"))
    data["dependencies"].update(PACKAGES)
    json.dump(data, open(man, "w", encoding="utf-8"), indent=2)
    return stub_guids


def install_ports(proj: str) -> list[str]:
    """The game's own classes the sequences run (agtools/unity/dlc, ported from the
    decompiled hot-update DLLs) over their field-only stubs, keeping each stub's guid.
    A ported file may declare several classes: stubs of the others are removed."""
    scripts = os.path.join(proj, "Assets", "Scripts")
    stubs = {}
    for dp, _, fns in os.walk(scripts):
        for fn in fns:
            if fn.endswith(".cs"):
                stubs.setdefault(fn[:-3], os.path.join(dp, fn))
    done = []
    for root, assembly in PORTS:
        src_root = os.path.join(root, assembly)
        for dp, _, fns in os.walk(src_root):
            for fn in fns:
                if not fn.endswith(".cs"):
                    continue
                src = os.path.join(dp, fn)
                main = fn[:-3]
                dst = stubs.get(main) or os.path.join(scripts, assembly, os.path.relpath(src, src_root))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                for other in _classes(src):
                    if other != main and other in stubs and os.path.normcase(stubs[other]) != os.path.normcase(dst):
                        os.remove(stubs[other])
                        if os.path.isfile(stubs[other] + ".meta"):
                            os.remove(stubs[other] + ".meta")
                done.append(main)
    # the ports use the packages, UI and each other
    refs = {"P08.Timeline": ["Unity.Timeline", "Unity.Cinemachine", "P08.Main", "UnityEngine.UI"],
            "P08.Main": ["Unity.Timeline", "Unity.Cinemachine"]}
    for asm, want in refs.items():
        path = os.path.join(scripts, asm, f"{asm}.asmdef")
        if os.path.isfile(path):
            data = json.load(open(path, encoding="utf-8-sig"))
            data["references"] = sorted((set(data.get("references", [])) - {"Cinemachine"}) | set(want))
            json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
    # AGTools (runtime) and Editor helpers
    for root in (DLC_HELPERS, CHARFX):
        for sub, dst in (("AGTools", os.path.join(proj, "Assets", "AGTools")), ("Editor", os.path.join(proj, "Assets", "Editor"))):
            if not os.path.isdir(os.path.join(root, sub)):
                continue
            os.makedirs(dst, exist_ok=True)
            for fn in os.listdir(os.path.join(root, sub)):
                shutil.copyfile(os.path.join(root, sub, fn), os.path.join(dst, fn))
    return done


# the pipeline stand-in (stage_unity) and its character add-on (charfx/AGTools)
PIPELINE_SOURCES = ("AGSimPipeline.cs", "AGSimShadows.cs", "AGSimPostFX.cs", "AGVolumes.cs", "AGSimCharacter.cs")
# Unity's own per-camera values, set as the camera renders: read back after it drew
UNITY_CAMERA_GLOBALS = ("_Time", "_SinTime", "_CosTime", "unity_DeltaTime", "_ProjectionParams",
                        "_ScreenParams", "_ZBufferParams", "unity_OrthoParams", "_WorldSpaceCameraPos")


def pipeline_globals(proj: str) -> int:
    """Resources/ag_globals.json: every shader global the pipeline stand-in sets, by name and
    kind, read from its source (Shader.SetGlobal* / CommandBuffer.SetGlobal*), for
    AGDlcRecord to read back each frame."""
    found = {n: "Vector" for n in UNITY_CAMERA_GLOBALS}
    for fn in PIPELINE_SOURCES:
        path = next((p for p in (os.path.join(stage_unity.HELPERS, fn), os.path.join(CHARFX, "AGTools", fn))
                     if os.path.isfile(p)), None)
        if not path:
            continue
        text = open(path, encoding="utf-8").read()
        for kind, name in re.findall(r"SetGlobal(Vector|Color|Float|Int|Matrix|VectorArray|FloatArray|MatrixArray|Texture)\(\"(\w+)\"", text):
            found.setdefault(name, kind)
        # names held in string arrays and set through a variable (the SH coefficients)
        for name in re.findall(r"\"(_Replica_SH\w+)\"", text):
            found.setdefault(name, "Vector")
    # a global texture's companions Unity sets with it: <name>_HDR (decode instructions)
    # and <name>_TexelSize
    for name, kind in list(found.items()):
        if kind == "Texture":
            found.setdefault(name + "_HDR", "Vector")
            found.setdefault(name + "_TexelSize", "Vector")
    res = os.path.join(proj, "Assets", "AGTools", "Resources")
    os.makedirs(res, exist_ok=True)
    json.dump({"globals": [{"name": n, "kind": k} for n, k in sorted(found.items())]},
              open(os.path.join(res, "ag_globals.json"), "w", encoding="utf-8"), indent=1)
    return len(found)


def shader_uniforms(proj: str) -> int:
    """Resources/ag_shader_uniforms.json: for every shader a material of the project uses, the
    uniform names its decompiled source declares (the #ifndef AG_D_<name> guards) -
    everything a property block on one of its renderers can hold, for AGDlcRecord to ask
    for (Unity cannot list a block's contents)."""
    assets = os.path.join(proj, "Assets")
    by_guid = {}
    used = set()
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            path = os.path.join(dp, fn)
            if fn.endswith(".shader.meta"):
                g = _guid(path)
                if g:
                    by_guid[g] = path[:-5]
            elif fn.endswith(".mat"):
                m = re.search(r"m_Shader: \{fileID: \d+, guid: (\w+)", open(path, encoding="utf-8", errors="replace").read())
                if m:
                    used.add(m.group(1))
    out = []
    for g in sorted(used):
        path = by_guid.get(g)
        if not path:
            continue
        name, names = None, set()
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if name is None:
                    m = re.search(r'Shader\s+"([^"]+)"', line)
                    if m:
                        name = m.group(1)
                if line.startswith("#define AG_D_"):
                    names.add(line[len("#define AG_D_"):].strip())
        if name:
            out.append({"shader": name, "names": sorted(names)})
    res = os.path.join(assets, "AGTools", "Resources")
    os.makedirs(res, exist_ok=True)
    json.dump({"shaders": out}, open(os.path.join(res, "ag_shader_uniforms.json"), "w", encoding="utf-8"), indent=1)
    return len(out)


def exposed_references(proj: str) -> int:
    """Make the timelines' exposed references resolve again.

    A build keeps an ExposedReference's name only as its PropertyName hash: AssetRipper
    writes the reference as `exposedName: {id: 1576634011}` and the PlayableDirector's table
    as `- 1576634011: {fileID: ...}`. The editor reads both names as strings and hashes
    them, so the id never meets its own hash and nothing resolves: the ControlTracks that
    switch a sequence's fx on (107402 touch1: the keyboard keys, the photo panels) did
    nothing. Both sides become the same string, agref_<id>.

    The director looks a name up by binary search over its table sorted by the name's hash
    (CRC-32 as a signed int), the order the build's ids were in: the renamed table is sorted
    again, or a reference that sorts out of place resolves to null (touch1's keyboard)."""
    ref = re.compile(r"(exposedName:)\s*\n(\s+)id: (-?\d+)")
    table = re.compile(r"(m_ExposedReferences:\s*\n\s+m_References:\s*\n)((?:\s+- (?:agref_)?-?\d+: \{[^\n]*\}\n)+)")
    entry = re.compile(r"(\s+- )(?:agref_)?(-?\d+)(: \{[^\n]*\}\n)")

    def hashed(name: str) -> int:
        h = zlib.crc32(name.encode())
        return h - (1 << 32) if h >= 1 << 31 else h

    def sort_table(m: re.Match) -> str:
        rows = [(f"agref_{e.group(2)}", e) for e in entry.finditer(m.group(2))]
        rows.sort(key=lambda r: hashed(r[0]))
        return m.group(1) + "".join(f"{e.group(1)}{name}{e.group(3)}" for name, e in rows)

    changed = 0
    for dp, _, fns in os.walk(os.path.join(proj, "Assets")):
        for fn in fns:
            if not fn.endswith(ASSET_EXT):
                continue
            path = os.path.join(dp, fn)
            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            if "exposedName:" not in text and "m_ExposedReferences:" not in text:
                continue
            new = ref.sub(lambda m: f"{m.group(1)} agref_{m.group(3)}", text)
            new = table.sub(sort_table, new)
            if new != text:
                open(path, "w", encoding="utf-8", errors="surrogateescape").write(new)
                changed += 1
    return changed


# a build keeps a material curve's property only as a hash: AssetRipper writes the
# binding as `attribute: material.path_0x<H>_<junk>`, H = kind << 28 | (CRC-32 of the
# property name) & 0x0FFFFFFF, kind 0-3 a vector's x..w, 4-7 a colour's r..a, 8 a float
MATERIAL_CURVE = re.compile(r"(attribute: material\.)path_0x([0-9A-Fa-f]{1,8})_\w+")
VECTOR_SUFFIX, COLOUR_SUFFIX = (".x", ".y", ".z", ".w"), (".r", ".g", ".b", ".a")


def material_curves(proj: str) -> tuple[int, list[str]]:
    """Give the timelines' material curves (fade a liquid, dissolve it: 102202's pour,
    `_Color` and `_DissovleStrength`) their property names back, so Unity plays them.
    Every shader property name of the project is hashed and matched; a curve whose
    hash matches none is left as it is and reported. Returns (curves named, unknown)."""
    assets = os.path.join(proj, "Assets")
    names = {}
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if not fn.endswith(".shader"):
                continue
            text = open(os.path.join(dp, fn), encoding="utf-8", errors="replace").read()
            for name, kind in re.findall(r"^\s*(?:\[[^\]]*\]\s*)*(_\w+)\s*\(\s*\"[^\"]*\"\s*,\s*(\w+)", text, re.M):
                names.setdefault(zlib.crc32(name.encode()) & 0x0FFFFFFF, (name, kind.lower()))
    named, unknown = 0, set()

    def sub(m: re.Match) -> str:
        nonlocal named
        h = int(m.group(2), 16)
        got = names.get(h & 0x0FFFFFFF)
        kind = h >> 28
        if not got or kind > 8:
            unknown.add(m.group(2))
            return m.group(0)
        name, ptype = got
        named += 1
        if kind == 8:
            return f"{m.group(1)}{name}"
        return f"{m.group(1)}{name}{(COLOUR_SUFFIX if ptype == 'color' else VECTOR_SUFFIX)[kind & 3]}"

    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if not fn.endswith(".anim"):
                continue
            path = os.path.join(dp, fn)
            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            if "material.path_0x" not in text:
                continue
            new = MATERIAL_CURVE.sub(sub, text)
            if new != text:
                open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(new)
    return named, sorted(unknown)


SCRIPT_CURVE = re.compile(r"(attribute: )(\S+)(\n\s+path:([^\n]*)\n\s+classID: 114\n"
                          r"\s+script: \{fileID: 11500000, guid: )(\w+)")
_YAML_KEY = re.compile(r"^( *)([A-Za-z_]\w*):(.*)$")


def _field_paths(doc: str) -> list[str]:
    """The dotted serialized field paths of one MonoBehaviour document (nested mappings by
    indentation, inline {x: .., y: ..} mappings by key; sequences are not descended)."""
    out, stack = [], []
    for line in doc.split("\n")[2:]:
        m = _YAML_KEY.match(line)
        if not m:
            continue
        ind, key, rest = len(m.group(1)), m.group(2), m.group(3).strip()
        while stack and stack[-1][0] >= ind:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if not rest:
            stack.append((ind, key))
            continue
        out.append(path)
        if rest.startswith("{") and "fileID" not in rest:
            out += [f"{path}.{k}" for k in re.findall(r"(\w+):", rest)]
    return out


def script_curves(proj: str) -> tuple[int, list[str]]:
    """Give the timelines' script curves their field names back, so Unity plays them:
    AssetRipper writes a MonoBehaviour float curve as `script_0x<CRC-32 of the field path>_..`
    (a vcam's m_Lens.FieldOfView and m_Lens.Dutch, a CharacterPointLightController's
    diffuseIntensity, a ReplicaAdditionalLightData's m_ShapeRadius...), which binds to nothing.
    The paths come from the serialized components of the project's prefabs and scenes; a
    curve whose script guid no component uses (a package stub swap_package_stubs removed)
    takes the guid of the components that carry the field. Returns (curves named, unknown)."""
    assets = os.path.join(proj, "Assets")
    anims = []
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if fn.endswith(".anim"):
                path = os.path.join(dp, fn)
                text = open(path, encoding="utf-8", errors="surrogateescape").read()
                if "classID: 114" in text:
                    anims.append((path, text))
    if not anims:
        return 0, []
    fields: dict[int, set[tuple[str, str]]] = {}
    used = set()
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if not fn.endswith((".prefab", ".unity")):
                continue
            text = open(os.path.join(dp, fn), encoding="utf-8", errors="surrogateescape").read()
            go_names = dict(re.findall(r"\n--- !u!1 &(-?\d+)\nGameObject:.*?\n  m_Name: ([^\n]*)", text, re.S))
            for doc in text.split("\n--- !u!114 ")[1:]:
                g = re.search(r"\n  m_Script: \{fileID: 11500000, guid: (\w+)", doc)
                if not g:
                    continue
                used.add(g.group(1))
                go = re.search(r"\n  m_GameObject: \{fileID: (-?\d+)\}", doc)
                go = go_names.get(go.group(1), "") if go else ""
                for p in _field_paths(doc):
                    fields.setdefault(zlib.crc32(p.encode()), set()).add((p, g.group(1), go))
    named, unknown = 0, set()

    def sub(m: re.Match) -> str:
        nonlocal named
        attr, guid = m.group(2), m.group(5)
        h = re.match(r"script_0x([0-9A-Fa-f]{1,8})_\w+$", attr)
        if not h and guid in used:
            return m.group(0)
        cands = fields.get(int(h.group(1), 16) if h else zlib.crc32(attr.encode()), set())
        names = {p for p, _, _ in cands}
        if len(names) != 1:
            if h:
                unknown.add(h.group(1))
            return m.group(0)
        if guid not in used:
            # the components that carry the field; when several classes do (a vcam's
            # m_Lens), the one on the object the curve animates (its path's last name)
            owners = {g for _, g, _ in cands}
            if len(owners) > 1:
                leaf = m.group(4).strip().split("/")[-1]
                owners = {g for _, g, go in cands if go == leaf} or owners
            if len(owners) == 1:
                guid = owners.pop()
        named += h is not None or guid != m.group(5)
        return f"{m.group(1)}{names.pop()}{m.group(3)}{guid}"

    for path, text in anims:
        new = SCRIPT_CURVE.sub(sub, text)
        if new != text:
            open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(new)
    return named, sorted(unknown)


def readable_meshes(proj: str) -> int:
    """Mesh assets as readable (m_IsReadable: 1): AGDlcRecord reads their vertex streams in
    Play mode, which Unity refuses for a mesh saved non-readable."""
    n = 0
    for dp, _, fns in os.walk(os.path.join(proj, "Assets")):
        for fn in fns:
            if not fn.endswith(".asset"):
                continue
            path = os.path.join(dp, fn)
            with open(path, "rb") as fh:
                head = fh.read(200)
            if b"--- !u!43 " not in head:           # 43 = Mesh
                continue
            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            new = text.replace("m_IsReadable: 0", "m_IsReadable: 1", 1)
            if new != text:
                open(path, "w", encoding="utf-8", errors="surrogateescape").write(new)
                n += 1
    return n


# the packages PACKAGES names are built into the editor: their sources (and script guids)
# are here before a project has ever opened
BUILTIN_PACKAGES = os.path.join(os.path.dirname(stage_unity.UNITY), "Data", "Resources", "PackageManager", "BuiltInPackages")


def rewrite_script_refs(proj: str, stub_guids: dict[str, str]) -> int:
    """Every asset reference to a stub script -> the package's script of that class. The
    package scripts' guids come from the editor's built-in packages (the very files Unity
    copies into Library/PackageCache), so this runs before the first import."""
    real = {}
    for root in [os.path.join(BUILTIN_PACKAGES, p) for p in PACKAGE_STUBS.values()] + [os.path.join(proj, "Library", "PackageCache")]:
        for dp, _, fns in os.walk(root):
            if not any(p in dp for p in PACKAGE_STUBS.values()):
                continue
            for fn in fns:
                if fn.endswith(".cs.meta"):
                    real.setdefault(fn[:-8], _guid(os.path.join(dp, fn)))
    remap = {g: real[c] for g, c in stub_guids.items() if c in real}
    missing = sorted(c for c in stub_guids.values() if c not in real)
    if missing:
        print(f"   no package script for {missing}")
    if not remap:
        return 0
    pat = re.compile(r"(m_Script: \{fileID: 11500000, guid: )(" + "|".join(remap) + r")\b")
    changed = 0
    for dp, _, fns in os.walk(os.path.join(proj, "Assets")):
        for fn in fns:
            if not fn.endswith(ASSET_EXT):
                continue
            path = os.path.join(dp, fn)
            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            new = pat.sub(lambda m: m.group(1) + remap[m.group(2)], text)
            if new != text:
                open(path, "w", encoding="utf-8", errors="surrogateescape").write(new)
                changed += 1
    return changed


def dlc_spec(proj: str, info: dict) -> dict:
    """ag_dlc.json: model prefab, stage scene, the sequences to play and their audio."""
    assets = os.path.join(proj, "Assets")
    rel = lambda p: os.path.relpath(p, proj).replace("\\", "/")
    model = None
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if fn == f"{info['skin']}ui_custom.prefab" and "ABResources" in dp:
                model = os.path.join(dp, fn)
    seq_dir = None
    for dp, _, fns in os.walk(assets):
        if dp.replace("\\", "/").endswith(f"UITimeLine/Charactor/{info['skin']}"):
            seq_dir = dp
    audio_dst = os.path.join(assets, "AGDlc", "Audio")
    os.makedirs(audio_dst, exist_ok=True)
    seqs = []
    for name in info["play"]:
        # touch1.prefab, or the skin-prefixed 109503ui_wedding_touch_101.prefab
        prefab = next((p for p in (os.path.join(seq_dir, f"{name}.prefab"), os.path.join(seq_dir, f"{info['skin']}ui_{name}.prefab"))
                       if os.path.isfile(p)), "") if seq_dir else ""
        wav = os.path.join(AUDIO, info["skin"], name, "audio.wav")
        audio = ""
        if os.path.isfile(wav):
            shutil.copyfile(wav, os.path.join(audio_dst, f"{name}.wav"))
            audio = rel(os.path.join(audio_dst, f"{name}.wav"))
        seqs.append({"name": name, "prefab": rel(prefab) if os.path.isfile(prefab) else "", "audio": audio})
    scene = next((s for s in stage_unity.find_scenes(os.path.dirname(proj)) if s.lower().endswith(info["stage"].lower() + ".unity")), "")
    spec = {"skin": info["skin"], "modelId": f"{info['skin']}ui_custom", "model": rel(model) if model else "",
            "stageScene": scene.replace("\\", "/").split("ExportedProject/", 1)[-1], "sequences": seqs}
    json.dump(spec, open(os.path.join(proj, "ag_dlc.json"), "w", encoding="utf-8"), indent=1)
    return spec


def unity(proj: str, log: str, method: str | None = None) -> int:
    cmd = [stage_unity.UNITY, "-batchmode", "-quit", "-projectPath", proj, "-logFile", log]
    if method:
        cmd += ["-executeMethod", method]
    return subprocess.run(cmd, check=False).returncode


def prepare_playable(project: str, info: dict) -> str:
    """Every file change of the play setup, made before Unity opens the project (after the
    stage fix-ups: the game's shaders are in), so its one launch imports each asset once,
    as it stays. The package stubs' references go straight to the editor's built-in
    package scripts."""
    proj = os.path.join(project, "ExportedProject")
    stage_unity.copy_helpers(project)          # the pipeline stand-in, current
    stub_guids = swap_package_stubs(proj)
    ports = install_ports(proj)
    spec = dlc_spec(proj, info)
    pipeline_globals(proj)
    shader_uniforms(proj)
    readable_meshes(proj)
    exposed_references(proj)
    named, unknown = material_curves(proj)
    if named or unknown:
        print(f"   material curves: {named} named" + (f", unknown hashes {unknown}" if unknown else ""))
    named, unknown = script_curves(proj)
    if named or unknown:
        print(f"   script curves: {named} named" + (f", unknown hashes {unknown}" if unknown else ""))
    changed = rewrite_script_refs(proj, stub_guids)
    return (f"{len(ports)} game classes ported, {len(stub_guids)} package stubs swapped, {changed} assets "
            f"repointed, scene for {[s['name'] for s in spec['sequences']]}")


def compile_errors(log: str) -> str:
    if not os.path.isfile(log):
        return f"; NO LOG {log}"
    errors = sorted({l.strip() for l in open(log, encoding="utf-8", errors="replace") if "error CS" in l})
    return f"; COMPILE ERRORS: {errors[:5]}" if errors else ""


def make_playable(project: str, info: dict) -> str:
    """The play setup on an existing export (--playable-only): files, then the DLC scene."""
    note = prepare_playable(project, info)
    proj = os.path.join(project, "ExportedProject")
    log = os.path.join(project, "_unity_dlc.log")
    rc = unity(proj, log, "AGDlcScene.Batch")
    return f"{note} (unity {rc})" + compile_errors(log)


def _clear(path: str) -> None:
    """Remove a previous export. A Unity or compiler-server process shutting down can
    hold a folder for a few seconds; AssetRipper then fails to clear it and exports
    nothing, so retry here and stop if it stays locked."""
    for _ in range(12):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(5)
    raise SystemExit(f"error: could not remove {path} (a process still holds it)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skins", nargs="+")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--play", default="touch1,touch2", help="sequences to play back to back")
    ap.add_argument("--playable-only", action="store_true", help="existing export: only (re)do the play setup")
    args = ap.parse_args()

    index = bundle_deps.load_index()
    for skin in args.skins:
        info = skin_info(skin)
        info["play"] = [x for x in args.play.split(",") if x]
        base = os.path.join(args.out, info["folder"])
        project = os.path.join(base, "unity")
        if args.playable_only:
            print(f"{skin}: {make_playable(project, info)}", flush=True)
            continue
        if args.refresh:
            meta = json.load(open(os.path.join(project, "ag_stage.json"), encoding="utf-8"))
            stage_unity.copy_helpers(project)
            note = stage_unity.finish(project, info["stage"].lower(), meta["bundles"], set(meta.get("unresolved", [])), "")
            print(f"{skin}: refreshed - {note}", flush=True)
            continue
        t0 = time.time()
        rs = roots(info, index)
        bundles, missing = bundle_deps.closure(rs, index)
        print(f"{skin} ({info['folder']}, stage {info['stage']}): {len(rs)} roots, {len(bundles)} bundles"
              + (f", {len(missing)} unresolved refs" if missing else ""), flush=True)
        for r in rs:
            print("   root", r)
        staged = os.path.join(STAGING, skin)
        size = stage_unity.link_into(bundles, staged)
        print(f"   {size / 1e6:.0f} MB staged", flush=True)
        _clear(project)
        os.makedirs(base, exist_ok=True)
        with assetripper.Server(os.path.join(base, "_assetripper.log")) as ar:
            ar.export(staged, project, ShaderExportMode="Dummy")
        shutil.rmtree(staged, ignore_errors=True)
        scripts = stage_unity.install_helpers(project, [os.path.join(bundle_deps.ROOT, b) for b in bundles])
        # the play setup's file changes go in after the stage fix-ups (the game's shaders
        # installed), then one Unity launch: import, the stage manifest, the DLC scene
        playable = []
        note = stage_unity.finish(project, info["stage"].lower(), bundles, missing, f"{scripts} game script(s) rebuilt; ",
                                  method="AGDlcScene.BatchAll",
                                  before_unity=lambda: playable.append(prepare_playable(project, info)))
        with open(os.path.join(base, "dlc.json"), "w", encoding="utf-8") as fh:
            json.dump({**info, "roots": rs}, fh, indent=1, ensure_ascii=False)
        note += "; " + "".join(playable) + compile_errors(os.path.join(project, "_unity_batch.log"))
        print(f"  -> {project}  ({time.time() - t0:.0f}s)\n     {note}\n     {stage_unity.summarize(project)}", flush=True)
    shutil.rmtree(STAGING, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
