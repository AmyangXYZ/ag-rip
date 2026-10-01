"""A character skin's game materials as a native look for a PMX that keeps the game's UVs.

A fan-extracted PMX of an Aether Gazer character usually carries the game's own textures
on the game's own UVs, re-split into materials of its author's choosing. Every per-model
map the game shades with (RMO, ramp mask, normal, rim mask, face SDF) then lines up with
it too, so the game's materials can be put back on it exactly: the reze engine's native
host (reze-engine src/unity/) draws them with the game's translated shaders. That is the
REFERENCE the AG look pack's graphs are checked against.

    python agtools/char_native_look.py <skin> <model.pmx> <out dir>

Writes <out>/look.json (shader variants inline, textures by file name) and <out>/tex/.
Each PMX material is paired with the game material whose UV footprint it overlaps most,
within the game materials sharing its texture.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import warnings

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import bundle_deps  # noqa: E402
import dlc_web  # noqa: E402
import shader_wgsl  # noqa: E402

warnings.filterwarnings("ignore")
import UnityPy  # noqa: E402
import UnityPy.helpers.MeshHelper as MeshHelper  # noqa: E402

UnityPy.config.FALLBACK_UNITY_VERSION = bundle_deps.UNITY_VERSION
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHADERS = os.path.join(HERE, "AG_shaders")
DXC = dlc_web.DXC
# The keywords the game's pipeline has on while a character stands in its home scene
# (recorded from 107402's: AGDlcRecord frame keywords).
HOME_KEYWORDS = {"SIM_MAIN_LIGHT", "sim_FOG_LINEAR", "PLUS_LIGHTING", "MAIN_LIGHT_SHADOWS", "UNITY_HDR_ON"}
GRID = 512


# ---------------------------------------------------------------- PMX
def read_pmx(path: str) -> dict:
    b = open(path, "rb").read()
    p = 0

    def take(n):
        nonlocal p
        v = b[p:p + n]
        p += n
        return v

    assert take(4) == b"PMX "
    take(4)
    g = list(take(take(1)[0]))
    enc = "utf-16-le" if g[0] == 0 else "utf-8"
    addv, vsz, tsz, msz, bsz = g[1:6]

    def text():
        n = struct.unpack("<i", take(4))[0]
        return take(n).decode(enc, "replace")

    def index(sz, unsigned=False):
        f = {1: "b", 2: "h", 4: "i"}[sz]
        return struct.unpack("<" + (f.upper() if unsigned and sz < 4 else f), take(sz))[0]

    for _ in range(4):
        text()
    nv = struct.unpack("<i", take(4))[0]
    uv = np.zeros((nv, 2), np.float32)
    for i in range(nv):
        take(24)
        uv[i] = struct.unpack("<2f", take(8))
        take(16 * addv)
        w = take(1)[0]
        take({0: bsz, 1: bsz * 2 + 4, 2: bsz * 4 + 16, 3: bsz * 2 + 40, 4: bsz * 4 + 16}[w])
        take(4)
    ni = struct.unpack("<i", take(4))[0]
    fmt = {1: "B", 2: "H", 4: "I"}[vsz]
    idx = np.frombuffer(take(ni * vsz), dtype={"B": np.uint8, "H": np.uint16, "I": np.uint32}[fmt]).astype(np.int64)
    textures = [text() for _ in range(struct.unpack("<i", take(4))[0])]
    mats, at = [], 0
    for _ in range(struct.unpack("<i", take(4))[0]):
        name = text()
        text()
        take(16 + 12 + 4 + 12 + 1 + 16 + 4)
        ti = index(tsz)
        index(tsz)
        take(1)
        if take(1)[0]:
            take(1)
        else:
            index(tsz)
        text()
        count = struct.unpack("<i", take(4))[0]
        mats.append({"name": name, "texture": textures[ti] if 0 <= ti < len(textures) else None,
                     "tris": idx[at:at + count].reshape(-1, 3)})
        at += count
    return {"uv": uv, "materials": mats}


# ---------------------------------------------------------------- UV coverage
def coverage(uv: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Texels of a GRID² image that the triangles cover (uv in [0,1], v up)."""
    img = Image.new("L", (GRID, GRID), 0)
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for t in tris:
        pts = [(float(uv[k, 0]) * GRID, (1.0 - float(uv[k, 1])) * GRID) for k in t]
        d.polygon(pts, fill=1)
    return np.asarray(img, bool)


