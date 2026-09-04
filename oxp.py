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

from . import slm
# for reading OXP files (zlib compressed binary LLSD format)
# https://wiki.secondlife.com/wiki/LLSD
# reference implementation: https://github.com/secondlife/python-llsd
from . import llsdz

import os
import time
import traceback
from bpy_extras.io_utils import ImportHelper
import zlib


class SLIZ_IMPORT_oxp(bpy.types.Operator, ImportHelper):
    """Import one or more OXP (.oxp) files"""
    bl_idname    = "import_scene.sliz_oxp"
    bl_label     = "Import OXP (.oxp)"
    filename_ext = ".oxp"
    filter_glob: bpy.props.StringProperty(default="*.oxp", options={'HIDDEN'})
    files: bpy.props.CollectionProperty(
        name="OXP Files",
        type=bpy.types.OperatorFileListElement,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    directory: bpy.props.StringProperty(
        name="Directory",
        subtype='DIR_PATH',
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    batch_create_collections: bpy.props.BoolProperty(
        name="Collection per File",
        description=(
            "Please each OXP in its own collection"
        ),
        default=True,
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
        parent_collection = (
            context.view_layer.active_layer_collection.collection
            if context.view_layer.active_layer_collection
            else context.scene.collection
        )

        try:
            for filepath in filepaths:
                self.filepath = filepath
                self._cached_root = None
                self._cached_profile = None
                self._last_import_error = None
                self._last_import_summary = None
                self._import_stage = "starting"
                self._target_collection = None

                if self._batch_mode and self.batch_create_collections:
                    collection_name = os.path.splitext(
                        os.path.basename(filepath)
                    )[0]
                    target_collection = bpy.data.collections.new(
                        collection_name
                    )
                    parent_collection.children.link(target_collection)
                    self._target_collection = target_collection

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
                    target = self._target_collection
                    if target is not None and not target.objects:
                        parent_collection.children.unlink(target)
                        bpy.data.collections.remove(target)
        finally:
            self.filepath = original_filepath
            self._batch_mode = False
            self._target_collection = None

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

        self._import_stage = "parsing OXP"

        # OXP is zlib compressed binary llsd
        try:
            decompressor = zlib.decompressobj()
            decompressed_data = []
            with open(self.filepath, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    decompressed_chunk = decompressor.decompress(chunk)
                    if decompressed_chunk:
                        decompressed_data.append(decompressed_chunk)

                final_chunk = decompressor.flush()
                if final_chunk:
                    decompressed_data.append(final_chunk)
            # Combine all parts
            decompressed_data = b''.join(decompressed_data)
            del decompressor
        except zlib.error as e:
            self._notify({'ERROR'}, f"Could not decompress file. {e}")
            return {'CANCELLED'}
        except Exception as e:
            self._notify({'ERROR'}, f"Could not read file. {e}")
            return {'CANCELLED'}

        try:
            oxp_data = llsdz.parse_binary_nohdr(decompressed_data)
        except Exception as e:
            self._notify({'ERROR'}, f"Could not parse LLSD. {e}")
            return {'CANCELLED'}
        del decompressed_data

        if self.create_debug_info:
            # Print structure into debug file within same folder
            tree_lines = []
            tree_lines.extend(llsdz.print_tree(oxp_data))
            tree_filepath = os.path.splitext(self.filepath)[0] + "_oxptree.txt"
            with open(tree_filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(tree_lines))

        self._import_stage = "parsing mesh in OXP"

        amount_meshes = 0
        amount_imported_meshes = 0
        if "mesh_asset" in oxp_data and isinstance(oxp_data["mesh_asset"], dict):
            mesh_assets = oxp_data.pop("mesh_asset")
            keys = list(mesh_assets.keys())
            for i, mesh_uuid in enumerate(keys):
                mesh_asset = mesh_assets.pop(mesh_uuid)
                if "data" not in mesh_asset or "type" not in mesh_asset or mesh_asset["type"] != "mesh":
                    continue
                amount_meshes += 1
                slm_data = mesh_asset.pop("data")
                print(f"Found mesh: {mesh_uuid} with length: {len(slm_data)}");

                slm_filepath = os.path.splitext(self.filepath)[0] + "_" + str(amount_meshes) + ".slm"
                if self.create_debug_info:
                    # Write slm file
                    with open(slm_filepath, 'wb') as f:
                        f.write(slm_data)

                slm.import_slm(slm_data, {
                    "filepath": slm_filepath,
                    "create_debug_info": self.create_debug_info
                })

        if amount_meshes == 0:
            self._notify({'INFO'}, f"No mesh found in {self.filepath}")
        elif amount_imported_meshes == 0:
            self._notify({'INFO'}, f"No legit mesh found in {self.filepath}")
        else:
            self._notify({'INFO'}, f"Imported {amount_meshes} meshes from {self.filepath}")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

def menu_func_import(self, context):
    self.layout.operator(SLIZ_IMPORT_oxp.bl_idname, text="OXP (.oxp)")

def register():
    bpy.utils.register_class(SLIZ_IMPORT_oxp)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(SLIZ_IMPORT_oxp)

if __name__ == "__main__":
    register()
