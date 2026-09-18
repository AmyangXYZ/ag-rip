#!/usr/bin/env python3
r"""
Decompile the game's shaders (D3D11 bytecode) to ShaderLab + HLSL -> AG_shaders/.

    python agtools/decompile_shaders.py            shader.ys + allshaders.ys
    python agtools/decompile_shaders.py <bundle>   any other bundle holding shaders

Uses tools/USCSandbox (nesrak1/USCSandbox, AssetRipper's shader converter run
headless), built with tools/dotnet and patched for this game:
  - Unity 2021.3+/2022 split each platform's blob into LZ4 segments (index in
    segment 0, data in later ones); upstream read one segment from offset 0
  - render state bound to material properties is written as [_Prop]; upstream
    wrote the literal 0 (Blend Zero Zero / ZWrite Off / Cull Off on every pass)
  - the game's own cbuffer members (SimLighting, SimFog, UnityPerMaterial, ...)
    are declared; upstream wrote them as comments, leaving them undeclared
  - one failing shader no longer aborts a batch
  - textures are named by their texture register, not the (shared) sampler slot
  - sample_c/sample_c_lz -> UNITY_SAMPLE_SHADOW with the reference value (upstream
    dropped the compare); compare-sampled textures declared UNITY_DECLARE_SHADOWMAP
  - dynamically indexed matrix arrays (cascade matrices) keep their runtime index
  - utof / ftou / sample_b are emitted (upstream silently dropped them); anything
    still unhandled is written as a visible "UNHANDLED" comment
  - float immediates: only denormal/NaN patterns are read as integer bits (upstream
    also did it for FLT_MIN, turning every normalize's max(x, 1.2e-38) into
    max(x, 8388608.0) and zeroing all normals); literals print round-trip exact
  - vertex+pixel declarations of one variant are guarded against redefinition;
    Unity's own built-ins (tools/uscs/builtin_names.txt) are not redeclared
  - optional keywords are multi_compile, not shader_feature: the game's pipeline
    toggles them globally (MAIN_LIGHT_SHADOWS, sim_DYN_FOG_LINEAR, ...)
  - bit-exact mode (UShaderFunctionToHLSL.BitExact.cs, default; AG_BITEXACT=0 for the
    old output): DXBC registers are typeless, so temps are uint4 holding raw bits and
    each instruction reads its operands as asfloat / asint / uint and writes raw bits.
    Upstream kept temps as float values and did integer/bitwise work through value
    conversions (uint(-1.0) == 0), which broke every bit mask (Forward+ light lists).
    Also: DXBC log is log2 (upstream printed the natural log, so every pow() was wrong),
    ineg is an integer negate, ushr a logical shift, integer immediates keep their bits
  - fix_cbuffers.py afterwards: the Forward+ bin/tile tables become real cbuffers again
The full diff is tools/uscsandbox-aethergazer.patch.
Every keyword variant is kept, each as an #if block, so this is the lossless
record of the game's shading; stage_unity.py --real-shaders installs from here.
Rebuild the tool after editing it:
    tools/dotnet/dotnet build tools/USCSandbox/USCSandbox/USCSandbox.csproj -c Release -o tools/uscs
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(HERE)
DOTNET = os.path.join(TOP, "tools", "dotnet", "dotnet.exe")
USCS = os.path.join(TOP, "tools", "uscs")
ROOT = os.path.join(TOP, "AetherGazer", "AetherGazer_Data", "StreamingAssets", "Windows_restored")
OUT = os.path.join(TOP, "AG_shaders")
VERSION = "2022.3.62f3"
DEFAULT = ["shader.ys", "allshaders.ys"]


def run(*args: str) -> str:
    r = subprocess.run([DOTNET, "USCSandbox.dll", *args], cwd=USCS,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout + r.stderr


def decompile(bundle: str) -> tuple[int, list[str]]:
    listing = run(bundle).splitlines()
    cabs = [l.strip() for l in listing[1:] if l.strip().startswith("CAB-") and "." not in l]
    ok, failed = 0, []
    for cab in cabs:
        text = run(bundle, cab, "--all", "--version", VERSION)
        ok += text.count(" decompiled")
        failed += [l.split(" FAILED")[0].strip() for l in text.splitlines() if " FAILED" in l]
    return ok, failed


def main() -> int:
    bundles = sys.argv[1:] or [os.path.join(ROOT, b) for b in DEFAULT]
    out_tmp = os.path.join(USCS, "out")
    shutil.rmtree(out_tmp, ignore_errors=True)
    total, fails = 0, []
    for b in bundles:
        ok, failed = decompile(os.path.abspath(b))
        total += ok
        fails += failed
        print(f"{os.path.basename(b)}: {ok} decompiled" + (f", failed: {failed}" if failed else ""))
    if os.path.isdir(out_tmp):
        shutil.rmtree(OUT, ignore_errors=True)
        shutil.copytree(out_tmp, OUT)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fix_cbuffers     # Forward+ bin/tile tables back into real cbuffers
        fix_cbuffers.fix_dir(OUT)
    print(f"{total} shader(s) -> {OUT}" + (f"; {len(fails)} failed" if fails else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
