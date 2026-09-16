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

from . import llsdz
from . import utils
from . import skeleton

import os
import io
import time
import traceback
import zlib
import struct
import math
from bpy_extras.io_utils import ImportHelper
from mathutils import Quaternion, Vector, Matrix


def affine_transform(matrix, v):
    col0 = matrix.col[0].xyz
    col1 = matrix.col[1].xyz
    col2 = matrix.col[2].xyz
    trans = matrix.col[3].xyz

    return Vector((
        v.x * col0.x + v.y * col1.x + v.z * col2.x + trans.x,
        v.x * col0.y + v.y * col1.y + v.z * col2.y + trans.y,
        v.x * col0.z + v.y * col1.z + v.z * col2.z + trans.z
    ))

Rz90 = Matrix((
       (0.0, 1.0, 0.0, 0.0),
       (-1.0, 0.0, 0.0, 0.0),
       (0.0, 0.0, 1.0, 0.0),
       (0.0, 0.0, 0.0, 1.0)
       ))
Rz90I = Rz90.inverted()


def matrix_from_array(array):
    M = Matrix()
    for i in range(0,4):
        for j in range(0,4):
            M[i][j] = array[4*j + i]
    return M

def print_matrix(matrix, name):
    translation, rotation, scale = matrix.decompose()
    print(name)
    print(str(matrix))
    print(f"Translation:\n{translation}")
    print(f"Rotation:\n{tuple(math.degrees(a) for a in rotation.to_euler())}")
    print(f"Scale:\n{scale}\n")

