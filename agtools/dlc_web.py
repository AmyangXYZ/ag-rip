#!/usr/bin/env python3
r"""A recorded DLC scene (AGDlcRecord) -> the shaders the WebGPU page draws it with.

    python agtools/dlc_web.py AG_dlc_play/107402-英招-效率至上
    python agtools/dlc_web.py AG_dlc_play/107402-英招-效率至上 --page   (page, index, serve.bat only)

Reads web/data/scene.json (materials: shader, enabled keywords; the global keywords the
pipeline had on) and, for every pass the game's pipeline draws (ForwardBase, Always -
the character outline - and ShadowCaster), translates the exact keyword variant it
compiles to with shader_wgsl.py. Writes:
  web/data/shaders/<variant>.vert.wgsl / .frag.wgsl / .json
  web/data/variants.json   material -> [{pass, lightMode, variant, state}] where state is the
                           pass's render state (Blend, ZWrite, ZTest, Cull, ColorMask) with its
                           [_Property] references resolved from the material
A keyword group of a multi_compile line with no "_" entry always has one keyword on: the
first when the material names none (Unity's rule), so OPAQUE for a Standard material
with no blend keyword.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
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


PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "agplay")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ffmpeg_exe() -> str | None:
    """ffmpeg from PATH, else the one the imageio-ffmpeg package ships."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def encode_unity_videos(data: str) -> dict[str, int]:
    """The full-rate Unity frames (AGDlcRecord unity_full/<seq>_<frame>.jpg, every frame)
    -> unity/<seq>.webm, which the page plays beside the WebGPU frames. VP9 4:4:4 (no chroma
    subsampling, so the difference view stays honest), a keyframe every 15 frames for quick
    seeks, frame n at n/30 s. The JPEGs are the recorder's intermediate and are removed once
    encoded; a sequence without them keeps its earlier video. Returns frames per sequence."""
    src = os.path.join(data, "unity_full")
    seqs = {}
    for fn in os.listdir(src) if os.path.isdir(src) else []:
        m = re.fullmatch(r"(.+)_(\d{4})\.jpg", fn)
        if m:
            seqs.setdefault(m.group(1), []).append(int(m.group(2)))
    exe = ffmpeg_exe() if seqs else None
    if seqs and not exe:
        print("  ! no ffmpeg (PATH or pip imageio-ffmpeg): unity_full/ left unencoded")
        return {}
    out = {}
    for seq, frames in sorted(seqs.items()):
        frames.sort()
        if frames != list(range(len(frames))):
            print(f"  ! {seq}: unity_full frames are not 0..{len(frames) - 1} without gaps; not encoded")
            continue
        video = os.path.join(data, "unity", f"{seq}.webm")
        os.makedirs(os.path.dirname(video), exist_ok=True)
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-framerate", "30", "-start_number", "0",
                            "-i", os.path.join(src, f"{seq}_%04d.jpg"),
                            "-c:v", "libvpx-vp9", "-pix_fmt", "yuv444p", "-crf", "34", "-b:v", "0", "-g", "15",
                            "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
                            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                            video], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ! {seq}: ffmpeg failed: {(r.stderr or r.stdout)[-500:]}")
            continue
        for f in frames:
            os.remove(os.path.join(src, f"{seq}_{f:04d}.jpg"))
        out[seq] = len(frames)
        print(f"  unity/{seq}.webm: {len(frames)} frames, {os.path.getsize(video) / 1e6:.0f} MB")
    if os.path.isdir(src) and not os.listdir(src):
        os.rmdir(src)
    return out


