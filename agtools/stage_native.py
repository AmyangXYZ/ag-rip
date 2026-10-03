"""One DLC's stage as a reze native-stage package: the game's own meshes, textures,
materials and translated shaders, for reze-engine's native stage (src/unity/stage.ts).

    python agtools/stage_native.py AG_dlc_play/<skin>-<角色>-<皮肤名> [--out DIR]

Reads the recording (web/data: AGDlcRecord's scene and frames, dlc_web.py's
shaders) and keeps the stage's part of it: every renderer not under the character's
root, at its state when the first sequence starts. Writes the folder
<dlc>/reze/<dlc>/ (pictures as WebP), loaded in reze-design like any stage
folder (Add stage):

    stage.json   meshes, textures, materials (passes, values, texture slots),
                 renderers (matrix, layers, property block), the stage's lights,
                 and `settings` - the pipeline globals the stage sets and the engine
                 does not compute (fog, tint, ambient SH, environment cube, ...)
    meshes/ textures/ shaders/   the files those name, copied as recorded

Nothing camera-, time- or shadow-derived goes into `settings`: the engine computes
those per frame from its own camera, clock, lights and cascades (src/unity/globals.ts).
"""
import argparse, json, os, shutil, sys

# Globals the engine computes every frame from its own state (unityFrameGlobals) or
# that belong to a render target rather than to the stage.
LIVE_PREFIXES = (
    "_WorldSpaceCameraPos", "unity_Matrix", "glstate_", "_NonJittered", "_ProjectionParams",
    "_ScreenParams", "_ScaledScreenParams", "_ZBufferParams", "unity_OrthoParams", "_Time",
    "_SinTime", "_CosTime", "unity_DeltaTime", "sim_Time", "sim_TowardMatrix",
    "SimMainLight", "_MainLight", "sim_ShadowLightDirection", "sim_ShadowBias",
    "_AdditionalLights", "SimAdditionalLight", "_PlusLighting", "_Cascade",
    "sim_Character", "_OutlineMaxOffsetMultiplier", "SimPipelineBloom", "_ColorGraddingLut",
    "_OpaqueTexture", "_DepthIntermediate", "_CameraDepthTexture", "identity_matrix",
)


def is_live(name):
    return name.startswith(LIVE_PREFIXES)


