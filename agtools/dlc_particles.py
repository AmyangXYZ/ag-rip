"""A recorded DLC sequence's particle systems as reze-design effects.

Called by dlc_reze_scene.py for each sequence; not run on its own.

What reze-design does for a GLB stage (tools/stages/unity_to_glb.particle_classes,
lib/gltf-stage.ts, lib/unity-particles.ts) is done here for the native DLC
scene, with reze-design's own code wherever it has some:

  spec       tools/stages/unity_particles.system_spec on the ParticleSystem as the
             Unity reference project (unity/ExportedProject) has it
  material   tools/stages/unity_effect_bake.live_effect (ZTong/Effect_Common) or
             tong_add_effect (Tong_jichu_Add); Tong_jichu_AB and AB_2_Mask with
             their masks unset are the Effect_Common class exactly (main x
             _Color x vertex colour, laid over at _DstBlend 10) and are written as
             one - see tong_ab_material
  WGSL       lib/unity-particles.ts particleEffectWgsl, run read-only through tsx
             (agtools/reze_particle_wgsl.mts), so the source in the scene is byte
             for byte what the app generates for that class
  emitters   ps<k>_NNN point pairs, as the GLB path writes them: the even point
             along the emitter's +Z, one game unit at its SIZE scale; the odd one
             along +X, one unit at its SHAPE scale. They are bones on one
             invisible prop, props/particle_emitters/particle_emitters.pmx
             (the engine's #points reads every visible model's bones), and its
             particle_emitters.lights.json + particles/ carry each effect's
             pictures the way a GLB stage's rig does (lib/effect-textures.ts
             registers them by the effect's WGSL when the prop loads)

Systems that share a spec and a material are one effect with several emitters
(X340's 53 candle flames are one). Emitter transforms are the recorded renderer
matrices (the ParticleSystem's GameObject), game space; PMX = (-x, y, -z) * 8.

A mesh particle (render mode 4) is not a billboard the app's generator draws:
one that holds a single still particle (X306a's hologram sphere, qiu) is
returned as a mesh prop at the particle's recorded pose instead.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import struct
import subprocess
import sys

import numpy as np
from PIL import Image

REZE = os.environ.get("REZE_DESIGN", r"D:\reze-design")
STAGES = os.path.join(REZE, "tools", "stages")
PARTICLES_TS = os.path.join(REZE, "lib", "unity-particles.ts")
WGSL_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reze_particle_wgsl.mts")

SCALE = 8.0
FLIP = np.diag([-1.0, 1.0, -1.0])
METRES_PER_UNIT = 0.64      # reze-design's game unit in metres; x 12.5 PMX/metre = 8
WORLD = METRES_PER_UNIT * 12.5
EMITTERS = "particle_emitters"
ROOT_BONE = "全ての親"
TONG_AB = ("ZTong/Tong_jichu_AB", "ZTong/Tong_jichu_AB_2_Mask")
TONG_ADD = ("ZTong/Tong_jichu_Add",)
EFFECT_COMMON = ("ZTong/Effect_Common", "ZTong/Effect_Common_VertexOffset")


def _reze_modules():
    """reze-design's stage tools, imported read-only (no .pyc written there)."""
    sys.dont_write_bytecode = True
    if STAGES not in sys.path:
        sys.path.insert(0, STAGES)
    import unity_effect_bake  # noqa: E402
    import unity_materials  # noqa: E402
    import unity_particles  # noqa: E402
    import unity_scene  # noqa: E402
    return unity_particles, unity_scene, unity_effect_bake, unity_materials


