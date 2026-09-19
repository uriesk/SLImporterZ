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
import json

from . import utils


def lsl_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')

def _get_trans(node):
    # get translation from image node
    texture_os = 0.0
    texture_ot = 0.0
    texture_ss = 1.0
    texture_st = 1.0
    texture_rot = 0.0
    if node.inputs["Vector"].links:
        mapping_node = node.inputs["Vector"].links[0].from_node
        if mapping_node.bl_idname == "ShaderNodeMapping":
            texture_os, texture_ot, _ = mapping_node.inputs["Location"].default_value
            texture_ss, texture_st, _ = mapping_node.inputs["Scale"].default_value
            _, _, texture_rot = mapping_node.inputs["Rotation"].default_value

    return (f"{texture_os:.6f}", f"{texture_ot:.6f}", f"{texture_ss:.6f}", f"{texture_st:.6f}", f"{texture_rot:.3f}")

def create_lsl_script():
    # creates lsl script and object tree for setting properties and textures
    # by uuid
    objects_tree = {}
    lsl_text = """vector WHITE = <1.0, 1.0, 1.0>;
string EMPTY_STRING = "";

default
{
    state_entry()
    {
        list used_names = [];

        integer i = llGetNumberOfPrims();
        integer offset = 0;
        if (i > 1)
        {
            offset += 1;
        }

        while (i >= 0)
        {
            i -= 1;
            integer link_num = i + offset;
            if (i < 0)
            {
                link_num = offset;
            }

            string name = llGetLinkName(link_num);
            integer did_set = TRUE;\n"""
    indent = "            "

    amount_added = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        object_tree = []
        objects_tree[obj.name] = object_tree

        if amount_added == 0:
            lsl_text += f"\n{indent}if (name == \"{lsl_escape(obj.name)}\" || (i == -1 && llListFindList(used_names, [\"{lsl_escape(obj.name)}\"]) == -1))\n{indent}{{\n"
        else:
            lsl_text += f"\n{indent}else if (name == \"{lsl_escape(obj.name)}\" || (i == -1 && llListFindList(used_names, [\"{lsl_escape(obj.name)}\"]) == -1))\n{indent}{{\n"
        subindent = indent + "    "
        parindent = subindent + "    "
        lsl_text += f"{subindent}used_names += [\"{lsl_escape(obj.name)}\"];\n"
        amount_added += 1

        params = []

        for i, material in enumerate(obj.data.materials):
            # recreate face properties based on material
            face_tree = {}
            object_tree.append(face_tree)

            # pbr material
            is_pbr_material = False
            # values that got overridden
            pbr_overrides = []
            if "sl_uuid" in material:
                is_pbr_material = True
                face_tree["PBR Material"] = material["sl_uuid"]
                params.append(f"\n{parindent}PRIM_RENDER_MATERIAL, {i}, \"{lsl_escape(material["sl_uuid"])}\"")
                if "sl_gltf_override" in material:
                    pbr_overrides = json.loads(material["sl_gltf_override"])

            # find principled bsdf node that is connected to surface output
            if not material.use_nodes:
                continue
            nodes = material.node_tree.nodes
            principled = None
            for node in nodes:
                if node.bl_idname == 'ShaderNodeOutputMaterial':
                    surface_input = node.inputs.get('Surface')
                    if surface_input and surface_input.links:
                        from_node = surface_input.links[0].from_node
                        if from_node.bl_idname == 'ShaderNodeBsdfPrincipled':
                            principled = from_node
                    break
            if not principled:
                continue

            # See https://wiki.secondlife.com/wiki/LlSetPrimitiveParams

            if "sl_fullbright" in material and material["sl_fullbright"] != 0:
                params.append(f"\n{parindent}PRIM_FULLBRIGHT, {i}, TRUE")
            if "sl_glow" in material and material["sl_glow"] != 0.0:
                params.append(f"\n{parindent}PRIM_GLOW, {i}, {material["sl_glow"]:.3f}")

            # tint color and alpha
            tint_color = "WHITE"
            alpha = 1.0

            # alpha modes
            alpha_mode = "PRIM_ALPHA_MODE_NONE"
            alpha_cutoff = 0.0
            alpha_input = principled.inputs["Alpha"]
            if alpha_input.links:
                from_node = alpha_input.links[0].from_node
                if from_node.bl_idname == "ShaderNodeMath" and from_node.operation == "MULTIPLY":
                    alpha = from_node.inputs[1].default_value
                    from_node_input = from_node.inputs[0]
                    if from_node_input.links:
                        from_node = from_node_input.links[0].from_node
                if from_node.bl_idname == "ShaderNodeTexImage":
                    alpha_mode = "PRIM_ALPHA_MODE_BLEND"
                elif from_node.bl_idname == "ShaderNodeMath" and from_node.operation == "GREATER_THAN":
                    alpha_mode = "PRIM_ALPHA_MODE_MASK"
                    alpha_cutoff = from_node.inputs[1].default_value
            else:
                alpha = principled.inputs["Alpha"].default_value
            params.append(f"\n{parindent}PRIM_ALPHA_MODE, {i}, {alpha_mode}, {round(alpha_cutoff * 255)}")

            # base color
            base_color_input = principled.inputs["Base Color"]
            if base_color_input.links:
                from_node = base_color_input.links[0].from_node
                if from_node.bl_idname == "ShaderNodeMix":
                    tint_color = from_node.inputs["B"].default_value
                    tint_color = f"<{tint_color[0]:.2f}, {tint_color[1]:.2f}, {tint_color[2]:.2f}>"
                    from_node_input = from_node.inputs["A"]
                    if from_node_input.links:
                        from_node = from_node_input.links[0].from_node
                if from_node.bl_idname == "ShaderNodeTexImage":
                    texture_os, texture_ot, texture_ss, texture_st, texture_rot = _get_trans(from_node)

                    image = from_node.image
                    if "sl_uuid" in image:
                        face_tree["Base Color Texture"] = image["sl_uuid"]
                        # blinn Phong
                        # [ string texture, vector repeats, vector offsets, float rotation_in_radians ]
                        params.append(f"\n{parindent}PRIM_TEXTURE, {i}, \"{lsl_escape(image["sl_uuid"])}\", <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}")
                        # pbr if overridden
                        if "pbrMetallicRoughness" in pbr_overrides:
                            # [ string texture, vector repeats, vector offsets, float rotation_in_radians, vector color, float alpha, integer gltf_alpha_mode, float alpha_mask_cutoff, integer double_sided ]
                            params.append(f"\n{parindent}PRIM_GLTF_BASE_COLOR, {i}, \"{lsl_escape(image["sl_uuid"])}\", <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}, {tint_color}, {alpha:.2f}, {alpha_mode}, {alpha_cutoff:.2f}, EMPTY_STRING")
            else:
                tint_color = principled.inputs["Base Color"].default_value
                tint_color = f"<{tint_color[0]:.2f}, {tint_color[1]:.2f}, {tint_color[2]:.2f}>"
                if "pbrMetallicRoughness" in pbr_overrides:
                    params.append(f"\n{parindent}PRIM_GLTF_BASE_COLOR, {i}, \"\", \"\", \"\", \"\", {tint_color}, {alpha:.2f}, {alpha_mode}, {alpha_cutoff:.2f}, EMPTY_STRING")

            params.append(f"\n{parindent}PRIM_COLOR, {i}, {tint_color}, {alpha:.2f}")

            # normal map
            normal_input = principled.inputs["Normal"]
            if normal_input.links:
                normal_input_node = normal_input.links[0].from_node
                if normal_input_node.bl_idname == "ShaderNodeNormalMap":
                    normal_color_input = normal_input_node.inputs.get("Color")
                    if normal_color_input and normal_color_input.links:
                        from_node = normal_color_input.links[0].from_node
                        if from_node.bl_idname == "ShaderNodeTexImage":
                            texture_os, texture_ot, texture_ss, texture_st, texture_rot = _get_trans(from_node)

                            image = from_node.image
                            if "sl_uuid" in image:
                                face_tree["Normal Map Texture"] = image["sl_uuid"]
                                # blinn Phong
                                # [ string texture, vector repeats, vector offsets, float rotation_in_radians ]
                                params.append(f"\n{parindent}PRIM_NORMAL, {i}, \"{lsl_escape(image["sl_uuid"])}\", <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}")
                                # pbr if overridden
                                if "normalTexture" in pbr_overrides:
                                    #  string texture, vector repeats, vector offsets, float rotation_in_radians ]
                                    params.append(f"\n{parindent}PRIM_GLTF_NORMAL, {i}, \"{lsl_escape(image["sl_uuid"])}\", <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}")

            # specular map  (blinn phong only, notice by invert color node)
            roughness_input = principled.inputs["Roughness"]
            if roughness_input.links:
                invert_input = roughness_input.links[0].from_node

                spec_color = "<1.0, 1.0, 1.0>"
                spec_glossiness = 51
                spec_environment = 0
                if "sl_specular_props" in material:
                    specular_props = json.loads(material["sl_specular_props"])
                    spec_color = specular_props["color"]
                    spec_glossiness = specular_props["glossiness"]
                    spec_environment = specular_props["environment"]
                    spec_color = f"<{spec_color[0]:.3f}, {spec_color[1]:.3f}, {spec_color[2]:.3f}>"

                if invert_input.bl_idname == "ShaderNodeInvert":
                    specular_color_input = invert_input.inputs.get("Color")
                    if specular_color_input and specular_color_input.links:
                        from_node = specular_color_input.links[0].from_node
                        if from_node.bl_idname == "ShaderNodeTexImage":
                            texture_os, texture_ot, texture_ss, texture_st, texture_rot = _get_trans(from_node)

                            image = from_node.image
                            if "sl_uuid" in image:
                                face_tree["Specular Map Texture"] = image["sl_uuid"]
                                # blinn phong
                                # [ string texture, vector repeats, vector offsets, float rotation_in_radians, vector color, integer glossiness integer environment ]
                                params.append(f"\n{parindent}PRIM_SPECULAR, {i}, \"{lsl_escape(image["sl_uuid"])}\", <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}, {spec_color}, {spec_glossiness}, {spec_environment}")
                else:
                    # if roughness is set by pbr material, the blinn phong
                    # specular map may exist as node but not be connected
                    for node in nodes:
                        if node.label == "Specular Map Image":
                            texture_os, texture_ot, texture_ss, texture_st, texture_rot = _get_trans(node)

                            image = node.image
                            if "sl_uuid" in image:
                                face_tree["Specular Map Texture"] = image["sl_uuid"]
                                params.append(f"\n{parindent}PRIM_SPECULAR, {i}, \"{lsl_escape(image["sl_uuid"])}\", <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}, {spec_color}, {spec_glossiness}, {spec_environment}")
                            break

            # orm map (pbr only)
            orm_texture_node = None
            if "occlusionTexture" in pbr_overrides or "occlusionTextureTransform" in pbr_overrides:
                orm_input = principled.inputs["Metallic"]
                if orm_input.links:
                    split_input = orm_input.links[0].from_node
                    if split_input.bl_idname == "ShaderNodeSeparateColor":
                        orm_color_input = split_input.inputs.get("Color")
                        if orm_color_input and orm_color_input.links:
                            from_node = orm_color_input.links[0].from_node
                            if from_node.bl_idname == "ShaderNodeTexImage":
                                image = from_node.image
                                if "sl_uuid" in image:
                                    face_tree["ORM Map Texture"] = image["sl_uuid"]
                                    orm_texture_node = from_node
            metallic_factor = "\"\""
            roughness_factor = "\"\""
            if "pbrMetallicRoughness" in pbr_overrides:
                metallic_factor = f"{principled.inputs["Metallic"].default_value:.2f}"
                roughness_factor = f"{principled.inputs["Roughness"].default_value:.2f}"
            if orm_texture_node is not None or metallic_factor != "\"\"" or roughness_factor != "\"\"":
                orm_texture = "\"\""
                texture_os = "\"\""
                texture_ot = "\"\""
                texture_ss = "\"\""
                texture_st = "\"\""
                texture_rot = "\"\""
                if orm_texture_node:
                    orm_texture = f"\"{lsl_escape(orm_texture_node.image["sl_uuid"])}\""
                    texture_os, texture_ot, texture_ss, texture_st, texture_rot = _get_trans(orm_texture_node)
                # pbr
                # [ string texture, vector repeats, vector offsets, float rotation_in_radians, float metallic_factor, float roughness_factor ]
                params.append(f"\n{parindent}PRIM_GLTF_METALLIC_ROUGHNESS, {i}, {orm_texture}, <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}, {metallic_factor}, {roughness_factor}")

            # emissive map (pbr only)
            emissive_texture_node = None
            if "emissiveTexture" in pbr_overrides or "emissiveTextureTransform" in pbr_overrides:
                emission_input = principled.inputs.get("Emission")
                if emission_input and emission_input.links:
                    from_node = emission_input.links[0].from_node
                    if from_node.bl_idname == "ShaderNodeTexImage":
                        image = from_node.image
                        if "sl_uuid" in image:
                            emissive_texture_node = from_node
                            face_tree["Emission Map Texture"] = image["sl_uuid"]
            emission_factor = "\"\""
            if "emissiveFactor" in pbr_overrides:
                emission_factor = principled.inputs["Emission Color"].default_value
                emission_factor = f"<{emission_factor[0]:.2f}, {emission_factor[1]:.2f}, {emission_factor[2]:.2f}>"
            if emissive_texture_node is not None or emission_factor != "\"\"":
                emission_texture = "\"\""
                texture_os = "\"\""
                texture_ot = "\"\""
                texture_ss = "\"\""
                texture_st = "\"\""
                texture_rot = "\"\""
                if emissive_texture_node:
                    emission_texture = f"\"{lsl_escape(emissive_texture_node.image["sl_uuid"])}\""
                    texture_os, texture_ot, texture_ss, texture_st, texture_rot = _get_trans(emissive_texture_node)
                # pbr
                #  [ string texture, vector repeats, vector offsets, float rotation_in_radians, vector emissive_tint ]
                params.append(f"\n{parindent}PRIM_GLTF_EMISSIVE, {i}, {emission_texture}, <{texture_ss}, {texture_st}, 1.0>, <{texture_os}, {texture_ot}, 0.0>, {texture_rot}, {emission_factor}")
                

        lsl_text += f"{subindent}llSetLinkPrimitiveParamsFast(link_num, [{",".join(params)}\n{subindent}]);\n{indent}}}"

    lsl_text += """
            else
            {
                did_set = FALSE;
            }

            if (did_set == TRUE && i == 0)
            {
                llRemoveInventory(llGetScriptName());
                return;
            }
        }

        llRemoveInventory(llGetScriptName());
    }
}"""
    return lsl_text, objects_tree
    
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
        lsl_text, _ = create_lsl_script()
        text_block.write(lsl_text)

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
        _, object_tree = create_lsl_script()
        tree_text = "\n".join(utils.print_tree(object_tree))
        text_block.write(tree_text)

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
