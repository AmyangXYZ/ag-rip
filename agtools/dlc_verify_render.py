#!/usr/bin/env python3
r"""DLC fidelity check V3, render pairs (docs/dlc-fidelity-plan.md).

    python agtools/dlc_verify_render.py                          every AG_dlc_play/<dlc>, every take with a zip
    python agtools/dlc_verify_render.py AG_dlc_play/102201-塞勒涅-海滩漫步 --take touch1
    python agtools/dlc_verify_render.py <dlc> --frames 120,360   reference frame numbers instead of the pick
    python agtools/dlc_verify_render.py <dlc> --no-isolate       pairs only, no per-effect isolation
    python agtools/dlc_verify_render.py <dlc> --capture          Unity reference frames first (see below)
    python agtools/dlc_verify_render.py <dlc> --zip-dir <dir>    the takes' zips from <dir> (default <dlc>/reze)

For each take and each picked frame it writes, to AG_dlc_play/<dlc>/verify/:
    <take>_<NNNN>_game.png   the Unity reference (AGDlcRecord -agEvery: web/data/unity/<take>_<NNNN>.png)
    <take>_<NNNN>_reze.png   reze-design's render of the same clip frame, same camera, 1600x900
    <take>_<NNNN>_diff.png   |reze - game| as a heat map, the cast's footprint (the user's model,
                             not the game character) dimmed
    <take>_<NNNN>_iso_<key>.png   for an effect or prop the isolation flags: game | reze |
                             reze without it | its footprint
    summary.json, summary.md per-frame difference metrics and the per-effect attribution

THE FRAME CLOCK. Reference <take>_<NNNN>.png is drawn for the recording's line NNNN, and
line k is clip frame k + 1 (dlc_reze_scene.load_frames: the director has stepped once when
the first line is written). The reze render of it is clip frame NNNN + 1, the frame the
take's motion, camera VMD, effect windows and prop keys are on.

THE PICK. Every 60th reference frame, plus each effect's and each prop's "peak": the
reference frame nearest to 15 frames into each of its windows (or its middle, for a short
one), as far as reference frames exist (the recorder writes one every 15).

THE REZE SIDE is agtools/dlc_verify_render.mjs: the user's dev server (http://localhost:3000,
never restarted here) in a browser of its own, the take's zip imported through Import scene
onto the default scene's cast, played offline from frame 0 at 1/30 s as the video export does.

ISOLATION. At each frame every effect in its window and every drawn prop (and the cast) is
switched off for one more render. Its footprint is where that changes the picture by more
than FOOT/255. Within the footprint the error against the game with it drawn (err_on) and
without it (err_off) says which way it moves the frame: err_off < err_on means the scene is
closer to the game WITHOUT it there - drawn too strong, too big, in the wrong place, or not
drawn by the game at all - and it is flagged. Footprints under MIN_AREA are ignored.

--capture runs the Unity reference project (unity/ExportedProject) with AGDlcRecord into
verify/_ref (a scratch recording, never web/data) with -agEvery 15, and uses its frames.
It refuses to start while a Unity has that project open.

Puppeteer: puppeteer-core is installed once into tools/verify-node (ignored by git).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import zipfile

import math
import struct

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
PLAY = os.path.join(HERE, "AG_dlc_play")
NODE_DIR = os.path.join(HERE, "tools", "verify-node")
RENDER_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dlc_verify_render.mjs")
W, H = 1600, 900
IW, IH = 400, 225
EVERY = 60
FOOT = 6           # a pixel is in an effect's footprint when switching it off moves it this much (0..255)
MIN_AREA = 0.002   # of the frame
FLAG = 0.85        # err_off < FLAG * err_on: flagged


def ensure_puppeteer():
    if os.path.exists(os.path.join(NODE_DIR, "node_modules", "puppeteer-core")):
        return NODE_DIR
    os.makedirs(NODE_DIR, exist_ok=True)
    if not os.path.exists(os.path.join(NODE_DIR, "package.json")):
        json.dump({"name": "ag-verify-node", "private": True}, open(os.path.join(NODE_DIR, "package.json"), "w"))
    npm = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm, "install", "--no-audit", "--no-fund", "puppeteer-core@23"], cwd=NODE_DIR, check=True)
    return NODE_DIR


def dlc_dirs(args):
    if args:
        return [os.path.normpath(a) for a in args]
    return sorted(d for d in glob.glob(os.path.join(PLAY, "*")) if os.path.isdir(os.path.join(d, "web", "data")))


def unity_running(proj):
    """Is a Unity open on this project (another agent's recording)? Others may run."""
    q = "Get-CimInstance Win32_Process -Filter \"name='Unity.exe'\" | ForEach-Object { $_.CommandLine }"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", q], capture_output=True, text=True, errors="replace").stdout
    norm = lambda x: os.path.normcase(os.path.abspath(x)).replace("\\", "/")
    return any(norm(proj) in line.replace("\\", "/").lower() or os.path.basename(os.path.dirname(os.path.dirname(proj))) in line
               for line in out.splitlines() if line.strip())


def capture_refs(dlc):
    """Unity reference frames into verify/_ref (a scratch recording)."""
    import stage_unity
    # absolute: Unity runs in the project folder, and a relative -agRecord lands inside it
    dlc = os.path.abspath(dlc)
    proj = os.path.join(dlc, "unity", "ExportedProject")
    if unity_running(proj):
        raise SystemExit(f"a Unity has {proj} open (another recording?) - not starting a second one")
    ref = os.path.join(dlc, "verify", "_ref")
    os.makedirs(ref, exist_ok=True)
    log = os.path.join(dlc, "verify", "_unity_ref.log")
    cmd = [stage_unity.UNITY, "-batchmode", "-nosound", "-projectPath", proj, "-logFile", log,
           "-executeMethod", "AGDlcScene.Capture", "-agRecord", ref, "-agEvery", "15"]
    print("  unity reference:", " ".join(cmd[1:]))
    # silent: the project's audio is disabled for the run (AudioManager m_DisableAudio) and put
    # back after; the voice track plays into nothing and nothing it records changes
    am = os.path.join(proj, "ProjectSettings", "AudioManager.asset")
    saved = open(am, "rb").read() if os.path.exists(am) else None
    if saved is not None:
        open(am, "wb").write(re.sub(rb"m_DisableAudio: \d", b"m_DisableAudio: 1", saved))
    try:
        rc = subprocess.run(cmd, check=False).returncode
    finally:
        if saved is not None:
            open(am, "wb").write(saved)
    print("  unity exit", rc)
    return os.path.join(ref, "unity")


def ref_frames(ref_dir, take):
    out = {}
    for p in glob.glob(os.path.join(ref_dir, f"{take}_*.png")):
        m = re.match(rf"{re.escape(take)}_(\d+)\.png$", os.path.basename(p))
        if m:
            out[int(m.group(1))] = p
    return out


def windows_of(zip_path):
    """Every effect's and prop's windows (clip frames), by name, from the zip's scene.json."""
    z = zipfile.ZipFile(zip_path)
    s = json.loads(z.read("scene.json"))
    out = []
    for e in s.get("settings", {}).get("background", {}).get("effects", []):
        src = e.get("source") or {}
        name = src.get("name") if isinstance(src, dict) else None
        for w in e.get("window") or []:
            out.append((name or "?", w["start"], w["end"]))
    for m in s.get("assets", {}).get("models", []):
        for w in m.get("visibility") or []:
            if "start" in w and "end" in w:
                out.append((os.path.basename(m["model"]), w["start"], w["end"]))
    return out


def pick(refs, wins, every=EVERY):
    have = sorted(refs)
    if not have:
        return []
    chosen = {n for n in have if n % every == 0}
    for _, s, e in wins:
        target = s + min(15, max(0, (e - s) // 2)) - 1      # clip frame -> reference number
        near = min(have, key=lambda n: abs(n - target))
        if s - 1 <= near <= e - 1:
            chosen.add(near)
    return sorted(chosen)


def _from_euler(rx, ry, rz):
    """reze-engine math.ts Quat.fromEuler, as a rotation matrix (columns: right, up, forward)."""
    cx, sx, cy, sy, cz, sz = (math.cos(rx / 2), math.sin(rx / 2), math.cos(ry / 2), math.sin(ry / 2),
                              math.cos(rz / 2), math.sin(rz / 2))
    w, x = cy * cx * cz + sy * sx * sz, cy * sx * cz + sy * cx * sz
    y, z = sy * cx * cz - cy * sx * sz, cy * cx * sz - sy * sx * cz
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def recorded_poses(rec_dir, take, last):
    """The game camera of each clip frame 0..last as a reze CameraPose (engine units: PMX =
    (-x, y, -z) * 8 of the game's), from the recording's per-line view matrix and fov.

    The engine builds a VMD-driven view from rotation columns right, up, forward with
    Quat.fromEuler(-rotation) (camera.ts vmdEye), eye = target + forward * distance: so the
    pose is target = the eye, distance 0, and the euler that rebuilds the game's basis
    (R = Ry Rx Rz, the order fromEuler composes in)."""
    from dlc_reze_scene import clip_lead, SCALE
    lines = [json.loads(l) for l in open(os.path.join(rec_dir, f"{take}.frames.jsonl"), encoding="utf-8") if l.strip()]
    lead = clip_lead(lines, take)
    flip = np.array([-1.0, 1.0, -1.0])
    poses = {}
    for f in range(last + 1):
        c = lines[min(max(f - lead, 0), len(lines) - 1)]["cam"]
        v = np.array(c["view"], dtype=np.float64).reshape(4, 4)     # row vectors: p @ view
        R = np.stack([v[:3, 0] * flip, v[:3, 1] * flip, -v[:3, 2] * flip], 1)
        b = math.asin(max(-1.0, min(1.0, -R[1, 2])))
        a = math.atan2(R[0, 2], R[2, 2])
        c3 = math.atan2(R[1, 0], R[1, 1])
        assert np.abs(_from_euler(b, a, c3) - R).max() < 1e-4, "camera basis not representable"
        eye = np.array(c["pos"], dtype=np.float64) * flip * SCALE
        poses[f] = {"target": {"x": eye[0], "y": eye[1], "z": eye[2]},
                    "rotation": {"x": -b, "y": -a, "z": -c3}, "distance": 0.0, "fov": math.radians(c["fov"]),
                    "fovDeg": c["fov"], "eye": eye.tolist()}
    return poses


def vmd_camera(zip_path):
    """The zip's camera.vmd keys: frame -> (fov degrees, eye in engine units), eye from the
    engine's own construction (no bezier: linear between keys is enough for a check)."""
    b = zipfile.ZipFile(zip_path).read("camera.vmd")
    o = 50
    n = struct.unpack_from("<I", b, o)[0]; o += 4 + n * 111
    n = struct.unpack_from("<I", b, o)[0]; o += 4 + n * 23
    n = struct.unpack_from("<I", b, o)[0]; o += 4
    keys = []
    for _ in range(n):
        f, d, tx, ty, tz, rx, ry, rz = struct.unpack_from("<If3f3f", b, o)
        fov = struct.unpack_from("<I", b, o + 56)[0]
        o += 61
        R = _from_euler(-rx, -ry, -rz)
        keys.append((f, fov, (np.array([tx, ty, tz]) + R[:, 2] * d)))
    keys.sort(key=lambda k: k[0])
    return keys


def vmd_at(keys, f):
    if not keys:
        return None
    prev = keys[0]
    for k in keys:
        if k[0] > f:
            if k[0] - prev[0] <= 1 or prev[0] >= f:
                return prev[1], prev[2]
            t = (f - prev[0]) / (k[0] - prev[0])
            return prev[1] + (k[1] - prev[1]) * t, prev[2] + (k[2] - prev[2]) * t
        prev = k
    return prev[1], prev[2]


def load(p, size=None):
    im = Image.open(p).convert("RGB")
    if size and im.size != size:
        im = im.resize(size, Image.BILINEAR)
    return np.asarray(im).astype(np.float32)


def heat(d, dim=None):
    """|difference| (0..255, HxW) as a heat map: black - red - yellow - white."""
    t = np.clip(d / 96.0, 0, 1)
    rgb = np.stack([np.clip(t * 3, 0, 1), np.clip(t * 3 - 1, 0, 1), np.clip(t * 3 - 2, 0, 1)], -1)
    if dim is not None:
        rgb = np.where(dim[..., None], rgb * 0.25 + 0.08, rgb)
    return Image.fromarray((rgb * 255).astype(np.uint8))


def analyse_frame(vdir, take, n, f, game_p, entry, isolate):
    game = load(game_p, (W, H))
    reze = load(os.path.join(vdir, "_render", take, f"reze_{f}.png"), (W, H))
    Image.fromarray(game.astype(np.uint8)).save(os.path.join(vdir, f"{take}_{n:04d}_game.png"))
    Image.fromarray(reze.astype(np.uint8)).save(os.path.join(vdir, f"{take}_{n:04d}_reze.png"))
    d = np.abs(reze - game).mean(-1)
    luma = lambda a: (a @ np.array([0.2126, 0.7152, 0.0722], np.float32))
    res = {"frame": n, "clip": f, "mad": round(float(d.mean()), 2),
           "luma_game": round(float(luma(game).mean()), 2), "luma_reze": round(float(luma(reze).mean()), 2)}
    cast_full = None
    attrib = []
    if isolate:
        rd = os.path.join(vdir, "_render", take)
        base = load(os.path.join(rd, f"iso_{f}_base.png"), (IW, IH))
        g_s = load(game_p, (IW, IH))
        for kind, items in (("effect", entry.get("effects", [])), ("prop", entry.get("props", []))):
            for it in items:
                p = os.path.join(rd, f"iso_{f}_{it['key']}.png")
                if not os.path.exists(p):
                    continue
                v = load(p, (IW, IH))
                c = np.abs(base - v).mean(-1)
                foot = c > FOOT
                area = float(foot.mean())
                if it["name"].startswith("cast:"):
                    cast_full = np.asarray(Image.fromarray(foot.astype(np.uint8) * 255).resize((W, H), Image.NEAREST)) > 0
                    res["cast_area"] = round(area, 4)
                    continue
                if area < MIN_AREA:
                    continue
                err_on = float(np.abs(base - g_s).mean(-1)[foot].mean())
                err_off = float(np.abs(v - g_s).mean(-1)[foot].mean())
                excess = float(((luma(base) - luma(v))[foot]).mean())     # how much light it adds there
                ys, xs = np.nonzero(foot)
                a = {"kind": kind, "name": it["name"], "key": it["key"], "area": round(area, 4),
                     "added_luma": round(excess, 1), "err_on": round(err_on, 1), "err_off": round(err_off, 1),
                     "bbox": [int(xs.min()) * W // IW, int(ys.min()) * H // IH, int(xs.max() + 1) * W // IW, int(ys.max() + 1) * H // IH],
                     "flag": err_off < FLAG * err_on}
                if a["flag"]:
                    sheet = Image.new("RGB", (IW * 4, IH))
                    for k, arr in enumerate((g_s, base, v)):
                        sheet.paste(Image.fromarray(arr.astype(np.uint8)), (IW * k, 0))
                    sheet.paste(heat(c * 3), (IW * 3, 0))
                    a["evidence"] = f"{take}_{n:04d}_iso_{re.sub(r'[^A-Za-z0-9_.-]+', '_', it['name'])[:60]}.png"
                    sheet.save(os.path.join(vdir, a["evidence"]))
                attrib.append(a)
    if cast_full is not None:
        res["mad_scene"] = round(float(d[~cast_full].mean()), 2)
    heat(d, cast_full).save(os.path.join(vdir, f"{take}_{n:04d}_diff.png"))
    attrib.sort(key=lambda a: (not a["flag"], -(a["err_on"] - a["err_off"]) * a["area"]))
    res["attribution"] = attrib
    return res


def run_take(dlc, take, zip_path, refs, frames, isolate, url, camera="recorded", rec_dir=None):
    vdir = os.path.join(dlc, "verify")
    rdir = os.path.join(vdir, "_render", take)
    os.makedirs(rdir, exist_ok=True)
    clip = [n + 1 for n in frames]
    # this tool's own evidence sheets from an earlier run of these frames
    for n in frames:
        for old in glob.glob(os.path.join(vdir, f"{take}_{n:04d}_iso_*.png")):
            os.remove(old)
    # the camera of the run the reference frames come from (a --capture records its own)
    poses = recorded_poses(rec_dir or os.path.join(dlc, "web", "data"), take, max(clip))
    keys = vmd_camera(zip_path)
    z = zipfile.ZipFile(zip_path)
    names = [(e.get("source") or {}).get("name") for e in json.loads(z.read("scene.json"))["settings"]["background"]["effects"]]
    job = {"zip": os.path.abspath(zip_path), "out": os.path.abspath(rdir), "frames": clip, "isolate": isolate,
           "width": W, "height": H, "isoWidth": IW, "url": url, "puppeteer": ensure_puppeteer(),
           # the take's own effects: the default scene's (the cast's) are switched off
           "effects": [n for n in names if n],
           "poses": [[p["target"], p["rotation"], p["fov"]] for _, p in sorted(poses.items())] if camera == "recorded" else None}
    jp = os.path.join(rdir, "job.json")
    json.dump(job, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    node = "node.exe" if os.name == "nt" else "node"
    r = subprocess.run([node, RENDER_JS, jp], check=False)
    if r.returncode != 0:
        print(f"  reze render failed ({r.returncode})")
        return None
    report = json.load(open(os.path.join(rdir, "render.json"), encoding="utf-8"))
    out = []
    for n, f in zip(frames, clip):
        entry = report["frames"].get(str(f), {})
        res = analyse_frame(vdir, take, n, f, refs[n], entry, isolate)
        vm = vmd_at(keys, f)
        if vm:
            res["camera"] = {"fov_game": round(poses[f]["fovDeg"], 2), "fov_vmd": round(vm[0], 2),
                             "eye_offset": round(float(np.linalg.norm(vm[1] - np.array(poses[f]["eye"]))), 2),
                             "rendered_with": camera}
        flagged = [a["name"] for a in res["attribution"] if a["flag"]]
        print(f"  {take} {n:04d}: mad {res['mad']:.1f}" + (f" (scene {res['mad_scene']:.1f})" if "mad_scene" in res else "")
              + f"  luma game {res['luma_game']:.0f} reze {res['luma_reze']:.0f}" + (f"  flagged: {', '.join(flagged)}" if flagged else ""))
        out.append(res)
    return {"take": take, "zip": zip_path, "frames": out, "errors": report.get("errors", [])}


def write_summary(dlc, takes):
    vdir = os.path.join(dlc, "verify")
    old = os.path.join(vdir, "summary.json")
    if os.path.exists(old):     # a run of some takes keeps the others' results
        names = {t["take"] for t in takes}
        takes = [t for t in json.load(open(old, encoding="utf-8"))["takes"] if t["take"] not in names] + takes
    json.dump({"dlc": os.path.basename(dlc), "takes": takes}, open(os.path.join(vdir, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    L = [f"# V3 render pairs: {os.path.basename(dlc)}", "",
         "mad: mean |reze - game| (0-255) over the frame; scene: without the cast's footprint. "
         "Flagged: the frame is closer to the game without it (err_off < "
         f"{FLAG} x err_on within its footprint).", ""]
    for t in takes:
        L += [f"## {t['take']}", "", "| frame | mad | scene | luma game / reze | fov game / vmd, eye off (PMX) | flagged (area, err on -> off, added luma) |", "|---|---|---|---|---|---|"]
        for r in t["frames"]:
            fl = "; ".join(f"{a['name']} ({a['area']:.1%}, {a['err_on']:.0f}->{a['err_off']:.0f}, +{a['added_luma']:.0f})"
                           for a in r["attribution"] if a["flag"])
            cam = r.get("camera")
            cs = f"{cam['fov_game']:.1f} / {cam['fov_vmd']:.1f}, {cam['eye_offset']:.1f}" if cam else ""
            L.append(f"| {r['frame']:04d} | {r['mad']:.1f} | {r.get('mad_scene', '')} | {r['luma_game']:.0f} / {r['luma_reze']:.0f} | {cs} | {fl} |")
        if t.get("errors"):
            L += ["", "page errors:", *[f"- `{e}`" for e in t["errors"][:10]]]
        L.append("")
    open(os.path.join(vdir, "summary.md"), "w", encoding="utf-8").write("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dlc", nargs="*")
    ap.add_argument("--take", nargs="*")
    ap.add_argument("--frames", help="reference frame numbers, comma separated")
    ap.add_argument("--every", type=int, default=EVERY)
    ap.add_argument("--no-isolate", action="store_true")
    ap.add_argument("--capture", action="store_true", help="Unity reference frames into verify/_ref first")
    ap.add_argument("--ref-dir", help="reference frames folder (default: verify/_ref/unity if present, else web/data/unity)")
    ap.add_argument("--zip-dir", help="the takes' zips (default <dlc>/reze)")
    ap.add_argument("--url", default="http://localhost:3000")
    ap.add_argument("--camera", choices=["recorded", "vmd"], default="recorded",
                    help="render with the recorded game camera (default: the pairs then compare the picture, "
                         "and the camera VMD is checked against it in numbers) or the take's camera.vmd")
    a = ap.parse_args()
    for dlc in dlc_dirs(a.dlc):
        name = os.path.basename(dlc)
        print(f"== {name}")
        index = json.load(open(os.path.join(dlc, "web", "data", "index.json"), encoding="utf-8"))
        takes = a.take or [s["name"] for s in index["sequences"]]
        ref_dir = a.ref_dir
        if a.capture:
            ref_dir = capture_refs(dlc)
        if not ref_dir:
            scratch = os.path.join(dlc, "verify", "_ref", "unity")
            ref_dir = scratch if os.path.isdir(scratch) else os.path.join(dlc, "web", "data", "unity")
        zip_dir = a.zip_dir or os.path.join(dlc, "reze")
        done = []
        for take in takes:
            zp = os.path.join(zip_dir, f"{name}-{take}.zip")
            refs = ref_frames(ref_dir, take)
            if not os.path.exists(zp):
                print(f"  {take}: no zip ({zp})")
                continue
            if len(refs) < 2:
                print(f"  {take}: no reference frames in {ref_dir} (--capture)")
                continue
            frames = [int(x) for x in a.frames.split(",")] if a.frames else pick(refs, windows_of(zp), a.every)
            frames = [n for n in frames if n in refs]
            print(f"  {take}: {len(frames)} frames {frames}")
            rec = os.path.dirname(os.path.abspath(ref_dir))
            rec = rec if os.path.exists(os.path.join(rec, f"{take}.frames.jsonl")) else None
            r = run_take(dlc, take, zp, refs, frames, not a.no_isolate, a.url, a.camera, rec)
            if r:
                done.append(r)
        if done:
            write_summary(dlc, done)
            print(f"  -> {os.path.join(dlc, 'verify', 'summary.md')}")


if __name__ == "__main__":
    main()