# ---------------------------------------------------------------- the game's side
def shader_file(name: str) -> str:
    for dp, _, fns in os.walk(SHADERS):
        for fn in fns:
            if fn.endswith(".shader"):
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8", errors="replace") as f:
                    if f.readline().strip().startswith(f'Shader "{name}"'):
                        return path
    raise SystemExit(f"no source for {name}")


def color_properties(path: str) -> set[str]:
    """Properties a shader declares as Color: Unity uploads those gamma -> linear."""
    out = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'\s*(?:\[[^\]]*\]\s*)*(\w+)\s*\("[^"]*",\s*Color\)', line)
            if m:
                out.add(m.group(1))
            if line.strip().startswith("SubShader"):
                break
    return out


def to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def game_materials(skin: str, tex_dir: str) -> tuple[list[dict], dict]:
    """The skin's home model: each renderer's materials with their UV footprints."""
    bundle = rf"comchar\char\{skin}ui_main.ys"
    deps = [d for d in bundle_deps.closure([bundle])[0] if "shader" not in d.lower()]
    env = UnityPy.load(*[os.path.join(bundle_deps.ROOT, d) for d in deps])
    objs = {(o.assets_file.name.lower(), o.path_id): o for o in env.objects}

    def resolve(o, ptr):
        if ptr.m_PathID == 0:
            return None
        cab = o.assets_file.name.lower() if ptr.m_FileID == 0 else re.split(r"[\\/]", o.assets_file.externals[ptr.m_FileID - 1].path)[-1].lower()
        return objs.get((cab, ptr.m_PathID))

    shader_names = {}
    for b in [v for v in set(json.load(open(bundle_deps.CACHE, encoding="utf-8")).values()) if "shader" in v.lower()]:
        try:
            senv = UnityPy.load(os.path.join(bundle_deps.ROOT, b))
        except Exception:
            continue
        for o in senv.objects:
            if o.type.name == "Shader":
                try:
                    shader_names[(o.assets_file.name.lower(), o.path_id)] = o.read().m_ParsedForm.m_Name
                except Exception:
                    pass

    os.makedirs(tex_dir, exist_ok=True)
    textures: dict[str, dict] = {}
    mats: dict[str, dict] = {}
    for o in env.objects:
        if o.type.name != "SkinnedMeshRenderer":
            continue
        if skin not in (o.assets_file.parent.name if o.assets_file.parent else "") + o.assets_file.name:
            continue
        r = o.read()
        mesh_obj = resolve(o, r.m_Mesh)
        if mesh_obj is None:
            continue
        h = MeshHelper.MeshHandler(mesh_obj.read())
        h.process()
        uv = np.asarray(h.m_UV0, np.float32)
        subs = h.get_triangles()
        for si, mp in enumerate(r.m_Materials):
            mo = resolve(o, mp)
            if mo is None or si >= len(subs):
                continue
            m = mo.read()
            so = resolve(mo, m.m_Shader)
            key = (so.assets_file.name.lower(), so.path_id) if so else None
            shader = shader_names.get(key) or (so.read().m_ParsedForm.m_Name if so else "?")
            entry = mats.get(m.m_Name)
            if entry is None:
                slots = {}
                for name, te in m.m_SavedProperties.m_TexEnvs:
                    to = resolve(mo, te.m_Texture)
                    if to is None or to.type.name != "Texture2D":
                        continue
                    t = to.read()
                    if t.m_Name not in textures:
                        t.image.save(os.path.join(tex_dir, t.m_Name + ".png"))
                        textures[t.m_Name] = {"file": f"tex/{t.m_Name}.png", "srgb": bool(getattr(t, "m_ColorSpace", 1))}
                    slots[name] = t.m_Name
                kw = getattr(m, "m_ValidKeywords", None) or getattr(m, "m_ShaderKeywords", "") or []
                if isinstance(kw, str):
                    kw = kw.split()
                entry = mats[m.m_Name] = {
                    "name": m.m_Name,
                    "shader": shader,
                    "keywords": list(kw),
                    "queue": m.m_CustomRenderQueue,
                    "floats": {k: v for k, v in m.m_SavedProperties.m_Floats},
                    "colors": {k: [v.r, v.g, v.b, v.a] for k, v in m.m_SavedProperties.m_Colors},
                    "textures": slots,
                    "cover": np.zeros((GRID, GRID), bool),
                }
            entry["cover"] |= coverage(uv, np.asarray(subs[si]).reshape(-1, 3))
    return list(mats.values()), textures


