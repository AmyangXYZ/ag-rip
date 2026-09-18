#!/usr/bin/env python3
r"""
Replace AssetRipper's decoded PNG textures with the game's exact texture data.

AssetRipper decodes every texture to PNG and Unity re-encodes it on import. That
is lossy twice over, and for HDR textures it is destructive: BC6H/half/float data
(reflection probes, lightmaps, HDR skies - authored as .exr) is clipped to 8-bit
0..1, so glass, lacquer and polished floors lose their highlights.

Here each texture becomes a native Unity asset (.asset, NativeFormatImporter)
holding the bundle's own bytes - same GPU format, same mip chain, same colour
space, same sampler settings - under the *same GUID*, so every material keeps
pointing at it. The decoded PNGs move to <project>/_png_textures/ for porting.

    python agtools/native_textures.py <project dir> <bundle> [<bundle> ...]
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import warnings

import UnityPy

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f3"
warnings.simplefilter("ignore")

CLASS = {"Texture2D": 28, "Cubemap": 89, "Texture3D": 117, "Texture2DArray": 187}
EDITOR_HEADER = ("  m_ObjectHideFlags: 0\n  m_CorrespondingSourceObject: {fileID: 0}\n"
                 "  m_PrefabInstance: {fileID: 0}\n  m_PrefabAsset: {fileID: 0}\n")


def yval(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(v) if v != int(v) else str(int(v))
    if v is None:
        return ""
    return str(v)


def emit(k, v, ind: str, out: list[str]) -> None:
    if isinstance(v, dict):
        if set(v) == {"m_FileID", "m_PathID"}:
            out.append(f"{ind}{k}: {{fileID: 0}}")
            return
        out.append(f"{ind}{k}:")
        if k == "m_TextureSettings" or k == "m_StreamData":
            out.append(f"{ind}  serializedVersion: 2")
        for kk, vv in v.items():
            emit(kk, vv, ind + "  ", out)
    elif isinstance(v, (bytes, bytearray)):
        out.append(f"{ind}{k}: " + (v.hex() if v else ""))
    elif isinstance(v, list):
        if not v:
            out.append(f"{ind}{k}: []" if k != "m_PlatformBlob" else f"{ind}{k}: ")
        elif all(isinstance(x, dict) and set(x) == {"m_FileID", "m_PathID"} for x in v):
            out.append(f"{ind}{k}:")
            out += [f"{ind}- {{fileID: 0}}"] * len(v)
        elif all(isinstance(x, int) for x in v):
            out.append(f"{ind}{k}: " + bytes(x & 0xFF for x in v).hex())
        else:
            out.append(f"{ind}{k}:")
            for x in v:
                sub: list[str] = []
                for kk, vv in (x.items() if isinstance(x, dict) else [("value", x)]):
                    emit(kk, vv, ind + "  ", sub)
                if sub:
                    sub[0] = ind + "- " + sub[0].lstrip()
                out += sub
    else:
        out.append(f"{ind}{k}: {yval(v)}")


def to_yaml(kind: str, tree: dict, image: bytes) -> str:
    cid = CLASS[kind]
    out = ["%YAML 1.1", "%TAG !u! tag:unity3d.com,2011:", f"--- !u!{cid} &{cid * 100000}", f"{kind}:"]
    body = EDITOR_HEADER.rstrip("\n").split("\n")
    out += body
    for k, v in tree.items():
        if k == "m_Name":
            out.append(f"  m_Name: {v}")
            out += ["  m_ImageContentsHash:", "    serializedVersion: 2",
                    "    Hash: 00000000000000000000000000000000"]
            continue
        if k in ("m_Width", "m_ColorSpace") and "  serializedVersion: 4" not in out:
            # Texture2D/Cubemap: before m_Width; Texture3D/Array: before m_ColorSpace
            if (kind in ("Texture2D", "Cubemap") and k == "m_Width") or \
               (kind in ("Texture3D", "Texture2DArray") and k == "m_ColorSpace"):
                out.append("  serializedVersion: 4")
        if k == "image data":
            out.append(f"  image data: {len(image)}")
            out.append(f"  _typelessdata: {image.hex()}")
            continue
        if k == "m_StreamData":
            out += ["  m_StreamData:", "    serializedVersion: 2", "    offset: 0", "    size: 0", "    path: "]
            continue
        if k == "m_TextureDimension" and kind == "Cubemap":
            v = 4   # the bundle stores 2; a Cubemap asset must say Cube
        if k == "m_IsReadable":
            v = True   # the data is inline; lets tools read it back
        emit(k, v, "  ", out)
    return "\n".join(out) + "\n"


def project_files(project: str) -> dict[str, list[str]]:
    """stem (lower) -> exported image paths."""
    out: dict[str, list[str]] = {}
    for dp, _, fns in os.walk(os.path.join(project, "ExportedProject", "Assets")):
        for fn in fns:
            if fn.lower().endswith((".png", ".exr", ".hdr", ".tga", ".jpg")):
                out.setdefault(os.path.splitext(fn)[0].lower(), []).append(os.path.join(dp, fn))
    return out


def convert(project: str, bundles: list[str]) -> tuple[int, int, list[str]]:
    files = project_files(project)
    assets = os.path.join(project, "ExportedProject", "Assets")
    pngdir = os.path.join(project, "_png_textures")
    done, hdr, skipped = 0, 0, []
    guid_map: set[str] = set()
    for b in bundles:
        try:
            env = UnityPy.load(b)
        except Exception:                                             # noqa: BLE001
            continue
        by_pid = {}
        for path, obj in env.container.items():
            by_pid[obj.path_id] = path
        for o in env.objects:
            kind = o.type.name
            if kind not in CLASS:
                continue
            try:
                tree = o.read_typetree()
                data = o.read()
                image = bytes(data.image_data or b"")
                sd = tree.get("m_StreamData") or {}
                if not image and sd.get("size"):
                    # pixel data lives in the bundle's .resS stream
                    from UnityPy.helpers.ResourceReader import get_resource_data
                    image = bytes(get_resource_data(sd["path"], o.assets_file, sd["offset"], sd["size"]))
            except Exception as exc:                                  # noqa: BLE001
                skipped.append(f"{kind}:{o.path_id}:{type(exc).__name__}")
                continue
            name = tree.get("m_Name", "")
            cands = files.get(name.lower(), [])
            cont = by_pid.get(o.path_id)
            if cont and len(cands) > 1:
                want = os.path.splitext(cont)[0].replace("/", os.sep).lower()
                cands = [c for c in cands if os.path.splitext(os.path.relpath(c, os.path.dirname(assets)))[0].lower() == want] or cands[:1]
            if len(cands) != 1:
                if not cands:
                    continue           # not exported into this project (unused)
                cands = cands[:1]
            src = cands[0]
            meta = src + ".meta"
            if not os.path.isfile(meta) or not image:
                skipped.append(name)
                continue
            guid = re.search(r"guid: (\w+)", open(meta, encoding="utf-8").read()).group(1)
            dst = os.path.splitext(src)[0] + ".asset"
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(to_yaml(kind, tree, image))
            with open(dst + ".meta", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(f"fileFormatVersion: 2\nguid: {guid}\nNativeFormatImporter:\n"
                         f"  externalObjects: {{}}\n  mainObjectFileID: {CLASS[kind] * 100000}\n"
                         f"  userData: \n  assetBundleName: \n  assetBundleVariant: \n")
            rel = os.path.relpath(src, assets)
            os.makedirs(os.path.dirname(os.path.join(pngdir, rel)), exist_ok=True)
            shutil.move(src, os.path.join(pngdir, rel))
            os.remove(meta)
            guid_map.add(guid)
            done += 1
            if tree.get("m_TextureFormat") in (15, 16, 17, 18, 19, 20, 22, 24):
                hdr += 1
    retype_references(assets, guid_map)
    return done, hdr, skipped


def retype_references(assets: str, guids: set[str]) -> None:
    """Imported-asset references are type 3, native-asset references type 2."""
    if not guids:
        return
    pat = re.compile(r"(guid: (\w{32}), type: )3")
    for dp, _, fns in os.walk(assets):
        for fn in fns:
            if not fn.endswith((".mat", ".unity", ".prefab", ".asset", ".controller", ".anim")):
                continue
            p = os.path.join(dp, fn)
            try:
                text = open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            new = pat.sub(lambda m: m.group(1) + ("2" if m.group(2) in guids else "3"), text)
            if new != text:
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    done, hdr, skipped = convert(sys.argv[1], sys.argv[2:])
    print(f"{done} texture(s) native ({hdr} HDR)" + (f"; skipped {len(skipped)}: {skipped[:5]}" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
