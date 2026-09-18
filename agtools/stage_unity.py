#!/usr/bin/env python3
r"""One stage -> a standalone Unity project that renders like the game.

    python ag.py stage x343                 -> AG_stages/x343/ExportedProject (Unity 6000.x)
    python ag.py stage x343 x317 comeffect/x100   several (folder/code picks the level folder)
    python ag.py stage sourcespace          the 7 modifier-mode spaces, one scene each
    python ag.py stage x305 --refresh       re-apply shaders/helpers/manifest, no re-export

Per stage:
  1. dependency closure of <code>.ys + <code>_dep.ys (bundle_deps.py), hardlinked into a
     staging folder, exported by AssetRipper
  2. the game's own shaders (AG_shaders, decompile_shaders.py: bit-exact HLSL from the
     D3D11 bytecode) replace AssetRipper's stubs; native texture data (HDR intact)
  3. game MonoBehaviours get their serialized fields back (gen_mono_scripts.py), scene
     fix-ups (inactive roots, material-less renderers), Linear colour space
  4. the pipeline stand-in (unity/AGSimPipeline.cs + AGVolumes.cs + AGSimPostFX.cs +
     AGSimShadows.cs) - a port of the game's decompiled render pipeline (README "Reverse
     engineering") - plus camera, edit-mode playback, viewpoints, LTC table
  5. AG_reference/ (pipeline reference) + PORTING.md, then one batch Unity run writes
     ag_render_manifest.json (AGManifest.cs) and prefab scenes (AGPrefabScenes.cs)
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assetripper  # noqa: E402
import bundle_deps  # noqa: E402
import scenes  # noqa: E402

OUT = r"C:\AetherGazerStarter\AG_stages"
STAGING = r"C:\_agstage"


# Stages that ship as prefabs instead of scenes, exported together into one project
# with a generated scene per prefab (AGPrefabScenes.cs). The modifier-mode
# ("SourceSpace") arenas: config table SourceSpace -> prefab, one per pantheon.
# Scene names stay ASCII: Unity's asset paths garble non-ASCII names on this locale.
GROUPS = {
    "sourcespace": [("comeffect/effect/sourcespacescene/x01.ys", "X01_Tongyong_General"),   # 通用
                    ("comeffect/effect/sourcespacescene/x02.ys", "X02_Zhenying_Sakura"),   # 真樱
                    ("comeffect/effect/sourcespacescene/x03.ys", "X03_Niluo_Nile"),   # 尼罗
                    ("comeffect/effect/sourcespacescene/x04.ys", "X04_Shengshu_HolyTree"),   # 圣树
                    ("comeffect/effect/sourcespacescene/x05.ys", "X05_Aoshan_Olympus"),   # 奥山
                    ("comeffect/effect/sourcespacescene/x06.ys", "X06_Zhongxing_Stars"),   # 众星
                    ("comeffect/effect/sourcespacescene/x07.ys", "X07_Tianyuan")],   # 天垣
}
# Scenes whose camera the game places in code (home scenes): the game's viewpoint,
# found by matching renders against the game's own pictures (AG_stage_names/home).
# scene name -> position, euler angles, vertical fov. AGStageCamera starts there.
VIEWPOINTS = {
    "X10": ([-2.2, 2.7, 16.5], [0.0, 180.0, 0.0], 35.0),      # home 6000 (day)
    "X10a": ([-2.2, 2.7, 16.5], [0.0, 180.0, 0.0], 35.0),     # home 6000, night
    "X202": ([-9.2, 2.2, 12.0], [-4.0, 180.0, 0.0], 50.0),     # home 6100
    "X202a": ([-9.2, 2.2, 12.0], [-4.0, 180.0, 0.0], 50.0),
    "X305": ([0.9, 1.55, 2.3], [4.9, -135.0, 0.0], 45.0),     # 1095 DLC piano stage (109501)
}
UNITY = r"C:\Program Files\Unity\Hub\Editor\6000.6.1f1\Editor\Unity.exe"
PIPELINE_REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AG_pipeline")
PORTING_DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unity", "PORTING.md")


def project_name(code: str) -> str:
    """AG_stages/<name>: 'comeffect/x100' -> 'x100'."""
    return code.replace("\\", "/").split("/")[-1]


def stage_roots(code: str) -> list[str]:
    if code in GROUPS:
        return [os.path.normpath(b) for b, _ in GROUPS[code]]
    folder, _, code = code.replace("\\", "/").rpartition("/")
    d = scenes.level_dir(code, folder)
    return [os.path.relpath(os.path.join(d, f"{code}{s}.ys"), bundle_deps.ROOT)
            for s in ("", "_dep") if os.path.isfile(os.path.join(d, f"{code}{s}.ys"))]


def stage_bundles(code: str, index: dict) -> tuple[list[str], set[str]]:
    roots = stage_roots(code)
    if not roots:
        raise SystemExit(f"error: no bundles for stage '{code}'")
    return bundle_deps.closure(roots, index)


def link_into(bundles: list[str], folder: str) -> int:
    """Hardlink (or copy, across volumes) each bundle, keeping its relative path so
    names stay unique."""
    shutil.rmtree(folder, ignore_errors=True)
    total = 0
    for rel in bundles:
        src = os.path.join(bundle_deps.ROOT, rel)
        dst = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)
        total += os.path.getsize(src)
    return total


def assets_dir(project: str) -> str:
    return os.path.join(project, "ExportedProject", "Assets")


def find_scenes(project: str) -> list[str]:
    out = []
    for dp, _, fns in os.walk(assets_dir(project)):
        out += [os.path.relpath(os.path.join(dp, f), project) for f in fns if f.endswith(".unity")]
    return sorted(out)


SHADER_SRC = r"C:\AetherGazerStarter\AG_shaders"   # decompiled game shaders (USCSandbox)


def install_real_shaders(project: str, src: str = SHADER_SRC) -> tuple[int, list[str]]:
    """Replace AssetRipper's stub shaders with the decompiled game shaders.

    Matched by the ShaderLab name on the first line ("SimPipeline/PBR/Standard"),
    which is also the decompiled file's path under AG_shaders. The .meta files -
    and so the GUIDs every material points at - are left alone. Returns (installed,
    stub names with no decompiled source)."""
    installed, missing = 0, []
    # every .shader, not just stubs: re-running refreshes a previous install
    paths = [os.path.join(dp, f) for dp, _, fns in os.walk(assets_dir(project))
             for f in fns if f.endswith(".shader")]
    for path in paths:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            head = fh.read(4096)
        # Unity's shader upgrader prepends "// Upgrade NOTE:" lines on import
        m = re.search(r'^\s*Shader\s+"([^"]+)"', head, re.M)
        if not m:
            continue
        # stage shaders first; pipeline-owned ones (Hidden/VolumetricLight, ...) are
        # decompiled from player data into AG_pipeline/shaders
        real = next((c for c in (os.path.join(root, *m.group(1).split("/")) + ".shader"
                                 for root in (src, os.path.join(PIPELINE_REF, "shaders")))
                     if os.path.isfile(c)), "")
        if real:
            shutil.copyfile(real, path)
            installed += 1
        elif not m.group(1).startswith("Hidden/AG/"):      # our own helper shaders
            missing.append(m.group(1))
    return installed, sorted(missing)


def set_linear_color_space(project: str) -> bool:
    """The game renders in Linear (globalgamemanagers PlayerSettings
    m_ActiveColorSpace = 1); a bundles-only export gets Unity's Gamma default,
    which shifts every colour the shaders produce."""
    path = os.path.join(project, "ExportedProject", "ProjectSettings", "ProjectSettings.asset")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    new = re.sub(r"(\n\s+m_ActiveColorSpace: )\d", r"\g<1>1", text)
    if new != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
    return "m_ActiveColorSpace: 1" in new


HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unity")
EDITOR_HELPERS = ("AGStageShot.cs", "AGOpenStage.cs")
PIPELINE_EDITOR_HELPERS = ("AGManifest.cs", "AGPrefabScenes.cs", "AGEditorPlayback.cs")   # need AGTools
RUNTIME_HELPERS = ("AGSimPipeline.cs", "AGVolumes.cs", "AGSimShadows.cs", "AGSimPostFX.cs", "AGStageCamera.cs", "AGLutStrip.shader")


def copy_helpers(project: str) -> None:
    """Editor helpers (Assets/Editor), pipeline stand-in (Assets/AGTools), viewpoints + LTC."""
    editor = os.path.join(assets_dir(project), "Editor")
    os.makedirs(editor, exist_ok=True)
    for h in EDITOR_HELPERS:
        shutil.copyfile(os.path.join(HELPERS, h), os.path.join(editor, h))
    tools = os.path.join(assets_dir(project), "AGTools")
    os.makedirs(tools, exist_ok=True)
    for h in RUNTIME_HELPERS:
        shutil.copyfile(os.path.join(HELPERS, h), os.path.join(tools, h))
    for h in PIPELINE_EDITOR_HELPERS:
        shutil.copyfile(os.path.join(HELPERS, h), os.path.join(editor, h))
    write_viewpoints(project)


def install_helpers(project: str, bundles: list[str]) -> int:
    """Helpers plus the game-script field layouts and native textures. Returns how many
    game scripts were rebuilt."""
    copy_helpers(project)
    import gen_mono_scripts
    import native_textures
    written, _ = gen_mono_scripts.generate(project, bundles)
    native_textures.convert(project, bundles)   # exact texture data, HDR intact
    return written


def match_asset_names(project: str) -> int:
    """Rename objects whose name AssetRipper had to sanitise for the filename.

    Unity's static batching names merged meshes "Combined Mesh (root: scene)"; ':'
    can't go in a Windows filename, so the file is "Combined Mesh (root_ scene)"
    and Unity warns on every such asset ("main object name should match the asset
    filename"). Same fix as its Fix button: take the filename as the name."""
    fixed = 0
    for dp, _, fns in os.walk(assets_dir(project)):
        for fn in fns:
            if not fn.endswith(".asset"):
                continue
            stem = fn[:-6]
            path = os.path.join(dp, fn)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            m = re.search(r"\n  m_Name: (.*)\n", text)
            # YAML quotes the name because of the ':' - compare without the quotes
            name = m.group(1).strip("'\"") if m else ""
            if not m or name == stem or re.sub(r'[:*?"<>|/\\]', "_", name) != stem:
                continue
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text[:m.start(1)] + stem + text[m.end(1):])
            fixed += 1
    return fixed