def write_index(project: str, data: str) -> None:
    """data/index.json: the recorded sequences in play order, the frames that have a
    Unity reference image (data/unity/<seq>_<frame>.png) and the full-rate Unity video
    (data/unity/<seq>.webm) when there is one."""
    meta = {}
    dlc = os.path.join(project, "dlc.json")
    if os.path.isfile(dlc):
        meta = json.load(open(dlc, encoding="utf-8"))
    def frames_in(folder):
        out = {}
        d = os.path.join(data, folder)
        for fn in os.listdir(d) if os.path.isdir(d) else []:
            m = re.fullmatch(r"(.+)_(\d{4})\.png", fn)
            if m:
                out.setdefault(m.group(1), []).append(int(m.group(2)))
        return out
    refs, raw = frames_in("unity"), frames_in("unity_raw")
    # the sequences' audio (voice + scene, extract_voice.py), played in step with the frames
    audio = {}
    skin_dir = os.path.join(ROOT, "AG_dlc_scene", str(meta.get("skin", "")))
    os.makedirs(os.path.join(data, "audio"), exist_ok=True)
    recorded = [fn[:-len(".frames.jsonl")] for fn in os.listdir(data) if fn.endswith(".frames.jsonl")]
    order = [s for s in meta.get("play", []) if s in recorded] + sorted(s for s in recorded if s not in meta.get("play", []))
    for s in order:
        wav = os.path.join(skin_dir, s, "audio.wav")
        if os.path.isfile(wav):
            shutil.copyfile(wav, os.path.join(data, "audio", f"{s}.wav"))
            audio[s] = f"audio/{s}.wav"
    json.dump({"skin": meta.get("skin", ""), "title": " ".join(x for x in (meta.get("skin"), meta.get("character"), meta.get("skin_name")) if x),
               "stage": meta.get("stage", ""),
               "sequences": [{"name": s, "unityFrames": sorted(refs.get(s, [])), "rawFrames": sorted(raw.get(s, [])),
                              "unityVideo": f"unity/{s}.webm" if os.path.isfile(os.path.join(data, "unity", f"{s}.webm")) else None,
                              "audio": audio.get(s)} for s in order]},
              open(os.path.join(data, "index.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)


README = """# {title}

Two ways to watch {skin}'s sequences ({seqs}) in stage {stage}, to check against the game.

## Unity (the reference)

1. Unity Hub > Add > Add project from disk: `unity/ExportedProject` (Unity 6000.6.1f1).
2. Open the scene `Assets/AGScenes/DLC_{skin}.unity` and press Play.

The sequences play back to back and loop, with their audio, bound the way the game
binds them. The game's shaders, pipeline, character rendering and timelines are ported
from its decompiled code (agtools/dlc_play.py).

## WebGPU (the port)

Double-click `serve.bat` (or run `python serve.py` in `web/`) and open
http://localhost:8765 in Chrome. The page draws the recorded frames with the game's own
shaders translated to WebGPU, beside Unity's frames of the same moments:

- **View**: side by side, swipe (move the mouse), blink (space), difference heatmap.
- **Unity with / without post**, and **WebGPU post** on or off, to compare shading alone.
- **Play** runs on the audio clock, Unity's side from its full-rate video (every frame).
  Paused, **exact reference frames only** shows Unity's lossless images (every 15th frame);
  unticked, any frame from the video. The arrow keys step between frames.
- The score is the mean colour difference against the Unity frame (0-255).
- The cards below list the uniforms the port does not supply (with the ones the game
  leaves at their defaults here, and why) and any GPU errors.
"""

SERVE = """@echo off
rem the WebGPU compare page for this DLC: http://localhost:8765
cd /d "%~dp0web"
start "" http://localhost:8765
rem serve.py, not `python -m http.server`: the videos need byte ranges to seek
python serve.py 8765
"""


def install_page(project: str) -> None:
    """The compare page (agtools/web/agplay) next to its data: web/index.html, web/js/*,
    plus serve.bat and README.md in the DLC folder."""
    meta = {}
    if os.path.isfile(os.path.join(project, "dlc.json")):
        meta = json.load(open(os.path.join(project, "dlc.json"), encoding="utf-8"))
    title = " ".join(x for x in (meta.get("skin"), meta.get("character"), meta.get("skin_name")) if x) or os.path.basename(project)
    with open(os.path.join(project, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README.format(title=title, skin=meta.get("skin", ""), stage=meta.get("stage", ""),
                               seqs=", ".join(meta.get("play", [])) or "all"))
    with open(os.path.join(project, "serve.bat"), "w", encoding="utf-8") as fh:
        fh.write(SERVE)
    dst = os.path.join(project, "web")
    for dp, _, fns in os.walk(PAGE):
        for fn in fns:
            src = os.path.join(dp, fn)
            out = os.path.join(dst, os.path.relpath(src, PAGE))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(src, "rb") as a, open(out, "wb") as b:
                b.write(a.read())


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project = os.path.abspath(args[0])
    data = os.path.join(project, "web", "data")
    if "--page" in sys.argv:  # the page, index and serve.bat alone; shaders as they are
        encode_unity_videos(data)
        write_index(project, data)
        install_page(project)
        return 0
    scene = json.load(open(os.path.join(data, "scene.json"), encoding="utf-8"))
    files = shader_files(project)
    glob = set(scene.get("globalKeywords", []))
    out_dir = os.path.join(data, "shaders")
    os.makedirs(out_dir, exist_ok=True)
    infos, done, variants, problems = {}, {}, {}, []
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
            if key not in done:
                name = re.sub(r"\W+", "_", mat["shader"]) + f"_p{i}_" + hashlib.md5(repr(key).encode()).hexdigest()[:8]
                try:
                    shader_wgsl.translate(path, want, i, os.path.join(out_dir, name), DXC)
                    done[key] = name
                except SystemExit as e:
                    done[key] = None
                    problems.append(f"{mat['shader']} pass {i} {sorted(want)}: {str(e)[:300]}")
            if done[key]:
                passes.append({"pass": i, "lightMode": p["lightMode"], "variant": done[key],
                               "state": resolve(p["state"], mat["props"])})
        variants[mat["id"]] = passes
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
    if post:
        out = {}
        for key, shader_name, passes, kws in (("final", post.get("finalShader"), [0], set(post.get("finalKeywords", []))),
                                              ("bloom", post.get("bloomShader"), [0, 1, 2, 3], set())):
            path = files.get(shader_name or "")
            if not path:
                problems.append(f"post {key}: no source for {shader_name}")
                continue
            if path not in infos:
                infos[path] = pass_info(path)
            names = []
            for i in passes:
                want = want_keywords(infos[path][i]["groups"], kws | glob)
                name = re.sub(r"\W+", "_", shader_name) + f"_p{i}_" + hashlib.md5(repr((path, i, sorted(want), "texture")).encode()).hexdigest()[:8]
                try:
                    shader_wgsl.translate(path, want, i, os.path.join(out_dir, name), DXC, texture_space=True)
                    names.append(name)
                except SystemExit as e:
                    problems.append(f"post {key} pass {i}: {str(e)[:300]}")
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
    encode_unity_videos(data)
    write_index(project, data)
    install_page(project)
    for p in problems:
        print("  !", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
