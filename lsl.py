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


def lsl_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')

def create_lsl_script():
    # creates lsl script for setting properties and textures
    # by uuid
    lsl_text = """default
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

            // link 0 is parent prim and its name is the linkset name,
            // so if it got named different on upload, it won't be available
            // under its real name anymore, so we choose whatever is left and
            // seen first, in case we can't find it (i == -1)

            string name = llGetLinkName(i);\n
            integer did_set = TRUE;\n"""
    indent = "            "

    amount_added = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue

        if amount_added == 0:
            lsl_text += f"\n{indent}if (name == \"{lsl_escape(obj.name)}\" || (i == -1 && llListFindList(used_names, [\"{lsl_escape(obj.name)}\"]) == -1))\n{indent}{{\n"
        else:
            lsl_text += f"\n{indent}else if (name == \"{lsl_escape(obj.name)}\" || (i == -1 && llListFindList(used_names, [\"{lsl_escape(obj.name)}\"]) == -1))\n{indent}{{\n"
        subindent = indent + "    "
        lsl_text += f"{subindent}used_names += [\"{lsl_escape(obj.name)}\"];\n"
        amount_added += 1

        params = []

        for i, material in enumerate(obj.data.materials):
            # recreate face properties based on material

            # pbr material
            is_pbr_material = False
            # values that got overridden
            pbr_overrides = []
            if "sl_uuid" in material:
                is_pbr_material = True
                params.append(f"PRIM_RENDER_MATERIAL, {i}, \"{lsl_escape(material["sl_uuid"])}\"")
                if "sl_gltf_override" in material:
                    pbr_overrides = json.loads(material["sl_gltf_override"])

            # find principled bsdf node that is connected to surface output
            if not material.use_nodes:
                continue
            nodes = material.node_tree.nodes
            links = material.node_tree.links
            bsdf_node = None
            for node in nodes:
                if node.bl_idname == 'ShaderNodeOutputMaterial':
                    surface_input = node.inputs.get('Surface')
                    if surface_input and surface_input.links:
                        from_node = surface_input.links[0].from_node
                        if from_node.bl_idname == 'ShaderNodeBsdfPrincipled':
                            bsdf_node = from_node
                    break
            if not bsdf_node:
                continue

            # See https://wiki.secondlife.com/wiki/LlSetPrimitiveParams

            # tint color
            tint_color = bsdf_node.inputs["Base Color"].default_value
            tint_color = f"<{tint_color[0]:.2f}, {tint_color[1]:.2f}, {tint_color[2]:.2f}>"
            params.append(f"PRIM_COLOR, {i}, {tint_color}, 1.0")

            if "sl_fullbright" in material and material["sl_fullbright"] != 0:
                params.append(f"PRIM_FULLBRIGHT, {i}, {material["sl_fullbright"]}")
            if "sl_glow" in material and material["sl_glow"] != 0.0:
                params.append(f"PRIM_GLOW, {i}, {material["sl_glow"]}")

            # base color
            base_color_input = bsdf_node.inputs.get("Base Color")
            if base_color_input and base_color_input.links:
                from_node = base_color_input.links[0].from_node
                if from_node.bl_idname == "ShaderNodeTexImage":
                    image = from_node.image
                    if "sl_uuid" in image:
                        # blinn Phong
                        # [ string texture, vector repeats, vector offsets, float rotation_in_radians ]
                        params.append(f"PRIM_TEXTURE, {i}, \"{lsl_escape(image["sl_uuid"])}\", <1.0, 1.0, 0.0>, ZERO_VECTOR, 0.0")
                        # pbr if overridden
                        if "pbrMetallicRoughness" in pbr_overrides:
                            # [ string texture, vector repeats, vector offsets, float rotation_in_radians, vector color, float alpha, integer gltf_alpha_mode, float alpha_mask_cutoff, integer double_sided ]
                            params.append(f"PRIM_GLTF_BASE_COLOR, {i}, \"{lsl_escape(image["sl_uuid"])}\", \"\", \"\", \"\", {tint_color}, \"\", \"\", \"\", \"\"")

            # normal map
            normal_input = bsdf_node.inputs.get("Normal")
            if normal_input and normal_input.links:
                normal_input_node = normal_input.links[0].from_node
                if normal_input_node.bl_idname == "ShaderNodeNormalMap":
                    normal_color_input = normal_input_node.inputs.get("Color")
                    if normal_color_input and normal_color_input.links:
                        from_node = normal_color_input.links[0].from_node
                        if from_node.bl_idname == "ShaderNodeTexImage":
                            image = from_node.image
                            if "sl_uuid" in image:
                                # blinn Phong
                                # [ string texture, vector repeats, vector offsets, float rotation_in_radians ]
                                params.append(f"PRIM_NORMAL, {i}, \"{lsl_escape(image["sl_uuid"])}\", <1.0, 1.0, 0.0>, ZERO_VECTOR, 0.0")
                                # pbr if overridden
                                if "normalTexture" in pbr_overrides:
                                    #  string texture, vector repeats, vector offsets, float rotation_in_radians ]
                                    params.append(f"PRIM_GLTF_NORMAL, {i}, \"{lsl_escape(image["sl_uuid"])}\", \"\", \"\", \"\"")

            # specular map  (blinn phong only, notice by invert color node)
            roughness_input = bsdf_node.inputs.get("Roughness")
            if roughness_input and roughness_input.links:
                invert_input = roughness_input.links[0].from_node
                if invert_input.bl_idname == "ShaderNodeInvert":
                    specular_color_input = invert_input.inputs.get("Color")
                    if specular_color_input and specular_color_input.links:
                        from_node = specular_color_input.links[0].from_node
                        if from_node.bl_idname == "ShaderNodeTexImage":
                            image = from_node.image
                            if "sl_uuid" in image:
                                # blinn Phong
                                # [ string texture, vector repeats, vector offsets, float rotation_in_radians, vector color, integer glossiness integer environment ]
                                params.append(f"PRIM_SPECULAR, {i}, \"{lsl_escape(image["sl_uuid"])}\", <1.0, 1.0, 0.0>, ZERO_VECTOR, 0.0, <1.0, 1.0, 1.0>, 255, 0")

            # orm map (pbr only)
            if "occlusionTexture" in pbr_overrides:
                orm_input = bsdf_node.inputs.get("Metallic")
                if orm_input and orm_input.links:
                    split_input = orm_input.links[0].from_node
                    if split_input.bl_idname == "ShaderNodeSeparateColor":
                        orm_color_input = split_input.inputs.get("Color")
                        if orm_color_input and orm_color_input.links:
                            from_node = orm_color_input.links[0].from_node
                            if from_node.bl_idname == "ShaderNodeTexImage":
                                image = from_node.image
                                if "sl_uuid" in image:
                                    # pbr
                                    # [ string texture, vector repeats, vector offsets, float rotation_in_radians, float metallic_factor, float roughness_factor ]
                                    params.append(f"PRIM_GLTF_METALLIC_ROUGHNESS, {i}, \"{lsl_escape(image["sl_uuid"])}\", \"\", \"\", \"\", \"\", \"\"")

            # emissive map (pbr only)
            if "emissiveTexture" in pbr_overrides:
                emission_input = bsdf_node.inputs.get("Emission")
                if emission_input and emission_input.links:
                    from_node = emission_input.links[0].from_node
                    if from_node.bl_idname == "ShaderNodeTexImage":
                        image = from_node.image
                        if "sl_uuid" in image:
                            # pbr
                            #  [ string texture, vector repeats, vector offsets, float rotation_in_radians, vector emissive_tint ]
                            params.append(f"PRIM_GLTF_EMISSIVE, {i}, \"{lsl_escape(image["sl_uuid"])}\", \"\", \"\", \"\", \"\"")

        lsl_text += f"{subindent}llSetLinkPrimitiveParamsFast(link_num, [{", ".join(params)}]);\n{indent}}}"

    lsl_text += """
            else
            {
                did_set = FALSE;
            }

            if (did_set == TRUE && i == 0)
            {
                return;
            }
        }
    }
}"""
    return lsl_text
    
