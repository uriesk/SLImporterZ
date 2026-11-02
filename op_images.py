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
import traceback
from bpy_extras.io_utils import ExportHelper

from . import utils

class SLIZ_EXPORT_textures(bpy.types.Operator, ExportHelper):
    bl_idname   = "export.sliz_textures"
    bl_label    = "Export All Images as PNG"
    filename_ext = "."
    use_filter_folder = True

    def execute(self, context):
        output_folder = os.path.dirname(self.filepath)

        if not output_folder:
            self.report({'ERROR'}, "Please select a valid folder")
            return {'CANCELLED'}

        if not os.path.exists(output_folder):
            try:
                os.makedirs(output_folder)
            except Exception as e:
                self.report({'ERROR'}, f"Could not create folder: {e}")
                return {'CANCELLED'}

        saved = 0
        try:
            for image in bpy.data.images:
                # Skip images with no pixel data (Render Result, Viewer Node, etc.)
                if image.type == "RENDER_RESULT" or image.source == "VIEWER" or image.size[0] == 0 or image.size[1] == 0:
                    continue

                filepath = utils.path_join(output_folder, image.name + ".png")
                image.save_render(filepath=filepath)
                saved += 1
        except Exception as e:
            traceback.print_exc()
            self._notify({'ERROR'}, str(e))
            return {'CANCELLED'}

        self.report({'INFO'}, f"Saved {saved} image(s)")
        return {'FINISHED'}

def menu_func_export(self, context):
    self.layout.separator()
    self.layout.operator(
        SLIZ_EXPORT_textures.bl_idname,
        text="All Images as PNG",
    )

def register():
    bpy.utils.register_class(SLIZ_EXPORT_textures)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(SLIZ_EXPORT_textures)

if __name__ == "__main__":
    register()