# ---------------------------------------------------------------- YAML
class YamlFile:
    """One scene or prefab: every ParticleSystem by its hierarchy path."""

    def __init__(self, path, unity_scene, unity_particles):
        self.path = path
        self.scene = unity_scene.Scene(path)
        up = unity_particles
        tf_of, father, go_name = {}, {}, {}
        for fid, (cls, body) in self.scene.docs.items():
            if cls in (4, 224):
                go = re.search(r"m_GameObject:\s*\{fileID:\s*(-?\d+)", body)
                fa = re.search(r"m_Father:\s*\{fileID:\s*(-?\d+)", body)
                if go:
                    tf_of[int(go.group(1))] = fid
                father[fid] = int(fa.group(1)) if fa else 0
            elif cls == 1:
                go_name[fid] = up_name(body)
        tf_go = {t: g for g, t in tf_of.items()}
        renderers = {}
        for fid, (cls, body) in self.scene.docs.items():
            if cls == 199:
                go = re.search(r"m_GameObject:\s*\{fileID:\s*(-?\d+)", body)
                if go:
                    renderers[int(go.group(1))] = body
        self.systems = {}
        for fid, (cls, body) in self.scene.docs.items():
            if cls != 198:
                continue
            go = int(re.search(r"m_GameObject:\s*\{fileID:\s*(-?\d+)", body).group(1))
            names, t, seen = [], tf_of.get(go), set()
            while t and t not in seen and t in tf_go:
                seen.add(t)
                names.append(go_name.get(tf_go[t], ""))
                t = father.get(t, 0)
            path = "/".join(reversed(names))
            self.systems[path] = {"go": go, "transform": tf_of.get(go), "ps": up._doc(body, "ParticleSystem"),
                                  "renderer": up._doc(renderers.get(go, ""), "ParticleSystemRenderer")}

    def find(self, recorded_path):
        hits = [p for p in self.systems if recorded_path == p or recorded_path.endswith("/" + p)]
        return self.systems[max(hits, key=len)] if hits else None


def up_name(body):
    m = re.search(r"^  m_Name:\s*(.*)$", body, re.M)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------- materials
def _png_bytes(png, max_size=1024):
    im = Image.open(png).convert("RGBA")
    if max(im.size) > max_size:
        k = max_size / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def tong_ab_material(mat, shader, png_for_guid, srgb_of, linear_colour):
    """ZTong/Tong_jichu_AB (and AB_2_Mask) as the Effect_Common class the app draws.

    The decompiled fragments (web/data/shaders/ZTong_Tong_jichu_AB*_p0_*.frag.wgsl):
      AB:        rgb = lerp(tex.rgb, luma, _Desaturate) * _Color.rgb * lerp(vc.rgb, 1, _Vertex_Color)
                 a   = luma(mask.rgb) * mask.a * tex.a * vc.a   (the vertex stage folds _Color.a in)
      AB_2_Mask: rgb = tex.rgb * _Color.rgb * vc.rgb
                 a   = mask2 * mask * tex.a * _Color.a * vc.a, uv nudged by _Tex_UVadd
      both Blend SrcAlpha OneMinusSrcAlpha.
    With the masks unset (white), single channel, desaturate and the vertex
    colour switch off and no UVadd, that is Effect_Common with _MainPow (1,1,1,0),
    no plus, no mask, _DstBlend 10: rgb = tex * _Color * vc, a = tex.a * _Color.a * vc.a,
    laid over - the same picture. Anything else is refused with the reason."""
    f, tex = mat["floats"], mat["textures"]
    why = []
    if "_Tex" not in tex:
        why.append("no _Tex")
    for slot in ("_Tex_Mask", "_Tex_Mask_2", "_Tex_UVadd"):
        if slot in tex:
            why.append(f"{slot} is set")
    for k in ("_Tex_IsSingleChannel", "_Desaturate", "_Vertex_Color", "_Color_Alpha_X", "_Tex_Rotate",
              "_Tex_U_X", "_Tex_V_Y", "_World_Mask", "_Tex_World_Mask", "_Tex_UVadd_Intensity"):
        if abs(f.get(k, 0.0)) > 1e-6:
            why.append(f"{k} = {f[k]}")
    if why:
        return None, f"{shader}: {', '.join(why)} - not the Effect_Common subset"
    st = tex["_Tex"]
    png = png_for_guid(st["guid"])
    if not png:
        return None, f"{shader}: _Tex picture not found"
    colour, alpha = linear_colour(mat, "_Color")
    effect = {
        "layers": {"main": {
            "png": base64.b64encode(_png_bytes(png)).decode("ascii"),
            "scale": [float(st["scale"][0]), float(st["scale"][1])],
            "offset": [float(st["offset"][0]), float(st["offset"][1])],
            "rotation": float(f.get("_Tex_Ang", 0.0)),
            "tiling": True,
            # Effect_Common slides (u + t*speed.y, v + t*speed.x); Tong (u + t*_Tex_U, v + t*_Tex_V)
            "speed": [float(f.get("_Tex_V", 0.0)), float(f.get("_Tex_U", 0.0))],
            "srgb": srgb_of(st["guid"]),
        }},
        "mainPow": [1.0, 1.0, 1.0, 0.0],
        "color": [float(c) for c in colour] + [float(alpha)],
        "redAlphaMain": False,
        "plusPow": [1.0, 1.0, 1.0, 0.0],
        "plusColor": [1.0, 1.0, 1.0, 1.0],
        "redAlphaPlus": False,
        "plusStrength": 0.0,
        "plusColorOn": 0.0,
        "plusAlphaOn": 0.0,
        "plusMode": 0,
        "redAlphaMask": False,
        "maskStrength": 0.0,
        "dstBlend": 10.0,
    }
    return effect, None


