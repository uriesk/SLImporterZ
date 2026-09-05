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

# for reading OXP files (zlib compressed binary LLSD format)
# https://wiki.secondlife.com/wiki/LLSD
# reference implementation: https://github.com/secondlife/python-llsd
from . import llsdz

import os
import io
import time
import traceback
import zlib
import struct
from bpy_extras.io_utils import ImportHelper
from mathutils import Vector


def import_lod_mesh(lod_data, name, **kwargs):
    if not lod_data:
        return
    collection_name = kwargs.get("collection_name", "")
    custom_properties = kwargs.get("custom_properties", None)
    # lod_data:
    #  [{
    #    Normal,
    #    NormalizedScale: [x, y, z],
    #    Position,
    #    PositionDomain: { Max: [x, y, z], Min: [x, y, z] },
    #    TexCoord0,
    #    TexCoord0Domain: { Max: [u, v], Min: [u, v] },
    #    TriangleList,
    #  }, ...]
    face_tirangle_offsets = []
    # vertice offset per face, of the NEXT face
    # i.e.: face0 has 10 vertices and face1 has 5 = [10, 15]
    face_vertices_offsets = []

    vertices = []
    triangles = []
    # Create Mesh out of Vertices and Triangles
    for face in lod_data:
        if face.get("NoGeometry", False):
            face_tirangle_offsets.append(len(triangles))
            face_vertices_offsets.append(len(vertices))
            continue

        if "PositionDomain" in face:
            domain_max = face["PositionDomain"]["Max"]
            domain_min = face["PositionDomain"]["Min"]
        else:
            domain_max = [0.5, 0.5, 0.5]
            domain_min = [-0.5, -0.5, -0.5]
        scale_x, scale_y, scale_z = face.get("NormalizedScale", [1.0, 1.0, 1.0])

        vertices_offset = len(vertices)
        data = face.get("Position", b'')
        num_vertices = len(data) // 6
        for i in range(num_vertices):
            offset = i * 6
            x = struct.unpack('<H', data[offset:offset+2])[0]
            y = struct.unpack('<H', data[offset+2:offset+4])[0]
            z = struct.unpack('<H', data[offset+4:offset+6])[0]
            # Unpack from 16-bit to float
            # Domain: [min, max] mapped to [0, 65535]
            x_float = domain_min[0] + (x / 65535.0) * (domain_max[0] - domain_min[0]) * scale_x
            y_float = domain_min[1] + (y / 65535.0) * (domain_max[1] - domain_min[1]) * scale_y
            z_float = domain_min[2] + (z / 65535.0) * (domain_max[2] - domain_min[2]) * scale_z
            vertices.append(Vector((x_float, y_float, z_float)))

        data = face.get("TriangleList", b'')
        num_indices = len(data) // 2
        for i in range(0, num_indices, 3):
            if i + 2 < num_indices:
                idx1 = vertices_offset + struct.unpack('<H', data[i*2:(i+1)*2])[0]
                idx2 = vertices_offset + struct.unpack('<H', data[(i+1)*2:(i+2)*2])[0]
                idx3 = vertices_offset + struct.unpack('<H', data[(i+2)*2:(i+3)*2])[0]
                triangles.append((idx1, idx2, idx3))

        face_tirangle_offsets.append(len(triangles))
        face_vertices_offsets.append(len(vertices))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], triangles)
    del vertices
    del triangles

    # Assign UV
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uv_data = uv_layer.data
    for face in lod_data:
        if "TexCoord0" in face and "TexCoord0Domain" in face and len(face["TexCoord0"]) == len(face["Position"]) / 6 * 4:
            # precalculate that face UV is valid and merge its domain info
            face["SLIZ_UV_domain"] = [
                *face["TexCoord0Domain"]["Min"],
                *face["TexCoord0Domain"]["Max"],
            ]
    for loop in mesh.loops:
        vertex_idx = loop.vertex_index
        face = 0
        while face_vertices_offsets[face] <= vertex_idx:
            vertex_idx -= face_vertices_offsets[face]
            face += 1
        face = lod_data[face]
        if "SLIZ_UV_domain" in face:
            domain = face["SLIZ_UV_domain"]
            uv_offset = vertex_idx * 4
            data = face["TexCoord0"]
            u = struct.unpack('<H', data[uv_offset:uv_offset+2])[0]
            v = struct.unpack('<H', data[uv_offset+2:uv_offset+4])[0]

            u_float = domain[0] + (u / 65535.0) * (domain[2] - domain[0])
            v_float = domain[1] + (v / 65535.0) * (domain[3] - domain[1])
            uv_data[loop.index].uv = (u_float, v_float)

    # Assign Normals
    normals = []
    for i, next_vertices_offset in enumerate(face_vertices_offsets):
        data = lod_data[i].get("Normal", None)
        num_vertices = next_vertices_offset - len(normals)
        if data is not None and len(data) == num_vertices * 6:
            for u in range(num_vertices):
                offset = u * 6
                nx = struct.unpack('<H', data[offset:offset+2])[0]
                ny = struct.unpack('<H', data[offset+2:offset+4])[0]
                nz = struct.unpack('<H', data[offset+4:offset+6])[0]

                # Domain: [-1.0, 1.0] mapped to [0, 65535]
                nx_float = -1.0 + (nx / 65535.0) * 2.0
                ny_float = -1.0 + (ny / 65535.0) * 2.0
                nz_float = -1.0 + (nz / 65535.0) * 2.0
                normals.append(Vector((nx_float, ny_float, nz_float)))
        else:
            for u in range(num_vertices):
                normals.append(Vector((0, 0, 0)))
    mesh.normals_split_custom_set_from_vertices(normals)
    del normals

    obj = bpy.data.objects.new(mesh.name, mesh)
    # add custom properties if we have some
    if custom_properties:
        for key, value in custom_properties.items():
            obj[key] = value

    # Assigne Empty Material Slots
    for i in range(len(face_tirangle_offsets)):
        obj.data.materials.append(None)
    if mesh.polygons:
        current_face = 0;
        current_threshold = face_tirangle_offsets[current_face]
        for poly in mesh.polygons:
            while poly.index >= current_threshold:
                current_face += 1
                current_threshold = face_tirangle_offsets[current_face]
            poly.material_index = current_face

    mesh.update()
    mesh.validate(clean_customdata=False)
    obj.location = (0, 0, 0)
    # assign to collection
    if collection_name:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            collection = bpy.data.collections.new(collection_name)
            bpy.context.scene.collection.children.link(collection)
        collection.objects.link(obj)
    else:
        bpy.context.collection.objects.link(obj)


