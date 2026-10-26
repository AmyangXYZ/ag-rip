#!/usr/bin/env python3
r"""A game skin as an MMD model: the skin's own mesh, weights, faces and physics chains on a
standard MMD rig taken from a reference PMX of the same character.

    python agtools/skin_pmx.py 119401 --template "D:/reze-design/【深空之眼】悼亡之蝶·海拉/海拉3.0.pmx"
        -> D:/reze-design/Models/<skin>-<角色>-<皮肤名>/<skin>-<角色>-<皮肤名>.pmx + tex/

Why a template
--------------
A PMX is only useful if MMD motions drive it, and they key a fixed set of bones (センター,
グルーブ, 上半身, 足ＩＫ, 腕捩, D bones...) with IK, append rotations and display frames that
a game Biped does not have. The fan PMX of the character's base skin carries exactly that
rig, and it was made from the game model: its joints sit within ~0.15 PMX units of the
game skeleton at the scene scale. So its standard bones are kept (names, hierarchy, flags,
IK, append, frames), each moved onto the skin's own joint, and everything else comes from
the game: mesh, UVs, weights, facial blendshapes and the skin's own chains (hat, bow,
shawl, skirt, hair), which get MMD rigid bodies and joints of their own. The template's
body colliders are kept for those chains to hit.

What maps where
---------------
  game (Blender world, metres/100)  ->  PMX = 8 game units: (x, z, y) * 800, mirrored, so
                                        triangle winding is flipped to match the template
  Bip001 / Bone_Spine* ...          ->  BONES: weights onto the MMD bone that moves them
  Bone_*_P1 chains                  ->  extra bones + one rigid each (root follows its bone)
  Eye_*, EB_*, Mouth_*, Pupil_*     ->  every blendshape under its game name, plus the MMD
                                        standard names (まばたき, あ, 困る...) where one matches
Materials keep the game's base maps on the game's UVs, so the skin's native look
(agtools/char_native_look.py) lines up with this PMX as it is.

Runs with system Python (UnityPy reads the game materials); re-launches itself in Blender
for the FBX import and the PMX write (mmd_tools).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_ROOT = r"D:\reze-design\Models"
SCALE = 800.0              # Blender world (game unit / 100) -> PMX, at the scene scale of 8

# game material -> (PMX name, outline on, double sided, keeps alpha)
MATERIALS = {
    "face01": ("脸", True, False, False),
    "eyebrow01": ("眉毛", False, False, False),
    "pupil01": ("眼睛", False, False, False),
    "Pupil_highlight01": ("眼睛高光", False, False, True),
    "hair01": ("头发", True, False, False),
    "hair01_bangs": ("刘海", True, False, False),
    "body01": ("身体", True, True, False),
    "body03": ("衣裙", True, False, False),
    "body04": ("饰品", True, False, False),
    "body02": ("披风衣", True, True, True),
}


# ------------------------------------------------------------------ system python side
def skin_name(skin: str) -> str:
    sys.path.insert(0, HERE)
    import stage_glbs
    for row in stage_glbs.table_rows():
        if row["skin"] == skin:
            return f"{skin}-{row['char']}-{row['skinName']}"
    return skin


def find_blender() -> str:
    c = sorted(glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                      "Blender Foundation", "Blender *", "blender.exe")))
    return c[-1] if c else ""


def model_fbx(skin: str) -> str:
    cid = skin[:4]
    for stem in (f"{skin}ui_tpose", f"{skin}_tpose"):
        hits = glob.glob(os.path.join(ROOT, "AG_fbx", cid, stem, "**", f"{stem}.fbx"), recursive=True)
        if hits:
            return hits[0]
    sys.exit(f"no {skin}ui_tpose / {skin}_tpose FBX under AG_fbx/{cid} (python ag.py rig {cid})")


def materials(skin: str, out_dir: str) -> dict:
    """Each game material's base map, written to <out>/tex: opaque materials get an RGB copy
    (body01_Base carries the cape's transparency in its alpha, which MMD would draw on every
    opaque part too), alpha-blended ones the RGBA original."""
    import char_native_look
    from PIL import Image
    raw = os.path.join(out_dir, "_game_tex")
    mats, _ = char_native_look.game_materials(skin, raw)
    os.makedirs(os.path.join(out_dir, "tex"), exist_ok=True)
    info = {}
    for m in mats:
        if m["name"] not in MATERIALS:
            continue
        name, edge, double, alpha = MATERIALS[m["name"]]
        tex = m["textures"].get("_BaseMap") or m["textures"].get("_MainTex") or m["textures"].get("_MaskTex")
        im = Image.open(os.path.join(raw, f"{tex}.png"))
        file = f"tex/{tex}.png" if alpha else f"tex/{tex}_rgb.png"
        (im.convert("RGBA") if alpha else im.convert("RGB")).save(os.path.join(out_dir, file))
        width = m["floats"].get("_OutlineWidth", 0.0)
        info[m["name"]] = {"name": name, "texture": file, "edge": edge and width > 0, "edgeSize": round(width / 2.5, 3),
                           "double": double or m["floats"].get("_Cull", 2.0) == 0.0, "alpha": alpha,
                           "queue": m["queue"]}
    shutil.rmtree(raw, ignore_errors=True)
    return info


def main() -> int:
    if "--blender" in sys.argv:
        return run_in_blender(sys.argv[sys.argv.index("--") + 1])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skin")
    ap.add_argument("--template", required=True, help="the character's MMD model (rig, frames, body colliders)")
    ap.add_argument("--out", help=f"output folder (default {OUT_ROOT}\\<name>)")
    a = ap.parse_args()
    name = skin_name(a.skin)
    out = a.out or os.path.join(OUT_ROOT, name)
    os.makedirs(out, exist_ok=True)
    print(f"{name}: game materials")
    mats = materials(a.skin, out)
    payload = {"skin": a.skin, "name": name, "fbx": model_fbx(a.skin), "template": os.path.abspath(a.template),
               "out": os.path.join(out, f"{name}.pmx"), "materials": mats}
    pj = os.path.join(out, "_payload.json")
    json.dump(payload, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    blender = find_blender()
    if not blender:
        sys.exit("Blender not found")
    print(f"{name}: building in {os.path.basename(os.path.dirname(blender))}")
    r = subprocess.run([blender, "-b", "--factory-startup", "--python", os.path.abspath(__file__), "--", pj, "--blender"])
    os.remove(pj)
    return r.returncode


# ------------------------------------------------------------------ Blender side
LR = (("L", "左"), ("R", "右"))
FINGERS = ((1, "人指"), (2, "中指"), (3, "薬指"), (4, "小指"))


def joint_map() -> dict[str, str]:
    """Game joint -> the template bone standing on it (positions)."""
    m = {"Bip001 Pelvis": "下半身", "Bip001 Spine": "上半身", "Bip001 Spine1": "上半身2",
         "Bip001 Neck": "首", "Bip001 Head": "頭"}
    for s, j in LR:
        m.update({f"Bip001 {s} Clavicle": f"{j}肩", f"Bip001 {s} UpperArm": f"{j}腕", f"Bip001 {s} Forearm": f"{j}ひじ",
                  f"Bip001 {s} Hand": f"{j}手首", f"Bip001 {s} Thigh": f"{j}足", f"Bip001 {s} Calf": f"{j}ひざ",
                  f"Bip001 {s} Foot": f"{j}足首", f"Bip001 {s} Toe0": f"{j}足先EX", f"Bone_{s}_Eye": f"{j}目",
                  f"Bip001 {s} Finger0": f"{j}親指０", f"Bip001 {s} Finger01": f"{j}親指１", f"Bip001 {s} Finger02": f"{j}親指２"})
        for f, n in FINGERS:
            for k in range(3):
                m[f"Bip001 {s} Finger{f}{'' if k == 0 else k}"] = f"{j}{n}{'１２３'[k]}"
    return m


def weight_map() -> dict[str, str]:
    """Game deform bone -> the MMD bone its vertices follow. The game skins the spine and
    legs to helper copies (Bone_Spine*, Bone_*_Thigh/Calf), not to the Biped; legs go to
    the D bones, which carry the leg through 腰キャンセル as MMD models do."""
    m = {"Bip001 Pelvis": "下半身", "Bone_Spine": "上半身", "Bone_Spine1": "上半身2", "Bone_Spine2": "上半身2",
         "Bip001 Spine": "上半身", "Bip001 Spine1": "上半身2", "Bip001 Spine2": "上半身2",
         "Bip001 Neck": "首", "Bip001 Head": "頭"}
    for s, j in LR:
        m.update({f"Bip001 {s} Clavicle": f"{j}肩", f"Bip001 {s} UpperArm": f"{j}腕", f"Bip001 {s} Forearm": f"{j}ひじ",
                  f"Bip001 {s} Hand": f"{j}手首", f"Bone_{s}_Thigh": f"{j}足D", f"Bip001 {s} Thigh": f"{j}足D",
                  f"Bone_{s}_Calf": f"{j}ひざD", f"Bip001 {s} Calf": f"{j}ひざD", f"Bip001 {s} Foot": f"{j}足首D",
                  f"Bip001 {s} Toe0": f"{j}足先EX", f"Bone_{s}_Eye": f"{j}目",
                  f"Bip001 {s} Finger0": f"{j}親指０", f"Bip001 {s} Finger01": f"{j}親指１", f"Bip001 {s} Finger02": f"{j}親指２"})
        for f, n in FINGERS:
            for k in range(3):
                m[f"Bip001 {s} Finger{f}{'' if k == 0 else k}"] = f"{j}{n}{'１２３'[k]}"
    return m


# game bones that become nothing: the Biped root, the weapon and its socket (the umbrella
# is a separate prop), and Bone_*_Knee, which is rebuilt as a half-way knee (below)
DROP = re.compile(r"^(root|Bip001|Bip001 Prop1|Bip001 [LR] (Thigh|Calf)|Bip001 Spine2?|Bone_umbrella.*)$")
TWISTS = {"Bone_{s}_UpArmTwist01": "腕", "Bone_{s}_UpArmTwist02": "腕", "Bip001 {s} ForeTwist": "手", "Bone_{s}_HandFix": "手"}

# MMD standard morphs from the game's blendshapes: (name, panel, game keys summed)
STANDARD_MORPHS = [
    ("まばたき", 2, ["Eye_Close"]), ("ウィンク", 2, ["Eye_Wink_L"]), ("ウィンク右", 2, ["Eye_Wink_R"]),
    ("じと目", 2, ["Eye_Jitom"]), ("ｷﾘｯ", 2, ["Eye_Angry"]), ("びっくり", 2, ["Eye_Open_R", "Eye_Open_L"]),
    ("瞳小", 2, ["Pupil_Odorok1_R", "Pupil_Odorok1_L"]),
    ("困る", 1, ["EB_Komari_R", "EB_Komari_L"]), ("怒り", 1, ["EB_Angry_R", "EB_Angry_L"]),
    ("にこり", 1, ["EB_Relax_R", "EB_Relax_L"]), ("上", 1, ["EB_Up_R", "EB_Up_L"]), ("下", 1, ["EB_Down_R", "EB_Down_L"]),
    ("あ", 3, ["Mouth_a"]), ("い", 3, ["Mouth_i"]), ("う", 3, ["Mouth_u"]), ("え", 3, ["Mouth_e"]), ("お", 3, ["Mouth_o"]),
    ("あ２", 3, ["Mouth_A"]), ("ワ", 3, ["Mouth_Laugh"]), ("∧", 3, ["Mouth_Unhappy1"]),
    ("口角上げ", 3, ["Z_Zuijiao_UP_R", "Z_Zuijiao_UP_L"]), ("口上", 3, ["Mouth_Offest_Up"]), ("口下", 3, ["Mouth_Offest_Down"]),
]

# physics chain kinds by bone name: (collision group, capsule radius per length, min, max,
# joint rotation limit, rotation spring)
CHAINS = [
    (re.compile(r"breast"), 4, 0.0, 0.35, 0.35, 0.12, 120.0),
    (re.compile(r"hair"), 1, 0.22, 0.08, 0.30, 0.30, 0.0),
    (re.compile(r"hat"), 3, 0.30, 0.10, 0.45, 0.20, 80.0),
    (re.compile(r"bow"), 2, 0.25, 0.08, 0.35, 0.35, 10.0),
    (re.compile(r"(skirt|shawl)"), 2, 0.25, 0.10, 0.40, 0.40, 0.0),
]
BODY_COLLIDERS = {"上半身", "上半身2", "首", "頭", "下半身", "右腕", "左腕", "右ひじ", "左ひじ", "右手首", "左手首",
                  "右足", "左足", "右ひざ", "左ひざ"}


def run_in_blender(payload_path: str) -> int:
    import addon_utils
    import bpy
    import numpy as np
    from mathutils import Matrix

    P = json.load(open(payload_path, encoding="utf-8"))
    addon_utils.enable("bl_ext.blender_org.mmd_tools")
    from bl_ext.blender_org.mmd_tools.core import pmx
    sys.path.insert(0, HERE)
    from blender_fbx_fixes import tolerate_foreign_skin

    def to_pmx(v):
        v = np.asarray(v, float)
        return np.stack([v[..., 0], v[..., 2], v[..., 1]], -1) * SCALE

    # ---- game model
    bpy.ops.wm.read_factory_settings(use_empty=True)
    tolerate_foreign_skin()
    bpy.ops.import_scene.fbx(filepath=P["fbx"])
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    gb = {b.name: b for b in arm.data.bones}
    ghead = {n: to_pmx(arm.matrix_world @ b.head_local) for n, b in gb.items()}
    gorder = [b.name for b in arm.data.bones]   # parents first

    tpl = pmx.load(P["template"])
    T = {b.name: i for i, b in enumerate(tpl.bones)}

    def facing(co, nrm, fs):
        """+1 when faces wind counter-clockwise about their vertex normals, -1 when clockwise."""
        e = np.cross(co[fs[:, 1]] - co[fs[:, 0]], co[fs[:, 2]] - co[fs[:, 0]])
        return np.sign(np.sum(np.einsum("ij,ij->i", e, nrm[fs[:, 0]])))

    tsign = facing(np.array([v.co for v in tpl.vertices]), np.array([v.normal for v in tpl.vertices]), np.array(tpl.faces))

    # ---- standard bones: the template's up to the last fingertip, minus props
    last = T["右小指先"]
    std = [i for i in range(last + 1) if tpl.bones[i].name not in ("メガネ",)]
    tpos = {tpl.bones[i].name: np.array(tpl.bones[i].location, float) for i in std}

    def tparent(i):
        p = tpl.bones[i].parent
        while p is not None and p >= 0 and p not in std:
            p = tpl.bones[p].parent
        return p if p is not None and p >= 0 else -1

    JM = joint_map()
    pos = {JM[g]: ghead[g] for g in JM if g in ghead and JM[g] in tpos}
    mapped = set(pos)
    segments = [(tpl.bones[i].name, tpl.bones[c].name) for i in std for c in std
                if tparent(c) == i and tpl.bones[i].name in mapped and tpl.bones[c].name in mapped]
    for i in std:
        n = tpl.bones[i].name
        if n in pos:
            continue
        t = tpos[n]
        hit = next((m for m in mapped if np.linalg.norm(tpos[m] - t) < 0.05), None)
        if hit:
            pos[n] = pos[hit]
            continue
        for a, b in segments:
            ab = tpos[b] - tpos[a]
            u = float((t - tpos[a]) @ ab / (ab @ ab))
            if 0 < u < 1 and np.linalg.norm(tpos[a] + u * ab - t) < 0.05:
                pos[n] = pos[a] + u * (pos[b] - pos[a])
                break
        else:
            p = tparent(i)
            while p >= 0 and tpl.bones[p].name not in mapped:
                p = tparent(p)
            if p >= 0:
                a = tpl.bones[p].name
                pos[n] = t + (pos[a] - tpos[a])
    # bones with no mapped ancestor (IK, センター...) stand on whatever they stood on
    for i in std:
        n = tpl.bones[i].name
        if n in pos:
            continue
        t = tpos[n]
        hit = min((m for m in pos), key=lambda m: np.linalg.norm(tpos[m] - t))
        pos[n] = t + (pos[hit] - tpos[hit]) if np.linalg.norm(tpos[hit] - t) < 0.05 else t

    bones = []
    index = {}
    for i in std:
        b = tpl.bones[i]
        index[b.name] = len(bones)
        bones.append(b)
    for b in bones:
        b.location = [float(x) for x in pos[b.name]]
    tnew = {i: index[tpl.bones[i].name] for i in std}

    def remap(i):
        return tnew.get(i, -1) if i is not None and i >= 0 else -1

    tpar = {i: tparent(i) for i in std}     # before any parent is rewritten
    for i in std:
        b = tpl.bones[i]
        b.parent = remap(tpar[i])
        if isinstance(b.displayConnection, int):
            b.displayConnection = remap(b.displayConnection)
        if b.additionalTransform:
            b.additionalTransform = (remap(b.additionalTransform[0]), b.additionalTransform[1])
        if b.isIK:
            b.target = remap(b.target)
            for link in b.ik_links:
                link.target = remap(link.target)

    WM = weight_map()

    def add_bone(name, parent, at, tail=None, append=None):
        nb = pmx.Bone()
        nb.name, nb.name_e = name, name
        nb.location = [float(x) for x in at]
        nb.parent = parent
        nb.transform_order = 0
        nb.displayConnection = [0.0, 0.0, 0.0] if tail is None else tail
        nb.isMovable = False
        if append:
            nb.hasAdditionalRotate = True
            nb.additionalTransform = append
        index[name] = len(bones)
        bones.append(nb)
        return index[name]

    # twist helpers onto the template's twist bones, nearest along the limb
    for s, j in LR:
        for tw, limb in TWISTS.items():
            g = tw.format(s=s)
            if g not in ghead:
                continue
            cands = [n for n in index if n.startswith(f"{j}{limb}捩") and n != f"{j}{limb}捩"]
            WM[g] = min(cands, key=lambda n: np.linalg.norm(np.array(bones[index[n]].location) - ghead[g]))
    # the game knee turns half-way between thigh and calf; MMD has no such bone, so it is
    # one that takes half of ひざD's rotation, with the game's knee pads under it
    for s, j in LR:
        k = add_bone(f"{j}ひざ補助", index[f"{j}足D"], ghead[f"Bip001 {s} Calf"], append=(index[f"{j}ひざD"], 0.5))
        WM[f"Bone_{s}_Knee"] = f"{j}ひざ補助"

    # every other game bone keeps its name under the MMD bone its game parent became
    def owner(g):
        while g is not None:
            if g in WM:
                return index[WM[g]]
            if g in index:
                return index[g]
            p = gb[g].parent
            g = p.name if p else None
        return index["センター"]

    extra = []
    for g in gorder:
        if g in WM or DROP.match(g):
            continue
        p = gb[g].parent
        add_bone(g, owner(p.name) if p else index["センター"], ghead[g])
        extra.append(g)
    for g in extra:
        kids = [c.name for c in gb[g].children if c.name in index]
        if len(kids) == 1:
            bones[index[g]].displayConnection = index[kids[0]]
    for i, b in enumerate(bones):
        assert b.parent < i, (b.name, b.parent, i)

    # ---- mesh
    verts, faces, mat_order = [], [], []
    morph_off = {}
    vweights = []
    face_groups = {}
    vbase = 0
    for o in [o for o in bpy.data.objects if o.type == "MESH" and not o.name.startswith("weapon")]:
        me = o.data
        me.calc_loop_triangles()
        mw = o.matrix_world
        nw = mw.to_3x3().inverted().transposed()
        nv, nl, nt = len(me.vertices), len(me.loops), len(me.loop_triangles)
        co = np.empty(nv * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        M4 = np.array(mw)
        wco = co @ M4[:3, :3].T + M4[:3, 3]
        lv = np.empty(nl, int)
        me.loops.foreach_get("vertex_index", lv)
        uv = np.empty(nl * 2)
        me.uv_layers[0].data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        ln = np.array([nw @ n.vector for n in me.corner_normals])
        tl = np.empty(nt * 3, int)
        me.loop_triangles.foreach_get("loops", tl)
        tm = np.empty(nt, int)
        me.loop_triangles.foreach_get("material_index", tm)
        # one PMX vertex per (vertex, uv, normal)
        key = np.concatenate([lv[:, None], np.round(uv * 1e5), np.round(ln * 1e3)], 1)
        uniq, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)
        src = lv[first]
        p = to_pmx(wco[src])
        n = to_pmx(ln[first])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        groups = {g.index: g.name for g in o.vertex_groups}
        wts = []
        for vi in src:
            ws = sorted(((g.weight, groups[g.group]) for g in me.vertices[vi].groups if g.weight > 1e-4), reverse=True)[:4]
            wts.append(ws)
        for k in range(len(src)):
            verts.append((p[k], n[k], (uv[first[k], 0], 1.0 - uv[first[k], 1])))
        vweights += wts
        tri = inv[tl].reshape(-1, 3) + vbase
        for slot in range(len(me.materials)):
            mname = me.materials[slot].name if me.materials[slot] else None
            if mname not in P["materials"]:
                continue
            face_groups.setdefault(mname, []).append(tri[tm == slot])
        if me.shape_keys:
            kb = me.shape_keys.key_blocks
            basis = np.empty(nv * 3)
            kb[0].data.foreach_get("co", basis)
            basis = basis.reshape(-1, 3)
            for k in kb[1:]:
                c = np.empty(nv * 3)
                k.data.foreach_get("co", c)
                d = to_pmx((c.reshape(-1, 3) - basis) @ M4[:3, :3].T)[src]
                moved = np.nonzero(np.linalg.norm(d, axis=1) > 1e-5)[0]
                morph_off[k.name] = [(vbase + int(i), d[i]) for i in moved]
        vbase += len(src)

    model = tpl
    model.name = P["name"].split("-", 1)[1]
    model.name_e = P["name"]
    model.comment = (f"Aether Gazer {P['name']}: the game's model and textures (版权所属深空之眼), converted by ag-rip "
                     f"skin_pmx.py. MMD rig, frames and body colliders from {os.path.basename(P['template'])} "
                     f"(模型编辑：神帝宇). 请勿二次配布, 请勿用于商业用途.")
    model.comment_e = model.comment
    model.vertices = []
    for (p, n, uv), ws in zip(verts, vweights):
        v = pmx.Vertex()
        v.co, v.normal, v.uv = [float(x) for x in p], [float(x) for x in n], [float(uv[0]), float(uv[1])]
        idx, w = [], []
        for wt, g in ws:
            bi = index[WM[g]] if g in WM else index.get(g)
            if bi is None:
                bi = owner(g)
            if bi in idx:
                w[idx.index(bi)] += wt
            else:
                idx.append(bi)
                w.append(wt)
        if not idx:
            idx, w = [index["頭"]], [1.0]
        s = sum(w)
        w = [x / s for x in w]
        bw = pmx.BoneWeight()
        if len(idx) == 1:
            bw.type, bw.bones, bw.weights = pmx.BoneWeight.BDEF1, idx, [1.0]
        elif len(idx) == 2:
            bw.type, bw.bones, bw.weights = pmx.BoneWeight.BDEF2, idx, [w[0]]
        else:
            idx += [0] * (4 - len(idx))
            w += [0.0] * (4 - len(w))
            bw.type, bw.bones, bw.weights = pmx.BoneWeight.BDEF4, idx, w
        v.weight = bw
        v.edge_scale = 1.0
        model.vertices.append(v)

    # winding: the mirror flips it; match the template's (as mmd_tools reads it)
    allv = np.array([v.co for v in model.vertices])
    alln = np.array([v.normal for v in model.vertices])
    order = [m for m in MATERIALS if m in face_groups]
    F = np.concatenate([np.concatenate(face_groups[m]) for m in order])
    osign = facing(allv, alln, F)
    flip = osign != tsign
    print(f"winding: template {tsign:+.0f}, game {osign:+.0f}{' -> flipped' if flip else ''}")

    model.faces = []
    model.textures = []
    model.materials = []
    tex_index = {}
    for m in order:
        info = P["materials"][m]
        fs = np.concatenate(face_groups[m])
        if flip:
            fs = fs[:, ::-1]
        model.faces += [tuple(int(x) for x in f) for f in fs]
        if info["texture"] not in tex_index:
            t = pmx.Texture()
            t.path = info["texture"]
            tex_index[info["texture"]] = len(model.textures)
            model.textures.append(t)
        mt = pmx.Material()
        mt.name, mt.name_e = info["name"], m
        mt.diffuse, mt.specular, mt.shininess, mt.ambient = [1.0, 1.0, 1.0, 1.0], [0.0, 0.0, 0.0], 5.0, [0.5, 0.5, 0.5]
        mt.is_double_sided = info["double"]
        mt.enabled_drop_shadow = mt.enabled_self_shadow_map = mt.enabled_self_shadow = True
        mt.enabled_toon_edge = info["edge"]
        mt.edge_color, mt.edge_size = [0.12, 0.08, 0.1, 1.0], max(info["edgeSize"], 0.3)
        mt.texture = tex_index[info["texture"]]
        mt.sphere_texture, mt.sphere_texture_mode = -1, 0
        mt.is_shared_toon_texture, mt.toon_texture = True, 1
        mt.comment = f"game material {m}"
        mt.vertex_count = len(fs) * 3
        model.materials.append(mt)

    # ---- morphs
    model.morphs = []

    def vmorph(name, panel, keys):
        acc = {}
        for k in keys:
            for vi, d in morph_off.get(k, []):
                acc[vi] = acc.get(vi, 0) + d
        if not acc:
            return
        mo = pmx.VertexMorph(name, name, panel)
        for vi, d in sorted(acc.items()):
            off = pmx.VertexMorphOffset()
            off.index, off.offset = vi, [float(x) for x in d]
            mo.offsets.append(off)
        model.morphs.append(mo)

    for name, panel, keys in STANDARD_MORPHS:
        vmorph(name, panel, keys)
    for k in morph_off:
        panel = 1 if k.startswith("EB_") else 3 if k.startswith(("Mouth", "Z_")) else 2 if k.startswith(("Eye", "Pupil")) else 4
        vmorph(k, panel, [k])

    # ---- physics: body colliders from the template, chains from the game
    template_rigids = tpl.rigids    # model is the template object: take its list before clearing
    model.rigids, model.joints = [], []
    shift = {n: np.array(bones[index[n]].location) - tpos[n] for n in tpos}
    colliders = []
    for r in template_rigids:
        bn = tpl.bones[r.bone].name if r.bone is not None and r.bone >= 0 else None
        if bn not in BODY_COLLIDERS or r.name.startswith("M-") or r.mode != 0:
            continue
        r.bone = index[bn]
        r.location = [float(x) for x in np.array(r.location) + shift[bn]]
        r.collision_group_number, r.collision_group_mask = 0, 0xFFFE
        model.rigids.append(r)
        colliders.append(r)

    def rot(rx, ry, rz):
        cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
        Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
        Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
        return Ry @ Rx @ Rz

    def seg_dist(p1, q1, p2, q2):
        d1, d2, r = q1 - p1, q2 - p2, p1 - p2
        a, e, f = d1 @ d1, d2 @ d2, d2 @ r
        c, b = d1 @ r, d1 @ d2
        den = a * e - b * b
        s = np.clip((b * f - c * e) / den, 0, 1) if den > 1e-12 else 0.0
        t = np.clip((b * s + f) / e, 0, 1) if e > 1e-12 else 0.0
        s = np.clip((b * t - c) / a, 0, 1) if a > 1e-12 else 0.0
        return float(np.linalg.norm(p1 + d1 * s - (p2 + d2 * t)))

    def collider_seg(r):
        c = np.array(r.location)
        if r.type == 2:
            y = rot(*r.rotation) @ np.array([0, 1, 0]) * r.size[1] / 2
            return c - y, c + y, r.size[0]
        return c, c, max(r.size) if r.type == 1 else r.size[0]

    body_segs = [collider_seg(r) for r in colliders]
    rigid_of = {}
    upper = next(i for i, r in enumerate(model.rigids) if r.bone == index["上半身2"])

    def capsule(name, bone, a, b, radius, mode, group, mask, damp=(0.9, 0.99)):
        r = pmx.Rigid()
        r.name, r.name_e, r.bone = name, name, index[bone] if isinstance(bone, str) else bone
        d = b - a
        L = float(np.linalg.norm(d))
        r.type = 2 if L > 1e-4 else 0
        r.size = [radius, L, 0.0] if r.type == 2 else [radius, 0.0, 0.0]
        r.location = [float(x) for x in (a + b) / 2]
        if L > 1e-4:
            u = d / L
            r.rotation = [float(np.arctan2(u[2], u[1])), 0.0, float(np.arcsin(np.clip(-u[0], -1, 1)))]
        else:
            r.rotation = [0.0, 0.0, 0.0]
        r.mass, r.velocity_attenuation, r.rotation_attenuation = 1.0, damp[0], damp[1]
        r.bounce, r.friction = 0.0, 0.5
        r.mode = mode
        r.collision_group_number, r.collision_group_mask = group, mask
        model.rigids.append(r)
        return len(model.rigids) - 1

    def joint(name, ra, rb, at, lim, spring):
        j = pmx.Joint()
        j.name, j.name_e = name, name
        j.mode = 0
        j.src_rigid, j.dest_rigid = ra, rb
        j.location, j.rotation = [float(x) for x in at], [0.0, 0.0, 0.0]
        j.minimum_location, j.maximum_location = [0.0] * 3, [0.0] * 3
        j.minimum_rotation, j.maximum_rotation = [-lim] * 3, [lim] * 3
        j.spring_constant, j.spring_rotation_constant = [0.0] * 3, [spring] * 3
        model.joints.append(j)

    chain_bones = [g for g in extra if "_P1" in g]
    kind = {g: next(c for c in CHAINS if c[0].search(g.lower())) for g in chain_bones}
    loose = []
    for g in chain_bones:
        _, group, per, rmin, rmax, lim, spring = kind[g]
        head = np.array(bones[index[g]].location)
        kids = [c.name for c in gb[g].children if c.name in kind]
        parent = gb[g].parent.name if gb[g].parent else None
        is_root = parent not in kind
        if kids:
            tip = np.mean([np.array(bones[index[k]].location) for k in kids], 0)
        elif not is_root:
            ph = np.array(bones[index[parent]].location)
            tip = head + (head - ph) * 0.7
        else:
            tip = head
        L = float(np.linalg.norm(tip - head))
        radius = float(np.clip(per * L, rmin, rmax)) if per else rmax
        clear = min(seg_dist(head, tip, a, b) - rb for a, b, rb in body_segs)
        mask = 0x0001
        if clear < radius:
            radius = max(0.05, clear - 0.02)
            if clear < 0.07:
                mask = 0x0000
                loose.append(g)
        single = is_root and not kids
        if single:                       # a lone bone (breast): hangs off the chest on a spring
            ri = capsule(g, g, head, tip, radius, 1, group, mask, (0.9, 0.99))
            joint(g, upper, ri, head, lim, spring)
        elif is_root:                    # a chain's root follows its bone
            ri = capsule(g, g, head, tip, radius, 0, group, mask)
        else:
            ri = capsule(g, g, head, tip, radius, 1, group, mask)
            joint(g, rigid_of[parent], ri, head, lim, spring)
        rigid_of[g] = ri
    print(f"rigids {len(model.rigids)} (body {len(colliders)}), joints {len(model.joints)}; "
          f"no body contact at rest-overlap: {', '.join(loose) or 'none'}")

    # ---- display frames: the template's, on the bones that remain, plus the skin's own
    disp = []
    for d in tpl.display:
        if d.isSpecial and d.name == "表情":
            d.data = [(1, i) for i in range(len(model.morphs))]
        else:
            d.data = [(t, index[tpl.bones[i].name]) for t, i in d.data
                      if t == 0 and i in tnew and tpl.bones[i].name in index]
            if not d.data and not d.isSpecial:
                continue
        disp.append(d)
    for title, pick in (("物理", lambda g: "_P1" in g), ("補助", lambda g: "_P1" not in g)):
        fr = pmx.Display()
        fr.name, fr.name_e, fr.isSpecial = title, title, False
        fr.data = [(0, index[g]) for g in extra if pick(g)]
        if fr.data:
            disp.append(fr)
    model.display = disp
    model.bones = bones

    os.makedirs(os.path.dirname(P["out"]), exist_ok=True)
    pmx.save(P["out"], model, add_uv_count=0)
    print(f"wrote {P['out']}: {len(model.vertices)} vertices, {len(model.faces)} faces, {len(model.materials)} materials, "
          f"{len(model.bones)} bones, {len(model.morphs)} morphs, {len(model.rigids)} rigids, {len(model.joints)} joints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
