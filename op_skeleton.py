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

if "skeleton" in locals():
    import importlib
    importlib.reload(skeleton)

from . import skeleton


class SLIZ_ADD_armature(bpy.types.Operator):
    bl_idname = "object.sliz_armature"
    bl_label = "Second Life Armature"
    bl_description = "Add a Second Life compatible armature with standard bone hierarchy"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        skeleton.add_skeleton(context)
        self.report({'INFO'}, "Second Life compatible armature added")
        return {'FINISHED'}

class SLIZ_OT_set_t_pose(bpy.types.Operator):
    bl_idname = "object.sliz_set_t_pose"
    bl_label = "Apply SL T-Pose as Pose"
    bl_description = "Reset the selected armature's pose to the default T-pose defined by the Second Life skeleton"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armature = context.active_object
        skeleton.set_t_pose(armature)
        self.report({'INFO'}, "Armature pose set to T-pose")
        return {'FINISHED'}

def menu_func_add(self, context):
    self.layout.separator()
    self.layout.operator(
        SLIZ_ADD_armature.bl_idname,
        text="Second Life Skeleton",
        icon='ARMATURE_DATA'
    )

def menu_func_pose(self, context):
    self.layout.separator()
    self.layout.operator(
        SLIZ_OT_set_t_pose.bl_idname,
        text="Apply SL T-Pose as Pose",
    )

def register():
    bpy.utils.register_class(SLIZ_ADD_armature)
    bpy.utils.register_class(SLIZ_OT_set_t_pose)
    bpy.types.VIEW3D_MT_add.append(menu_func_add)
    bpy.types.VIEW3D_MT_pose_apply.append(menu_func_pose)

def unregister():
    bpy.types.VIEW3D_MT_pose_apply.remove(menu_func_pose)
    bpy.types.VIEW3D_MT_add.remove(menu_func_add)
    bpy.utils.unregister_class(SLIZ_OT_set_t_pose)
    bpy.utils.unregister_class(SLIZ_ADD_armature)


if __name__ == "__main__":
    register()
