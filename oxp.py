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
from . import utils

import os
import time
import tempfile
import traceback
from bpy_extras.io_utils import ImportHelper
import json
import zlib
from mathutils import Quaternion


class OXPParser():
    def __init__(self, **kwargs):
        self.create_debug_info = kwargs.get("create_debug_info", False)
        self.extract_lods = kwargs.get("extract_lods", False)
        self.create_collections = kwargs.get("create_collections", True)
        self.temp_dir = None
        self.oxp_data = None
        self.filepath = None
        self.amount_imported_meshes = 0
        self.amount_meshes = 0

    def parse_from_file(self, filepath):
        self.filepath = filepath

        with tempfile.TemporaryDirectory() as temp_dir:
            if self.create_debug_info:
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

    def get_prims_to_mesh_uuid(self, mesh_uuid):
        found_prims = []
        if self.oxp_data is None or not mesh_uuid:
            return found_prims
        prims = self.oxp_data.get("prim")
        if not isinstance(prims, dict):
            return found_prims

        for prim in prims.values():
            mesh = prim.get("mesh")
            # allow different ways of definign the uuid of the mesh in a prim
            if isinstance(mesh, str) and mesh == mesh_uuid:
                found_prims.append(prim)
            elif isinstance(mesh, dict):
                for value in mesh.values():
                    if value == mesh_uuid:
                        found_prims.append(prim)
        return found_prims

    def get_prim_by_uuid(self, prim_uuid):
        if self.oxp_data is None or not prim_uuid:
            return None
        prims = self.oxp_data.get("prim")
        if not isinstance(prims, dict):
            return None
        return prims.get(prim_uuid)

    def get_texture(self, uuid, alternative_name=None):
        if not uuid or self.oxp_data is None:
            return None
        if uuid == "00000000-0000-0000-0000-000000000000":
            return None

        # search in existing images first
        for image in bpy.data.images:
            if image.get("sl_uuid") == uuid:
                return image

        assets = self.oxp_data.get("asset")
        if not isinstance(assets, dict):
            return None
        texture_asset = assets.get(uuid)
        if not isinstance(texture_asset, dict) or texture_asset.get("type") != "texture":
            return None
        filepath = texture_asset.get("filepath")
        if not filepath or not os.path.exists(filepath):
            return None

        image = bpy.data.images.load(filepath)
        image["sl_uuid"] = uuid
        name = texture_asset.get("name")
        # use alternative_name if name from oxp data isn't available or an uuid
        if not name or (len(name) == 36 and name[8] == "-"):
            name = alternative_name
        if name:
            image.name = name
        image.pack()
        return image

    def get_text(self, uuid, alternative_name=None):
        if not uuid or self.oxp_data is None:
            return None
        if uuid == "00000000-0000-0000-0000-000000000000":
            return None

        # search in existing textblocks first
        for text in bpy.data.texts:
            if text.get("sl_uuid") == uuid:
                return text

        assets = self.oxp_data.get("asset")
        if not isinstance(assets, dict):
            return None
        text_asset = assets.get(uuid)
        if not isinstance(text_asset, dict):
            return None
        filepath = text_asset.get("filepath")
        if not filepath or not os.path.exists(filepath):
            return None

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        name = text_asset.get("name")
        # use alternative_name if name from oxp data isn't available or an uuid
        if not name or (len(name) == 36 and name[8] == "-"):
            name = alternative_name
        text = bpy.data.texts.new(name + ".lsl")
        text["sl_uuid"] = uuid
        text.write(content)
        return text

    def _get_material_from_gltf(self, gltf_data):
        # gltf_data is a json string in gltf format with materials inside
        try:
            gltf_data = json.loads(gltf_data)
            materials_data = gltf_data.get("materials", [])
            if  len(materials_data) < 1:
                return None
            images_data = gltf_data.get("images")
            textures_data = gltf_data.get("textures")

            # we take the first material (in sl we only have one inside)
            pbr_material = materials_data[0]
            self._resolve_textures_in_gltf_material(
                pbr_material, textures_data, images_data
            )
            return pbr_material
        except Exception as e:
            traceback.print_exc()
            print(f"Could not parse gltf material: {e}")
            return None

    def _resolve_textures_in_gltf_material(self, data, textures_data, images_data):
        # Takes gltf_data dict and resoles textures to their uris
        # texture keys are in the form of xxxxxTexture: { index }.
        # Also resolves their trnsforms and stores them as tuples in
        # xxxxxTextureTransform
        # material -> texures -> images -> uri
        if isinstance(data, dict):
            added = {};
            for key in data.keys():
                if key.endswith("Texture"):
                    texture_data = data[key]
                    if isinstance(texture_data, dict):
                        tex_ind = texture_data.get("index")
                        extensions = texture_data.get("extensions")
                        if extensions is not None:
                            transforms = extensions.get("KHR_texture_transform")
                            if transforms is not None:
                                offsets = transforms.get("offset", [0.0, 0.0])
                                scale = transforms.get("scale", [1.0, 1.0])
                                rotation = transforms.get("rotation", 0.0)
                                added[key + "Transform"] = (offsets[0], offsets[1], scale[0], scale[1], rotation)
                        if tex_ind is not None:
                            src_ind = textures_data[tex_ind]["source"]
                            data[key] = images_data[src_ind]["uri"]
            for key, value in added.items():
                data[key] = value
            for value in data.values():
                self._resolve_textures_in_gltf_material(value, textures_data, images_data)
        elif isinstance(data, list):
            for item in data:
                self._resolve_textures_in_gltf_material(item, textures_data, images_data)

    def _get_pbr_material(self, uuid):
        if not uuid or self.oxp_data is None:
            return None
        assets = self.oxp_data.get("asset")
        if not isinstance(assets, dict):
            return None
        material_asset = assets.get(uuid)
        if not isinstance(material_asset, dict):
            return None
        filepath = material_asset.get("filepath")
        if not filepath or not os.path.exists(filepath):
            return None

        try:
            # llsd binary file, with header and
            # { data: gltf_json, type: "GLTF 2.0", version: 1.1}
            llsd_parser = llsdz.parseobj()
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    if llsd_parser.parse(chunk):
                        break
            slmat_data, _ = llsd_parser.flush()
        except Exception as e:
            traceback.print_exc()
            print(f"Could not parse slmat material: {e}")
            return None

        if self.create_debug_info:
            tree_lines = []
            tree_lines.extend(utils.print_tree(slmat_data))
            tree_filepath = os.path.splitext(filepath)[0] + "_slmattree.txt"
            with open(tree_filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(tree_lines))

        pbr_material = self._get_material_from_gltf(slmat_data.get("data"))
        if pbr_material:
            name = material_asset.get("name")
            # set name only if its defined and not an uuid
            if name and not (len(name) == 36 and name[8] == "-"):
                pbr_material["sl_name"] = name
        return pbr_material

    def _add_unused_assets(self):
        # adds assets that are not used in any mesh
        assets = self.oxp_data.get("asset")
        if not isinstance(assets, dict):
            return None
        for uuid, asset_data in assets.items():
            asset_type = asset_data.get("type")
            if not asset_type:
                continue
            name = asset_data.get("name", uuid)
            match asset_type:
                # those methodes only add new objects if no current one exists
                case "texture":
                    self.get_texture(uuid, name)
                case "lsltext":
                    self.get_text(uuid, name)

    def _create_materials_for_prim(self, prim_data, name="slmat"):
        materials = []
        materials_data = prim_data.get("materials", [])
        textures_data = prim_data.get("texture", [])

        # PBR material that is referenced
        # from:
        #   { entries: [{ id: uuid, te_idx: index }, ...] }
        # to:
        #   { index: pbr_material }
        pbr_render_materials = {}
        if "render_material" in prim_data:
            pbr_render_materials = prim_data["render_material"]
            if isinstance(pbr_render_materials, dict):
                entries = pbr_render_materials.get("entries")
                if isinstance(entries, list):
                    for pbr_material_ref in entries:
                        if isinstance(pbr_material_ref, dict):
                            te_idx = pbr_material_ref.get("te_idx")
                            uuid = pbr_material_ref.get("id")
                            if te_idx is not None and uuid and uuid != "00000000-0000-0000-0000-000000000000":
                                pbr_material = self._get_pbr_material(uuid)
                                if pbr_material:
                                    pbr_material["sl_uuid"] = uuid
                                    pbr_render_materials[te_idx] = pbr_material
                                else:
                                    # material that exists but we dont have
                                    pbr_render_materials[te_idx] = { "sl_uuid": uuid }

        if not pbr_render_materials and not materials_data and not textures_data:
            return materials

        # ensure same length
        amount_materials = max(len(materials_data), len(textures_data))
        if len(materials_data) < amount_materials:
            materials_data += [None] * (amount_materials - len(materials_data))
        if len(textures_data) < amount_materials:
            textures_data += [None] * (amount_materials - len(textures_data))

        for i in range(amount_materials):
            material_name = f"{name}{i}"
            material_data = materials_data[i]
            texture_data = textures_data[i]
            pbr_material = pbr_render_materials.get(i)

            gltf_override = None
            if "gltf_override" in texture_data:
                gltf_override = self._get_material_from_gltf(texture_data["gltf_override"])
                if gltf_override:
                    pbr_material = utils.merge_dicts(pbr_material or {}, gltf_override)

            # create hash
            material_hash = utils.create_obj_hash({
                "pbr": pbr_material, "txd": texture_data, "mtd": material_data,
            })
            # resolve already existing material if possible
            mat = None
            for existing_material in bpy.data.materials:
                if existing_material.get("sl_hash") == material_hash:
                    mat = existing_material
                    break
            if mat is not None:
                materials.append(mat)
                continue

            # create material
            mat = bpy.data.materials.new(material_name)
            mat["sl_hash"] = material_hash
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links

            # known values in pbr_material:
            #   normalTexture: uuid
            #   emissiveTexture: uuid
            #   occlusionTexture: uuid
            #   emissiveFactor: [3 float]
            #   pbrMetallicRoughness: {
            #     baseColorTexture: uuid
            #     baseColorFactor: [4]
            #     metallicFactor: float
            #     roughnessFactor: float
            #   }
            #   alphaMode: str [ BLEND | MASK | OPAQUE ]
            #   alphaCutoff: float

            # create basic nodes
            nodes.clear()
            output = nodes.new("ShaderNodeOutputMaterial")
            principled = nodes.new("ShaderNodeBsdfPrincipled")
            output.location = (600, 0)
            principled.location = (300, 0)
            links.new(principled.outputs["BSDF"], output.inputs["Surface"])

            # texture uuids
            color_texture = texture_data.get("imageid")
            normal_texture = material_data.get("NormMap")
            specular_texture = material_data.get("SpecMap")
            emissive_texture = None
            orm_texture = None

            # offsets, scale and rotation
            # base color
            color_texture_os = texture_data.get("offsets", 0.0)
            color_texture_ot = texture_data.get("offsett", 0.0)
            color_texture_ss = texture_data.get("scales", 1.0)
            color_texture_st = texture_data.get("scalet", 1.0)
            color_texture_rot = texture_data.get("imagerot", 0.0)
            # normal
            normal_texture_os = material_data.get("NormOffsetX", 0)
            normal_texture_ot = material_data.get("NormOffsetY", 0)
            normal_texture_ss = material_data.get("NormRepeatX", 10000)
            normal_texture_st = material_data.get("NormRepeatY", 10000)
            normal_texture_rot = material_data.get("NormRotation", 0)
            normal_texture_os /= 10000.0
            normal_texture_ot /= 10000.0
            normal_texture_ss /= 10000.0
            normal_texture_st /= 10000.0
            normal_texture_rot /= 10000.0
            # specular
            specular_texture_os = material_data.get("SpecOffsetX", 0)
            specular_texture_ot = material_data.get("SpecOffsetY", 0)
            specular_texture_ss = material_data.get("SpecRepeatX", 10000)
            specular_texture_st = material_data.get("SpecRepeatY", 10000)
            specular_texture_rot = material_data.get("SpecRotation", 0)
            specular_texture_os /= 10000.0
            specular_texture_ot /= 10000.0
            specular_texture_ss /= 10000.0
            specular_texture_st /= 10000.0
            specular_texture_rot /= 10000.0
            # orm
            orm_texture_os = 0.0
            orm_texture_ot = 0.0
            orm_texture_ss = 1.0
            orm_texture_st = 1.0
            orm_texture_rot = 0.0
            # emissive
            emissive_texture_os = 0.0
            emissive_texture_ot = 0.0
            emissive_texture_ss = 1.0
            emissive_texture_st = 1.0
            emissive_texture_rot = 0.0

            # base values
            base_color_tint = texture_data.get("colors", [1.0, 1.0, 1.0, 1.0])
            alpha = base_color_tint[3]
            alpha_mode = material_data.get("DiffuseAlphaMode", 0)
            match alpha_mode:
                case 0:
                    alpha_mode = "OPAQUE"
                case 1:
                    alpha_mode = "BLEND"
                case 2:
                    alpha_mode = "MASK"
                case _:
                    # EMISSIVE would be 3, but we can't represent that
                    alpha_mode = "BLEND"
            # int 0 to 255
            alpha_cutoff = material_data.get("AlphaMaskCutoff", 0)
            alpha_cutoff /= 255

            if pbr_material:
                pbr_metallic_roughness = pbr_material.get("pbrMetallicRoughness")
                if isinstance(pbr_metallic_roughness, dict):
                    color_texture = pbr_metallic_roughness.get("baseColorTexture", color_texture)
                    if color_texture:
                        color_texture_os, color_texture_ot, color_texture_ss, color_texture_st, color_texture_rot = pbr_metallic_roughness.get("baseColorTextureTransform", (0.0, 0.0, 1.0, 1.0, 0.0))
                    base_color_tint = pbr_metallic_roughness.get("baseColorFactor", base_color_tint)
                    if "metallicFactor" in pbr_metallic_roughness:
                        principled.inputs["Metallic"].default_value = pbr_metallic_roughness["metallicFactor"]
                    if "roughnessFactor" in pbr_metallic_roughness:
                        principled.inputs["Roughness"].default_value = pbr_metallic_roughness["roughnessFactor"]
                normal_texture = pbr_material.get("normalTexture", normal_texture)
                if normal_texture:
                    normal_texture_os, normal_texture_ot, normal_texture_ss, normal_texture_st, normal_texture_rot = pbr_material.get("normalTextureTransform", (0.0, 0.0, 1.0, 1.0, 0.0))
                orm_texture = pbr_material.get("occlusionTexture")
                if orm_texture and "occlusionTextureTransform" in pbr_material:
                    orm_texture_os, orm_texture_ot, orm_texture_ss, orm_texture_st, orm_texture_rot = pbr_material["occlusionTextureTransform"]
                emissive_texture = pbr_material.get("emissiveTexture")
                if emissive_texture and "emissiveTextureTransform" in pbr_material:
                    emissive_texture_os, emissive_texture_ot, emissive_texture_ss, emissive_texture_st, emissive_texture_rot = pbr_material["emissiveTextureTransform"]
                if "sl_uuid" in pbr_material:
                    mat["sl_uuid"] = pbr_material["sl_uuid"]
                if gltf_override:
                    mat["sl_gltf_override"] = json.dumps(list(gltf_override.keys()))
                if "sl_name" in pbr_material:
                    mat.name = pbr_material["sl_name"]
                if "emissiveFactor" in pbr_material:
                    emission_factor = pbr_material["emissiveFactor"]
                    if len(emission_factor) == 3:
                        emission_factor.append(1.0)
                    principled.inputs["Emission Color"].default_value = emission_factor

                if "alphaMode" in pbr_material:
                    alpha_mode = pbr_material.get("alphaMode", "BLEND")
                if "alphaCutoff" in pbr_material:
                    alpha_cutoff = pbr_material.get("alphaCutoff", 0.0)

            # NOTE: Did not find a way to represent those in blender without
            # losing other informaion, so we store them in custom properties.
            # The least we have to store this way, the better.
            # Fullbright could be base_color -> emission link with emission
            # intensity to 1.0, but that would override the emission map.
            # We don't have any proper represenation of specular, because PBR
            # would override this anyway.
            mat["sl_fullbright"] = texture_data.get("fullbright", 0)
            mat["sl_glow"] = texture_data.get("glow", 0.0)
            spec_color = material_data.get("SpecColor", [255, 255, 255, 255]);
            spec_glossiness = material_data.get("SpecExp", 51);
            spec_environment = material_data.get("EnvIntensity", 0);
            mat["sl_specular_props"] = json.dumps({
                "color": [spec_color[0] / 255, spec_color[1] / 255, spec_color[2] / 255],
                "glossiness": spec_glossiness,
                "environment": spec_environment
            })

            # uuids to image
            color_texture = self.get_texture(color_texture, material_name)
            normal_texture = self.get_texture(normal_texture, material_name + "_n")
            specular_texture = self.get_texture(specular_texture, material_name + "_s")
            emissive_texture = self.get_texture(emissive_texture, material_name + "_e")
            orm_texture = self.get_texture(orm_texture, material_name + "_orm")

            vertical_start = 260
            # multply node for tint color
            multiply_node = None
            alpha_multiply_node = None
            if (base_color_tint[0] != 1.0 or base_color_tint[1] != 1.0 or base_color_tint[2] != 1.0) and color_texture:
                multiply_node = nodes.new("ShaderNodeMix")
                multiply_node.blend_type = "MULTIPLY"
                multiply_node.data_type = "RGBA"
                multiply_node.label = "Tint Color"
                multiply_node.location = (-100, vertical_start)
                links.new(multiply_node.outputs["Result"], principled.inputs["Base Color"])
                multiply_node.inputs["B"].default_value = base_color_tint
            else:
                principled.inputs["Base Color"].default_value = base_color_tint
            if alpha != 1.0 and color_texture and alpha_mode != "OPAQUE":
                alpha_multiply_node = nodes.new("ShaderNodeMath")
                alpha_multiply_node.operation = "MULTIPLY"
                alpha_multiply_node.label = "Alpha Factor"
                alpha_multiply_node.location = (70, vertical_start - 120)
                links.new(alpha_multiply_node.outputs["Value"], principled.inputs["Alpha"])
                alpha_multiply_node.inputs[1].default_value = alpha
                alpha_multiply_node.use_clamp = True
            else:
                principled.inputs["Alpha"].default_value = alpha

            if color_texture:
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.label = "Base Color Image"
                tex_node.image = color_texture
                tex_node.location = (-600, vertical_start)
                color_input_node = principled.inputs["Base Color"] if multiply_node is None else multiply_node.inputs["A"]
                alpha_input_node = principled.inputs["Alpha"] if alpha_multiply_node is None else alpha_multiply_node.inputs[0]

                links.new(tex_node.outputs["Color"], color_input_node)
                if alpha_mode == "BLEND": 
                    links.new(tex_node.outputs["Alpha"], alpha_input_node)
                elif alpha_mode == "MASK":
                    greater_than_node = nodes.new("ShaderNodeMath")
                    greater_than_node.operation = "GREATER_THAN"
                    greater_than_node.label = "Alpha Cutoff"
                    greater_than_node.location = (-270, vertical_start - 120)
                    links.new(greater_than_node.outputs["Value"], alpha_input_node)
                    links.new(tex_node.outputs["Alpha"], greater_than_node.inputs[0])
                    greater_than_node.inputs[1].default_value = alpha_cutoff
                if color_texture_os != 0.0 or color_texture_ot != 0.0 or color_texture_ss != 1.0 or color_texture_st != 1.0 or color_texture_rot != 0.0:
                    # need to transform
                    texcoord = nodes.new("ShaderNodeTexCoord")
                    texcoord.location = (-1000, vertical_start)
                    mapping_node = nodes.new("ShaderNodeMapping")
                    mapping_node.vector_type = "POINT"
                    mapping_node.location = (-800, vertical_start)
                    links.new(texcoord.outputs["UV"], mapping_node.inputs["Vector"])
                    links.new(mapping_node.outputs["Vector"], tex_node.inputs["Vector"])
                    mapping_node.inputs["Location"].default_value = (color_texture_os, color_texture_ot, 0.0)
                    mapping_node.inputs["Scale"].default_value    = (color_texture_ss, color_texture_st, 1.0)
                    mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, color_texture_rot)
                vertical_start -= 280

            if normal_texture:
                normal_tex = nodes.new("ShaderNodeTexImage")
                normal_tex.label = "Normal Map Image"
                normal_tex.image = normal_texture
                normal_tex.image.colorspace_settings.name = 'Non-Color'
                normal_map = nodes.new("ShaderNodeNormalMap")
                links.new(normal_tex.outputs["Color"], normal_map.inputs["Color"])
                links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
                normal_tex.location = (-600, vertical_start)
                normal_map.location = (-250, vertical_start)
                if normal_texture_os != 0.0 or normal_texture_ot != 0.0 or normal_texture_ss != 1.0 or normal_texture_st != 1.0 or normal_texture_rot != 0.0:
                    # need to transform
                    texcoord = nodes.new("ShaderNodeTexCoord")
                    texcoord.location = (-1000, vertical_start)
                    mapping_node = nodes.new("ShaderNodeMapping")
                    mapping_node.vector_type = 'POINT'           # important
                    mapping_node.location = (-800, vertical_start)
                    links.new(texcoord.outputs["UV"], mapping_node.inputs["Vector"])
                    links.new(mapping_node.outputs["Vector"], normal_tex.inputs["Vector"])
                    mapping_node.inputs["Location"].default_value = (normal_texture_os, normal_texture_ot, 0.0)
                    mapping_node.inputs["Scale"].default_value    = (normal_texture_ss, normal_texture_st, 1.0)
                    mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, normal_texture_rot)
                vertical_start -= 280

            if orm_texture:
                orm_node = nodes.new("ShaderNodeTexImage")
                orm_node.label = "ORM Map Image"
                orm_node.image = orm_texture
                orm_node.image.colorspace_settings.name = 'Non-Color'
                orm_node.location = (-600, vertical_start)
                # split RGB channels
                separate = nodes.new("ShaderNodeSeparateColor")
                separate.location =  (-250, vertical_start)
                links.new(orm_node.outputs["Color"], separate.inputs["Color"])
                links.new(separate.outputs["Green"], principled.inputs["Roughness"])
                links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
                if orm_texture_os != 0.0 or orm_texture_ot != 0.0 or orm_texture_ss != 1.0 or orm_texture_st != 1.0 or orm_texture_rot != 0.0:
                    # need to transform
                    texcoord = nodes.new("ShaderNodeTexCoord")
                    texcoord.location = (-1000, vertical_start)
                    mapping_node = nodes.new("ShaderNodeMapping")
                    mapping_node.vector_type = 'POINT'           # important
                    mapping_node.location = (-800, vertical_start)
                    links.new(texcoord.outputs["UV"], mapping_node.inputs["Vector"])
                    links.new(mapping_node.outputs["Vector"], orm_node.inputs["Vector"])
                    mapping_node.inputs["Location"].default_value = (orm_texture_os, orm_texture_ot, 0.0)
                    mapping_node.inputs["Scale"].default_value    = (orm_texture_ss, orm_texture_st, 1.0)
                    mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, orm_texture_rot)
                vertical_start -= 280

            if specular_texture:
                spec_tex = nodes.new("ShaderNodeTexImage")
                spec_tex.label = "Specular Map Image"
                spec_tex.image = specular_texture
                spec_tex.image.colorspace_settings.name = 'Non-Color'
                # specular is inverted roughness
                invert = nodes.new("ShaderNodeInvert")
                links.new(spec_tex.outputs["Color"], invert.inputs["Color"])
                spec_tex.location = (-600, vertical_start)
                invert.location = (-250, vertical_start)
                if not orm_texture:
                    # move it out of the way and don't connect if if orm exists
                    links.new(invert.outputs["Color"], principled.inputs["Roughness"])
                if specular_texture_os != 0.0 or specular_texture_ot != 0.0 or specular_texture_ss != 1.0 or specular_texture_st != 1.0 or specular_texture_rot != 0.0:
                    # need to transform
                    texcoord = nodes.new("ShaderNodeTexCoord")
                    texcoord.location = (-1000, vertical_start)
                    mapping_node = nodes.new("ShaderNodeMapping")
                    mapping_node.vector_type = 'POINT'           # important
                    mapping_node.location = (-800, vertical_start)
                    links.new(texcoord.outputs["UV"], mapping_node.inputs["Vector"])
                    links.new(mapping_node.outputs["Vector"], spec_tex.inputs["Vector"])
                    mapping_node.inputs["Location"].default_value = (specular_texture_os, specular_texture_ot, 0.0)
                    mapping_node.inputs["Scale"].default_value    = (specular_texture_ss, specular_texture_st, 1.0)
                    mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, specular_texture_rot)
                vertical_start -= 280

            if emissive_texture:
                emissive_node = nodes.new("ShaderNodeTexImage")
                emissive_node.label = "Emission Map Image"
                emissive_node.image = emissive_texture
                emissive_node.image.colorspace_settings.name = 'Non-Color'
                emissive_node.location = (-600, vertical_start)
                links.new(emissive_node.outputs["Color"], principled.inputs["Emission Color"])
                if emissive_texture_os != 0.0 or emissive_texture_ot != 0.0 or emissive_texture_ss != 1.0 or emissive_texture_st != 1.0 or emissive_texture_rot != 0.0:
                    # need to transform
                    texcoord = nodes.new("ShaderNodeTexCoord")
                    texcoord.location = (-1000, vertical_start)
                    mapping_node = nodes.new("ShaderNodeMapping")
                    mapping_node.vector_type = 'POINT'           # important
                    mapping_node.location = (-800, vertical_start)
                    links.new(texcoord.outputs["UV"], mapping_node.inputs["Vector"])
                    links.new(mapping_node.outputs["Vector"], emissive_node.inputs["Vector"])
                    mapping_node.inputs["Location"].default_value = (emissive_texture_os, emissive_texture_ot, 0.0)
                    mapping_node.inputs["Scale"].default_value    = (emissive_texture_ss, emissive_texture_st, 1.0)
                    mapping_node.inputs["Rotation"].default_value = (0.0, 0.0, emissive_texture_rot)

            materials.append(mat)
        return materials

    def _parse_oxp_data(self):
        if self.oxp_data is None:
            return

        if self.create_debug_info:
            # Print structure into debug file within same folder
            tree_lines = []
            tree_lines.extend(utils.print_tree(self.oxp_data))
            tree_filepath = os.path.splitext(self.filepath)[0] + "_oxptree.txt"
            with open(tree_filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(tree_lines))

        self._parse_meshes_in_oxp_data()
        self._add_unused_assets()

    def _parse_meshes_in_oxp_data(self):
        # all assets within "mesh_asset" are considered meshes
        # and all "type": "mesh" assets in "asset"
        mesh_assets = self.oxp_data.get("mesh_asset")
        assets = self.oxp_data.get("asset")
        if not isinstance(mesh_assets, dict):
            mesh_assets = {}
        if isinstance(assets, dict):
            for uuid, asset in self.oxp_data.get("asset", {}).items():
                if "type" in asset and asset["type"] == "mesh":
                    mesh_assets[uuid] = asset

        for mesh_uuid, mesh_asset in mesh_assets.items():
            slm_filepath = mesh_asset.get("filepath")
            if not slm_filepath:
                continue
            self.amount_meshes += 1

            # get prim_data of all prims that include this mesh
            prims_with_mesh = self.get_prims_to_mesh_uuid(mesh_uuid)
            if not prims_with_mesh:
                continue

            imported_mesh_objects = []
            with open(slm_filepath, 'rb') as f:
                # [{
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
                imported_mesh_objects = slm.import_slm(
                    f,
                    mesh_uuid, # name
                    filepath=slm_filepath,
                    create_debug_info=self.create_debug_info,
                    extract_lods=self.extract_lods
                )
            if not imported_mesh_objects:
                continue

            for i, prim_data in enumerate(prims_with_mesh):
                mesh_objects = imported_mesh_objects.copy()

                # duplicate existing object for all prims except last
                if i != len(prims_with_mesh) - 1:
                    for u, obj_data in enumerate(mesh_objects):
                        mesh_objects[u] = obj_data.copy()
                        mesh_objects[u]["obj"] = mesh_objects[u]["obj"].copy()

                name = prim_data.get("name")
                if not name:
                    name = mesh_uuid

                # collection we put the mesh into
                collection_name = None
                # get parent_uuid (if in linkset)
                parent_uuid = prim_data.get("parent")

                if self.create_collections:
                    collection_name = name
                    if parent_uuid:
                        parent_data = self.get_prim_by_uuid(parent_uuid)
                        if parent_data is not None:
                            collection_name = parent_data.get("name", parent_uuid)

                slm.move_objs_into_collections(mesh_objects, collection_name)

                # determine translation
                prim_scale=prim_data.get("scale", [1.0, 1.0, 1.0])
                prim_position = None
                prim_rotation = None
                if parent_uuid:
                    # If we are a child in a linkset, positions are relative to
                    # parent so we may apply them
                    prim_position = prim_data.get("position")
                    # quaternion
                    prim_rotation = prim_data.get("rotation")

                custom_properties = { "sl_uuid": mesh_uuid }
                # prim data that we cant interpret otherwise
                if "light" in prim_data:
                    light_data = prim_data["light"].copy()
                    # unused
                    del light_data["cutoff"]
                    # color alpha value is intensity
                    color = light_data.get("color", [1.0, 1.0, 1.0, 1.0])
                    light_data["intensity"] = color[3]
                    light_data["color"] = color[:3]
                    # projector lights
                    if "light_texture" in prim_data:
                        light_data["texture_uuid"] = prim_data["light_texture"].get("texture", "00000000-0000-0000-0000-000000000000")
                        light_texture_params = prim_data["light_texture"].get("params", [1.571, 0.0, 0.0])
                        light_data["fov"] = light_texture_params[0]
                        light_data["focus"] = light_texture_params[1]
                        light_data["ambiance"] = light_texture_params[2]
                    # { color: list<4>, falloff, radius, texture_uuid, fov, focus, ambiance }
                    custom_properties["sl_light"] = json.dumps(light_data)
                if "ExtraPhysics" in prim_data:
                    physics_data = prim_data["ExtraPhysics"]
                    custom_properties["sl_physics"] = json.dumps({
                        # 0: PRIM, 1: NONE, 2: CONVEX
                        "shape": physics_data.get("PhysicsShapeType", 2),
                        "gravity": physics_data.get("GravityMultiplier", 1.0),
                        "friction": physics_data.get("Friction", 0.6),
                        "density": physics_data.get("Density", 1000.0),
                        "bounciness": physics_data.get("Restitution", 0.5),
                        "material_type": prim_data.get("material", 3)
                    })

                materials = self._create_materials_for_prim(prim_data, name)

                for obj_data in mesh_objects:
                    type_name = obj_data["type_name"]
                    obj = obj_data["obj"]

                    suffix = f"_{type_name}" if type_name else ""
                    obj.name = name + suffix

                    for key, value in custom_properties.items():
                        obj[key] = value

                    if not obj_data["is_skinned"]:
                        if prim_scale:
                            scale_x, scale_y, scale_z = obj_data["normalized_scale"]
                            obj.scale = (prim_scale[0] / scale_x, prim_scale[1] / scale_y, prim_scale[2] / scale_z)
                        if prim_position:
                            obj.location = (prim_position[0], prim_position[1], prim_position[2])
                        if prim_rotation:
                            obj.rotation_mode = 'QUATERNION'
                            obj.rotation_quaternion = Quaternion((prim_rotation[3], prim_rotation[0], prim_rotation[1], prim_rotation[2]))

                    # apply materials (to object, not mesh data)
                    for i, material in enumerate(materials):
                        slot = obj.material_slots[i]
                        slot.link = 'OBJECT'
                        slot.material = material

                self.amount_imported_meshes += len(imported_mesh_objects)

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
        default=True,
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
