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

if "bpy" in locals():
    import importlib
    if "slm" in locals():
        importlib.reload(slm)
    if "oxp" in locals():
        importlib.reload(oxp)
    if "llsdz" in locals():
        importlib.reload(llsdz)
    if "utils" in locals():
        importlib.reload(utils)
    if "skeleton" in locals():
        importlib.reload(skeleton)

from . import oxp
from . import slm
from . import skeleton

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
    slm.register()
    oxp.register()
    skeleton.register()

def unregister():
    skeleton.unregister()
    oxp.unregister()
    slm.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_seperator)
    bpy.utils.unregister_class(SLIZ_preferences)

if __name__ == "__main__":
    register()
