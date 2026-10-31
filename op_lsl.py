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

if "lsl" in locals():
    import importlib
    importlib.reload(lsl)

from . import lsl
from . import utils

class TEXT_OT_generate_script(bpy.types.Operator):
    bl_idname = "text.sliz_generate_texture_script"
    bl_label = "Generate Texture Script"
    bl_description = "Generate LSL script that sets textures inworld according to Materials"

    def execute(self, context):
        # get or create text block
        text_block = context.space_data.text
        if not text_block:
            text_block = bpy.data.texts.new("Set Textures LSL")
            context.space_data.text = text_block

        text_block.clear()
        lsl_text, _ = lsl.create_lsl_script()
        text_block.write(lsl_text)
        text_block.cursor_set(0)

        self.report({'INFO'}, "Script generated!")
        return {'FINISHED'}

class TEXT_OT_generate_object_tree(bpy.types.Operator):
    bl_idname = "text.sliz_generate_object_tree"
    bl_label = "Generate UUID tree"
    bl_description = "Generate tree showing UUIDs of materials"

    def execute(self, context):
        # get or create text block
        text_block = context.space_data.text
        if not text_block:
            text_block = bpy.data.texts.new("SL UUIDs")
            context.space_data.text = text_block

        text_block.clear()
        _, object_tree = lsl.create_lsl_script()
        tree_text = "\n".join(utils.print_tree(object_tree))
        text_block.write(tree_text)
        text_block.cursor_set(0)

        self.report({'INFO'}, "UUID tree generated!")
        return {'FINISHED'}

class TEXT_MT_my_generator_menu(bpy.types.Menu):
    bl_label = "LSL"
    bl_idname = "TEXT_MT_lsl_generator_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator("text.sliz_generate_texture_script")
        layout.operator("text.sliz_generate_object_tree")

def draw_my_menu(self, context):
    layout = self.layout
    layout.menu(TEXT_MT_my_generator_menu.bl_idname)

def register():
    bpy.utils.register_class(TEXT_OT_generate_object_tree)
    bpy.utils.register_class(TEXT_OT_generate_script)
    bpy.utils.register_class(TEXT_MT_my_generator_menu)
    bpy.types.TEXT_HT_header.append(draw_my_menu)

def unregister():
    bpy.types.TEXT_HT_header.remove(draw_my_menu)
    bpy.utils.unregister_class(TEXT_MT_my_generator_menu)
    bpy.utils.unregister_class(TEXT_OT_generate_script)
    bpy.utils.unregister_class(TEXT_OT_generate_object_tree)

if __name__ == "__main__":
    register()