def import_slm(stream, name, **kwargs):
    filepath = kwargs.get("filepath", None)
    create_debug_info = kwargs.get("create_debug_info", False)
    extract_lods = kwargs.get("extract_lods", False)
    custom_properties = kwargs.get("custom_properties", None)

    # create stream if its not one, stream needs to be seekable
    if isinstance(stream, bytes):
        stream = io.BytesIO(stream)

    # SLM format
    # https://wiki.secondlife.com/wiki/Mesh/Mesh_Asset_Format
    # header as binary llsd map
    start_pos = stream.tell()
    parser = llsdz.parseobj()
    while True:
        chunk = stream.read(1024)
        if not chunk:
            break
        if parser.parse(chunk):
            break
    slm_metadata, header_size = parser.flush()

    if create_debug_info and filepath is not None:
        # Print structure into debug file within same folder
        tree_lines = []
        tree_lines.extend(llsdz.print_tree(slm_metadata))
        tree_filepath = os.path.splitext(filepath)[0] + "_slmtree" + ".txt"
        with open(tree_filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(tree_lines))

    for lod_name, type_name in (("high_lod", None), ("medium_lod", "LOD2"), ("low_lod", "LOD1"), ("lowest_lod", "LOD0")):

        if lod_name in slm_metadata and (extract_lods or lod_name == "high_lod") and isinstance(slm_metadata[lod_name], dict):
            lod_offset = start_pos + header_size + slm_metadata[lod_name]["offset"]
            lod_size = slm_metadata[lod_name]["size"]
            stream.seek(lod_offset - stream.tell(), io.SEEK_CUR)

            decompressor = zlib.decompressobj()
            parser = llsdz.parseobj()
            size_left = lod_size
            while size_left > 0:
                chunk_size = min(131072, size_left)
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                size_left -= chunk_size
                decompressed_chunk = decompressor.decompress(chunk)
                if decompressed_chunk:
                    if parser.parse(decompressed_chunk):
                        break
        
            final_chunk = decompressor.flush()
            if final_chunk and not parser.done:
                parser.parse(final_chunk)
            lod_data, _ = parser.flush()
            del decompressor
            del parser

            suffix = f"_{type_name}" if type_name else ""

            if create_debug_info and filepath is not None:
                # Print structure into debug file within same folder
                tree_lines = []
                tree_lines.extend(llsdz.print_tree(lod_data))
                tree_filepath = os.path.splitext(filepath)[0] + suffix + "_meshtree" + ".txt"
                with open(tree_filepath, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(tree_lines))

            import_lod_mesh(
                lod_data,
                name  + suffix,
                collection_name=type_name,
                custom_properties=custom_properties,
            )

    if extract_lods:
        for type_name in ("LOD2", "LOD1", "LOD0"):
            collection = bpy.data.collections.get(type_name)
            collection.hide_viewport = True
            collection.hide_render = True

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

        self._import_stage = "parsing SLM"

        try:
            with open(self.filepath, 'rb') as f:
                slm_data = f.read()
            import_slm(slm_data,
                filepath=self.filepath,
                create_debug_info=self.create_debug_info
            )
        except Exception as e:
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