class Materials:
    """The reference project's materials, read and turned into effect materials
    the way reze-design's GLB converter does."""

    def __init__(self, project_root, data, scene, mods, scratch):
        _up, unity_scene, bake, umat = mods
        self.bake, self.umat = bake, umat
        self.proj = unity_scene.Project(project_root)
        self.read_material = unity_scene.read_material
        self.png_root = os.path.join(os.path.dirname(project_root), "_png_textures")
        self.recorded = {}
        for t in scene["textures"]:
            if t["files"] and t["files"][0].endswith(".png"):
                self.recorded.setdefault(t["name"], os.path.join(data, "textures", t["files"][0]))
        self.white = os.path.join(scratch, "_white.png")
        if not os.path.exists(self.white):
            os.makedirs(scratch, exist_ok=True)
            Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(self.white)
        self.cache = {}

    def material(self, guid):
        path = self.proj.path(guid)
        return self.read_material(path) if path and os.path.exists(path) else None

    def shader(self, mat):
        return self.proj.shader_name(mat["shader_guid"]) if mat else None

    def png_for_guid(self, guid):
        if guid == "__white__":
            return self.white
        asset = self.proj.path(guid) or ""
        png = self.umat.png_for(self.png_root, asset)
        if png:
            return png
        # not decoded under _png_textures (the sequence prefabs' effect textures):
        # the picture the recording took of the same texture, by name
        stem = os.path.splitext(os.path.basename(asset))[0]
        return self.recorded.get(stem)

    def srgb_of(self, guid):
        return self.bake._is_srgb(self.proj.path(guid) or "") if guid != "__white__" else True

    def effect(self, guid):
        """(material for the class, [(png bytes, srgb) | None] x 4, note) for a material guid."""
        if guid in self.cache:
            return self.cache[guid]
        mat = self.material(guid)
        shader = self.shader(mat)
        out = (None, None, f"material {guid} not in the project")
        if mat and shader in EFFECT_COMMON:
            # a slot its keywords use with no picture samples the shader's
            # default, white (Effect_Common declares every colour slot "white"):
            # X340's candle flame is white raised by _MainPow under its mask
            m = json.loads(json.dumps(mat))
            filled = []
            for slot in self.bake._used_slots({**m, "textures": {s: {} for s in ("_MainTex", "_MainPlusTex", "_MaskTex")}}):
                if slot not in m["textures"]:
                    m["textures"][slot] = {"guid": "__white__", "scale": (1.0, 1.0), "offset": (0.0, 0.0)}
                    filled.append(slot)
            eff = self.bake.live_effect(m, self.png_for_guid, always=True,
                                        asset_for_guid=lambda g: self.proj.path(g) if g != "__white__" else None)
            note = f"{', '.join(filled)} unset - the shader's white" if filled else None
            out = (eff, None, note) if eff else (None, None, f"{shader}: a picture is missing")
        elif mat and shader in TONG_ADD:
            eff = self.bake.tong_add_effect(mat, self.png_for_guid, asset_for_guid=self.proj.path)
            out = (eff, None, None) if eff else (None, None, f"{shader}: no _Tex")
        elif mat and shader in TONG_AB:
            eff, why = tong_ab_material(mat, shader, self.png_for_guid, self.srgb_of, self.bake._linear_colour)
            out = (eff, None, why)
        elif mat:
            out = (None, None, f"{shader} has no particle effect in reze-design")
        eff, _, note = out
        if eff:
            layers = eff["layers"]
            noise = eff.get("noise")
            textures = [(base64.b64decode(l["png"]), bool(l.get("srgb", True))) if l else None
                        for l in (layers.get("main"), layers.get("plus"), layers.get("mask"), noise)]
            strip = lambda d: {k: v for k, v in d.items() if k != "png"}   # noqa: E731
            material = {**{k: v for k, v in eff.items() if k not in ("layers", "noise")},
                        "layers": {k: strip(v) for k, v in layers.items()},
                        **({"noise": strip(noise)} if noise else {})}
            out = (material, textures, note)
        self.cache[guid] = (*out, mat["name"] if mat else guid)
        return self.cache[guid]


