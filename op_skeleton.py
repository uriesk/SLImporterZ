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
