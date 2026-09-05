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
from . import llsdz

import os
import time
import tempfile
import traceback
from bpy_extras.io_utils import ImportHelper
import zlib


class OXPParser():
    def __init__(self, **kwargs):
        self.create_debug_info = kwargs.get("create_debug_info", False)
        self.extract_lods = kwargs.get("extract_lods", False)
        self.create_collections = kwargs.get("create_collections", True)
        self.oxp_data = None
        self.filepath = None
        self.amount_imported_meshes = 0
        self.amount_meshes = 0

    def parse_from_file(self, filepath):
        self.filepath = filepath
        # TODO: just for debugging
        # with tempfile.TemporaryDirectory() as temp_dir:
        if True:
            temp_dir = os.path.dirname(filepath)
            # OXP is zlib compressed binary llsd
            try:
                decompressor = zlib.decompressobj()
                llsd_parser = llsdz.parseobj(asset_folder=temp_dir)
                with open(filepath, 'rb') as f:
                    while True:
                        chunk = f.read(131072)
                        if not chunk:
                            break
                        decompressed_chunk = decompressor.decompress(chunk)
                        if decompressed_chunk:
                            if llsd_parser.parse(decompressed_chunk):
                                break

                    final_chunk = decompressor.flush()
                    if final_chunk and not llsd_parser.done:
                        llsd_parser.parse(final_chunk)
                self.oxp_data, _ = llsd_parser.flush()
            except zlib.error as e:
                raise Exception(f"Could not decompress file. {e}")
            except llsdz.error as e:
                raise Exception(f"Could not parse llsd in file. {e}")
            except Exception as e:
                raise Exception(f"Could not read file. {e}")
            finally:
                llsd_parser.destruct()
            del decompressor
            del llsd_parser
            self._parse_oxp_data()
        return self.amount_imported_meshes, self.amount_meshes

    def get_prim_to_mesh_uuid(self, mesh_uuid):
        if self.oxp_data is None or not mesh_uuid:
            return None
        mesh_uuid = str(mesh_uuid)
        prims = self.oxp_data.get("prim", None)
        if not isinstance(prims, dict):
            return None

        for prim in prims.values():
            mesh = prim.get("mesh", None)
            # allow different ways of definign the uuid of the mesh in a prim
            if (isinstance(mesh, str) and mesh == mesh_uuid) or (type(mesh).__name__ == "UUID" and str(mesh) == mesh_uuid):
                return prim
            if isinstance(mesh, dict):
                for value in mesh.values():
                    if str(value) == mesh_uuid:
                        return prim
        return None

    def get_prim_by_uuid(self, prim_uuid):
        if self.oxp_data is None or not prim_uuid:
            return None
        prims = self.oxp_data.get("prim", None)
        if not isinstance(prims, dict):
            return None
        return prims.get(str(prim_uuid), None)

    def _parse_oxp_data(self):
        if self.oxp_data is None:
            return

        if self.create_debug_info:
            # Print structure into debug file within same folder
            tree_lines = []
            tree_lines.extend(llsdz.print_tree(self.oxp_data))
            tree_filepath = os.path.splitext(self.filepath)[0] + "_oxptree.txt"
            with open(tree_filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(tree_lines))

        self._parse_meshes_in_oxp_data()

    def _parse_meshes_in_oxp_data(self):
        mesh_assets = self.oxp_data.get("mesh_asset", None)
        if not isinstance(mesh_assets, dict):
            print("No mesh assets")
            return

        for mesh_uuid, mesh_asset in mesh_assets.items():
            slm_filepath = mesh_asset.get("filepath", None)
            if not slm_filepath or "type" not in mesh_asset or mesh_asset["type"] != "mesh":
                continue
            self.amount_meshes += 1
            print(f"Found mesh {mesh_uuid}: {slm_filepath}")

            prim_data = self.get_prim_to_mesh_uuid(mesh_uuid)
            if prim_data is None:
                print("no prim data")
                continue
            name = prim_data.get("name", mesh_uuid)

            # collection we put the mesh into
            collection_name = None
            if self.create_collections:
                collection_name = name
                if "parent" in prim_data:
                    parent_uuid = prim_data["parent"]
                    parent_data = self.get_prim_by_uuid(parent_uuid)
                    if parent_data is not None:
                        collection_name = parent_data.get("name", parent_uuid)

            with open(slm_filepath, 'rb') as f:
                slm.import_slm(
                    f,
                    name,
                    filepath=slm_filepath,
                    create_debug_info=self.create_debug_info,
                    extract_lods=self.extract_lods,
                    custom_properties={ "sl_uuid": mesh_uuid },
                    collection_name=collection_name
                )

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
    create_collections: bpy.props.BoolProperty(
        name="Create Collection per Linkset",
        description=(
            "Create a Collection for every imported Linkset"
        ),
        default=False,
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
        oxp_parser = OXPParser(
            create_debug_info=self.create_debug_info,
            extract_lods=self.extract_lods,
            create_collections=self.create_collections
        )
        try:
            amount_imported_meshes, amount_meshes = oxp_parser.parse_from_file(self.filepath)
        except Exception as e:
            self._notify({'ERROR'}, str(e))
            return {'CANCELLED'}

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
