#!/usr/bin/env python3
r"""A recorded DLC scene (AGDlcRecord) -> the game's shaders in WGSL, for the reze export.

    python agtools/dlc_web.py AG_dlc_play/107402-英招-效率至上

Reads web/data/scene.json (materials: shader, enabled keywords; the global keywords the
pipeline had on) and, for every pass the game's pipeline draws (ForwardBase, Always -
the character outline - and ShadowCaster), translates the exact keyword variant it
compiles to with shader_wgsl.py. Writes:
  web/data/shaders/<variant>.vert.wgsl / .frag.wgsl / .json
  web/data/variants.json   material -> [{pass, lightMode, variant, state}] where state is the
                           pass's render state (Blend, ZWrite, ZTest, Cull, ColorMask) with its
                           [_Property] references resolved from the material
  web/data/index.json      the recorded sequences in play order
  README.md                the Unity project and how the reze zips are made
A keyword group of a multi_compile line with no "_" entry always has one keyword on: the
first when the material names none (Unity's rule), so OPAQUE for a Standard material
with no blend keyword.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shader_wgsl  # noqa: E402

# the built-in forward passes, the shadow casters, and the character's after-opaque passes
# (AGSimCharacter: hair/face shadow, eye overrides)
DRAWN = {"FORWARDBASE", "ALWAYS", "SHADOWCASTER", "CHARHAIRSHADOW", "CHARFACESHADOW", "CHARHAIRTRANSE",
         "OVERRIDE1", "OVERRIDE2", "OVERRIDE3"}
DXC = os.environ.get("DXC") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                            "tools", "dxc", "bin", "x64", "dxc.exe")


def shader_files(project: str) -> dict[str, str]:
    """Shader name -> file, for every .shader in the Unity project."""
    out = {}
    for dp, _, fns in os.walk(os.path.join(project, "unity", "ExportedProject", "Assets")):
        for fn in fns:
            if fn.endswith(".shader"):
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(600)
                m = re.search(r'Shader\s+"([^"]+)"', head)
                if m:
                    out.setdefault(m.group(1), path)
    return out


def pass_info(path: str) -> list[dict]:
    """Each pass: LightMode, its keyword groups, its render-state lines (header only)."""
    lines = open(path, encoding="utf-8", errors="replace").read().split("\n")
    out = []
    for lo, hi in shader_wgsl.passes(lines):
        head = []
        for l in lines[lo:hi]:
            if re.match(r"^\s*#if|^\s*CGPROGRAM|^\s*HLSLPROGRAM", l) and head and any("#pragma" in h for h in head):
                break
            head.append(l)
            if len(head) > 400:
                break
        text = "\n".join(head)
        lm = re.search(r'"LIGHTMODE"\s*=\s*"([^"]+)"', text, re.I)
        groups = [g.split() for g in re.findall(r"#pragma\s+(?:multi_compile|shader_feature)(?:_local)?(?:_fragment|_vertex)?\s+([^\n]+)", text)]
        state = {}
        for key in ("Blend", "ZWrite", "ZTest", "Cull", "ColorMask", "BlendOp", "Offset"):
            m = re.search(r"^\s*" + key + r"\s+([^\n]+)$", text, re.M)
            if m:
                state[key] = m.group(1).strip()
        st = re.search(r"Stencil\s*\{([^}]*)\}", text)
        if st:
            for k, v in re.findall(r"^\s*(\w+)\s+([^\n]+)$", st.group(1), re.M):
                state["Stencil" + k] = v.strip()
        out.append({"lightMode": (lm.group(1) if lm else "").upper(), "groups": groups, "state": state})
    return out


def want_keywords(groups: list[list[str]], enabled: set[str]) -> set[str]:
    want = set()
    for g in groups:
        on = [k for k in g if k != "_" and k in enabled]
        if on:
            want.add(on[0])
        elif "_" not in g and "__" not in g and g:
            want.add(g[0])
    return want


def resolve(state: dict, props: dict) -> dict:
    def val(tok: str):
        m = re.fullmatch(r"\[(\w+)\]", tok)
        if not m:
            return tok
        v = props.get(m.group(1))
        return v if not isinstance(v, list) else v[0]
    return {k: [val(t) for t in re.findall(r"\[\w+\]|[^\s,]+", v)] for k, v in state.items()}


def write_index(project: str, data: str) -> None:
    """data/index.json: the recorded sequences in play order (dlc_reze_scene.py and
    stage_native.py read it)."""
    meta = {}
    dlc = os.path.join(project, "dlc.json")
    if os.path.isfile(dlc):
        meta = json.load(open(dlc, encoding="utf-8"))
    recorded = [fn[:-len(".frames.jsonl")] for fn in os.listdir(data) if fn.endswith(".frames.jsonl")]
    order = [s for s in meta.get("play", []) if s in recorded] + sorted(s for s in recorded if s not in meta.get("play", []))
    json.dump({"skin": meta.get("skin", ""), "title": " ".join(x for x in (meta.get("skin"), meta.get("character"), meta.get("skin_name")) if x),
               "stage": meta.get("stage", ""),
               "sequences": [{"name": s} for s in order]},
              open(os.path.join(data, "index.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)


README = """# {title}