def reference(project: str, code: str) -> None:
    """Everything the port needs beside the scene, inside the project (outside
    Assets, so Unity ignores it): the extracted pipeline (AG_pipeline: pipeline
    shaders, compute kernels, feature settings, lookup textures), this stage's
    slice of the component catalogue, and PORTING.md."""
    dst = os.path.join(project, "ExportedProject", "AG_reference")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(PIPELINE_REF, os.path.join(dst, "pipeline"),
                    ignore=shutil.ignore_patterns("volume_components.json"))
    cat_path = os.path.join(PIPELINE_REF, "volume_components.json")
    if os.path.isfile(cat_path):
        with open(cat_path, encoding="utf-8") as fh:
            cat = json.load(fh)
        mine = {k: {"assembly": e["assembly"], "fields": e["fields"], "values": e["values"][code]}
                for k, e in cat.items() if code in e["values"]}
        with open(os.path.join(dst, "stage_components.json"), "w", encoding="utf-8") as fh:
            json.dump(mine, fh, indent=1, ensure_ascii=False)
    shutil.copyfile(PORTING_DOC, os.path.join(project, "ExportedProject", "PORTING.md"))


def _guid_paths(assets: str) -> dict[str, str]:
    out = {}
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if fn.endswith(".meta"):
                with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as fh:
                    m = re.search(r"^guid: ([0-9a-f]{32})", fh.read(400), re.M)
                if m:
                    out[m.group(1)] = os.path.relpath(os.path.join(dp, fn[:-5]),
                                                      os.path.dirname(assets)).replace("\\", "/")
    return out


