#!/usr/bin/env python3
r"""
Rip every AnimationClip out of the restored bundles into AG_animations.

This is the step that produces the clip library everything else reads. It drives a
headless AssetRipper over the restored bundle tree and keeps only .anim and
.controller assets, discarding the meshes, textures and audio that would otherwise
make the output hundreds of gigabytes.

Why it works in chunks
----------------------
AssetRipper holds a whole load in memory and exports it in one pass. Pointed at all
of StreamingAssets it exhausts RAM long before it finishes, so the bundle tree is
split into batches of roughly --chunk-mb and each is loaded, exported and pruned on
its own. Batching also means a failure costs one chunk rather than the whole run.

Per-chunk results are appended to AG_animations/_rip_results.json so an interrupted
run can be resumed without redoing finished work.

Usage
-----
  python ag.py rip-anims --dry-run          # show the chunk plan, rip nothing
  python ag.py rip-anims                    # everything (hours, ~26 GB)
  python ag.py rip-anims --only comchar     # one bundle group
  python ag.py rip-anims --only animatorcontroller --force   # smallest group; a
                                                             # good smoke test

Prerequisite: run `ag.py restore` first - AssetRipper cannot read the game's
hash-named, NUL-padded bundles.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

EXE = r"C:\Users\amyan\Downloads\AssetRipper_win_x64\AssetRipper.GUI.Free.exe"
SRC = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data\StreamingAssets"
       r"\Windows_restored")
OUT = r"C:\AetherGazerStarter\AG_animations"
STAGE = r"C:\_agrip"
RESULTS = os.path.join(OUT, "_rip_results.json")
UNITY_VERSION = "2022.3.62f3"
PORT = 5602
BASE = f"http://localhost:{PORT}"

KEEP = (".anim", ".controller")

# Pure media: textures, audio, video, fonts, lip-sync and localisation images. These
# hold no AnimationClips, and SofdecAsset alone is 4.5 GB of video, so ripping them
# would cost hours for nothing.
MEDIA_ONLY = {"music", "sound", "fonts", "SofdecAsset", "crilipsexdata", "texturebg",
              "textureconfig", "textures", "i18nimg", "i18ntranslate", "atlas",
              "splash", "packages", "gamepadconfig", "goldminerleveldata"}


def default_groups() -> list[str]:
    """Every bundle group that could hold animation - i.e. all but pure media."""
    if not os.path.isdir(SRC):
        return []
    return sorted(d for d in os.listdir(SRC)
                  if os.path.isdir(os.path.join(SRC, d)) and d not in MEDIA_ONLY)


def stage_files(paths: list[str]) -> None:
    """Copy a chunk's bundles into the staging folder.

    AssetRipper keeps handles on the files it loaded, so the previous chunk's
    bundles can still be locked when the next one is staged - that killed a run
    with PermissionError on cameracontroller.ys. Callers must /Reset first; this
    additionally retries, because the handles are released asynchronously.
    """
    for attempt in range(12):
        shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(STAGE, exist_ok=True)
        try:
            for p in paths:
                shutil.copyfile(p, os.path.join(STAGE, os.path.basename(p)))
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(5)


def post(path: str, fields: dict | None = None, timeout: int = 7200) -> str:
    data = urllib.parse.urlencode(fields or {}).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def alive() -> bool:
    try:
        urllib.request.urlopen(BASE + "/", timeout=5).read()
        return True
    except Exception:
        return False


def start_server(log_path: str):
    subprocess.run(["taskkill", "/F", "/IM", os.path.basename(EXE)],
                   capture_output=True)
    time.sleep(1)
    proc = subprocess.Popen([EXE, "--headless", "--port", str(PORT),
                             "--log-path", log_path],
                            cwd=os.path.dirname(EXE),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        if alive():
            return proc
        time.sleep(1)
    raise SystemExit("AssetRipper did not start")


def plan_chunks(groups: list[str], chunk_mb: int) -> list[dict]:
    """Split each bundle group into batches of roughly chunk_mb."""
    chunks = []
    for group in groups:
        gdir = os.path.join(SRC, group)
        if not os.path.isdir(gdir):
            continue
        files = []
        for dp, _, fns in os.walk(gdir):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    files.append((p, os.path.getsize(p)))
                except OSError:
                    pass
        files.sort()
        batch, size, n = [], 0, 0
        for p, sz in files:
            batch.append(p)
            size += sz
            if size >= chunk_mb * 1024 * 1024:
                n += 1
                chunks.append({"chunk": f"{group}#{n}", "files": batch,
                               "mb": size / 1024 ** 2})
                batch, size = [], 0
        if batch:
            n += 1
            chunks.append({"chunk": f"{group}#{n}", "files": batch,
                           "mb": size / 1024 ** 2})
    return chunks


def prune(root: str) -> tuple[int, int, int]:
    """Delete everything that isn't a clip or controller. Returns (anim, ctrl, kept)."""
    anims = ctrls = 0
    for dp, _, fns in os.walk(root):
        for fn in fns:
            low = fn.lower()
            if low.endswith(".anim"):
                anims += 1
            elif low.endswith(".controller"):
                ctrls += 1
            elif low.endswith(".meta"):
                stem = low[:-5]
                if stem.endswith(KEEP):
                    continue
                os.remove(os.path.join(dp, fn))
            else:
                os.remove(os.path.join(dp, fn))
    # Drop the directories left empty behind us.
    for dp, dns, fns in os.walk(root, topdown=False):
        if not os.listdir(dp):
            try:
                os.rmdir(dp)
            except OSError:
                pass
    return anims, ctrls, anims + ctrls


