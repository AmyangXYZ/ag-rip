#!/usr/bin/env python3
r"""Every stage GLB in reze-design, rebuilt from the game with the current tools.

    python ag.py glbs                      every stages/*.glb in reze-design
    python ag.py glbs x348 x333            just those
    python ag.py glbs --jobs 3 --fresh     three Blender builds at once; re-extract every project
    python ag.py glbs --package-only       only lay out every stage's folder (below), no rebuild

Per stage: its Unity project (AG_stages/<code>, `ag.py stage`) when it has none
yet (or --fresh), then reze-design's tools/stages/unity_to_glb.py. The GLB it
replaces is kept in AG_stages/_glb_prev/.

NAMES come from the README's DLC table: <skin>-<角色>-<皮肤名>-<scene title>, from
the row whose Stage GLB column names the file (109503-托特-扉页之吻-飞羽栖处是归乡,
once X348-托特花嫁.glb), and the row's Stage GLB column follows the rename. Each
stage is a folder, what a friend is handed:

    stages/<name>/<name>.glb
    stages/<name>/animations/<seq>/{character.vmd, camera.vmd, audio.wav}

the skin's exported takes (AG_dlc_scene/<skin>/<seq>/, those three files only);
109502's two stages split its takes by __day / __night. A GLB no row names keeps
its own name, in a folder of that name. The build itself writes in
AG_stages/_glb_build/ and the GLB then moves into its folder, so nothing of
the build lands in what is shared.

Extraction runs one stage at a time, as it must: AssetRipper stages every
export in one folder (stage_unity.STAGING). The Blender builds read only their
own project and build folder, so they run beside it, --jobs at once, each
starting the moment its project is ready.
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage_unity  # noqa: E402

DESIGN = r"D:\reze-design"
STAGES = os.path.join(DESIGN, "stages")
BUILDER = os.path.join(DESIGN, "tools", "stages", "unity_to_glb.py")
PREV = os.path.join(stage_unity.OUT, "_glb_prev")
LOGS = os.path.join(stage_unity.OUT, "_glb_logs")
# where a GLB is built (unity_to_glb keeps its build folder beside --out), out of stages/
WORK = os.path.join(stage_unity.OUT, "_glb_build")
HERE = os.path.dirname(os.path.abspath(__file__))
README = os.path.join(os.path.dirname(HERE), "README.md")
README_LOCK = threading.Lock()
# the level a table stage was built from, where the two differ (README: "built as X203.glb")
LEVEL = {"x203b": "x203", "x203c": "x203a"}
# a stage sharing its skin's takes with another keeps only its own time of day
TAKES = {"x203b": "__night", "x203c": "__day"}   # the suffix it leaves out
SCENES = os.path.join(os.path.dirname(HERE), "AG_dlc_scene")
TAKE_FILES = ("character.vmd", "camera.vmd", "audio.wav")


def code_of(glb: str) -> str:
    """'X348-托特花嫁.glb' -> 'x348'; 'X306a.glb' -> 'x306a'."""
    return re.split(r"[-.]", os.path.basename(glb), 1)[0].lower()


def table_rows() -> list[dict]:
    """The README DLC table's rows that name a stage GLB: skin, character, skin
    name, stage, scene title, the GLB file name, and the line it is on."""
    rows = []
    lines = open(README, encoding="utf-8").read().split("\n")
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 9 or not re.fullmatch(r"\**\d{6}\**", cells[0]):
            continue
        m = re.search(r"[^\s|]+\.glb", cells[7])
        if not m:
            continue
        rows.append({"skin": cells[0].strip("*"), "char": re.sub(r"\s*\(\d+\)$", "", cells[1]), "skinName": cells[3],
                     "stage": cells[5], "title": cells[6], "glb": m.group(0), "line": i})
    return rows


def full_name(row: dict) -> str:
    return f"{row['skin']}-{row['char']}-{row['skinName']}-{row['title']}.glb"


def stage_glbs(codes: list[str]) -> list[dict]:
    """Every GLB in reze-design/stages: {code (its level), stage, path, target
    (its full name), row}, narrowed to `codes` (level or stage codes) when given."""
    rows = table_rows()
    by_file = {r["glb"]: r for r in rows}
    by_full = {full_name(r): r for r in rows}
    found = []   # (file name, path): a loose GLB, or one in its own folder
    for fn in sorted(os.listdir(STAGES)):
        full = os.path.join(STAGES, fn)
        if fn.lower().endswith(".glb") and os.path.isfile(full):
            found.append((fn, full))
        elif os.path.isfile(os.path.join(full, fn + ".glb")):
            found.append((fn + ".glb", os.path.join(full, fn + ".glb")))
    out = []
    for fn, path in found:
        row = by_file.get(fn) or by_full.get(fn)
        stage = row["stage"].lower() if row else code_of(fn)
        out.append({"code": LEVEL.get(stage, stage), "stage": stage, "path": path,
                    "target": full_name(row) if row else fn, "row": row})
    if codes:
        want = {c.lower() for c in codes}
        out = [x for x in out if x["code"] in want or x["stage"] in want]
        missing = want - {x["code"] for x in out} - {x["stage"] for x in out}
        if missing:
            print(f"no GLB in {STAGES} for: {', '.join(sorted(missing))}")
    return out


def folder_of(item: dict) -> str:
    return os.path.join(STAGES, os.path.splitext(item["target"])[0])


def rename(item: dict) -> str:
    """Move the GLB to <name>/<name>.glb, and its README row to the new file name. Returns the path."""
    target = os.path.join(folder_of(item), item["target"])
    if os.path.abspath(item["path"]) != os.path.abspath(target):
        os.makedirs(folder_of(item), exist_ok=True)
        shutil.move(item["path"], target)   # across drives: the build is on C:, stages/ on D:
        print(f"[name] {os.path.basename(item['path'])} -> {os.path.relpath(target, STAGES)}", flush=True)
        item["path"] = target
    row = item["row"]
    if row and row["glb"] != item["target"]:
        with README_LOCK:
            # newline="": the README is CRLF and must stay so (text mode would
            # turn every line ending to LF)
            lines = open(README, encoding="utf-8", newline="").read().split("\n")
            lines[row["line"]] = lines[row["line"]].replace(row["glb"], item["target"], 1)
            open(README, "w", encoding="utf-8", newline="").write("\n".join(lines))
        row["glb"] = item["target"]
    return target


def copy_takes(item: dict) -> int:
    """The skin's exported takes into <name>/animations/<seq>/. Returns how many."""
    row = item["row"]
    src = os.path.join(SCENES, row["skin"]) if row else None
    if not src or not os.path.isdir(src):
        return 0
    skip = TAKES.get(item["stage"])
    n = 0
    for seq in sorted(os.listdir(src)):
        files = [f for f in TAKE_FILES if os.path.isfile(os.path.join(src, seq, f))]
        if "character.vmd" not in files or (skip and seq.endswith(skip)):
            continue
        dst = os.path.join(folder_of(item), "animations", seq)
        os.makedirs(dst, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(src, seq, f), os.path.join(dst, f))
        n += 1
    return n


