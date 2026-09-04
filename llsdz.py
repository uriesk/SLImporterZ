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
import struct
import uuid
from enum import Enum

class PARSER_STATE(Enum):
    BEGIN = 0
    FETCHING = 1
    MAP = 2
    ARRAY = 3
    STRING = 4
    RAW_STRING = 5
    DATE = 6
    BINARY = 7
    END = 8

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

def _check_for_delimiter(chunk, delimiter):
    i = 0
    while i < len(chunk):
        if chunk[i] == _BACKSLASH_ORD:
            i += 1
        elif chunk[i] == delimiter:
            return i
        i += 1
    return -1

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
                cc = _escaped.get(cc, cc)
        buff[insert_idx] = cc
        insert_idx += 1
        i += 1
    return buff[:insert_idx].decode('utf-8')

class LLSDBinaryParser():
    """
        Parse llsd binary to a python object.
    """
    def __init__(self, **kwargs):
        self.state = 0
        self.error = None
        self.size_to_read = 1
        self.delimiter = None
        self.overhead = b''
        self._data = None
        self._levels = []
        self._c_key = None
        # starts
        self.bytes_read = 0

    """
        method called from outside to consume stream in chunks
    """
    def parse(self, chunk):
        if self.overhead:
            chunk = self.overhead + chunk

        while self.delimiter is not None or self.size_to_read > 0:
            # read specific amount of bytes
            if self.size_to_read > 0:
                if len(chunk) < self.size_to_read:
                    self.overhead = chunk
                    return False
                else:
                    data = chunk[:self.size_to_read]
                    chunk = chunk[self.size_to_read:]
                    self.size_to_read = 0
                    if self._parse(data):
                        self.done = True
                        return True

            # read till delimiter
            if self.delimiter is not None:
                i = _check_for_delimiter(chunk)
                if i < 0:
                    self.overhead = chunk
                    return False
                else:
                    data = chunk[:i]
                    chunk = chunk[i+1:]
                    self.delimiter = None
                    if self._parse(data):
                        self.done = True
                        return True
        self.done = True
        return True

    """
        finalizing parsing
    """
    def flush(self):
        finished = self.parse(b'')
        if not finished:
            raise LLSDParseError("Data incomplete")
        return self._data, self.bytes_read

    def _parse(self, data):
        value = None
        if len(data) == 1:
            print("op: " + str(ord(data)))
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
                        print("map opens")
                        self.state = 9
                        self.size_to_read = 4
                    case 91:
                        # '[' = array
                        self.state = 23
                        self.size_to_read = 4
                    case 93:
                        # ']' array closes
                        self._levels.pop()
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

            case 1:
                # integer
                value = struct.unpack("!i", data)[0]
            case 2:
                # size of string
                self.state = 3
                self.size_to_read = struct.unpack("!i", data)[0]
                print("got size: " + str(self.size_to_read))
                if self.size_to_read == 0:
                    value = ""
            case 3:
                # string
                value = _bytes_to_string(data)
            case 4:
                # real number
                value = struct.unpack("!d", data)[0]
            case 5:
                # uuid
                value = uuid.UUID(bytes=data)
            case 6:
                # date
                print("parse date: " + str(len(data)))
                timestamp = struct.unpack("<d", data)[0]
                value = datetime.datetime.utcfromtimestamp(timestamp)
            case 7:
                # size of binary
                self.state = 8
                self.size_to_read = struct.unpack("!i", data)[0]
                if self.size_to_read == 0:
                    value = b''
            case 8:
                # binary
                value = data
            case 9:
                # map
                # we ignore its length, which we would have fetched here
                value = {}
                print("got map length " + str(struct.unpack("!i", data)[0]))
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
                        print("close dict")
                        self._levels.pop()
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
                self._c_key = _bytes_to_string(data)
                print("got key " + self._c_key)
            case 23:
                # array
                # we ignore its length, which we would have fetched here
                value = []

        if value is not None:
            print("value: " + type(value).__name__)
            if not self._levels:
                self._data = value
                if not isinstance(value, dict) and not isinstance(value, list):
                    return True
            else:
                level = self._levels[-1]
                if isinstance(level, dict):
                    # dict
                    print("add " + self._c_key + " to dic of " + type(value).__name__)
                    level[self._c_key] = value
                    self._c_key = None
                else:
                    # list
                    print("add to array " + type(value).__name__)
                    level.append(value)

            if isinstance(value, dict) or isinstance(value, list):
                print("added dict or map")
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
    print("parse")
    if isinstance(something, bytes):
        something = io.BytesIO(something)

    if isinstance(something, io.IOBase):
        llsd_reader = LLSDBinaryParser(**kwargs)

        chunk = something.read(65536)
        binary_header = b'<? llsd/binary ?>'
        if chunk.startswith(binary_header):
            chunk = chunk[len(binary_header):]
        finished = llsd_reader.parse(chunk)

        while not finished:
            chunk = something.read(65536)
            if not chunk:
                break
            finished = llsd_reader.parse(chunk)
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
        if len(value_str) > 80:
            value_str = value_str[:77] + "..."
        lines.append(f"{indent}    └── {value_str}")

    elif isinstance(data, bytes):
        lines.append(f"{indent}    └── {type(data).__name__} (len {len(data)}): {repr(data)[:50]}")

    else:
        lines.append(f"{indent}    └── {type(data).__name__}: {repr(data)[:50]}")
    
    return lines

