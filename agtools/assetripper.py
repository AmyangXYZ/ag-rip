"""Drive a headless AssetRipper (GUI.Free build) over its local HTTP API."""
from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
import urllib.request

EXE = r"C:\Users\amyan\Downloads\AssetRipper_win_x64\AssetRipper.GUI.Free.exe"
PORT = 5601
BASE = f"http://localhost:{PORT}"
UNITY_VERSION = "2022.3.62f3"

# Settings every export here shares. Level0 = no script stubs: the game is IL2CPP
# and only its bundles are loaded, so MonoBehaviours come out as "missing script"
# components that keep their serialized data - geometry, materials, lights and
# transforms are all unaffected.
BASE_SETTINGS = {
    "DefaultVersion": UNITY_VERSION, "TargetVersion": UNITY_VERSION,
    "ScriptContentLevel": "Level0", "BundledAssetsExportMode": "DirectExport",
    "ImageExportFormat": "Png", "AudioExportFormat": "Default",
    "IgnoreStreamingAssets": "false", "EnableStaticMeshSeparation": "false",
    "EnablePrefabOutlining": "false", "EnableAssetDeduplication": "false",
}


def post(path: str, fields: dict | None = None, timeout: int = 3600) -> str:
    data = urllib.parse.urlencode(fields or {}).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def alive() -> bool:
    try:
        urllib.request.urlopen(BASE + "/", timeout=5).read()
        return True
    except Exception:                                                 # noqa: BLE001
        return False


class Server:
    """`with Server(log) as ar: ar.export(folder, out)` - one process, many exports."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.proc = None

    def __enter__(self) -> "Server":
        subprocess.run(["taskkill", "/F", "/IM", os.path.basename(EXE)], capture_output=True)
        time.sleep(1)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self.proc = subprocess.Popen([EXE, "--headless", "--port", str(PORT),
                                      "--log-path", self.log_path],
                                     cwd=os.path.dirname(EXE),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(90):
            if alive():
                return self
            time.sleep(1)
        raise RuntimeError("AssetRipper did not start")

    def export(self, folder: str, out: str, **settings: str) -> None:
        post("/Reset", timeout=300)
        post("/Settings/Update", {**BASE_SETTINGS, **settings}, timeout=120)
        post("/LoadFolder", {"Path": folder})
        post("/Export/UnityProject", {"Path": out})

    def __exit__(self, *exc) -> None:
        subprocess.run(["taskkill", "/F", "/IM", os.path.basename(EXE)], capture_output=True)