def unity_batch(project: str, prefab_scenes: list[tuple[str, str]]) -> str:
    """One Unity launch: import, generate prefab scenes, write ag_render_manifest.json
    (AGManifest.cs), then give every guid in it its asset path."""
    proj = os.path.join(project, "ExportedProject")
    if prefab_scenes:
        with open(os.path.join(proj, "ag_prefab_scenes.txt"), "w", encoding="utf-8") as fh:
            fh.writelines(f"{p}\t{n}\n" for p, n in prefab_scenes)
        # regenerate from scratch (renamed scenes must not linger)
        shutil.rmtree(os.path.join(proj, "Assets", "AGScenes"), ignore_errors=True)
        if os.path.isfile(os.path.join(proj, "Assets", "AGScenes.meta")):
            os.remove(os.path.join(proj, "Assets", "AGScenes.meta"))
    set_linear_color_space(project)     # again: AssetRipper can write ProjectSettings late
    if not os.path.isfile(UNITY):
        return "Unity not found - open the project and run AGManifest.Batch to write the manifest"
    log = os.path.join(project, "_unity_batch.log")
    subprocess.run([UNITY, "-batchmode", "-quit", "-projectPath", proj,
                    "-executeMethod", "AGManifest.Batch", "-logFile", log], check=False)
    man = os.path.join(proj, "ag_render_manifest.json")
    if not os.path.isfile(man):
        return f"manifest NOT written - see {log}"
    with open(man, encoding="utf-8") as fh:
        data = json.load(fh)
    paths = _guid_paths(os.path.join(proj, "Assets"))

    def walk(x):
        if isinstance(x, dict):
            g = x.get("guid")
            if isinstance(g, str) and g in paths:
                x["assetPath"] = paths[g]
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    with open(man, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    return f"manifest: {len(data['scenes'])} scene(s) -> {man}"


def prefab_scene_list(project: str, code: str) -> list[tuple[str, str]]:
    """(prefab asset path, scene name) for a prefab group, found in the export."""
    out = []
    assets = assets_dir(project)
    for bundle, name in GROUPS.get(code, []):
        stem = os.path.splitext(os.path.basename(bundle))[0].lower()
        for dp, _, fns in os.walk(assets):
            hit = [f for f in fns if f.lower() == stem + ".prefab"]
            if hit:
                out.append((os.path.relpath(os.path.join(dp, hit[0]), os.path.dirname(assets))
                            .replace("\\", "/"), name))
                break
    return out


def activate_roots(project: str) -> list[str]:
    """Switch on scene root objects saved inactive. Some stages (x10, x10a, x100)
    ship with their root disabled and the game enables it when it shows the scene;
    as exported they would open empty. Only the roots are touched - children stay
    as authored. Returns 'scene: object' for each one switched on (kept in
    ag_stage.json so the change is on record)."""
    changed = []
    for rel in find_scenes(project):
        path = os.path.join(project, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        docs = text.split("\n--- ")
        roots = set()
        for d in docs:
            if re.match(r"!u!(4|224) ", d) and "m_Father: {fileID: 0}" in d:
                m = re.search(r"m_GameObject: \{fileID: (-?\d+)\}", d)
                if m:
                    roots.add(m.group(1))
        for i, d in enumerate(docs):
            m = re.match(r"!u!1 &(-?\d+)", d)
            if m and m.group(1) in roots and "\n  m_IsActive: 0" in d:
                docs[i] = d.replace("\n  m_IsActive: 0", "\n  m_IsActive: 1", 1)
                changed.append(f"{os.path.basename(rel)}: {re.search(r'm_Name: (.*)', d).group(1)}")
        new = "\n--- ".join(docs)
        if new != text:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
    return changed


def write_viewpoints(project: str) -> None:
    res = os.path.join(assets_dir(project), "AGTools", "Resources")
    os.makedirs(res, exist_ok=True)
    # area-light LUT the game builds at startup (AreaLightFeature: 64x64 RGBAHalf), as bytes
    ltc = os.path.join(PIPELINE_REF, "textures", "LTC_LUT_64x64_RGBAHalf.raw")
    if os.path.isfile(ltc):
        shutil.copyfile(ltc, os.path.join(res, "ag_ltc.bytes"))
    with open(os.path.join(res, "ag_viewpoints.json"), "w", encoding="utf-8") as fh:
        json.dump({"views": [{"scene": k, "position": p, "euler": e, "fov": f}
                             for k, (p, e, f) in VIEWPOINTS.items()]}, fh, indent=1)


def disable_unmaterialed(project: str) -> list[str]:
    """Disable renderers whose every material slot is empty. The game ships a few
    (x10a's window glass): a player build draws nothing for them, the editor draws
    them solid magenta. Returns 'scene: object' per renderer disabled."""
    changed = []
    for rel in find_scenes(project):
        path = os.path.join(project, rel)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        docs = text.split("\n--- ")
        names = {}
        for d in docs:
            m = re.match(r"!u!1 &(-?\d+)", d)
            if m:
                names[m.group(1)] = re.search(r"m_Name: (.*)", d).group(1)
        for i, d in enumerate(docs):
            if not re.match(r"!u!(23|137) ", d):
                continue
            mats = re.search(r"\n  m_Materials:\n((?:  - .*\n)+)", d)
            if mats and all("fileID: 0}" in ln for ln in mats.group(1).splitlines()) \
                    and "\n  m_Enabled: 1" in d:
                docs[i] = d.replace("\n  m_Enabled: 1", "\n  m_Enabled: 0", 1)
                go = re.search(r"m_GameObject: \{fileID: (-?\d+)\}", d).group(1)
                changed.append(f"{os.path.basename(rel)}: {names.get(go, go)}")
        new = "\n--- ".join(docs)
        if new != text:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
    return changed


def summarize(project: str) -> dict:
    counts: dict[str, int] = {}
    for dp, _, fns in os.walk(assets_dir(project)):
        for fn in fns:
            e = os.path.splitext(fn)[1].lower()
            if e != ".meta":
                counts[e] = counts.get(e, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])


def finish(project: str, code: str, bundles: list[str], missing: set[str], note: str) -> str:
    """Everything after the AssetRipper export (also what --refresh re-runs)."""
    mats, unmatched = install_real_shaders(project)
    note += (f"{mats} decompiled shader(s) installed"
             + (f"; no source for {len(unmatched)}: {unmatched[:6]}" if unmatched else ""))
    match_asset_names(project)
    activated = activate_roots(project)
    unmaterialed = disable_unmaterialed(project)
    set_linear_color_space(project)
    reference(project, project_name(code))
    note += "; " + unity_batch(project, prefab_scene_list(project, code))
    found = find_scenes(project)
    with open(os.path.join(project, "ag_stage.json"), "w", encoding="utf-8") as fh:
        json.dump({"code": code, "bundles": bundles, "scenes": found,
                   "activated_roots": activated,
                   "disabled_no_material": unmaterialed,
                   "unresolved": sorted(missing)}, fh, indent=1)
    with open(os.path.join(project, "OPEN ExportedProject IN UNITY.txt"), "w") as fh:
        fh.write("Open this folder in Unity Hub (Add > Add project from disk):\n"
                 f"  {os.path.join(project, 'ExportedProject')}\n"
                 "Opening the folder above it makes Unity create a new, empty project there.\n")
    print(f"     scenes: {found or 'NONE'}")
    return note


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="+", help="stage codes, e.g. x343 (or folder/code, or 'sourcespace')")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--refresh", action="store_true",
                    help="existing projects: re-install shaders + helpers, re-run fix-ups and the "
                         "manifest (after changing AG_shaders or agtools/unity), no re-export")
    args = ap.parse_args()

    if args.refresh:
        for code in args.codes:
            project = os.path.join(args.out, project_name(code))
            meta = json.load(open(os.path.join(project, "ag_stage.json"), encoding="utf-8"))
            copy_helpers(project)
            note = finish(project, code, meta["bundles"], set(meta.get("unresolved", [])), "")
            print(f"{code}: refreshed - {note}", flush=True)
        return 0

    index = bundle_deps.load_index()
    log = os.path.join(args.out, "_assetripper.log")
    with assetripper.Server(log) as ar:
        for code in args.codes:
            t0 = time.time()
            bundles, missing = stage_bundles(code, index)
            staged = os.path.join(STAGING, project_name(code))
            size = link_into(bundles, staged)
            print(f"{code}: {len(bundles)} bundles, {size / 1e6:.0f} MB"
                  + (f", {len(missing)} unresolved refs" if missing else ""), flush=True)
            project = os.path.join(args.out, project_name(code))
            shutil.rmtree(project, ignore_errors=True)
            ar.export(staged, project, ShaderExportMode="Dummy")
            shutil.rmtree(staged, ignore_errors=True)
            scripts = install_helpers(project, [os.path.join(bundle_deps.ROOT, b) for b in bundles])
            note = finish(project, code, bundles, missing, f"{scripts} game script(s) rebuilt; ")
            print(f"  -> {project}  ({time.time() - t0:.0f}s)")
            print(f"     {note}")
            print(f"     {summarize(project)}", flush=True)
    shutil.rmtree(STAGING, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
