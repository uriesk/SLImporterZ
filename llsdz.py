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

import calendar
import datetime
import io
import os
import struct
import json
import uuid

MAX_PARSE_DEPTH = 200
_X_ORD = ord(b'x')
_BACKSLASH_ORD = ord(b'\\')
_DECODE_BUFF_ALLOC_SIZE = 1024

class uri(str):
    pass

class LLSDError(Exception):
    pass

error = LLSDError

class LLSDParseError(LLSDError):
    pass

class LLSDSerializationError(LLSDError):
    pass

def path_join(trusted_path, filename):
    basename = os.path.basename(filename)
    if not basename:
        raise AttributeError("No basename")
    return os.path.join(trusted_path, basename)

class LLSDBinaryParser():
    """
        Parse llsd binary to a python object.
    """
    def __init__(self, **kwargs):
        self.binary_size_limit = kwargs.get("binary_size_limit")
        self.asset_folder = kwargs.get("asset_folder")

        self.state = 0
        self.error = None

        # those variables control how much data we fetch
        self.size_to_read = 1
        self.delimiter = None
        # offset of bytes to fast forward to
        self.ff_to_byte = 0
        # file writestream and path, set when fast forwarded data gets saved
        self.asset_writestream = None
        self.asset_filepath = None

        self.overhead = b''
        self._data = None
        # order of recursive object
        self._levels = []
        # keeps track of encountered keys
        self._paths = []
        self._c_key = None
        # starts
        self.bytes_read = 0

    # map char following escape char
    _escaped = {
        ord(b'a'): ord(b'\a'),
        ord(b'b'): ord(b'\b'),
        ord(b'f'): ord(b'\f'),
        ord(b'n'): ord(b'\n'),
        ord(b'r'): ord(b'\r'),
        ord(b't'): ord(b'\t'),
        ord(b'v'): ord(b'\v'),
    }

    @staticmethod
    def _bytes_to_string(data):
        buff = bytearray(len(data))
        size = len(data)
        insert_idx = 0
        i = 0
        while i < size:
            cc = data[i]
            if cc == _BACKSLASH_ORD:
                i += 1
                if i >= size:
                    break;
                cc = data[i]
                if cc == _X_ORD:
                    # It's a hex escape. char is the value of the two
                    # following hex nybbles.
                    i += 1
                    if i + 2 >= size:
                        break;
                    cc = int(data[i:i+2], 16)
                    i += 1
                else:
                    cc = LLSDBinaryParser._escaped.get(cc, cc)
            buff[insert_idx] = cc
            insert_idx += 1
            i += 1
        return buff[:insert_idx].decode('utf-8')

    @staticmethod
    def _check_for_delimiter(chunk, delimiter):
        i = 0
        while i < len(chunk):
            if chunk[i] == _BACKSLASH_ORD:
                i += 1
            elif chunk[i] == delimiter:
                return i
            i += 1
        return -1

    """
        method called from outside to consume stream in chunks
    """
    def parse(self, chunk):
        if self.overhead:
            chunk = self.overhead + chunk

        while True:
            # fast-foward
            if self.ff_to_byte > 0:
                if self.bytes_read + len(chunk) < self.ff_to_byte:
                    if self.asset_writestream is not None:
                        self.asset_writestream.write(chunk)
                    self.bytes_read += len(chunk)
                    self.overhead = None
                    return False
                else:
                    remaining_ff_length = self.ff_to_byte - self.bytes_read
                    if self.asset_writestream is not None:
                        data = chunk[:remaining_ff_length]
                        self.asset_writestream.write(data)
                        self.asset_writestream.flush()
                    chunk = chunk[remaining_ff_length:]
                    self.bytes_read = self.ff_to_byte
                    self.ff_to_byte = 0
                    # if we didn't get a next command, announce that we are
                    # done by passing empty bytes
                    if not self.size_to_read and not self.delimiter:
                        if not self._parse(b''):
                            continue;

            # read specific amount of bytes
            if self.size_to_read > 0:
                if len(chunk) < self.size_to_read:
                    self.overhead = chunk
                    return False
                else:
                    data = chunk[:self.size_to_read]
                    chunk = chunk[self.size_to_read:]
                    self.size_to_read = 0
                    if not self._parse(data):
                        continue

            # read till delimiter
            if self.delimiter is not None:
                i = LLSDBinaryParser._check_for_delimiter(chunk)
                if i < 0:
                    self.overhead = chunk
                    return False
                else:
                    data = chunk[:i]
                    chunk = chunk[i+1:]
                    self.delimiter = None
                    if not self._parse(data):
                        continue
            break
        self.done = True
        self.overhead = None
        return True

    """
        finalizing parsing, parser is theoretically still usable after this
    """
    def flush(self):
        finished = self.parse(b'')
        if self.asset_folder:
            self.destruct()
            self._ensure_asset_extension()
        if not finished:
            raise LLSDParseError("Data incomplete")
        return self._data, self.bytes_read

    def destruct(self):
        if self.asset_writestream:
            self.asset_writestream.close()
            self.asset_writestream = None
            self.asset_filepath = None

    def _ensure_asset_extension(self):
        # We stored assets with .bin extension, because we could not know
        # its type during parsing.
        # This method renames them according to the type defined somewhere
        # within the oxp data.
        if not self.asset_folder or not isinstance(self._data, dict):
            return

        for filename in os.listdir(self.asset_folder):
            if not filename.endswith(".bin"):
                continue
            uuid = filename[:-4]
            origin_filepath = os.path.join(self.asset_folder, filename)

            asset_occurances = self._find_asset_in_data(self._data, uuid)

            target_filepath = None
            for asset_data in asset_occurances:
                if "type" in asset_data:
                    match asset_data["type"]:
                        case "texture":
                            target_filepath = path_join(self.asset_folder, uuid + ".jp2")
                            break
                        case "lsltext":
                            target_filepath = path_join(self.asset_folder, uuid + ".lsl")
                            break
                        case "material":
                            target_filepath = path_join(self.asset_folder, uuid + ".slmat")
                            break
                        case "mesh":
                            target_filepath = path_join(self.asset_folder, uuid + ".slm")
                            break
            if target_filepath is None or not os.path.exists(origin_filepath):
                continue

            os.rename(origin_filepath, target_filepath)
            for asset_data in asset_occurances:
                asset_data["filepath"] = target_filepath

    def _find_asset_in_data(self, data, uuid):
        # find all orrucances of { uuid: { filepath }} where the filepath
        # matches the asset_folder and uuid filename
        occurances = []
        if isinstance(data, dict):
            if uuid in data:
                candidate = data[uuid]
                if isinstance(candidate, dict) and "filepath" in candidate and candidate["filepath"].startswith(os.path.join(self.asset_folder, uuid)):
                    occurances.append(candidate)
            for value in data.values():
                result = self._find_asset_in_data(value, uuid)
                if result:
                    occurances.extend(result)
        elif isinstance(data, list):
            for item in data:
                result = self._find_asset_in_data(item, uuid)
                if result:
                    occurances.extend(result)
        return occurances

    def _parse(self, data):
        value = None
        self.bytes_read += len(data)
        match self.state:
            case 0: # outside
                match ord(data):
                    case 33:
                        # '!' = null
                        value = None
                    case 48:
                        # '0' = false
                        value = False
                    case 49:
                        # '1' = true
                        value = True
                    case 105:
                        # 'i' = integer
                        self.state = 1;
                        self.size_to_read = 4
                    case 115 | 108:
                        # 's' = string
                        # 'l' = uri
                        self.state = 2
                        self.size_to_read = 4
                    case 114:
                        # 'r' = real number
                        self.state = 4
                        self.size_to_read = 8
                    case 117:
                        # 'u' = uuid
                        self.state = 5
                        self.size_to_read = 16
                    case 100:
                        # 'd' = date
                        self.state = 6
                        self.size_to_read = 8
                    case 98:
                        # 'b' = binary
                        self.state = 7
                        self.size_to_read = 4
                    case 123:
                        # '{' = map
                        self.state = 9
                        self.size_to_read = 4
                    case 91:
                        # '[' = array
                        self.state = 23
                        self.size_to_read = 4
                    case 93:
                        # ']' array closes
                        self._levels.pop()
                        self._paths.pop()
                        if not self._levels:
                            return True
                        else:
                            level = self._levels[-1]
                            if isinstance(level, dict):
                                self.state = 20
                                self.size_to_read = 1
                            if isinstance(level, list):
                                self.state = 0
                                self.size_to_read = 1
                    case 60:
                        # '<' header
                        # <? LLSD/Binary ?> in either case and with possible
                        # newlines
                        if self._data is not None:
                            raise LLSDParseError("Invalid data")
                        # dont check it, just skip past
                        self.ff_to_byte = self.bytes_read + 16
                        self.state = 0
                        self.size_to_read = 1
                    case 10 | 32 | 9:
                        # whitespace at start
                        if self._data is not None:
                            raise LLSDParseError("Invalid data")
                        self.state = 0
                        self.size_to_read = 1
                    case _:
                        raise LLSDParseError("Invalid binary token")

            case 1:
                # integer
                value = struct.unpack("!i", data)[0]
            case 2:
                # size of string
                self.state = 3
                self.size_to_read = struct.unpack("!i", data)[0]
                if self.size_to_read == 0:
                    value = ""
            case 3:
                # string
                value = LLSDBinaryParser._bytes_to_string(data)
            case 4:
                # real number
                value = struct.unpack("!d", data)[0]
            case 5:
                # uuid
                value = str(uuid.UUID(bytes=data))
            case 6:
                # date
                timestamp = struct.unpack("<d", data)[0]
                value = datetime.datetime.utcfromtimestamp(timestamp)
            case 7:
                # size of binary
                self.state = 8
                self.size_to_read = struct.unpack("!i", data)[0]
                if self.size_to_read == 0:
                    value = b''
                elif self.asset_folder is not None and self._c_key == "data" and len(self._paths) >= 2 and self._paths[-2] in ("asset", "mesh_asset"):
                    # store asset file if possible
                    # { mesh_asset: { uuid: { data, description, name, type }, ... } }
                    asset_uuid = self._paths[-1]
                    self.asset_filepath = path_join(self.asset_folder, asset_uuid + ".bin")
                    self.asset_writestream = open(self.asset_filepath, "wb")
                    self.ff_to_byte = self.bytes_read + self.size_to_read
                    self.size_to_read = 0
                elif self.binary_size_limit is not None and self.binary_size_limit < self.size_to_read:
                    self.ff_to_byte = self.bytes_read + self.size_to_read
                    self.size_to_read = 0
            case 8:
                # binary
                if self.asset_filepath:
                    self._c_key = "filepath"
                    value = self.asset_filepath
                    self.asset_writestream.close()
                    self.asset_writestream = None
                    self.asset_filepath = None
                else:
                    value = data
            case 9:
                # map
                # we ignore its length, which we would have fetched here
                value = {}
            case 20:
                # map key format
                ord_key = ord(data)
                match ord_key:
                    case 107:
                        # 'k' length proceeded string
                        self.state = 21
                        self.size_to_read = 4
                    case 39 | 34:
                        # "'" | '"' deliminated string
                        self.state = 22
                        self.delimiter = ord_key
                    case 125:
                        # '}' map closes
                        self._levels.pop()
                        self._paths.pop()
                        if not self._levels:
                            return True
                        else:
                            level = self._levels[-1]
                            if isinstance(level, dict):
                                self.state = 20
                                self.size_to_read = 1
                            if isinstance(level, list):
                                self.state = 0
                                self.size_to_read = 1
                    case _:
                        raise LLSDParseError("Invalid binary token")
            case 21:
                # size of key in map
                self.state = 22
                self.size_to_read = struct.unpack("!i", data)[0]
            case 22:
                # key in map
                self.state = 0
                self.size_to_read = 1
                self._c_key = LLSDBinaryParser._bytes_to_string(data)
            case 23:
                # array
                # we ignore its length, which we would have fetched here
                value = []

        if value is not None:
            c_key = self._c_key

            if not self._levels:
                self._data = value
                if not isinstance(value, dict) and not isinstance(value, list):
                    return True
            else:
                level = self._levels[-1]
                if c_key is None:
                    # list
                    level.append(value)
                else:
                    # dict
                    level[c_key] = value
                    self._c_key = None

            if isinstance(value, dict) or isinstance(value, list):
                self._paths.append((c_key or ".") if self._paths else "/")
                self._levels.append(value)
                if len(self._levels) > MAX_PARSE_DEPTH:
                    raise LLSDParseError("Parse depth exceeded maximum") 

            self.size_to_read = 1
            if isinstance(self._levels[-1], dict):
                # get next key
                self.state = 20
            else:
                # get next element
                self.state = 0

        return False

def parse_binary(something, **kwargs):
    if isinstance(something, bytes):
        something = io.BytesIO(something)

    if isinstance(something, io.IOBase):
        llsd_reader = LLSDBinaryParser(**kwargs)

        while True:
            chunk = something.read(65536)
            if not chunk:
                break
            if llsd_reader.parse(chunk):
                break
        llsd_data = llsd_reader.flush()
        return llsd_data
    else:
        raise LLSDParseError(
                "Cannot parse LLSD from {0}. "
                "Expected bytes or a seekable io.IOBase object.".format(
                    type(something).__name__
                )
            )

def parseobj(**kwargs):
    return LLSDBinaryParser(**kwargs)

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
            lines.extend(print_tree(value, new_indent, prefix + key, is_last_key))

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

