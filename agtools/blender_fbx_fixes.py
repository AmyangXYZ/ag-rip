"""Runtime patches for Blender's FBX importer, for rigs it cannot otherwise read.

Imported inside Blender (bpy / io_scene_fbx must be loaded). Each patch is idempotent.
"""
from __future__ import annotations


def tolerate_foreign_skin() -> None:
    """Let a mesh skinned across two skeleton roots import instead of aborting.

    Some ripped rigs (113701's home model, 1075's) carry a second skeleton root,
    "root", beside Bip001, and a mesh with weights on bones of both. Blender files
    the mesh under the outer armature but records its bind matrices under the inner
    one, then looks them up by the outer: `mesh.armature_setup[self]` raises
    `KeyError: root` and the whole import is lost.

    The importer already assumes a mesh was bound with one mesh/armature matrix for
    every bone ("we do not support otherwise in Blender anyway"), so the record it
    kept for the inner armature is the one the outer should use. Hand it over before
    linking; a mesh with no record at all is left out of the armature rather than
    taking the import down with it.
    """
    from io_scene_fbx import import_fbx

    node = import_fbx.FbxImportHelperNode
    if getattr(node.link_hierarchy, "_ag_patched", False):
        return
    original = node.link_hierarchy

    def link_hierarchy(self, fbx_tmpl, settings, scene):
        if self.is_armature and self.meshes:
            for mesh in list(self.meshes):
                if self in mesh.armature_setup:
                    continue
                if mesh.armature_setup:
                    mesh.armature_setup[self] = next(iter(mesh.armature_setup.values()))
                else:
                    self.meshes.discard(mesh)
        return original(self, fbx_tmpl, settings, scene)

    link_hierarchy._ag_patched = True
    node.link_hierarchy = link_hierarchy