# ---------------------------------------------------------------- recording
def particle_meshes(frames, blob, rid):
    """Per frame: (positions [n,3], uvs [n,4], colours [n,4] u8, indices) or None."""
    out, cur = [], None
    for fr in frames:
        rec = fr["r"].get(rid)
        if rec is not None:
            cur = rec.get("particles") if rec.get("on") else None
        if not cur:
            out.append(None)
            continue
        off, nv, ni = cur
        p = np.frombuffer(blob, np.float32, nv * 3, off).reshape(nv, 3)
        uv = np.frombuffer(blob, np.float32, nv * 4, off + nv * 12).reshape(nv, 4)
        c = np.frombuffer(blob, np.uint8, nv * 4, off + nv * 28).reshape(nv, 4)
        idx = np.frombuffer(blob, np.uint32, ni, off + nv * 32)
        out.append((p, uv, c, idx))
    return out


def runs(mask):
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i))
            start = None
    return out


# ---------------------------------------------------------------- emitters
def emitter_frame(M, spec, own_scale):
    """(origin, z, x, size_k, shape_k) in game space, as unity_to_glb.particle_classes."""
    A = M[:3, :3]
    lossy = np.linalg.norm(A, axis=0)
    mode = spec["scalingMode"]
    own = [abs(v) for v in own_scale]
    size_k = lossy.mean() if mode == 0 else (sum(own) / 3 if mode == 1 else 1.0)
    shape_k = sum(own) / 3 if mode == 1 else lossy.mean()
    z = A[:, 2] / (lossy[2] or 1.0)
    x = A[:, 0] / (lossy[0] or 1.0)
    return M[:3, 3].copy(), z, x, float(size_k), float(shape_k)


def _text(s):
    b = s.encode("utf-16-le")
    return struct.pack("<i", len(b)) + b


def write_points_pmx(name, points, comment):
    """An invisible model of point bones: points = [(name, head3, tail offset3)].
    One degenerate triangle under an alpha-0 material, so the model loads as any
    prop does and draws nothing."""
    out = io.BytesIO()
    w = out.write
    w(b"PMX " + struct.pack("<f", 2.0))
    w(bytes([8, 0, 0, 4, 4, 4, 4, 4, 4]))
    for s in (name, name, comment, comment):
        w(_text(s))
    w(struct.pack("<i", 3))
    for _ in range(3):
        w(struct.pack("<3f3f2f", 0, 0, 0, 0, 1, 0, 0, 0))
        w(bytes([0]) + struct.pack("<i", 0) + struct.pack("<f", 0.0))
    w(struct.pack("<i", 3) + struct.pack("<3i", 0, 1, 2))
    w(struct.pack("<i", 0))  # textures
    w(struct.pack("<i", 1))
    w(_text("emitters") + _text("emitters"))
    w(struct.pack("<4f3ff3f", 1, 1, 1, 0, 0, 0, 0, 5.0, 0, 0, 0))
    w(bytes([0]))
    w(struct.pack("<4ff", 0, 0, 0, 1, 0.0))
    w(struct.pack("<ii", -1, -1))
    w(bytes([0, 1, 0]))
    w(_text(""))
    w(struct.pack("<i", 3))
    bones = [(ROOT_BONE, (0.0, 0.0, 0.0), -1, (0.0, 0.0, 0.0))] + [(n, h, 0, t) for n, h, t in points]
    w(struct.pack("<i", len(bones)))
    for bn, pos, parent, tail in bones:
        w(_text(bn) + _text(bn))
        w(struct.pack("<3fii", *pos, parent, 0))
        w(struct.pack("<H", 0x0002 | 0x0004 | 0x0008 | 0x0010))  # tail as an offset
        w(struct.pack("<3f", *tail))
    w(struct.pack("<i", 0))  # morphs
    w(struct.pack("<i", 2))
    w(_text("Root") + _text("Root") + bytes([1]) + struct.pack("<i", 1) + bytes([0]) + struct.pack("<i", 0))
    w(_text("表情") + _text("Exp") + bytes([1]) + struct.pack("<i", 0))
    w(struct.pack("<ii", 0, 0))
    return out.getvalue()


