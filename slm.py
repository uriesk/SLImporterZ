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
import traceback
import zlib
import struct
import math
from mathutils import Vector, Matrix


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

def move_objs_into_collections(imported_mesh_objects, collection_name=None):
    # move lod objects into collections
    # @param imported_mesh_objects [{
    #   lod_name: "high_lod",
    #   type_name: None,
    #   is_skinned: False,
    #   obj,
    # }{
    #   lod_name: "lowest_lod",
    #   type_name: "LOD0",
    #   is_skinned: False,
    #   obj,
    # }, ...]

    for obj_data in imported_mesh_objects:
        type_name = obj_data["type_name"]
        prefix = f"{type_name}_" if type_name else ""
        obj = obj_data["obj"]
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

        # assign to collection
        if mesh_collection is not None:
            mesh_collection.objects.link(obj)
        else:
            bpy.context.scene.collection.objects.link(obj)

def import_lod_mesh(lod_data, name, **kwargs):
    if not lod_data:
        return None
    bind_shape_matrix = kwargs.get("bind_shape_matrix")
    joint_names = kwargs.get("joint_names")
    inverse_bind_matrices = kwargs.get("inverse_bind_matrices")
    skin_to_armature = kwargs.get("skin_to_armature", False)
    bones = kwargs.get("bones")
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
            if skin_to_armature and bones and len(vertex_influences):
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
    # @return [{
    #   lod_name: "high_lod",
    #   type_name: None,
    #   is_skinned: False,
    #   normalized_scale: [1.0, 1.0, 1.0]
    #   obj,
    # }{
    #   lod_name: "lowest_lod",
    #   type_name: "LOD0",
    #   is_skinned: False,
    #   normalized_scale: [1.0, 1.0, 1.0]
    #   obj,
    # }, ...]
    imported_mesh_objects = []
    filepath = kwargs.get("filepath")
    create_debug_info = kwargs.get("create_debug_info", False)
    extract_lods = kwargs.get("extract_lods", False)

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
    bake_to_existing_armature = False
    inverse_bind_matrices = None
    bind_shape_matrix = None
    joint_names = None
    bones = None
    old_bone_matrices = {}
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
            for armature_obj in bpy.data.objects:
                if armature_obj.type != 'ARMATURE':
                    continue
                existing_bones = set(bone.name for bone in armature_obj.data.bones)
                if all(joint in existing_bones for joint in joint_names):
                    armature = armature_obj
                    bake_to_existing_armature = True
            if armature is None:
                armature = skeleton.add_skeleton(bpy.context)

            bones = []
            for i, joint_name in enumerate(joint_names):
                bone = armature.data.bones[joint_name]
                bone.use_deform = True
                bones.append(bone)

            if "inverse_bind_matrix" in skin_data:
                # corret inverse bind matrices according to standard skeleton
                bind_matrix_transforms = skeleton.get_bind_matrix_transforms()
                inverse_bind_matrices = []

                if not bake_to_existing_armature:
                    bpy.ops.object.mode_set(mode='EDIT')

                debug_text = f"bind shape matrix:\n{str(bind_shape_matrix)}\n\n"

                for i, inverse_bind_matrix in enumerate(skin_data["inverse_bind_matrix"]):
                    joint_name = joint_names[i]
                    inverse_bind_matrix = matrix_from_array(inverse_bind_matrix)

                    # NOTE: not implemented yet, ignore alt_inverse_bind_matrix, we
                    # only test meshes that do not have this currently
                    # if "alt_inverse_bind_matrix" in skin_data:
                    #     inverse_bind_matrix = matrix_from_array(skin_data["alt_inverse_bind_matrix"][i])

                    bind_matrix_transform = bind_matrix_transforms[joint_name]
                    bind_matrix = inverse_bind_matrix.inverted()

                    if create_debug_info:
                        translation, rotation, scale = bind_matrix.decompose()
                        debug_text += joint_name + "\n" + str(bind_matrix) + f"\nTranslation:    {translation}\nRotation:    {tuple(math.degrees(a) for a in rotation.to_euler())}\nScale:    {scale}\n\n"

                    bind_matrix = bind_matrix @ bind_matrix_transform

                    if not bake_to_existing_armature:
                        armature_bone = armature.data.edit_bones[joint_name]
                        sx, sy, sz = bind_matrix.to_scale()
                        scale_matrix = Matrix.Diagonal((sx, sy, sz, 1.0))
                        old_bone_matrices[joint_names[i]] = armature_bone.matrix.copy() @ scale_matrix.inverted()
                        armature_bone.matrix = bind_matrix

                    inverse_bind_matrix = bind_matrix.inverted()
                    inverse_bind_matrices.append(inverse_bind_matrix)

                if create_debug_info and filepath is not None:
                    ibm_filepath = os.path.splitext(filepath)[0] + "_ibm" + ".txt"
                    with open(ibm_filepath, 'w', encoding='utf-8') as f:
                        f.write(debug_text)

                if not bake_to_existing_armature:
                    bpy.ops.object.mode_set(mode='EDIT')

        # NOTE: not implemented yet, ignore alt_inverse_bind_matrix, we only
        # test meshes that do not have this currently
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
        # NOTE: not implemented yet, ignore pelvis_offset, we only test meshes
        # that do not have this currently
        if "pelvis_offset" in skin_data:
            # optional, used to provide a pelvis fixup for avatar rigs that
            # alter the default Second Life skeleton
            print("pelvis_offset" + str(skin_data["pelvis_offset"]))

    for lod_name, type_name in (("high_lod", None), ("medium_lod", "LOD2"), ("low_lod", "LOD1"), ("lowest_lod", "LOD0"), ("physics_mesh", "PHYS")):

        if lod_name in slm_metadata and (extract_lods or lod_name == "high_lod") and isinstance(slm_metadata[lod_name], dict):
            lod_offset = start_pos + header_size + slm_metadata[lod_name]["offset"]
            lod_size = slm_metadata[lod_name]["size"]
            lod_data = read_compressed_llsd_from_stream(stream, lod_offset, lod_size)

            suffix = f"_{type_name}" if type_name else ""

            if create_debug_info and filepath is not None:
                # Print structure into debug file within same folder
                tree_lines = []
                tree_lines.extend(utils.print_tree(lod_data))
                tree_filepath = os.path.splitext(filepath)[0] + suffix + "_meshtree" + ".txt"
                with open(tree_filepath, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(tree_lines))

            mesh_object = import_lod_mesh(
                lod_data,
                name  + suffix,
                joint_names=joint_names,
                bind_shape_matrix=bind_shape_matrix,
                inverse_bind_matrices=inverse_bind_matrices,
                bones=bones,
                skin_to_armature=bake_to_existing_armature,
            )
            if mesh_object is not None:
                imported_mesh_objects.append({
                    "lod_name": lod_name,
                    "type_name": type_name,
                    "is_skinned": bool(bind_shape_matrix),
                    # assume that all faces have the same normalized scale
                    "normalized_scale": lod_data[0].get("NormalizedScale", [1.0, 1.0, 1.0]),
                    "obj": mesh_object
                })

                if armature is not None:
                    mesh_object.parent = armature
                    mesh_object.matrix_parent_inverse = armature.matrix_world.inverted()
                    mod = mesh_object.modifiers.new(name="Armature", type='ARMATURE')
                    mod.object = armature
                    mod.use_vertex_groups = True

    if armature is not None and not bake_to_existing_armature:
        skeleton.set_pose_matrices(armature, old_bone_matrices)

    return imported_mesh_objects
