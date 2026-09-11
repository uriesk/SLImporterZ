'''
    SLImporterZ - Import meshes from virtual worlds into Blender
    Copyright (C) 2026 uriesk <uriesk@posteo.de>

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
'''

import bpy
import os
import xml.etree.ElementTree as ET
import math
import mathutils


Rz90 = mathutils.Matrix((
       (0.0, 1.0, 0.0, 0.0),
       (-1.0, 0.0, 0.0, 0.0),
       (0.0, 0.0, 1.0, 0.0),
       (0.0, 0.0, 0.0, 1.0)
       ))
Rz90I = Rz90.inverted()

def get_skeleton():
    tree = ET.parse(os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "avatar_skeleton.xml"))
    root = tree.getroot()

    result = {}
    def getRecursive(bone, parent=None):
        name = bone.attrib["name"]
        entry = {
            "group": bone.attrib["group"],
            "pos_orig": [float(i) for i in bone.attrib["pos"].split(" ")],
            "end_orig": [float(i) for i in bone.attrib["end"].split(" ")],
            "rot_orig": [float(i) for i in bone.attrib["rot"].split(" ")],
            "scale_orig": [float(i) for i in bone.attrib["scale"].split(" ")],
            "parent": parent or False,
            "connected": bone.attrib.get("connected", "false").lower() == "true",
            "type": bone.tag
        }

        if parent:
            offset = result[parent]["pos"]
            entry["pos"] = [entry["pos_orig"][i]+offset[i] for i in range(0,3)]
        else:
            entry["pos"] = entry["pos_orig"]
        entry["end"] = [entry["end_orig"][i]+entry["pos"][i] for i in range(0,3)]

        entry["scale"] = mathutils.Matrix.Scale(entry["scale_orig"][0], 4, (1,0,0))
        entry["scale"] *= mathutils.Matrix.Scale(entry["scale_orig"][1], 4, (0,1,0))
        entry["scale"] *= mathutils.Matrix.Scale(entry["scale_orig"][2], 4, (0,0,1))

        entry["rot"] = mathutils.Euler(entry["rot_orig"], "XYZ").to_matrix().to_4x4()
        print(name, entry["rot"])

        # --- FIX HERE ---
        # NOTE: unclear whether or not we have to care about converting it to
        # X+ forward, which would be z rotation +90 degrees
        # Convert from SL (X+ forward) to Blender (Y+ forward)
        # Rotate -90° around Z
        #conversion = mathutils.Matrix.Rotation(math.radians(-90), 4, 'Z')
        #entry["rot"] = conversion @ entry["rot"] @ conversion.inverted()
        # --- END FIX ---

        result[name] = entry
        
        for child in bone:
            getRecursive(child, parent=name)
        return result
    return getRecursive(root[0])

def add_skeleton(context):
    bones = get_skeleton()
    
    armature = bpy.data.armatures.new(name="Armature")
    armature_obj = bpy.data.objects.new("Armature", armature)
    context.collection.objects.link(armature_obj)
    armature_obj.select_set(True)
    context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT', toggle=False)
    edit_bones = armature.edit_bones
    pose = armature_obj.pose
    boners = {}
    for bone_name, bone in bones.items():
        b = edit_bones.new(bone_name)
        boners[bone_name] = b
        b.head = bone["pos"]
        b.tail = bone["end"]
        if bone["parent"]:
            b.parent = boners[bone["parent"]]
        
        if bone["connected"]:
            b.use_connect = True

    # Remove auto-created bone
    bpy.ops.object.mode_set(mode='EDIT', toggle=True)

    # Create bone collections
    bone_collection = armature.collections.new(name="bone")
    collision_collection = armature.collections.new(name="collision_volume")

    for bone_name, bone in bones.items():
        # Assign bones to collections based on type
        if bone["type"] == "collision_volume":
            armature.collections["collision_volume"].assign(pose.bones[bone_name])
        else:
            armature.collections["bone"].assign(pose.bones[bone_name])

    armature_obj.location = (0,0,0)
    return armature_obj

class SLIZ_ADD_armature(bpy.types.Operator):
    bl_idname = "object.sliz_armature"
    bl_label = "Second Life Armature"
    bl_description = "Add a Second Life compatible armature with standard bone hierarchy"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        add_skeleton(context)
        self.report({'INFO'}, "Second Life compatible armature added")
        return {'FINISHED'}

def menu_func_add(self, context):
    self.layout.separator()
    self.layout.operator(
        SLIZ_ADD_armature.bl_idname,
        text="Second Life Skeleton",
        icon='ARMATURE_DATA'
    )

def register():
    bpy.utils.register_class(SLIZ_ADD_armature)
    bpy.types.VIEW3D_MT_add.append(menu_func_add)

def unregister():
    bpy.types.VIEW3D_MT_add.remove(menu_func_add)
    bpy.utils.unregister_class(SLIZ_ADD_armature)

if __name__ == "__main__":
    register()