def import_lod_mesh(lod_data, name, **kwargs):
    if not lod_data:
        return None
    collection = kwargs.get("collection")
    custom_properties = kwargs.get("custom_properties")
    prim_scale = kwargs.get("prim_scale")
    prim_position = kwargs.get("prim_position")
    prim_rotation = kwargs.get("prim_rotation")
    bind_shape_matrix = kwargs.get("bind_shape_matrix")
    joint_names = kwargs.get("joint_names")
    inverse_bind_matrices = kwargs.get("inverse_bind_matrices")
    bones = kwargs.get("bones")
    armature=kwargs.get("armature")
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

        # Get vertices
        vertices_offset = len(vertices) # total vertices offset of face
        data = face.get("Position", b'')
        num_vertices = len(data) // 6
        for i in range(num_vertices):
            offset = i * 6
            x = struct.unpack('<H', data[offset:offset+2])[0]
            y = struct.unpack('<H', data[offset+2:offset+4])[0]
            z = struct.unpack('<H', data[offset+4:offset+6])[0]
            # Unpack from 16-bit to float
            # Domain: [min, max] mapped to [0, 65535]
            x_float = domain_min[0] + (x / 65535.0) * (domain_max[0] - domain_min[0])
            y_float = domain_min[1] + (y / 65535.0) * (domain_max[1] - domain_min[1])
            z_float = domain_min[2] + (z / 65535.0) * (domain_max[2] - domain_min[2])
            pos = Vector((x_float, y_float, z_float))
            vertices.append(pos)

        # set SLIZ_UV_domain and SLIZ_UV_offset if uv seems legit
        if "TexCoord0" in face and "TexCoord0Domain" in face and len(face["TexCoord0"]) == num_vertices * 4:
            domain = [
                *face["TexCoord0Domain"]["Min"],
                *face["TexCoord0Domain"]["Max"],
            ]
            face["SLIZ_UV_domain"] = domain
            face["SLIZ_UV_offset"] = [0, 0]
            if domain[0] <= 0 and domain[2] <= 0:
                face["SLIZ_UV_offset"][0] = 1
            elif domain[0] >= 1 and domain[2] >= 1:
                face["SLIZ_UV_offset"][0] = -1
            if domain[1] <= 0 and domain[3] <= 0:
                face["SLIZ_UV_offset"][1] = 1
            elif domain[1] >= 1 and domain[3] >= 1:
                face["SLIZ_UV_offset"][1] = -1

        # Get triangles and UV
        data = face.get("TriangleList", b'')
        num_indices = len(data) // 2
        for i in range(0, num_indices, 3):
            if i + 2 < num_indices:
                idx1 = struct.unpack('<H', data[i*2:(i+1)*2])[0]
                idx2 = struct.unpack('<H', data[(i+1)*2:(i+2)*2])[0]
                idx3 = struct.unpack('<H', data[(i+2)*2:(i+3)*2])[0]
                triangles.append((
                    vertices_offset + idx1,
                    vertices_offset + idx2,
                    vertices_offset + idx3
                ))

        face_tirangle_offsets.append(len(triangles))
        face_vertices_offsets.append(len(vertices))
    if not vertices or not triangles:
        return None

    # create mesh
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], triangles)
    del vertices

    # assign UV
    uv_layer = mesh.uv_layers.new(name="SLIZ_UVMap")
    uv_data = uv_layer.data

    face_index = 0
    vertices_offset = 0
    next_triangle_offset = face_tirangle_offsets[face_index]
    domain = lod_data[face_index].get("SLIZ_UV_domain")
    offset = lod_data[face_index].get("SLIZ_UV_offset")
    data = lod_data[face_index].get("TexCoord0")
    for i, triangle in enumerate(triangles):
        while next_triangle_offset <= i:
            vertices_offset = face_vertices_offsets[face_index]
            face_index += 1
            next_triangle_offset = face_tirangle_offsets[face_index]
            domain = lod_data[face_index].get("SLIZ_UV_domain")
            offset = lod_data[face_index].get("SLIZ_UV_offset")
            data = lod_data[face_index].get("TexCoord0")
        loop_idx = i * 3

        if domain:
            for j, idx in enumerate(triangle):
                uv_offset = (idx - vertices_offset) * 4
                u = struct.unpack('<H', data[uv_offset:uv_offset+2])[0]
                v = struct.unpack('<H', data[uv_offset+2:uv_offset+4])[0]

                u_float = domain[0] + (u / 65535.0) * (domain[2] - domain[0]) + offset[0]
                v_float = domain[1] + (v / 65535.0) * (domain[3] - domain[1]) + offset[1]

                uv_data[i * 3 + j].uv = (u_float, v_float)
    del triangles

    # assign Normals
    normals = []
    for i, next_vertices_offset in enumerate(face_vertices_offsets):
        data = lod_data[i].get("Normal")
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

    # transform vertices to final position, get and apply weights if neccessary
    weights_per_joint = []
    if joint_names:
        weights_per_joint = [[] for _ in range(len(joint_names))]

    for i, face in enumerate(lod_data):
        vertices_offset = 0
        if i > 0:
            vertices_offset = face_vertices_offsets[i -1]
        next_vertices_offset = face_vertices_offsets[i]

        denormalization_matrix = Matrix()
        if bind_shape_matrix:
            denormalization_matrix = bind_shape_matrix
        elif "NormalizedScale" in face:
            scale_x, scale_y, scale_z = face["NormalizedScale"]
            denormalization_matrix = Matrix.Diagonal((scale_x, scale_y, scale_z, 1.0))
        else:
            continue
        normal_denormalization_matrix = denormalization_matrix.to_3x3().inverted().transposed()

        # Get weights as well with vertices
        weight_data = face.get("Weights", b'')
        weight_offset = 0
        weight_length = len(weight_data)

        u = vertices_offset
        while u < next_vertices_offset:
            vertice = mesh.vertices[u]
            pos = vertice.co

            vertex_influences = []
            vertex_weights = []
            while len(vertex_influences) < 4 and weight_offset < weight_length:
                joint_idx = weight_data[weight_offset]
                weight_offset += 1
                if joint_idx == 0xFF:
                    break

                weight_val = struct.unpack('<H', weight_data[weight_offset:weight_offset+2])[0]

                weight_float = weight_val / 65535.0
                # for skinning into rest pose
                vertex_influences.append(joint_idx)
                vertex_weights.append(weight_float)
                # for applying weights
                weights_per_joint[joint_idx].append((u, weight_float))

                weight_offset += 2

            vertice_transformation_matrix = denormalization_matrix
            normal_transformation_matrix = normal_denormalization_matrix

            # move into rest pose
            if bones and len(vertex_influences):
                skin_mat = Matrix([
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0]
                ])
                # normalizing weights is mostly unneccessary, meshes should never
                # need this, but firestorm is doing it as well, so we follow,
                # just in case that there are broken meshes that indeed need it
                weight_scale = sum(vertex_weights)

                # a skinning shader
                for w, joint_idx in enumerate(vertex_influences):
                    weight_float = vertex_weights[w] / weight_scale

                    inverse_bind_matrix = inverse_bind_matrices[joint_idx]
                    bone_matrix = bones[joint_idx].matrix_local
                    joint_matrix =  bone_matrix @ inverse_bind_matrix
                    skin_mat += joint_matrix * weight_float
                vertice_transformation_matrix = skin_mat @ denormalization_matrix
                normal_transformation_matrix = vertice_transformation_matrix.to_3x3().inverted().transposed()

            pos = affine_transform(vertice_transformation_matrix, pos)
            normals[u] = (normal_transformation_matrix @ normals[u]).normalized()

            vertice.co = pos
            u += 1

    mesh.update()
    mesh.validate(clean_customdata=False)
    mesh.normals_split_custom_set_from_vertices(normals)
    del normals

    obj = bpy.data.objects.new(mesh.name, mesh)

    # assign weights
    if weights_per_joint:
        for i, joint_weights in enumerate(weights_per_joint):
            joint_name = joint_names[i]
            vertex_group = obj.vertex_groups.new(name=joint_name)
            for vertex_idx, weight in joint_weights:
                vertex_group.add([vertex_idx], weight, 'REPLACE')
        del weights_per_joint

    # add custom properties if we have some
    if custom_properties:
        for key, value in custom_properties.items():
            obj[key] = value

    # assign Empty Material Slots
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

    obj.location = (0, 0, 0)
    # assign to collection
    if collection is not None:
        collection.objects.link(obj)
    else:
        bpy.context.scene.collection.objects.link(obj)

    # translate obect if we aren't bound to an armature
    if not bind_shape_matrix:
        if prim_scale:
            # assume that all faces have the same normalized scale
            scale_x, scale_y, scale_z = lod_data[0].get("NormalizedScale", [1.0, 1.0, 1.0])
            obj.scale = (prim_scale[0] / scale_x, prim_scale[1] / scale_y, prim_scale[2] / scale_z)
        if prim_position:
            obj.location = (prim_position[0], prim_position[1], prim_position[2])
        if prim_rotation:
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = Quaternion((prim_rotation[3], prim_rotation[0], prim_rotation[1], prim_rotation[2]))

    return obj

