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
import time
import traceback
from bpy_extras.io_utils import ImportHelper

if "slm" in locals():
    import importlib
    importlib.reload(slm)

from . import slm


class SLIZ_IMPORT_slm(bpy.types.Operator, ImportHelper):
    """Import one or more SLM (.slm) files"""
    bl_idname    = "import_scene.sliz_slm"
    bl_label     = "Import SLM (.slm)"
    filename_ext = ".slm"
    filter_glob: bpy.props.StringProperty(default="*.slm", options={'HIDDEN'})
    files: bpy.props.CollectionProperty(
        name="SLM Files",
        type=bpy.types.OperatorFileListElement,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    directory: bpy.props.StringProperty(
        name="Directory",
        subtype='DIR_PATH',
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    extract_lods: bpy.props.BoolProperty(
        name="Load all LOD levels",
        description=(
            "Extracts all LOD level"
        ),
        default=False,
    )
    create_debug_info: bpy.props.BoolProperty(
        name="Create Debug Info",
        description=(
            "Create textfile with tree of content"
        ),
        default=False,
    )

    def _selected_filepaths(self):
        if self.files:
            base_directory = (
                self.directory
                or os.path.dirname(self.filepath)
            )
            return [
                os.path.normpath(os.path.join(base_directory, item.name))
                for item in self.files
            ]
        return [os.path.normpath(self.filepath)] if self.filepath else []

    def _notify(self, levels, message):
        if 'ERROR' in levels:
            self._last_import_error = message
        elif 'INFO' in levels:
            self._last_import_summary = message

    def execute(self, context):
        filepaths = self._selected_filepaths()
        if not filepaths:
            self.report({'ERROR'}, "No files selected.")
            return {'CANCELLED'}

        original_filepath = self.filepath
        self._batch_mode = len(filepaths) > 1
        successes = []
        failures = []

        try:
            for filepath in filepaths:
                self.filepath = filepath
                self._cached_root = None
                self._cached_profile = None
                self._last_import_error = None
                self._last_import_summary = None
                self._import_stage = "starting"

                started = time.perf_counter()
                try:
                    result = self._execute_single(context)
                except Exception as error:
                    traceback.print_exc()
                    result = {'CANCELLED'}
                    self._last_import_error = (
                        f"{type(error).__name__} during "
                        f"{self._import_stage}: {error}"
                    )

                elapsed = time.perf_counter() - started
                filename = os.path.basename(filepath)
                if result == {'FINISHED'}:
                    successes.append((filename, elapsed))
                else:
                    failures.append(
                        (
                            filename,
                            self._last_import_error
                            or f"Import stopped during {self._import_stage}",
                        )
                    )
        finally:
            self.filepath = original_filepath
            self._batch_mode = False

        if len(filepaths) == 1:
            if failures:
                filename, reason = failures[0]
                self.report({'ERROR'}, f"{filename}: {reason}")
                return {'CANCELLED'}
            filename, elapsed = successes[0]
            summary = self._last_import_summary or f"Imported {filename}"
            self.report({'INFO'}, f"{summary} ({elapsed:.2f}s)")
            return {'FINISHED'}

        if failures:
            print("[Batch Import] Failures:")
            for filename, reason in failures:
                print(f"  {filename}: {reason}")
        message = (
            f"Batch import: {len(successes)} succeeded, "
            f"{len(failures)} failed."
        )
        self.report(
            {'WARNING'} if failures else {'INFO'},
            message + (
                " See the console for per-file errors."
                if failures else ""
            ),
        )
        return {'FINISHED'} if successes else {'CANCELLED'}

    def _execute_single(self, context):
        if not os.path.isfile(self.filepath):
            self._notify({'ERROR'}, "File not found")
            return {'CANCELLED'}

        self._import_stage = "parsing SLM"

        try:
            with open(self.filepath, 'rb') as f:
                name = os.path.splitext(os.path.basename(self.filepath))[0]

                imported_mesh_objects = slm.import_slm(f,
                    name,
                    filepath=self.filepath,
                    create_debug_info=self.create_debug_info,
                    extract_lods=self.extract_lods
                )
                move_objs_into_collections(imported_mesh_objects)
        except Exception as e:
            traceback.print_exc()
            self._notify({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

def menu_func_import(self, context):
    self.layout.operator(SLIZ_IMPORT_slm.bl_idname, text="SLM (.slm)")

def register():
    bpy.utils.register_class(SLIZ_IMPORT_slm)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(SLIZ_IMPORT_slm)

if __name__ == "__main__":
    register()
