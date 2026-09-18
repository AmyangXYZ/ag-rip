"""Extract one character's bundle in full (model + skeleton + clips) for inspection."""
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXE = r"C:\Users\amyan\Downloads\AssetRipper_win_x64\AssetRipper.GUI.Free.exe"
HERO = (r"C:\AetherGazerStarter\AetherGazer\AetherGazer_Data\StreamingAssets"
        r"\Windows_restored\comchar\assets\comchar\artresources\char\hero")
STAGE = r"C:\_agsample"
PORT = 5601
BASE = f"http://localhost:{PORT}"

char_ids = sys.argv[1:] or ["1044"]
OUT_ROOT = r"C:\AetherGazerStarter\AG_sample"


def post(path, fields=None, timeout=3600):
    data = urllib.parse.urlencode(fields or {}).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def alive():
    try:
        urllib.request.urlopen(BASE + "/", timeout=5).read()
        return True
    except Exception:
        return False


subprocess.run(["taskkill", "/F", "/IM", "AssetRipper.GUI.Free.exe"], capture_output=True)
time.sleep(1)
proc = subprocess.Popen([EXE, "--headless", "--port", str(PORT),
                         "--log-path", os.path.join(OUT_ROOT, "ar_sample.log")],
                        cwd=os.path.dirname(EXE),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.makedirs(OUT_ROOT, exist_ok=True)
for _ in range(90):
    if alive():
        break
    time.sleep(1)
else:
    raise SystemExit("AssetRipper did not start")
print("server up", flush=True)

try:
    for cid in char_ids:
        src = os.path.join(HERO, f"{cid}.ys")
        if not os.path.exists(src):
            print(f"{cid}: no such hero bundle", flush=True)
            continue
        shutil.rmtree(STAGE, ignore_errors=True)
        os.makedirs(STAGE, exist_ok=True)
        shutil.copyfile(src, os.path.join(STAGE, f"{cid}.ys"))

        out = os.path.join(OUT_ROOT, cid)
        shutil.rmtree(out, ignore_errors=True)

        t0 = time.time()
        post("/Reset", timeout=300)
        post("/Settings/Update", {
            "DefaultVersion": "2022.3.62f3", "TargetVersion": "2022.3.62f3",
            "ScriptContentLevel": "Level0", "BundledAssetsExportMode": "DirectExport",
            "ImageExportFormat": "Png", "AudioExportFormat": "Default",
            "IgnoreStreamingAssets": "false", "EnableStaticMeshSeparation": "false",
            "EnablePrefabOutlining": "false", "EnableAssetDeduplication": "false",
        }, timeout=120)
        post("/LoadFolder", {"Path": STAGE})
        post("/Export/UnityProject", {"Path": out})

        counts = {}
        for dp, _, fns in os.walk(out):
            for fn in fns:
                e = os.path.splitext(fn)[1].lower()
                if e != ".meta":
                    counts[e] = counts.get(e, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        print(f"{cid}: {time.time()-t0:.0f}s  {dict(top)}", flush=True)
finally:
    subprocess.run(["taskkill", "/F", "/IM", "AssetRipper.GUI.Free.exe"], capture_output=True)
    shutil.rmtree(STAGE, ignore_errors=True)
print("done", flush=True)