def generate_wgsl(classes):
    """particleEffectWgsl for each class, by reze-design's own file."""
    items = [{"cls": c, "world": WORLD} for c in classes]
    if not items:
        return []
    tsx = "tsx.cmd" if os.name == "nt" else "tsx"
    r = subprocess.run([tsx, WGSL_SCRIPT, PARTICLES_TS], input=json.dumps(items).encode("utf-8"),
                       capture_output=True, check=False)
    if r.returncode != 0:
        raise SystemExit(f"tsx {WGSL_SCRIPT} failed:\n{r.stderr.decode('utf-8', 'replace')}")
    return json.loads(r.stdout.decode("utf-8"))


def class_label(path):
    """'X340/sc_effectFog/lazhu/huomiao01 (23)' -> 'lazhu/huomiao01'."""
    parts = [re.sub(r"\s*\(\d+\)$", "", p) for p in path.split("/")]
    return "/".join(parts[-2:])


# ---------------------------------------------------------------- one sequence
def build(ctx, frames, blob, particle_renderers, tracks):
    """Effects for one sequence. ctx: data, scene, dlc (folder), seq names, scratch.

    Returns {"files": {zip path: bytes}, "model": scene model entry | None,
    "effects": [SceneEffect], "classes": [summary], "mesh_props": [(renderer, track)],
    "skipped": [(path, why)], "verify": [lines]}."""
    mods = ctx.get("_mods") or _reze_modules()
    ctx["_mods"] = mods
    up, unity_scene, _bake, _umat = mods
    project = os.path.join(ctx["dlc"], "unity", "ExportedProject")
    if "_yaml" not in ctx:
        cfg = json.load(open(os.path.join(project, "ag_dlc.json"), encoding="utf-8"))
        files = [cfg["stageScene"]] + [s["prefab"] for s in cfg["sequences"]] + [cfg["model"]]
        ctx["_yaml"] = [YamlFile(os.path.join(project, f), unity_scene, up) for f in files
                        if os.path.exists(os.path.join(project, f))]
        ctx["_mats"] = Materials(project, ctx["data"], ctx["scene"], mods, ctx["scratch"])
    yamls, mats = ctx["_yaml"], ctx["_mats"]
    n = len(frames)
    classes, order, skipped, mesh_props = {}, [], [], []
    for r in particle_renderers:
        tr = tracks[r["id"]]
        if not tr["on"].any():
            continue
        found = next((y.find(r["path"]) for y in yamls if y.find(r["path"])), None)
        if not found:
            skipped.append((r["path"], "not found in the reference project's scenes/prefabs"))
            continue
        renderer = found["renderer"]
        spec = up.system_spec(found["ps"], renderer)
        meshes = particle_meshes(frames, blob, r["id"])
        alive = np.array([m is not None and len(m[3]) > 0 for m in meshes])
        on_idx = np.nonzero(tr["on"])[0]
        Ms = tr["M"][on_idx]
        if np.abs(Ms - Ms[0]).max() > 1e-5:
            print(f"  warning: {r['path']} moves during the sequence - its emitter stands at its first pose")
        M = Ms[0]
        mode = int(renderer.get("m_RenderMode", 0))
        if mode == 4:
            # a mesh particle: a prop when it is one still particle
            still = spec["max"] == 1 and spec["speed"]["hi"] == 0 and spec["speed"]["lo"] == 0
            first = next((m for m in meshes if m is not None), None)
            if still and first is not None and r.get("mesh"):
                mesh_props.append((r, first))
            else:
                skipped.append((r["path"], "mesh particles (render mode 4) the effect generator does not draw"))
            continue
        if mode not in (0, 1):
            skipped.append((r["path"], f"render mode {mode} the effect generator does not draw"))
            continue
        guids = [m.get("guid") for m in (renderer.get("m_Materials") or []) if isinstance(m, dict) and m.get("guid")]
        if not guids:
            skipped.append((r["path"], "no material"))
            continue
        material, textures, note, mat_name = mats.effect(guids[0])
        if not material:
            skipped.append((r["path"], note))
            continue
        own = found["transform"] and found_scale(yamls, found)
        frame = emitter_frame(M, spec, own or (1.0, 1.0, 1.0))
        key = json.dumps([spec, guids[0]], sort_keys=True)
        if key not in classes:
            classes[key] = {"label": f"{class_label(r['path'])} ({mat_name})", "spec": spec, "material": material,
                            "textures": textures, "note": note, "emitters": [], "alive": np.zeros(n, bool),
                            "on": np.zeros(n, bool)}
            order.append(key)
        c = classes[key]
        c["emitters"].append({"path": r["path"], "rid": r["id"], "frame": frame, "M": M, "meshes": meshes})
        c["alive"] |= alive
        c["on"] |= tr["on"]

    # ---- the classes, their points, their pictures
    files, points, out_classes, summaries = {}, [], [], []
    taken = set()
    for k, key in enumerate(order):
        c = classes[key]
        prefix = f"ps{k}_"
        i = 0
        for e in c["emitters"]:
            o, z, x, size_k, shape_k = e["frame"]
            head = SCALE * FLIP @ o
            for axis, length in ((z, size_k), (x, shape_k)):
                i += 1
                points.append((f"{prefix}{i:03d}", tuple(head), tuple(SCALE * FLIP @ (axis * length))))
        name = c["label"]
        while name in taken:
            name += "'"
        taken.add(name)
        cls = {"name": name, "prefix": prefix, "emitters": len(c["emitters"]), "metresPerUnit": METRES_PER_UNIT,
               "spec": c["spec"], "material": c["material"]}
        out_classes.append(cls)
        tex_entries = []
        for slot, t in enumerate(c["textures"]):
            if not t:
                tex_entries.append(None)
                continue
            rel = f"particles/{prefix}{slot}.png"
            files[rel] = t[0]
            tex_entries.append({"path": rel, "srgb": t[1]})
        c["tex_entries"] = tex_entries
    wgsl = generate_wgsl(out_classes)
    effects, rig = [], []
    for k, key in enumerate(order):
        c, cls = classes[key], out_classes[k]
        rig.append({"name": cls["name"], "wgsl": wgsl[k], "textures": c["tex_entries"], "class": cls})
        # WHEN: an always-on system none; a looping one while its object is on; a
        # one-shot (looping off) while the recording has its particles alive -
        # the app's pool respawns, the game's burst does not
        alive = c["alive"] if not c["spec"]["looping"] else c["on"]
        win = None if alive.all() else [{"start": int(a), "end": int(b - 1)} for a, b in runs(alive)]
        effect = {"source": {"name": cls["name"], "wgsl": wgsl[k]}}
        if win:
            effect["window"] = win
        effects.append(effect)
        summaries.append({"name": cls["name"], "prefix": cls["prefix"], "emitters": cls["emitters"], "window": win,
                          "note": c["note"], "lines": len(wgsl[k].splitlines()), "paths": [e["path"] for e in c["emitters"]]})
    model = None
    if order:
        files[f"{EMITTERS}.pmx"] = write_points_pmx(EMITTERS, points, "Aether Gazer particle emitters (ag-rip): ps<k>_NNN point pairs")
        files[f"{EMITTERS}.lights.json"] = json.dumps({"particles": rig}, ensure_ascii=False, indent=1).encode("utf-8")
        model = {"model": f"props/{EMITTERS}/{EMITTERS}.pmx", "animation": None, "morph": None, "prop": True,
                 "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1}}
    verify = [verify_class(classes[key], out_classes[k], frames) for k, key in enumerate(order)]
    return {"files": files, "model": model, "effects": effects, "classes": summaries, "mesh_props": mesh_props,
            "skipped": skipped, "verify": verify}


def found_scale(yamls, found):
    for y in yamls:
        if found in y.systems.values():
            t = y.scene._transform(found["transform"])
            return t["scale"] if t else None
    return None


# ---------------------------------------------------------------- ground truth
def mean_curve(c):
    if c.get("curve"):
        return float(np.mean([(a + b) / 2 for a, b in zip(c["curve"], c.get("curveLo") or c["curve"])]))
    return (c["lo"] + c["hi"]) / 2


def verify_class(c, cls, frames):
    """The recording against the spec and the emitters: alive counts, and where
    the particles are in each emitter's own shape space."""
    s = c["spec"]
    life = mean_curve(s["lifetime"])
    rate = mean_curve(s["emission"]["rate"]) if s["emission"]["on"] else 0.0
    burst = sum(b["count"] * max(1, b["cycles"]) for b in s["emission"]["bursts"])
    expect = min(s["max"], rate * life + (burst if not s["looping"] or rate * life < 1 else 0))
    meanLife = (s["lifetime"]["lo"] + s["lifetime"]["hi"]) / 2
    # lib/unity-particles.ts' perEmitter (JS Math.round; a rate random between two constants at their mean)
    r = s["emission"]["rate"]
    gen_rate = (r["lo"] + r["hi"]) / 2 if r.get("mode") == 3 else r["hi"]
    pool = max(1, min(s["max"], math.floor(((gen_rate * meanLife + burst) if s["emission"]["on"] else 1) + 0.5) or 1))
    counts, dist, spread, ratio, lives = [], [], [], [], []
    rate_s = 1.0 / 30
    for e in c["emitters"]:
        o, z, x, size_k, shape_k = e["frame"]
        A = e["M"][:3, :3]
        y = np.cross(z, x)
        basis = np.stack([x / np.linalg.norm(x), y / np.linalg.norm(y), z], axis=1)
        live = [m for m in e["meshes"] if m is not None and len(m[3])]
        cnt = [len(m[3]) // 6 for m in e["meshes"] if m is not None]
        if not s["looping"]:
            cnt = [len(m[3]) // 6 for m in live]
        counts.extend(cnt)
        for p, _uv, _c, idx in live:
            # a billboard is four corners; its centre is where the particle is
            centres = p.reshape(-1, 4, 3).mean(axis=1) if len(p) % 4 == 0 else p
            local = (centres - o) @ basis / max(shape_k, 1e-9)
            dist.append(np.linalg.norm(centres.mean(axis=0) - o))
            spread.append(np.abs(local).max(axis=0))
        # LIFETIMES, where one particle can be followed: a flipbook's frame runs
        # up over a life and starts again at a rebirth
        if s["uv"] and s["max"] == 1:
            tx, ty = s["uv"]["tiles"]
            seq = []
            for m in e["meshes"]:
                if m is None or len(m[0]) != 4:
                    seq.append(None)
                    continue
                u0, v0 = m[1][:, 0].min(), m[1][:, 1].min()
                seq.append((ty - 1 - int(round(v0 * ty))) * tx + int(round(u0 * tx)))
            births = [i for i in range(1, len(seq)) if seq[i] is not None and seq[i - 1] is not None and seq[i] < seq[i - 1]]
            lives += [(b - a) * rate_s for a, b in zip(births, births[1:])]
        if not s["looping"]:
            for a, b in runs([m is not None and len(m[3]) > 0 for m in e["meshes"]]):
                lives.append((b - a) * rate_s)
        sh = s["shape"]
        if sh["on"] and sh["type"] in (5, 15, 16) and spread:
            half = np.array(sh["scale"]) / 2
            ratio.append(np.array(spread).max(axis=0) / np.where(half > 1e-9, half, 1))
    line = (f"{cls['name']}: {len(c['emitters'])} emitters; alive per emitter recorded {np.mean(counts) if counts else 0:.2f} "
            f"(max {max(counts) if counts else 0}), spec rate x life {expect:.2f} (rate {rate:.3g}/s, life {life:.3g}s, max {s['max']}), "
            f"generator pool {pool}")
    if lives:
        lo, hi = s["lifetime"]["lo"], s["lifetime"]["hi"]
        inside = np.mean([(lo - 0.05) <= v <= (hi + 0.05) for v in lives])
        line += (f"; recorded lifetimes median {np.median(lives):.2f}s, p10-p90 {np.percentile(lives, 10):.2f}-{np.percentile(lives, 90):.2f}s, "
                 f"{inside:.0%} within spec {lo:.3g}..{hi:.3g}s (n={len(lives)})")
    elif counts and rate > 0 and rate * life < s["max"] * 0.95:
        line += f"; lifetime from alive/rate {np.mean(counts) / rate:.2f}s (spec mean {life:.3g}s)"
    if dist:
        line += f"; particle centroid to emitter median {np.median(dist):.4f} game units (max {np.max(dist):.4f})"
    if ratio:
        r = np.max(ratio, axis=0)
        line += f"; farthest particle / box half-extent (x,y,z) {r[0]:.2f} {r[1]:.2f} {r[2]:.2f}"
    elif spread:
        r = np.array(spread).max(axis=0)
        line += f"; farthest particle in shape units (x,y,z) {r[0]:.3f} {r[1]:.3f} {r[2]:.3f}"
    return line
