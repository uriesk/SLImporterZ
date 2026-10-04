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

import json
import hashlib

def merge_dicts(dict1, dict2):
    # merge two dicts together
    result = dict1.copy()
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result

def create_obj_hash(obj):
    # return hash string of serializable dictionary
    hash_object = hashlib.sha256(json.dumps(obj).encode("utf-8"))
    return hash_object.hexdigest()

def print_tree(data, indent="", prefix="", is_last=True):
    """Return a string representation of the tree structure."""
    lines = []

    if isinstance(data, dict):
        keys = list(data.keys())
        for i, key in enumerate(keys):
            is_last_key = (i == len(keys) - 1)
            value = data[key]

            connector = "└── " if is_last_key else "├── "
            lines.append(f"{indent}{connector}{key}: {type(value).__name__}")
            
            new_indent = indent + ("    " if is_last_key else "│   ")
            lines.extend(print_tree(value, new_indent, prefix + str(key), is_last_key))

    elif isinstance(data, list):
        lines.append(f"{indent}└── (List with {len(data)} items)")
        for i, item in enumerate(data[:10]):
            is_last_item = (i == min(9, len(data) - 1))
            connector = "└── " if is_last_item else "├── "

            if isinstance(item, dict):
                lines.append(f"{indent}    {connector}[{i}] Map ({len(item)} keys)")
                lines.extend(print_tree(item, indent + "    ", f"[{i}]", is_last_item))
            elif isinstance(item, list):
                lines.append(f"{indent}    {connector}[{i}] List ({len(item)} items)")
                lines.extend(print_tree(item, indent + "        ", f"[{i}]", is_last_item))
            else:
                lines.append(f"{indent}    {connector}[{i}] {repr(item)[:100]}")

        if len(data) > 10:
            lines.append(f"{indent}    └── ... and {len(data) - 10} more items")

    elif isinstance(data, (str, int, float, bool)):
        value_str = repr(data)
        json_lines = None
        if isinstance(data, str) and data[:1] in ("{", "["):
            try:
                parsed = json.loads(data)
                lines.append(f"{indent}    └── {type(data).__name__} (JSON):")
                json_lines = json.dumps(parsed, indent=2).split('\n')
            except json.JSONDecodeError:
                pass
        if json_lines:
            for line in json_lines:
                lines.append(f"{indent}        {line}")
        elif len(value_str) > 80:
            lines.append(f"{indent}    └── {value_str[:77]}...")
        else:
            lines.append(f"{indent}    └── {value_str}")

    elif isinstance(data, bytes):
        lines.append(f"{indent}    └── {type(data).__name__} (len {len(data)}): {repr(data)[:50]}")

    else:
        lines.append(f"{indent}    └── {type(data).__name__}: {repr(data)[:50]}")
    
    return lines
