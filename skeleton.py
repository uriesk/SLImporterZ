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
from mathutils import Vector, Matrix, Euler
import json


def get_skeleton(use_pivot=False):
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

        if "pivot" in bone.attrib:
            entry["pivot_orig"] = [float(i) for i in bone.attrib["pivot"].split(" ")]
        else:
            entry["pivot_orig"] = entry["pos_orig"]

        if use_pivot:
            entry["pos"] = entry["pivot_orig"]
        else:
            entry["pos"] = entry["pos_orig"]
        if parent:
            offset = result[parent]["pos"]
            entry["pos"] = [entry["pos"][i]+offset[i] for i in range(0,3)]

        entry["rot"] = Euler(
            (math.radians(entry["rot_orig"][0]),
            math.radians(entry["rot_orig"][1]),
            math.radians(entry["rot_orig"][2])),
            "XYZ"
        ).to_matrix().to_4x4()

        entry["end"] = [entry["end_orig"][i]+entry["pos"][i] for i in range(0,3)]

        sx, sy, sz = entry["scale_orig"]
        entry["scale"] = Matrix.Diagonal((sx, sy, sz, 1.0))

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
        if bone["group"] in ["Limb", "Tail", "Wing", "Nose", "Lips", "Mouth"]:
            continue
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

    # Assign bone collections
    for bone_name, bone in bones.items():
        if bone["group"] in ["Limb", "Tail", "Wing", "Nose", "Lips", "Mouth"]:
            continue
        collection = armature.collections.get(bone["group"])
        if collection is None:
            collection = armature.collections.new(name=bone["group"])
        collection.assign(pose.bones[bone_name])

    # inverse_bind_matrix from SL data includes absolute translation, but
    # already transformed relative rotation and scale.
    # bind_matrix_transform can be used to add it
    json_data = {}
    for bone_name in boners.keys():
        boner_matrix = armature.bones[bone_name].matrix_local

        rot = bones[bone_name]["rot"].to_euler('XYZ')
        if rot.x != 0.0 or rot.y != 0.0 or rot.z != 0.0:
            print(bone_name, [math.degrees(a) for a in rot])

        # rotation from bones created in blender
        boner_rotation_matrix = boner_matrix.to_quaternion().to_matrix().to_4x4()
        # inverted scale from avatar_skeleton.xml
        bind_matrix_transform = bones[bone_name]["scale"].inverted() @ bones[bone_name]["rot"].inverted() @ boner_rotation_matrix

        json_data[bone_name] = [list(row) for row in bind_matrix_transform]
    json_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "bind_matrix_transform.json")
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2)

    armature_obj.location = (0,0,0)
    return armature_obj

def get_bind_matrix_transform():
    path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "bind_matrix_transform.json")
    with open(path, "r") as f:
        data = json.load(f)
    for bone_name, bind_matrix_transform in data.items():
        data[bone_name] = Matrix(bind_matrix_transform)
    return data

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