{skin}'s sequences ({seqs}) in stage {stage}.

## Unity

1. Unity Hub > Add > Add project from disk: `unity/ExportedProject` (Unity 6000.6.1f1).
2. Open the scene `Assets/AGScenes/DLC_{skin}.unity` and press Play.

The sequences play back to back and loop, with their audio, bound the way the game
binds them. The game's shaders, pipeline, character rendering and timelines are ported
from its decompiled code (agtools/dlc_play.py).

## reze

`python ag.py reze AG_dlc_play/{folder}` writes one reze-design scene zip per sequence
to `reze/` (Import scene). It reads `web/data/`: the recording (AGDlcRecord) and the
game's shaders translated to WGSL (agtools/dlc_web.py).
"""


def write_readme(project: str) -> None:
    """README.md in the DLC folder: the Unity project and how the reze zips are made."""
    meta = {}
    if os.path.isfile(os.path.join(project, "dlc.json")):
        meta = json.load(open(os.path.join(project, "dlc.json"), encoding="utf-8"))
    title = " ".join(x for x in (meta.get("skin"), meta.get("character"), meta.get("skin_name")) if x) or os.path.basename(project)
    with open(os.path.join(project, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README.format(title=title, skin=meta.get("skin", ""), stage=meta.get("stage", ""),
                               seqs=", ".join(meta.get("play", [])) or "all", folder=os.path.basename(project)))


def _translate_job(job: tuple, out_dir: str, dxc: str):
    path, want, i, name, texture_space, _ = job
    try:
        shader_wgsl.translate(path, want, i, os.path.join(out_dir, name), dxc, texture_space=texture_space)
        return name
    except SystemExit as e:
        return e


def translate_all(jobs: dict, out_dir: str) -> dict:
    """key -> variant name, or the SystemExit its translation stopped with. The variants
    are independent: a process per core (AG_WGSL_JOBS=1 runs them in this process)."""
    workers = int(os.environ.get("AG_WGSL_JOBS") or os.cpu_count() or 1)
    if workers <= 1 or len(jobs) <= 1:
        return {k: _translate_job(j, out_dir, DXC) for k, j in jobs.items()}
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futs = {k: pool.submit(_translate_job, j, out_dir, DXC) for k, j in jobs.items()}
        return {k: f.result() for k, f in futs.items()}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project = os.path.abspath(args[0])
    data = os.path.join(project, "web", "data")
    scene = json.load(open(os.path.join(data, "scene.json"), encoding="utf-8"))
    files = shader_files(project)
    glob = set(scene.get("globalKeywords", []))
    out_dir = os.path.join(data, "shaders")
    os.makedirs(out_dir, exist_ok=True)
    infos, done, variants, problems = {}, {}, {}, []
    # every distinct variant is listed first and translated once, in parallel (each runs
    # dxc and naga in a temp folder of its own); the results are put together in the
    # order of the materials, as one by one
    jobs: dict = {}            # key -> (shader path, keywords, pass, out, texture_space, label)
    mat_passes = []
    for mat in scene["materials"]:
        path = files.get(mat["shader"])
        if not path:
            problems.append(f"{mat['name']}: no source for {mat['shader']}")
            continue
        if path not in infos:
            infos[path] = pass_info(path)
        enabled = set(mat["keywords"]) | glob
        passes = []
        for i, p in enumerate(infos[path]):
            if p["lightMode"] not in DRAWN:
                continue
            if i < len(mat.get("passes", [])) and not mat["passes"][i].get("enabled", 1):
                continue
            want = want_keywords(p["groups"], enabled)
            key = (path, i, tuple(sorted(want)))
            if key not in jobs:
                name = re.sub(r"\W+", "_", mat["shader"]) + f"_p{i}_" + hashlib.md5(repr(key).encode()).hexdigest()[:8]
                jobs[key] = (path, want, i, name, False, f"{mat['shader']} pass {i} {sorted(want)}")
            passes.append((key, i, p))
        mat_passes.append((mat, passes))
    # the post chain (AGSimPostFX): Final pass 0 with the keywords it had, Bloom passes 0-3
    post = None
    for fn in sorted(os.listdir(data)):
        if fn.endswith(".frames.jsonl"):
            # the post values are read before the image effects run: from frame 1 or 2 on
            with open(os.path.join(data, fn), encoding="utf-8") as fh:
                for line in fh:
                    post = json.loads(line).get("post")
                    if post and post.get("lut"):
                        break
            break
    post_keys = {}
    if post:
        for key, shader_name, passes, kws in (("final", post.get("finalShader"), [0], set(post.get("finalKeywords", []))),
                                              ("bloom", post.get("bloomShader"), [0, 1, 2, 3], set())):
            path = files.get(shader_name or "")
            if not path:
                post_keys[key] = f"post {key}: no source for {shader_name}"
                continue
            if path not in infos:
                infos[path] = pass_info(path)
            keys = []
            for i in passes:
                want = want_keywords(infos[path][i]["groups"], kws | glob)
                name = re.sub(r"\W+", "_", shader_name) + f"_p{i}_" + hashlib.md5(repr((path, i, sorted(want), "texture")).encode()).hexdigest()[:8]
                jk = ("post", path, i, tuple(sorted(want)))
                jobs.setdefault(jk, (path, want, i, name, True, f"post {key} pass {i}"))
                keys.append(jk)
            post_keys[key] = keys
    results = translate_all(jobs, out_dir)
    for key, name in results.items():
        if isinstance(name, str):
            done[key] = name
        else:
            done[key] = None
    for mat, passes in mat_passes:
        out = []
        for key, i, p in passes:
            if key in results and not isinstance(results[key], str):
                problems.append(f"{jobs[key][5]}: {str(results.pop(key))[:300]}")
            if done[key]:
                out.append({"pass": i, "lightMode": p["lightMode"], "variant": done[key],
                            "state": resolve(p["state"], mat["props"])})
        variants[mat["id"]] = out
    if post:
        out = {}
        for key in ("final", "bloom"):
            ks = post_keys.get(key)
            if isinstance(ks, str):
                problems.append(ks)
                continue
            names = []
            for jk in ks:
                r = results.get(jk)
                if isinstance(r, str):
                    names.append(r)
                else:
                    problems.append(f"{jobs[jk][5]}: {str(r)[:300]}")
                    names.append(None)
            out[key] = names
        variants["_post"] = out
    # an empty texture slot reads the default its shader's Properties block names
    # ("white", "black", "gray", "bump", "red"), as Unity binds it
    defaults = {}
    for mat in scene["materials"]:
        path = files.get(mat["shader"])
        if path and mat["shader"] not in defaults:
            with open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(20000)
            props = head[head.find("Properties"):head.find("SubShader")] if "Properties" in head else ""
            defaults[mat["shader"]] = {n: d for n, d in re.findall(
                r'^\s*(?:\[[^\]]*\]\s*)*(\w+)\s*\("[^"]*",\s*(?:2D|Cube|3D|2DArray|Any)\)\s*=\s*"(\w*)"', props, re.M)}
    variants["_textureDefaults"] = defaults
    json.dump(variants, open(os.path.join(data, "variants.json"), "w", encoding="utf-8"), indent=1)
    print(f"{sum(1 for v in done.values() if v)} variants translated for {len(variants)} materials -> {out_dir}")
    write_index(project, data)
    write_readme(project)
    for p in problems:
        print("  !", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
