"""Render a stage glb to PNG thumbnails, inside Blender.

    blender -b --python render_glb.py -- <scene.glb> <out_prefix>

Frames the camera on where the geometry actually is: the 10th-90th percentile
of object centres, so a skydome or a far backdrop plane does not shrink the
stage to a dot. Writes <out_prefix>_high.png (3/4 overhead) and _eye.png.
Flat, emission-free lighting: the point is to recognise the place.
"""
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
glb, prefix = argv[0], argv[1]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no meshes in " + glb)

centres = []
for o in meshes:
    bb = [o.matrix_world @ Vector(c) for c in o.bound_box]
    size = max((bb[6] - bb[0]).length, 1e-6)
    centres.append((sum(bb, Vector()) / 8, size))
# drop objects far bigger than the typical one (skydomes, backdrop cards)
sizes = sorted(s for _, s in centres)
cap = sizes[int(len(sizes) * 0.98)] if len(sizes) > 20 else sizes[-1]
pts = [c for c, s in centres if s <= cap]


def pct(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(len(vals) * q)))]


lo = Vector((pct([p.x for p in pts], .1), pct([p.y for p in pts], .1), pct([p.z for p in pts], .1)))
hi = Vector((pct([p.x for p in pts], .9), pct([p.y for p in pts], .9), pct([p.z for p in pts], .9)))
mid = (lo + hi) / 2
span = max((hi - lo).length, 2.0)

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 960, 540
scene.world = bpy.data.worlds.new("w")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs[1].default_value = 1.2
scene.view_settings.view_transform = "Standard"
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 2.5
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.lens = 24
cam.data.clip_end = span * 50
scene.collection.objects.link(cam)
scene.camera = cam


def shoot(name, direction, dist):
    cam.location = mid + direction.normalized() * dist
    cam.rotation_euler = (mid - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{prefix}_{name}.png"
    bpy.ops.render.render(write_still=True)


shoot("high", Vector((1, -1, 0.9)), span * 0.9)
shoot("eye", Vector((0.2, -1, 0.12)), span * 0.55)
print("rendered", prefix)