class TEXT_OT_generate_script(bpy.types.Operator):
    bl_idname = "text.generate_dynamic_script"
    bl_label = "Generate Texture Script"
    bl_description = "Generate LSL script that sets textures inworld according to Materials"

    def execute(self, context):
        # Get the text block (create if it doesn't exist)
        text_block = context.space_data.text
        if not text_block:
            text_block = bpy.data.texts.new("sliz_set_textures.lsl")
            context.space_data.text = text_block

        text_block.clear()
        text_block.write(create_lsl_script())
        
        self.report({'INFO'}, "Script generated!")
        return {'FINISHED'}

class TEXT_MT_my_generator_menu(bpy.types.Menu):
    bl_label = "LSL"
    bl_idname = "TEXT_MT_lsl_generator_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator("text.generate_dynamic_script")

def draw_my_menu(self, context):
    layout = self.layout
    layout.menu(TEXT_MT_my_generator_menu.bl_idname)

def register():
    bpy.utils.register_class(TEXT_OT_generate_script)
    bpy.utils.register_class(TEXT_MT_my_generator_menu)
    bpy.types.TEXT_HT_header.append(draw_my_menu)

def unregister():
    bpy.types.TEXT_HT_header.remove(draw_my_menu)
    bpy.utils.unregister_class(TEXT_MT_my_generator_menu)
    bpy.utils.unregister_class(TEXT_OT_generate_script)

if __name__ == "__main__":
    register()
