#!/usr/bin/env python3
r"""
Catalogue every pipeline/scene-rendering component used by any stage -> AG_pipeline/.

The pipeline's feature objects (player data) are switches and resource references;
the *tuning* - cascade splits, AO strength, fog, bloom, reflection, per-light
extras - is serialized per scene, in Volume profile components and scene
components (SceneSetting, SimMainLight, ReplicaAdditionalLightData, ...). Stage
bundles keep type trees, so this records, for every such class:

  volume_components.json   class -> {assembly, fields (name + type, in order),
                                     stages using it, every stage's values}

Scans comscene/comsceneq/comeffect/levels scene + _dep bundles (544 stages).

    python agtools/volume_catalog.py
"""
from __future__ import annotations

import json
import os
import sys
import warnings

import UnityPy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenes  # noqa: E402

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
warnings.simplefilter("ignore")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AG_pipeline")
# game rendering namespaces / classes worth cataloguing (not gameplay scripts)
KEEP_NS = ("UnityEngine.Rendering", "UnityEngine.Pipelines")
KEEP_CLS = {"SceneSetting", "SceneSettingFogConfig", "BakeSetting", "LightmappedLOD",
            "CharacterSceneEnvironment", "DynamicObjectBinding", "QWSceneDitherComponennt",
            "LocalVolumetricFog"}


def fields(node, depth=0) -> list:
    out = []
    for c in node.m_Children:
        if c.m_Name in ("m_GameObject", "m_Enabled", "m_Script", "m_Name"):
            continue
        entry = {"name": c.m_Name, "type": c.m_Type}
        if c.m_Children and depth < 3 and c.m_Type not in ("string",) and not c.m_Type.startswith("PPtr"):
            entry["fields"] = fields(c, depth + 1)
        out.append(entry)
    return out


def main() -> int:
    cat: dict[str, dict] = {}
    todo = scenes.codes()
    for i, (folder, code) in enumerate(todo, 1):
        d = scenes.LEVEL_DIRS[folder]
        for suffix in ("", "_dep"):
            b = os.path.join(d, f"{code}{suffix}.ys")
            if not os.path.isfile(b):
                continue
            try:
                env = UnityPy.load(b)
            except Exception:                                 # noqa: BLE001
                continue
            for o in env.objects:
                if o.type.name != "MonoBehaviour" or not o.serialized_type or not o.serialized_type.node:
                    continue
                try:
                    dd = o.read(check_read=False)
                    s = dd.m_Script.read()
                except Exception:                             # noqa: BLE001
                    continue
                if not (s.m_Namespace.startswith(KEEP_NS) or s.m_ClassName in KEEP_CLS):
                    continue
                key = f"{s.m_Namespace}.{s.m_ClassName}".lstrip(".")
                e = cat.setdefault(key, {"assembly": s.m_AssemblyName,
                                         "fields": fields(o.serialized_type.node),
                                         "stages": [], "values": {}})
                if code not in e["stages"]:
                    e["stages"].append(code)
                try:
                    v = o.read_typetree()
                    for k in ("m_GameObject", "m_Script", "m_Enabled"):
                        v.pop(k, None)
                    e["values"].setdefault(code, []).append(v)
                except Exception:                             # noqa: BLE001
                    pass
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "volume_components.json"), "w", encoding="utf-8") as fh:
        json.dump(cat, fh, indent=1, ensure_ascii=False, default=str)
    for k, e in sorted(cat.items(), key=lambda kv: -len(kv[1]["stages"])):
        print(f"{len(e['stages']):4d} stages  {k}  ({len(e['fields'])} fields)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
