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

if "bpy" in locals():
    import importlib
    if "op_slm" in locals():
        importlib.reload(op_slm)
    if "op_oxp" in locals():
        importlib.reload(op_oxp)
    if "llsdz" in locals():
        importlib.reload(llsdz)
    if "utils" in locals():
        importlib.reload(utils)
    if "op_skeleton" in locals():
        importlib.reload(op_skeleton)
    if "op_lsl" in locals():
        importlib.reload(op_lsl)

import bpy

from . import op_oxp
from . import op_slm
from . import op_skeleton
from . import op_lsl

class SLIZ_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    ui_help: bpy.props.BoolProperty(name="Help Messages", default=True, options=set())

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(self, "ui_help")

def menu_func_import_seperator(self, context):
    self.layout.separator()

def register():
    bpy.utils.register_class(SLIZ_preferences)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_seperator)
    op_slm.register()
    op_oxp.register()
    op_skeleton.register()
    op_lsl.register()

def unregister():
    op_lsl.unregister()
    op_skeleton.unregister()
    op_oxp.unregister()
    op_slm.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_seperator)
    bpy.utils.unregister_class(SLIZ_preferences)

if __name__ == "__main__":
    register()