def merge_into(src: str, dst: str) -> None:
    """Fold one chunk's ExportedProject/Assets tree into the shared output."""
    assets = os.path.join(src, "ExportedProject", "Assets")
    if not os.path.isdir(assets):
        return
    for entry in os.listdir(assets):
        s, d = os.path.join(assets, entry), os.path.join(dst, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        elif not os.path.exists(d):
            shutil.copyfile(s, d)


def load_results() -> list[dict]:
    if os.path.isfile(RESULTS):
        try:
            with open(RESULTS, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            pass
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[],
                    help="restrict to these bundle groups; repeatable")
    ap.add_argument("--chunk-mb", type=int, default=500,
                    help="approximate bytes of bundles per AssetRipper load")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true", help="show the plan and stop")
    ap.add_argument("--force", action="store_true",
                    help="redo chunks already recorded as ok")
    ap.add_argument("--timeout", type=int, default=21600,
                    help="seconds to allow one load/export (default 6 h); a 500 MB "
                         "comeffect chunk once needed more than the old 2 h cap")
    args = ap.parse_args()

    if not os.path.isfile(EXE):
        sys.exit(f"error: AssetRipper not found at {EXE}")
    if not os.path.isdir(SRC):
        sys.exit(f"error: restored bundles not found at {SRC}\n"
                 f"       run 'python ag.py restore' first")

    groups = args.only or default_groups()
    chunks = plan_chunks(groups, args.chunk_mb)
    if not chunks:
        sys.exit(f"error: no bundles found for {groups}")

    # Work out what is already ripped at the level of individual bundles, not chunk
    # names: names are positional, so changing --chunk-mb renumbers them and would
    # otherwise redo everything. Entries written before file lists were recorded are
    # reconstructed by re-planning their group at the size they used.
    results_so_far = load_results()
    done_files: set[str] = set()
    plan_cache: dict[tuple[str, int], dict[str, list[str]]] = {}
    for r in results_so_far:
        if r.get("status") != "ok":
            continue
        if r.get("files"):
            done_files.update(r["files"])
            continue
        group, size = r["chunk"].split("#")[0], int(r.get("chunk_mb", 500))
        key = (group, size)
        if key not in plan_cache:
            plan_cache[key] = {c["chunk"]: [os.path.basename(p) for p in c["files"]]
                               for c in plan_chunks([group], size)}
        done_files.update(plan_cache[key].get(r["chunk"], []))

    todo = []
    for c in chunks:
        if args.force:
            todo.append(c)
            continue
        remaining = [p for p in c["files"]
                     if os.path.basename(p) not in done_files]
        if remaining:
            todo.append({**c, "files": remaining,
                         "mb": sum(os.path.getsize(p) for p in remaining) / 1024 ** 2})

    total_mb = sum(c["mb"] for c in chunks)
    todo_mb = sum(c["mb"] for c in todo)
    print(f"{len(chunks)} chunk(s), {total_mb / 1024:.1f} GiB of bundles")
    print(f"{len(done_files)} bundle(s) already ripped; "
          f"{len(todo)} chunk(s) / {todo_mb / 1024:.2f} GiB to go")
    for c in todo[:10]:
        print(f"  {c['chunk']:<28} {len(c['files']):>5} files  {c['mb']:>7.1f} MB")
    if len(todo) > 10:
        print(f"  ... and {len(todo) - 10} more")
    if args.dry_run:
        return 0
    if not todo:
        print("\nnothing to do")
        return 0

    os.makedirs(args.out, exist_ok=True)
    results = load_results()
    proc = start_server(os.path.join(args.out, "ar_rip.log"))
    print("\nAssetRipper up\n", flush=True)

    failed = 0
    try:
        for i, chunk in enumerate(todo, 1):
            t0 = time.time()
            work = os.path.join(args.out, "_chunk")
            shutil.rmtree(work, ignore_errors=True)
            try:
                # Reset before staging: it makes AssetRipper drop the file handles
                # it still holds on the previous chunk's bundles.
                post("/Reset", timeout=600)
                stage_files(chunk["files"])
                post("/Settings/Update", {
                    "DefaultVersion": UNITY_VERSION, "TargetVersion": UNITY_VERSION,
                    "ScriptContentLevel": "Level0",
                    "BundledAssetsExportMode": "DirectExport",
                    "ImageExportFormat": "Png", "AudioExportFormat": "Default",
                    "IgnoreStreamingAssets": "false",
                    "EnableStaticMeshSeparation": "false",
                    "EnablePrefabOutlining": "false",
                    "EnableAssetDeduplication": "false",
                }, timeout=120)
                post("/LoadFolder", {"Path": STAGE}, timeout=args.timeout)
                post("/Export/UnityProject", {"Path": work}, timeout=args.timeout)
                anims, ctrls, kept = prune(os.path.join(work, "ExportedProject",
                                                        "Assets"))
                merge_into(work, args.out)
                row = {"chunk": chunk["chunk"], "status": "ok", "anims": anims,
                       "controllers": ctrls, "kept": kept,
                       "mb": round(chunk["mb"], 1),
                       "seconds": round(time.time() - t0, 1),
                       # Recorded so a resume at a different --chunk-mb still knows
                       # what has been done; chunk names shift when the size changes.
                       "files": [os.path.basename(p) for p in chunk["files"]],
                       "chunk_mb": args.chunk_mb}
            except Exception as exc:                                  # noqa: BLE001
                failed += 1
                row = {"chunk": chunk["chunk"], "status": "failed",
                       "error": f"{type(exc).__name__}: {exc}",
                       "mb": round(chunk["mb"], 1),
                       "seconds": round(time.time() - t0, 1)}
            finally:
                shutil.rmtree(work, ignore_errors=True)

            results = [r for r in results if r["chunk"] != chunk["chunk"]] + [row]
            with open(RESULTS, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=1)
            print(f"[{i}/{len(todo)}] {row['chunk']}: {row['status']} "
                  f"{row.get('kept', 0)} kept ({row['seconds']:.0f}s)", flush=True)
    finally:
        subprocess.run(["taskkill", "/F", "/IM", os.path.basename(EXE)],
                       capture_output=True)
        shutil.rmtree(STAGE, ignore_errors=True)

    kept = sum(r.get("kept", 0) for r in results if r.get("status") == "ok")
    print(f"\n{len(todo) - failed}/{len(todo)} chunks ok, {kept} clips/controllers "
          f"in {args.out}")
    print("run 'python ag.py find --reindex' to pick up the new clips")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