# ---------------------------------------------------------------- shaders
def translate_passes(mat: dict, out_dir: str, cache: dict) -> list[dict]:
    path = shader_file(mat["shader"])
    if path not in cache:
        cache[path] = {"info": dlc_web.pass_info(path), "colors": color_properties(path)}
    info = cache[path]["info"]
    props = {**mat["floats"], **{k: v for k, v in mat["colors"].items()}}
    passes = []
    for i, p in enumerate(info):
        if p["lightMode"] not in dlc_web.DRAWN or p["lightMode"] == "SHADOWCASTER":
            continue
        want = dlc_web.want_keywords(p["groups"], set(mat["keywords"]) | HOME_KEYWORDS)
        name = re.sub(r"\W+", "_", mat["shader"]) + f"_p{i}_" + "_".join(sorted(want))[:60]
        key = (path, i, tuple(sorted(want)))
        if key not in cache:
            try:
                shader_wgsl.translate(path, want, i, os.path.join(out_dir, name), DXC)
                cache[key] = name
            except SystemExit as e:
                print(f"  {mat['name']} pass {i}: {str(e)[:200]}")
                cache[key] = None
        if cache[key]:
            passes.append({"lightMode": p["lightMode"], "shader": cache[key], "state": dlc_web.resolve(p["state"], props)})
    return passes


# ---------------------------------------------------------------- main
def main() -> int:
    skin, pmx_path, out = sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3])
    os.makedirs(out, exist_ok=True)
    shader_dir = os.path.join(out, "shaders")
    os.makedirs(shader_dir, exist_ok=True)
    mats, textures = game_materials(skin, os.path.join(out, "tex"))
    print(f"{len(mats)} game materials, {len(textures)} textures")
    pmx = read_pmx(pmx_path)
    pmx_dir = os.path.dirname(pmx_path)

    # which game base map each PMX texture is: the same image
    def thumb(p):
        return np.asarray(Image.open(p).convert("RGB").resize((128, 128)), float)
    bases = {}
    for m in mats:
        b = m["textures"].get("_BaseMap") or m["textures"].get("_MainTex")
        if b:
            bases.setdefault(b, thumb(os.path.join(out, textures[b]["file"])))
    tex_of = {}
    for t in {m["texture"] for m in pmx["materials"] if m["texture"]}:
        a = thumb(os.path.join(pmx_dir, t))
        best = min(bases, key=lambda b: np.abs(a - bases[b]).mean())
        if np.abs(a - bases[best]).mean() < 3:
            tex_of[t] = best

    cache: dict = {}
    look = {"shaders": [], "textures": {}, "materials": []}
    used_shaders = set()
    for pm in pmx["materials"]:
        base = tex_of.get(pm["texture"])
        if not base:
            print(f"  {pm['name']}: texture {pm['texture']} is not the game's — left to its graph")
            continue
        cover = coverage(pmx["uv"] * np.array([1, -1]) + np.array([0, 1]), pm["tris"])
        cands = [m for m in mats if (m["textures"].get("_BaseMap") or m["textures"].get("_MainTex")) == base]
        best = max(cands, key=lambda m: (m["cover"] & cover).sum())
        share = (best["cover"] & cover).sum() / max(cover.sum(), 1)
        print(f"  {pm['name']} -> {best['name']} ({share:.0%} of its footprint)")
        if "passes" not in best:
            best["passes"] = translate_passes(best, shader_dir, cache)
        colors = color_properties(shader_file(best["shader"]))
        values = {k: v for k, v in best["floats"].items()}
        for k, v in best["colors"].items():
            values[k] = [to_linear(c) for c in v[:3]] + [v[3]] if k in colors else v
        slots = {k: v for k, v in best["textures"].items()}
        main_slot = "_BaseMap" if "_BaseMap" in slots else "_MainTex"
        tex_map = {k: ("@diffuse" if k == main_slot else v) for k, v in slots.items()}
        for k, v in slots.items():
            if k != main_slot:
                look["textures"][v] = textures[v]
        look["materials"].append({
            "materials": [pm["name"]],
            "game": best["name"],
            "queue": best["queue"] if best["queue"] > 0 else 2000,
            "passes": best["passes"],
            "values": values,
            "textures": tex_map,
        })
        used_shaders.update(p["shader"] for p in best["passes"])
    for name in sorted(used_shaders):
        look["shaders"].append({
            "name": name,
            "vert": open(os.path.join(shader_dir, name + ".vert.wgsl"), encoding="utf-8").read(),
            "frag": open(os.path.join(shader_dir, name + ".frag.wgsl"), encoding="utf-8").read(),
            "info": json.load(open(os.path.join(shader_dir, name + ".json"), encoding="utf-8")),
        })
    json.dump(look, open(os.path.join(out, "look.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"look: {len(look['materials'])} materials, {len(look['shaders'])} shader variants, {len(look['textures'])} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