def first_states(frames_path):
    """Each renderer's record at the start of the sequence (records are deltas)."""
    states, first = {}, None
    with open(frames_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            fr = json.loads(line)
            if first is None:
                first = fr
            for rid, rec in fr["r"].items():
                cur = states.setdefault(rid, {})
                for k, v in rec.items():
                    cur.setdefault(k, v)
    return first, states


def lights_of(g, shadow=None):
    """The stage's lights as the pipeline held them (game units, linear colour).
    `shadow`: the frame's recorded cascades, for the main light's bias."""
    d = g.get("SimMainLightDir") or [0, 1, 0, 0]
    main = {
        # the way the light travels
        "direction": [-d[0], -d[1], -d[2]],
        "color": (g.get("_MainLightColor") or [1, 1, 1, 1])[:3],
        "shadow": (g.get("_MainLightShadowParams") or [1])[0],
    }
    # what the pipeline packs for it, as recorded (LightingFeatures.SetupLightings):
    # SimMainLightColor / _MainLightColor linear with the intensity in w, and
    # SimMainLightColorNoInt, the colour before it - the stage's shaders read all three
    if g.get("_MainLightColor"):
        main["unity"] = {
            "color": g["_MainLightColor"][:4],
            "simColor": (g.get("SimMainLightColor") or g["_MainLightColor"])[:4],
            "simColorNoInt": (g.get("SimMainLightColorNoInt") or g["_MainLightColor"])[:4],
        }
    # the Light's shadowBias / shadowNormalBias, in texels: ShadowUtils.GetShadowBias
    # sets _ShadowBias = (texel x bias, -texel x normalBias), texel = 2 / proj.m00 /
    # the tile's resolution, which the recorded cascade inverts
    cas = [c for c in ((shadow or {}).get("cascades") or []) if c.get("bias") and c.get("proj") and c.get("rect")]
    if cas:
        c = cas[0]
        texel = 2 / c["proj"][0] / c["rect"][2]
        main["shadowBias"] = round(c["bias"][0] / texel, 4)
        main["shadowNormalBias"] = round(-c["bias"][1] / texel, 4)
    out = []
    pos = g.get("_AdditionalLightsPosition") or []
    col = g.get("_AdditionalLightsColor") or []
    att = g.get("_AdditionalLightsAttenuation") or []
    spot = g.get("_AdditionalLightsSpotDir") or []
    types = g.get("_AdditionalLightsLightTypes") or []
    for i, p in enumerate(pos):
        c = col[i] if i < len(col) else [0, 0, 0, 0]
        if not (c[0] or c[1] or c[2]):
            continue
        a = att[i]
        rng = (1 / a[0]) ** 0.5 if a[0] > 0 else 0
        light = {"position": p[:3], "range": rng, "color": c[:3], "type": "spot" if types[i] == 0 else "point",
                 # what the pipeline packs beside them (AGSimPipeline.SetupPlusLighting), as
                 # recorded: spot direction with w = 1 / shape radius (the sphere light's size the
                 # LTC term reads), colour alpha, Extra, WorldToLight, light type
                 "unity": {
                     "spotDir": spot[i], "colorW": c[3],
                     "extra": (g.get("_AdditionalLightsExtra") or [[0, 0, 0, 0]] * (i + 1))[i],
                     "worldToLight": (g.get("_AdditionalLightsWorldToLights") or [None] * (i + 1))[i],
                     "lightType": types[i],
                     "simSpotDir": (g.get("SimAdditionalLightSpotDir") or [None] * (i + 1))[i] if i < 16 else None,
                     "simColorW": ((g.get("SimAdditionalLightColor") or [[0, 0, 0, 1]] * (i + 1))[i] or [0, 0, 0, 1])[3] if i < 16 else 1,
                 }}
        if light["type"] == "spot":
            # att.z = 1 / (cosInner - cosOuter), att.w = -cosOuter * att.z
            inv = a[2] or 1
            cos_outer = -a[3] / inv
            light.update(aim=spot[i][:3], cosOuter=cos_outer, cosInner=cos_outer + 1 / inv)
        out.append(light)
    return {"main": main, "additional": out}


def view_of(data, seq):
    """How the game showed it: its Final pass (Hidden/SimPipeline/Final) as the
    engine's "soft" transform - (1 - e^(-exposure x))^contrast, here
    2.5 and 1.4 - and its bloom's threshold, knee and scatter. No grading LUT is read:
    a stage whose Final samples one would need it carried (not X306a)."""
    with open(os.path.join(data, f"{seq}.frames.jsonl"), encoding="utf-8") as f:
        for line in f:
            fr = json.loads(line)
            post = fr.get("post")
            if not post:
                continue
            fin = post.get("final") or {}
            if fin.get("tonemapping", 1) and (abs(fin.get("exposure", 2.5) - 2.5) > 1e-3 or abs(fin.get("contrast", 1.4) - 1.4) > 1e-3):
                print(f"  note: Final exposure {fin.get('exposure')} / contrast {fin.get('contrast')} - the engine's curve is 2.5 / 1.4")
            if post.get("lut"):
                print("  note: this Final samples a grading LUT, not carried")
            p = (post.get("bloom") or {}).get("_Params") or [0.77, 100, 0.7, 0.35]
            return {
                "transform": "soft",
                "exposure": 0,
                "bloom": {"enabled": bool(post.get("bloomEnabled", 1)), "threshold": p[2], "knee": p[3] / p[2] if p[2] else 0.5,
                          "scatter": p[0]},
            }
    return None


def is_cast(r, mats, root=None):
    """The character herself: drawn with the game's character shaders (her
    body, face, eyes, hair - a prop she handles, like the cup, is not).

    With `root` (cast_root: her model's folder, found by her skeleton) the test
    is by skeleton and path too: a rigid mesh is never her, even under a
    character shader (128402's weapon01_1 in her hand, 102201's weapon01..03);
    a skinned renderer under her root is her; one elsewhere is another
    character's part (128402's commander arm, 36 bones) when it has more than
    one bone - left out like her, a prop cannot bend - and a prop when it has
    one (128402's wedding ring)."""
    if not any(m and mats[m]["shader"].startswith("SimPipeline/Character/") for m in r["materials"]):
        return False
    if root is None:
        return True
    if r["kind"] != "skinned":
        return False
    if r["path"].startswith(root + "/"):
        return True
    return len(r.get("bones") or []) > 1


def cast_root(scene, mats):
    """Her model's folder: the parent of the character-shaded skinned renderer
    with the most bones (her body), or None."""
    body = max((r for r in scene["renderers"] if r["kind"] == "skinned" and is_cast(r, mats)),
               key=lambda r: len(r.get("bones") or []), default=None)
    return body["path"].rsplit("/", 1)[0] if body else None


def unbatch(data, mesh, start, count):
    """A statically batched renderer's own part of its batch's combined mesh: the
    `count` submeshes from `start` (AGDlcRecord's subMeshStart), with only the
    vertices they use. Unity draws such a renderer's material i on submesh
    start + i; the stage draws material i on submesh i, so each one gets its own
    mesh. Returns (mesh entry, .bin bytes); the vertices stay in batch-root space
    (the renderer's recorded matrix)."""
    import numpy as np
    raw = open(os.path.join(data, "meshes", f"{mesh['id']}.bin"), "rb").read()
    n = mesh["vertexCount"]
    subs = mesh["submeshes"][start:start + count]
    idx = [np.frombuffer(raw, np.uint32, sm["count"], sm["offset"]) for sm in subs]
    used = np.unique(np.concatenate(idx)) if idx else np.zeros(0, np.uint32)
    remap = np.zeros(n, np.uint32)
    remap[used] = np.arange(len(used), dtype=np.uint32)
    out, streams, off = [], {}, 0
    for sem, (o, dim) in mesh["streams"].items():
        a = np.frombuffer(raw, np.float32, n * dim, o).reshape(n, dim)[used]
        streams[sem] = [off, dim]
        out.append(a.tobytes())
        off += a.nbytes
    submeshes = []
    for sm, ix in zip(subs, idx):
        b = remap[ix].astype(np.uint32).tobytes()
        submeshes.append({**sm, "offset": off, "count": len(ix)})
        out.append(b)
        off += len(b)
    p = np.frombuffer(raw, np.float32, n * 3, mesh["streams"]["POSITION"][0]).reshape(n, 3)[used]
    lo, hi = (p.min(0), p.max(0)) if len(p) else (np.zeros(3), np.zeros(3))
    entry = {**mesh, "id": f"{mesh['id']}_s{start}", "name": f"{mesh['name']} [{start}]",
             "vertexCount": int(len(used)), "streams": streams, "submeshes": submeshes,
             "bounds": {"center": ((lo + hi) / 2).tolist(), "size": (hi - lo).tolist()}}
    return entry, b"".join(out)


def export_stage(dlc, out=None):
    """Write the stage folder; returns its path."""
    data = os.path.join(dlc, "web", "data")
    index = json.load(open(os.path.join(data, "index.json"), encoding="utf-8"))
    scene = json.load(open(os.path.join(data, "scene.json"), encoding="utf-8"))
    variants = json.load(open(os.path.join(data, "variants.json"), encoding="utf-8"))
    seq = index["sequences"][0]["name"]
    first, states = first_states(os.path.join(data, f"{seq}.frames.jsonl"))
    name = os.path.basename(os.path.normpath(dlc))
    # the folder a stage is in reze-design, named as every export is (<id>-<角色>-<皮肤名>)
    out = out or os.path.join(dlc, "reze", name)

    # the stage: everything not under the character's root, drawn as meshes
    # (a sequence's own objects become props: agtools/dlc_reze_scene.py)
    renderers = []
    mesh_by_id = {m["id"]: m for m in scene["meshes"]}
    unbatched = {}  # mesh id -> (entry, bytes): statically batched renderers' own parts
    for r in scene["renderers"]:
        # a renderer with no mesh draws nothing (X348's flower colliders)
        if r["path"].startswith("Char/") or r["kind"] != "mesh" or "mesh" not in r:
            continue
        st = states.get(r["id"])
        if not st or not st.get("on") or "m" not in st:
            continue
        mpb = st.get("mpb") or {}
        mat_list = st.get("mats") or r["materials"]
        mesh_id = r["mesh"]
        if "subMeshStart" in r:
            entry, b = unbatch(data, mesh_by_id[mesh_id], r["subMeshStart"], len(mat_list))
            unbatched.setdefault(entry["id"], (entry, b))
            mesh_id = entry["id"]
        renderers.append({
            "id": r["id"], "path": r["path"], "mesh": mesh_id,
            "materials": mat_list,
            "matrix": st["m"], "layers": st.get("layerBits", 1),
            "castShadows": r.get("shadowCasting", 1), "receiveShadows": r.get("receiveShadows", 1),
            "values": {k: v for k, v in mpb.items() if not (isinstance(v, dict) and "tex" in v)},
            "textures": {k: v["tex"] for k, v in mpb.items() if isinstance(v, dict) and v.get("tex")},
        })

    tex_info = {t["id"]: t for t in scene["textures"]}
    used_tex, used_mesh, used_shaders = set(), set(), set()
    mats_by_id = {m["id"]: m for m in scene["materials"]}
    defaults = variants.get("_textureDefaults", {})
    materials = []
    mat_ids = {m for r in renderers for m in r["materials"] if m}
    for mid in sorted(mat_ids, key=lambda s: int(s[3:])):
        m = mats_by_id[mid]
        values, textures = {}, {}
        for k, v in m["props"].items():
            if isinstance(v, dict) and "tex" in v:
                # the slot, and the _ST / _TexelSize / _HDR the shader reads beside it
                values[f"{k}_ST"] = v.get("st") or [1, 1, 0, 0]
                values[f"{k}_HDR"] = [1, 1, 0, 0]
                t = tex_info.get(v["tex"]) if v.get("tex") else None
                values[f"{k}_TexelSize"] = [1 / t["width"], 1 / t["height"], t["width"], t["height"]] if t else [1, 1, 1, 1]
                if t:
                    textures[k] = v["tex"]
                    used_tex.add(v["tex"])
            else:
                values[k] = v
        passes = []
        want = m["shader"].replace("/", "_")
        stale = [p["variant"] for p in variants.get(mid, []) if not p["variant"].startswith(want)]
        if stale:
            raise SystemExit(f"{mid} {m['name']} is {m['shader']} but variants.json gives {stale}: "
                             f"the recording is newer than the translation - rerun agtools/dlc_web.py")
        for p in variants.get(mid, []):
            if p["lightMode"] not in ("FORWARDBASE", "ALWAYS", "SHADOWCASTER"):
                continue
            passes.append({"lightMode": p["lightMode"], "shader": p["variant"], "state": p.get("state") or {}})
            used_shaders.add(p["variant"])
        materials.append({
            "id": mid, "name": m["name"], "shader": m["shader"], "queue": m["queue"],
            "keywords": m.get("keywords", []), "passes": passes, "values": values, "textures": textures,
            # what an empty slot reads (white / black / grey / bump), per the shader
            "defaults": defaults.get(m["shader"], {}),
        })
    for r in renderers:
        used_mesh.add(r["mesh"])
        used_tex.update(r["textures"].values())

    g = first["globals"]
    settings = {k: v for k, v in g.items() if not is_live(k)}
    global_textures = {k: v for k, v in (scene.get("globalTextures") or {}).items()}
    for k, t in global_textures.items():
        used_tex.add(t)
        ti = tex_info[t]
        settings[f"{k}_TexelSize"] = [1 / ti["width"], 1 / ti["height"], ti["width"], ti["height"]]

    meshes = [m for m in scene["meshes"] if m["id"] in used_mesh] + \
        [unbatched[k][0] for k in sorted(unbatched) if k in used_mesh]
    textures = [tex_info[t] for t in sorted(used_tex, key=lambda s: int(s[1:]))]
    pkg = {
        "version": 1,
        "kind": "unity-native-stage",
        "name": index.get("stage") or os.path.basename(dlc),
        "source": index.get("title"),
        # engine (PMX) units per game unit
        "scale": 8,
        "keywords": first.get("keywords", []),
        "shaders": sorted(used_shaders),
        "meshes": meshes,
        "textures": textures,
        "materials": materials,
        "renderers": renderers,
        "lights": lights_of(g, first.get("shadow")),
        "view": view_of(data, index["sequences"][0]["name"]),
        "settings": settings,
        "globalTextures": global_textures,
    }

    # The folder: stage.json and what it names. Pictures as WebP - colour
    # (sRGB) ones lossy at q90, data ones (normals, property maps: read as
    # numbers) lossless; raw HDR and cube faces as recorded.
    import io
    from PIL import Image
    texs = []
    for t in textures:
        t = dict(t)
        t["files"] = [f[:-4] + ".webp" if f.endswith(".png") else f for f in t["files"]]
        texs.append(t)
    pkg["textures"] = texs
    for sub in ("meshes", "textures", "shaders"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    for m in meshes:
        dst = os.path.join(out, "meshes", f"{m['id']}.bin")
        if m["id"] in unbatched:
            with open(dst, "wb") as f:
                f.write(unbatched[m["id"]][1])
        else:
            shutil.copyfile(os.path.join(data, "meshes", f"{m['id']}.bin"), dst)
    for t0, t in zip(textures, texs):
        for src, dst in zip(t0["files"], t["files"]):
            path = os.path.join(data, "textures", src)
            if dst.endswith(".webp"):
                im = Image.open(path)
                im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im
                # exact: libwebp otherwise zeroes the RGB under alpha 0, and a
                # game texture's alpha is data, not coverage - PBR/Standard's
                # _PropertyTex keeps metallic/roughness/AO in RGB and its emission
                # mask in A (0 on 99.9% of Props_object_337_M)
                if t["srgb"]:
                    im.save(os.path.join(out, "textures", dst), "WEBP", quality=90, method=6, exact=True)
                else:
                    im.save(os.path.join(out, "textures", dst), "WEBP", lossless=True, method=6, exact=True)
            else:
                shutil.copyfile(path, os.path.join(out, "textures", dst))
    for s in used_shaders:
        for ext in (".vert.wgsl", ".frag.wgsl", ".json"):
            shutil.copyfile(os.path.join(data, "shaders", s + ext), os.path.join(out, "shaders", s + ext))
    with open(os.path.join(out, "stage.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(pkg, f, ensure_ascii=False, separators=(",", ":"))
    size = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(out) for fn in fns)
    print(f"{out}: {len(renderers)} renderers, {len(materials)} materials, {len(meshes)} meshes, "
          f"{len(textures)} textures, {len(used_shaders)} shaders, {size / 1e6:.1f} MB")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dlc")
    ap.add_argument("--out")
    a = ap.parse_args()
    export_stage(a.dlc, a.out)


if __name__ == "__main__":
    sys.exit(main())
