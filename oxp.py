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

    def get_prim_to_mesh_uuid(self, mesh_uuid):
        if self.oxp_data is None or not mesh_uuid:
            return None
        prims = self.oxp_data.get("prim")
        if not isinstance(prims, dict):
            return None

        for prim in prims.values():
            mesh = prim.get("mesh")
            # allow different ways of definign the uuid of the mesh in a prim
            if isinstance(mesh, str) and mesh == mesh_uuid:
                return prim
            if isinstance(mesh, dict):
                for value in mesh.values():
                    if value == mesh_uuid:
                        return prim
        return None

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
        # takes gltf_data dict and resoles textures to their uris
        # texture keys are in the form of xxxxxTexture: { index }
        # material -> texures -> images -> uri
        if isinstance(data, dict):
            for key in data.keys():
                if key.endswith("Texture"):
                    texture_data = data[key]
                    if isinstance(texture_data, dict):
                        tex_ind = texture_data.get("index")
                        if tex_ind is not None:
                            src_ind = textures_data[tex_ind]["source"]
                            data[key] = images_data[src_ind]["uri"]
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
                            if te_idx is not None and uuid:
                                pbr_material = self._get_pbr_material(uuid)
                                if pbr_material:
                                    pbr_material["sl_uuid"] = uuid
                                    pbr_render_materials[te_idx] = pbr_material

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

            # create material
            mat = bpy.data.materials.new(material_name)
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links

            if "gltf_override" in texture_data:
                gltf_override = self._get_material_from_gltf(texture_data["gltf_override"])
                if gltf_override:
                    pbr_material = utils.merge_dicts(pbr_material or {}, gltf_override)
                    mat["sl_gltf_override"] = json.dumps(list(gltf_override.keys()))

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
            #   alphaCutoff: float

            # create basic nodes
            nodes.clear()
            output = nodes.new("ShaderNodeOutputMaterial")
            principled = nodes.new("ShaderNodeBsdfPrincipled")
            output.location = (300, 0)
            principled.location = (0, 0)
            links.new(principled.outputs["BSDF"], output.inputs["Surface"])

            # texture uuids
            color_texture = texture_data.get("imageid")
            normal_texture = material_data.get("NormMap")
            specular_texture = material_data.get("SpecMap")
            emissive_texture = None
            orm_texture = None

            # base values
            base_color_tint = texture_data.get("colors", [1.0, 1.0, 1.0, 1.0])

            if pbr_material:
                pbr_metallic_roughness = pbr_material.get("pbrMetallicRoughness")
                if isinstance(pbr_metallic_roughness, dict):
                    color_texture = pbr_metallic_roughness.get("baseColorTexture", color_texture)
                    base_color_tint = pbr_metallic_roughness.get("baseColorFactor", base_color_tint)
                    if "metallicFactor" in pbr_metallic_roughness:
                        principled.inputs["Metallic"].default_value = pbr_metallic_roughness["metallicFactor"]
                    if "roughnessFactor" in pbr_metallic_roughness:
                        principled.inputs["Roughness"].default_value = pbr_metallic_roughness["roughnessFactor"]
                normal_texture = pbr_material.get("normalTexture", normal_texture)
                orm_texture = pbr_material.get("occlusionTexture")
                emissive_texture = pbr_material.get("emissiveTexture")
                if "sl_uuid" in pbr_material:
                    mat["sl_uuid"] = pbr_material["sl_uuid"]
                if "sl_name" in pbr_material:
                    mat.name = pbr_material["sl_name"]
                if "emissiveFactor" in pbr_material:
                    emission_factor = pbr_material["emissiveFactor"]
                    if len(emission_factor) == 3:
                        emission_factor.append(1.0)
                    principled.inputs["Emission Color"].default_value = emission_factor

            mat["sl_fullbright"] = texture_data.get("fullbright", 0)
            mat["sl_glow"] = texture_data.get("glow", 0.0)
            # TODO could multiply this on color_texture,
            # hoever, it is easier to recreate when on the default value
            principled.inputs["Base Color"].default_value = base_color_tint

            # uuids to image
            color_texture = self.get_texture(color_texture, material_name)
            normal_texture = self.get_texture(normal_texture, material_name + "_n")
            specular_texture = self.get_texture(specular_texture, material_name + "_s")
            emissive_texture = self.get_texture(emissive_texture, material_name + "_e")
            orm_texture = self.get_texture(orm_texture, material_name + "_orm")

            if color_texture:
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.image = color_texture
                links.new(tex_node.outputs["Color"], principled.inputs["Base Color"])
                if color_texture.depth == 32: 
                    links.new(tex_node.outputs["Alpha"], principled.inputs["Alpha"])
                tex_node.location = (-300, 300)

            if normal_texture:
                normal_tex = nodes.new("ShaderNodeTexImage")
                normal_tex.image = normal_texture
                normal_tex.image.colorspace_settings.name = 'Non-Color'
                normal_map = nodes.new("ShaderNodeNormalMap")
                links.new(normal_tex.outputs["Color"], normal_map.inputs["Color"])
                links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
                normal_tex.location = (-600, -20)
                normal_map.location = (-250, -15)

            if orm_texture:
                orm_node = nodes.new("ShaderNodeTexImage")
                orm_node.image = orm_texture
                orm_node.image.colorspace_settings.name = 'Non-Color'
                orm_node.location = (-600, -300)
                # split RGB channels
                separate = nodes.new("ShaderNodeSeparateColor")
                separate.location =  (-300, -260)
                links.new(orm_node.outputs["Color"], separate.inputs["Color"])
                links.new(separate.outputs["Green"], principled.inputs["Roughness"])
                links.new(separate.outputs["Blue"], principled.inputs["Metallic"])

            if specular_texture:
                spec_tex = nodes.new("ShaderNodeTexImage")
                spec_tex.image = specular_texture
                spec_tex.image.colorspace_settings.name = 'Non-Color'
                # specular is inverted roughness
                invert = nodes.new("ShaderNodeInvert")
                links.new(spec_tex.outputs["Color"], invert.inputs["Color"])
                if orm_texture:
                    # move it out of the way and don't connect if if orm exists
                    spec_tex.location = (-600, -600)
                    invert.location = (-300, -560)
                else:
                    spec_tex.location = (-600, -300)
                    invert.location = (-300, -260)
                    links.new(invert.outputs["Color"], principled.inputs["Roughness"])

            if emissive_texture:
                emissive_node = nodes.new("ShaderNodeTexImage")
                emissive_node.image = emissive_texture
                emissive_node.image.colorspace_settings.name = 'Non-Color'
                emissive_node.location = (-300, -500)
                links.new(emissive_node.outputs["Color"], principled.inputs["Emission"])

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

            prim_data = self.get_prim_to_mesh_uuid(mesh_uuid)
            if prim_data is None:
                continue
            name = prim_data.get("name")
            if not name:
                name = mesh_uuid

            # collection we put the mesh into
            collection_name = None
            # get parent_uuid (if in linkset)
            parent_uuid = prim_data.get("parent")
            if parent_uuid:
                parent_uuid = parent_uuid

            if self.create_collections:
                collection_name = name
                if parent_uuid:
                    parent_data = self.get_prim_by_uuid(parent_uuid)
                    if parent_data is not None:
                        collection_name = parent_data.get("name", parent_uuid)

            # determine translation
            prim_scale = prim_data.get("scale")
            prim_position = None
            prim_rotation = None
            if parent_uuid:
                # If we are a child in a linkset, positions are relative to
                # parent so we may apply them
                prim_position = prim_data.get("position")
                # quaternion
                prim_rotation = prim_data.get("rotation")

            with open(slm_filepath, 'rb') as f:
                imported_mesh_objects = slm.import_slm(
                    f,
                    name,
                    filepath=slm_filepath,
                    create_debug_info=self.create_debug_info,
                    extract_lods=self.extract_lods,
                    custom_properties={ "sl_uuid": mesh_uuid },
                    collection_name=collection_name,
                    prim_scale=prim_data.get("scale", [1.0, 1.0, 1.0])
                )

            # apply materials
            materials = self._create_materials_for_prim(prim_data, name)
            for obj in imported_mesh_objects:
                for i, material in enumerate(materials):
                    obj.data.materials[i] = material

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
            # TODO: only for debugging
            raise e
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