def package(item: dict) -> None:
    rename(item)
    n = copy_takes(item)
    print(f"[pack] {os.path.basename(folder_of(item))}: {n} take(s)", flush=True)


def project_of(code: str) -> str:
    return os.path.join(stage_unity.OUT, stage_unity.project_name(code))


def ready(code: str) -> bool:
    return os.path.isfile(os.path.join(project_of(code), "ExportedProject", "ag_render_manifest.json"))


def level_of(code: str) -> str | None:
    """The stage's own level scene, ExportedProject-relative."""
    want = f"{code}.unity"
    for s in stage_unity.find_scenes(project_of(code)):   # relative to the project folder
        if os.path.basename(s).lower() == want:
            return s.replace("\\", "/").split("ExportedProject/", 1)[-1]
    return None


def extract(code: str) -> bool:
    log = os.path.join(LOGS, f"{code}.stage.log")
    t = time.time()
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.run([sys.executable, os.path.join(HERE, "stage_unity.py"), code],
                            stdout=f, stderr=subprocess.STDOUT).returncode
    ok = rc == 0 and ready(code)
    print(f"[extract] {code}: {'ok' if ok else f'FAILED ({rc}), see {log}'} ({time.time() - t:.0f}s)", flush=True)
    return ok


def build(item: dict) -> bool:
    code = item["code"]
    level = level_of(code)
    if not level:
        print(f"[glb] {code}: no {code}.unity in its project - skipped", flush=True)
        return False
    os.makedirs(PREV, exist_ok=True)
    shutil.copy2(item["path"], os.path.join(PREV, os.path.basename(item["path"])))
    # built in WORK (unity_to_glb puts its build folder next to --out), then
    # moved into its folder: stages/ holds only what is shared
    os.makedirs(WORK, exist_ok=True)
    flat = os.path.join(WORK, item["target"])
    log = os.path.join(LOGS, f"{code}.glb.log")
    name = code[0].upper() + code[1:]
    t = time.time()
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.run([sys.executable, BUILDER, "--project", os.path.join(project_of(code), "ExportedProject"),
                             "--scene", level, "--out", flat, "--name", name],
                            cwd=DESIGN, stdout=f, stderr=subprocess.STDOUT).returncode
    size = os.path.getsize(flat) / 1e6 if os.path.isfile(flat) else 0
    print(f"[glb] {code}: {'ok' if rc == 0 else f'FAILED ({rc}), see {log}'}, {size:.1f} MB ({time.time() - t:.0f}s)", flush=True)
    if rc != 0:
        return False
    if os.path.abspath(item["path"]) != os.path.abspath(flat) and os.path.isfile(item["path"]):
        os.remove(item["path"])   # the old file, kept in _glb_prev
    item["path"] = flat
    package(item)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*", help="stage codes (default: every GLB in reze-design/stages)")
    ap.add_argument("--jobs", type=int, default=2, help="Blender builds at once")
    ap.add_argument("--fresh", action="store_true", help="re-extract every project, even one already there")
    ap.add_argument("--package-only", action="store_true", help="only lay out each stage's folder: name, GLB, takes")
    a = ap.parse_args()
    os.makedirs(LOGS, exist_ok=True)
    todo = stage_glbs(a.codes)
    if not todo:
        return 1
    if a.package_only:
        for item in todo:
            package(item)
        return 0
    print(f"{len(todo)} stage(s): {', '.join(x['code'] for x in todo)}; logs in {LOGS}", flush=True)

    jobs: queue.Queue = queue.Queue()
    results: dict[str, bool] = {}

    def worker():
        while True:
            item = jobs.get()
            if item is None:
                return
            results[item["code"]] = build(item)

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, a.jobs))]
    for w in workers:
        w.start()
    # what is already extracted builds at once; the rest join as extraction finishes them
    pending = []
    for item in todo:
        if ready(item["code"]) and not a.fresh:
            jobs.put(item)
        else:
            pending.append(item)
    for item in pending:
        if extract(item["code"]):
            jobs.put(item)
        else:
            results[item["code"]] = False
    for _ in workers:
        jobs.put(None)
    for w in workers:
        w.join()
    bad = sorted(c for c, ok in results.items() if not ok)
    print(f"done: {len(results) - len(bad)} rebuilt" + (f", failed: {', '.join(bad)}" if bad else ""), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