def read_compressed_llsd_from_stream(stream, offset, size):
    stream.seek(offset - stream.tell(), io.SEEK_CUR)

    decompressor = zlib.decompressobj()
    parser = llsdz.parseobj()
    size_left = size
    while size_left > 0:
        chunk_size = min(65536, size_left)
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
    data, _ = parser.flush()
    return data

def import_slm(stream, name, **kwargs):
    imported_mesh_objects = []
    filepath = kwargs.get("filepath")
    create_debug_info = kwargs.get("create_debug_info", False)
    extract_lods = kwargs.get("extract_lods", False)
    custom_properties = kwargs.get("custom_properties")
    collection_name = kwargs.get("collection_name")
    prim_scale = kwargs.get("prim_scale")
    prim_position = kwargs.get("prim_position")
    prim_rotation = kwargs.get("prim_rotation")

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
        tree_lines.extend(utils.print_tree(slm_metadata))
        tree_filepath = os.path.splitext(filepath)[0] + "_slmtree" + ".txt"
        with open(tree_filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(tree_lines))

    armature = None
    inverse_bind_matrices = None
    bind_shape_matrix = None
    joint_names = None
    bones = None
    if "skin" in slm_metadata:
        skin_offset = start_pos + header_size + slm_metadata["skin"]["offset"]
        skin_size = slm_metadata["skin"]["size"]
        skin_data = read_compressed_llsd_from_stream(stream, skin_offset, skin_size)

        if create_debug_info and filepath is not None:
            # Print structure into debug file within same folder
            tree_lines = []
            tree_lines.extend(utils.print_tree(skin_data))
            tree_filepath = os.path.splitext(filepath)[0] + "_skintree" + ".txt"
            with open(tree_filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(tree_lines))

        if "bind_shape_matrix" in skin_data:
            bind_shape_matrix = matrix_from_array(skin_data["bind_shape_matrix"])
        if "joint_names" in skin_data:
            joint_names = skin_data["joint_names"]

            # find existing armature or create new one
            armature = None
            for armature_obj in bpy.data.objects:
                if armature_obj.type != 'ARMATURE':
                    continue
                existing_bones = set(bone.name for bone in armature_obj.data.bones)
                if all(joint in existing_bones for joint in joint_names):
                    armature = armature_obj
            if armature is None:
                armature = skeleton.add_skeleton(bpy.context)

            bones = []
            for i, joint_name in enumerate(joint_names):
                bones.append(armature.data.bones[joint_name])
        if "inverse_bind_matrix" in skin_data:
            # corret inverse bind matrices according to standard skeleton
            bind_matrix_transforms = skeleton.get_bind_matrix_transform()

            inverse_bind_matrices = []
            for i, inverse_bind_matrix in enumerate(skin_data["inverse_bind_matrix"]):
                inverse_bind_matrix = matrix_from_array(inverse_bind_matrix)

                if "alt_inverse_bind_matrix" in skin_data:
                    inverse_bind_matrix = matrix_from_array(skin_data["alt_inverse_bind_matrix"][i])

                bind_matrix = inverse_bind_matrix.inverted()
                bind_matrix_transform = bind_matrix_transforms[joint_names[i]]

                bind_matrix = bind_matrix @ bind_matrix_transform

                inverse_bind_matrix = bind_matrix.inverted()
                inverse_bind_matrices.append(inverse_bind_matrix)
        if "alt_inverse_bind_matrix" in skin_data:
            # optional, if joint offsets are used then the alternate bind matrix
            # will contain translational information that will override the
            # default Second Life skeleton. Rotational and scaling components
            # are at this moment, unused.
            print("Model has alt_inverse_bind_matrix")
            for i, joint_name in enumerate(joint_names):
                print(joint_name)
                print(matrix_from_array(skin_data["alt_inverse_bind_matrix"][i]))
                print(matrix_from_array(skin_data["inverse_bind_matrix"][i]))
        if "pelvis_offset" in skin_data:
            # optional, used to provide a pelvis fixup for avatar rigs that
            # alter the default Second Life skeleton
            print("pelvis_offset" + str(skin_data["pelvis_offset"]))

    for lod_name, type_name in (("high_lod", None), ("medium_lod", "LOD2"), ("low_lod", "LOD1"), ("lowest_lod", "LOD0")):

        if lod_name in slm_metadata and (extract_lods or lod_name == "high_lod") and isinstance(slm_metadata[lod_name], dict):
            lod_offset = start_pos + header_size + slm_metadata[lod_name]["offset"]
            lod_size = slm_metadata[lod_name]["size"]
            lod_data = read_compressed_llsd_from_stream(stream, lod_offset, lod_size)

            suffix = f"_{type_name}" if type_name else ""
            prefix = f"{type_name}_" if type_name else ""

            if create_debug_info and filepath is not None:
                # Print structure into debug file within same folder
                tree_lines = []
                tree_lines.extend(utils.print_tree(lod_data))
                tree_filepath = os.path.splitext(filepath)[0] + suffix + "_meshtree" + ".txt"
                with open(tree_filepath, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(tree_lines))

            mesh_collection_name = prefix + collection_name if collection_name else type_name
            mesh_collection = None
            if mesh_collection_name:
                mesh_collection = bpy.data.collections.get(mesh_collection_name)
                if mesh_collection is None:
                    mesh_collection = bpy.data.collections.new(mesh_collection_name)
                    bpy.context.scene.collection.children.link(mesh_collection)
                    # hide if not high_lod
                    if type_name:
                        mesh_collection.hide_render = True
                        mesh_collection.color_tag = "COLOR_08"
                        for lc in bpy.context.view_layer.layer_collection.children:
                            if lc.collection.name == mesh_collection_name:
                                lc.hide_viewport = True
                                break

            mesh_object = import_lod_mesh(
                lod_data,
                name  + suffix,
                collection=mesh_collection,
                custom_properties=custom_properties,
                prim_scale=prim_scale,
                prim_position=prim_position,
                prim_rotation=prim_rotation,
                joint_names=joint_names,
                bind_shape_matrix=bind_shape_matrix,
                inverse_bind_matrices=inverse_bind_matrices,
                armature=armature,
                bones=bones
            )
            if mesh_object is not None:
                imported_mesh_objects.append(mesh_object)

                if armature is not None:
                    mesh_object.parent = armature
                    mesh_object.matrix_parent_inverse = armature.matrix_world.inverted()
                    mod = mesh_object.modifiers.new(name="Armature", type='ARMATURE')
                    mod.object = armature
                    mod.use_vertex_groups = True
    return imported_mesh_objects

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

                import_slm(f,
                    name,
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
