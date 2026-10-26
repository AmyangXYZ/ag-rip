#!/usr/bin/env python3
r"""Character-page reference renders + per-skin look data, from a DLC skin's Unity project.

    python ag.py charpage 109503 [104903 ...]   -> AG_charpage/<skin>/ref_*.png, look/

Uses the project `python ag.py dlc <skin>` built (AG_dlc_play/<skin>/unity). The game's
character page (scene X100, `hero`) lights the character with character lighting only:
no main light, no fog, no scene shadows. Its SceneSetting and CharacterSceneEnvironment
values are read from AG_stages/x100 (python ag.py stage comeffect/x100) and set on the
project's own components in a fresh scene, so the pipeline stand-in reads them as it
reads any scene. The model is the skin's display model (SkinCfg.display_model_id,
<skin>ui_display) in the first frame of its controller's default state (the page's
stand). AGCharPage.cs (agtools/unity/charpage) does the rendering and the export.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dlc_play  # noqa: E402
import stage_unity  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "AG_charpage")
X100 = os.path.join(ROOT, "AG_stages", "x100", "ExportedProject")
X100_SCENE = os.path.join(X100, "Assets", "ComEffect", "ABResources", "Levels", "X100.unity")
CHARPAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unity", "charpage")
BACKGROUND = [0.5, 0.5, 0.5]    # sRGB mid grey


def _docs(text: str) -> list[str]:
    return text.split("\n--- ")


def _value(raw: str):
    """A YAML scalar / inline mapping of a scene field -> (kind, floats, string)."""
    raw = raw.strip()
    m = re.match(r"\{fileID: (-?\d+)(?:, guid: (\w+), type: \d+)?\}", raw)
    if m:
        return "asset", [], m.group(2) or ""
    m = re.match(r"\{(.*)\}$", raw)
    if m:
        vals = re.findall(r"(\w+): (-?[\d.eE+-]+)", m.group(1))
        if not vals:
            return None
        return "vector", [float(v) for _, v in vals], ""
    try:
        return "number", [float(raw)], ""
    except ValueError:
        return None


def _fields(doc: str, comp: str) -> list[dict]:
    out = []
    for line in doc.split("\n"):
        m = re.match(r"^  (\w+): (.+)$", line)
        if not m or m.group(1).startswith("m_"):
            continue
        v = _value(m.group(2))
        if v:
            out.append({"comp": comp, "field": m.group(1), "kind": v[0], "v": v[1], "s": v[2]})
    return out


def _guid_path(root: str, guid: str) -> str | None:
    for dp, _, fns in os.walk(os.path.join(root, "Assets")):
        for fn in fns:
            if fn.endswith(".meta"):
                with open(os.path.join(dp, fn), encoding="utf-8", errors="replace") as fh:
                    if f"guid: {guid}" in fh.read(300):
                        return os.path.join(dp, fn[:-5])
    return None


def x100_fields(proj: str) -> list[dict]:
    """SceneSetting + CharacterSceneEnvironment + RenderSettings ambient of X100; the
    assets they reference copied into the project (same path and guid) when missing."""
    if not os.path.isfile(X100_SCENE):
        raise SystemExit(f"error: no {X100_SCENE} (python ag.py stage comeffect/x100)")
    text = open(X100_SCENE, encoding="utf-8", errors="replace").read()
    fields = []
    for doc in _docs(text):
        if doc.startswith("!u!114 ") and "\n  _probeLightingBase:" in doc:
            fields += _fields(doc, "SceneSetting")
        elif doc.startswith("!u!114 ") and "\n  equatorColor:" in doc and "\n  reflectionMap:" in doc:
            fields += _fields(doc, "CharacterSceneEnvironment")
        elif doc.startswith("!u!104 "):
            fields += [f for f in _fields_rs(doc)]
    for f in fields:
        if f["kind"] != "asset":
            continue
        if not f["s"]:
            f["s"] = ""
            continue
        src = _guid_path(X100, f["s"])
        if not src:
            print(f"   {f['comp']}.{f['field']}: guid {f['s']} not in the x100 project")
            f["s"] = ""
            continue
        rel = os.path.relpath(src, X100)
        dst = os.path.join(proj, rel)
        if not os.path.isfile(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            shutil.copyfile(src + ".meta", dst + ".meta")
            print(f"   copied {rel}")
        f["s"] = rel.replace("\\", "/")
    return fields


def _fields_rs(doc: str) -> list[dict]:
    keep = ("m_AmbientSkyColor", "m_AmbientEquatorColor", "m_AmbientGroundColor", "m_SubtractiveShadowColor", "m_AmbientIntensity")
    out = []
    for line in doc.split("\n"):
        m = re.match(r"^  (\w+): (.+)$", line)
        if m and m.group(1) in keep:
            v = _value(m.group(2))
            if v:
                out.append({"comp": "RenderSettings", "field": m.group(1), "kind": v[0], "v": v[1], "s": ""})
    return out


def install(proj: str) -> None:
    """The current pipeline stand-in, character ports and the AGCharPage editor script."""
    stage_unity.copy_helpers(os.path.dirname(proj))
    stage_unity.install_real_shaders(os.path.dirname(proj))   # current shader fix-ups
    dlc_play.install_ports(proj)
    dlc_play.pipeline_globals(proj)
    fixed = stage_unity.fix_hex_arrays(os.path.dirname(proj))
    if fixed:
        print(f"   {fixed} asset(s): hex arrays repaired")


def run(skin: str, out_root: str, bind_pose: bool = False, model_id: str = "") -> str:
    info = dlc_play.skin_info(skin)
    project = os.path.join(dlc_play.OUT, info["folder"], "unity")
    proj = os.path.join(project, "ExportedProject")
    if not os.path.isdir(proj):
        raise SystemExit(f"error: no project {proj} (python ag.py dlc {skin})")
    model = dlc_play._rows("skincfg", "hero")
    model = model_id or next(r for r in model if r["id"] == int(skin)).get("display_model_id") or f"{skin}ui_display"
    prefab = None
    for want in (model, f"{skin}ui_custom"):
        for dp, _, fns in os.walk(os.path.join(proj, "Assets")):
            if f"{want}.prefab" in fns and "ABResources" in dp:
                prefab = os.path.join(dp, f"{want}.prefab")
                break
        if prefab:
            break
    if not prefab:
        raise SystemExit(f"error: no {model}.prefab in {proj}")
    install(proj)
    out = os.path.join(out_root, skin)
    os.makedirs(out, exist_ok=True)
    spec = {"skin": skin, "prefab": os.path.relpath(prefab, proj).replace("\\", "/"), "outDir": out,
            "background": BACKGROUND, "bindPose": bind_pose, "width": 1920, "height": 1080, "fields": x100_fields(proj)}
    with open(os.path.join(proj, "ag_charpage.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=1)
    log = os.path.join(project, "_unity_charpage.log")
    rc = subprocess.run([stage_unity.UNITY, "-batchmode", "-nosound", "-quit", "-projectPath", proj,
                         "-executeMethod", "AGCharPage.Batch", "-logFile", log], check=False).returncode
    refs = sorted(f for f in os.listdir(out) if f.startswith("ref_"))
    return f"{spec['prefab']}: unity {rc}, {len(refs)} reference file(s) in {out}" + dlc_play.compile_errors(log)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skins", nargs="+")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--bind-pose", action="store_true", help="diagnosis: no stand clip")
    ap.add_argument("--model", default="", help="diagnosis: another model prefab id (109503ui_custom)")
    args = ap.parse_args()
    for skin in args.skins:
        print(f"{skin}: {run(skin, args.out, args.bind_pose, args.model)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
