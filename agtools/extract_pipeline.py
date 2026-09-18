#!/usr/bin/env python3
r"""
Extract the game's render pipeline ("Replica"/"SimPipeline") from player data -> AG_pipeline/.

Stages only carry scene data; the renderer itself - its settings, its own shaders,
its compute shaders and lookup textures - ships in AetherGazer_Data/
globalgamemanagers.assets. Everything a port needs as reference:

  shaders/<name>.shader     the 53 pipeline shaders, decompiled (patched USCSandbox) -
                            SSAO, VolumetricLight, Volumetric/Final, CharacterShadow,
                            DecalShadow, SMAA, Bloom/Final, DoF, motion vectors, ...
  compute/<name>/           each compute shader: kernels.json (keywords, bindings,
                            thread group size, constant-buffer layouts with names and
                            offsets) + <kernel>_<n>.dxbc and .asm (d3dcompiler
                            disassembly) per variant
  materials/<id>_<name>.json  the pipeline's own materials (Bloom, Final, Lut, SSAO,
                            ...) with their shader and every property value
  textures/<name>.*         raw texture data (+ .json header, + .png/.exr preview)
  textures/LTC_LUT_64x64_RGBAHalf.raw   the area-light LUT the pipeline builds in code
                            (AreaLightFeature.Create from LtcData.s_LtcMatrixData_BRDF_GGX):
                            read from the decrypted IL2CPP metadata (tools/re/codephil.py +
                            Il2CppDumper, README "Reverse engineering")
  settings/<id>_<Class>.json  every pipeline object (ReplicaRenderPipelineAsset,
                            ReplicaRendererData and each renderer feature: SSAO,
                            PlusLighting, AreaLight, LightCookie, VolumetricLighting,
                            CascadeShadow, PostProcess, MirrorReflection, PPR, ...).
                            Player data strips type trees, so fields are unnamed: the
                            blob is kept verbatim (hex) and decoded as 4-byte words
                            (float / int) with object references resolved to names.

The pipeline's C# logic is compiled into GameAssembly.dll (IL2CPP, encrypted
metadata) and is not recovered here.

    python agtools/extract_pipeline.py
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import subprocess
import sys
import warnings

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
warnings.simplefilter("ignore")

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(TOP, "AetherGazer", "AetherGazer_Data")
SRC = os.path.join(DATA, "globalgamemanagers.assets")
OUT = os.path.join(TOP, "AG_pipeline")
USCS = os.path.join(TOP, "tools", "uscs")
DOTNET = os.path.join(TOP, "tools", "dotnet", "dotnet.exe")


def disasm(blob: bytes) -> str:
    i = blob.find(b"DXBC")
    if i < 0:
        return "; no DXBC container"
    blob = blob[i:]
    blob = blob[:int.from_bytes(blob[24:28], "little")]
    d3d = ctypes.WinDLL("d3dcompiler_47.dll")
    out = ctypes.c_void_p()
    if d3d.D3DDisassemble(ctypes.c_char_p(blob), ctypes.c_size_t(len(blob)), 0, None, ctypes.byref(out)):
        return "; D3DDisassemble failed"
    vt = ctypes.cast(out, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(out)
    n = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(out)
    return ctypes.string_at(ptr, n).decode("ascii", "replace").replace("\0", "")


def safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def shaders() -> int:
    tmp = os.path.join(USCS, "out")
    shutil.rmtree(tmp, ignore_errors=True)
    subprocess.run([DOTNET, "USCSandbox.dll", "null", SRC, "--all", "--version", "2022.3.62f3"],
                   cwd=USCS, capture_output=True, text=True)
    dst = os.path.join(OUT, "shaders")
    shutil.rmtree(dst, ignore_errors=True)
    if os.path.isdir(tmp):
        shutil.copytree(tmp, dst)
    return sum(len(f) for _, _, f in os.walk(dst))


def compute(env) -> int:
    n = 0
    for o in env.objects:
        if o.type.name != "ComputeShader":
            continue
        t = o.read_typetree()
        folder = os.path.join(OUT, "compute", safe(t["m_Name"]))
        os.makedirs(folder, exist_ok=True)
        meta = {"name": t["m_Name"], "variants": []}
        for v in t["variants"]:
            vm = {"targetRenderer": v["targetRenderer"], "targetLevel": v["targetLevel"],
                  "constantBuffers": v["constantBuffers"], "kernels": []}
            for k in v["kernels"]:
                km = {"name": k["name"], "globalKeywords": k["globalKeywords"],
                      "localKeywords": k["localKeywords"], "variantIndices": k["variantIndices"],
                      "variants": []}
                for i, uv in enumerate(k["uniqueVariants"]):
                    code = bytes(x & 0xFF for x in uv["code"])
                    base = f"{safe(k['name'])}_{i}"
                    open(os.path.join(folder, base + ".dxbc"), "wb").write(code)
                    open(os.path.join(folder, base + ".asm"), "w", encoding="utf-8").write(disasm(code))
                    km["variants"].append({k2: v2 for k2, v2 in uv.items() if k2 != "code"} | {"file": base})
                    n += 1
                vm["kernels"].append(km)
            meta["variants"].append(vm)
        json.dump(meta, open(os.path.join(folder, "kernels.json"), "w"), indent=1, default=str)
    return n


def textures(env) -> int:
    dst = os.path.join(OUT, "textures")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for o in env.objects:
        if o.type.name != "Texture2D":
            continue
        d = o.read()
        t = o.read_typetree()
        name = safe(d.m_Name)
        raw = bytes(d.image_data or b"")
        sd = t.get("m_StreamData") or {}
        if not raw and sd.get("size"):
            from UnityPy.helpers.ResourceReader import get_resource_data
            raw = bytes(get_resource_data(sd["path"], o.assets_file, sd["offset"], sd["size"]))
        open(os.path.join(dst, name + ".raw"), "wb").write(raw)
        head = {k: v for k, v in t.items() if k not in ("image data",)}
        json.dump(head, open(os.path.join(dst, name + ".json"), "w"), indent=1, default=str)
        try:
            d.image.save(os.path.join(dst, name + ".png"))   # 8-bit preview; HDR stays in .raw
        except Exception:                                     # noqa: BLE001
            pass
        n += 1
    return n


def materials(env) -> int:
    """Pipeline materials (engine class, full type info): shader + every property."""
    dst = os.path.join(OUT, "materials")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for o in env.objects:
        if o.type.name != "Material":
            continue
        t = o.read_typetree()
        d = o.read()
        try:
            t["_shader_name"] = d.m_Shader.read().m_ParsedForm.m_Name
        except Exception:                                     # noqa: BLE001
            pass
        json.dump(t, open(os.path.join(dst, f"{o.path_id}_{safe(t['m_Name'])}.json"), "w"),
                  indent=1, default=str)
        n += 1
    return n


def settings(env) -> int:
    names = {}
    for o in env.objects:
        try:
            d = o.read(check_read=False)
            nm = getattr(d, "m_Name", "") or ""
            if o.type.name == "Shader":
                nm = d.m_ParsedForm.m_Name
            if o.type.name == "MonoBehaviour":
                nm = d.m_Script.read().m_ClassName
            names[o.path_id] = f"{o.type.name}:{nm}"
        except Exception:                                     # noqa: BLE001
            names[o.path_id] = o.type.name
    dst = os.path.join(OUT, "settings")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        raw = o.get_raw_data()
        d = o.read(check_read=False)
        scr = d.m_Script.read()
        # header: m_GameObject (12) m_Enabled (4) m_Script (12) m_Name (len + chars, 4-aligned)
        p = 28
        ln = struct.unpack_from("<i", raw, p)[0]
        p += 4 + ln
        p = (p + 3) & ~3
        body = raw[p:]
        words = []
        i = 0
        while i + 4 <= len(body):
            iv = struct.unpack_from("<i", body, i)[0]
            fv = struct.unpack_from("<f", body, i)[0]
            entry = {"offset": i, "int": iv, "float": round(fv, 7)}
            # PPtr = fileID (int32) + pathID (int64)
            if i + 12 <= len(body) and iv in (0, 1, 2, 3):
                pid = struct.unpack_from("<q", body, i + 4)[0]
                if pid in names:
                    entry = {"offset": i, "pptr": {"fileID": iv, "pathID": pid, "target": names[pid]}}
                    words.append(entry)
                    i += 12
                    continue
            words.append(entry)
            i += 4
        out = {"pathID": o.path_id, "class": f"{scr.m_Namespace}.{scr.m_ClassName}",
               "assembly": scr.m_AssemblyName, "name": getattr(d, "m_Name", ""),
               "payload_hex": body.hex(), "words": words}
        json.dump(out, open(os.path.join(dst, f"{o.path_id}_{safe(scr.m_ClassName)}.json"), "w"),
                  indent=1)
        n += 1
    return n


RE = os.path.join(TOP, "AG_cache", "re")
# <PrivateImplementationDetails> field holding LtcData's static array initializer (32 KB)
LTC_FIELD = "CE0A5C74B1CAA82C10CD26F0B88446B2EB21A2E5B57FAC7A18AA99CEACC73ED1"


def ltc() -> str:
    dump = os.path.join(RE, "dump", "dump.cs")
    meta = os.path.join(RE, "global-metadata.dat")
    if not (os.path.isfile(dump) and os.path.isfile(meta)):
        return "skipped (run tools/re/codephil.py metadata + Il2CppDumper first)"
    import re
    m = re.search(LTC_FIELD + r" /\*Metadata offset 0x([0-9A-F]+)\*/", open(dump, encoding="utf-8").read())
    if not m:
        return "LtcData initializer not found in dump.cs"
    off = int(m.group(1), 16)
    data = open(meta, "rb").read()[off:off + 32768]
    os.makedirs(os.path.join(OUT, "textures"), exist_ok=True)
    open(os.path.join(OUT, "textures", "LTC_LUT_64x64_RGBAHalf.raw"), "wb").write(data)
    return f"LTC LUT from metadata offset {off:#x}"


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    env = UnityPy.load(SRC)
    print(f"shaders:  {shaders()} decompiled")
    print(f"compute:  {compute(env)} kernel variants (dxbc + asm)")
    print(f"textures: {textures(env)}")
    print(f"materials: {materials(env)}")
    print(f"settings: {settings(env)} pipeline objects")
    print(f"ltc: {ltc()}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
