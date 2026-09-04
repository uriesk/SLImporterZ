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


MAX_PARSE_DEPTH = 200
BINARY_HEADER = b'<? llsd/binary ?>'

_X_ORD = ord(b'x')
_BACKSLASH_ORD = ord(b'\\')
_DECODE_BUFF_ALLOC_SIZE = 1024

class uri(str):
    pass

class LLSDParseError(Exception):
    pass

class LLSDSerializationError(TypeError):
    pass

class LLSDBaseParser(object):
    """
    Utility methods useful for parser subclasses.
    """
    def __init__(self, something):
        self._reset(something)
        # Scratch space for decoding delimited strings
        self._decode_buff = bytearray(_DECODE_BUFF_ALLOC_SIZE)

    def _reset(self, something):
        if isinstance(something, LLSDBaseParser):
            # When passed an existing LLSDBaseParser (subclass) instance, just
            # borrow its existing _stream.
            self._stream = something._stream
        elif isinstance(something, bytes):
            # Wrap an incoming bytes string into a stream. If the passed bytes
            # string is so large that the overhead of copying it into a
            # BytesIO is significant, advise caller to pass a stream instead.
            self._stream = io.BytesIO(something)
        elif isinstance(something, io.IOBase):
            # 'something' is a proper IO stream - must be seekable for parsing
            if something.seekable():
                self._stream = something
            else:
                raise LLSDParseError(
                    "Cannot parse LLSD from non-seekable stream."
                )
        else:
            # Invalid input type - raise a clear error
            # This catches MagicMock and other non-stream objects that might
            # have read/seek attributes but aren't actual IO streams
            raise LLSDParseError(
                "Cannot parse LLSD from {0}. "
                "Expected bytes or a seekable io.IOBase object.".format(
                    type(something).__name__
                )
            )

    def remainder(self):
        # return a stream object representing the parse input (from last
        # _reset() call), whose read position is set past scanned input
        return self._stream

    def _next_nonblank(self):
        # we directly call read() rather than getc() because our caller is
        # prepared to handle empty string, meaning EOF
        # (YES we want the walrus operator)
        c = self._stream.read(1)
        while c.isspace():
            c = self._stream.read(1)
        return c

    def _getc(self, num=1, full=True):
        got = self._stream.read(num)
        if full and len(got) < num:
            self._error("Trying to read past end of stream")
        return got

    def _error(self, message, offset=0):
        oldpos = self._stream.tell()
        # 'offset' is relative to current pos
        self._stream.seek(offset, io.SEEK_CUR)
        raise LLSDParseError("%s at byte %d: %r" %
                             (message, oldpos+offset, self._getc(1, full=False)))

    # map char following escape char to corresponding character
    _escaped = {
        ord(b'a'): ord(b'\a'),
        ord(b'b'): ord(b'\b'),
        ord(b'f'): ord(b'\f'),
        ord(b'n'): ord(b'\n'),
        ord(b'r'): ord(b'\r'),
        ord(b't'): ord(b'\t'),
        ord(b'v'): ord(b'\v'),
    }

    def _parse_string_delim(self, delim):
        "Parse a delimited string."
        insert_idx = 0
        delim_ord = ord(delim)
        # Preallocate a working buffer for the decoded string output
        # to avoid allocs in the hot loop.
        decode_buff = self._decode_buff
        # Cache this in locals, otherwise we have to perform a lookup on
        # `self` in the hot loop.
        getc = self._getc
        cc = 0
        while True:
            try:
                cc = ord(getc())

                if cc == _BACKSLASH_ORD:
                    # Backslash, figure out if this is an \xNN hex escape or
                    # something like \t
                    cc = ord(getc())
                    if cc == _X_ORD:
                        # It's a hex escape. char is the value of the two
                        # following hex nybbles. This slice may result in
                        # a short read (0 or 1 bytes), but either a
                        # `ValueError` will be triggered by the first case,
                        # and the second will cause an `IndexError` on the
                        # next iteration of the loop.
                        hex_bytes = getc(2)
                        try:
                            # int() can parse a `bytes` containing hex,
                            # no explicit `bytes.decode("ascii")` required.
                            cc = int(hex_bytes, 16)
                        except ValueError as e:
                            # One of the hex characters was likely invalid.
                            # Wrap the ValueError so that we can provide a
                            # byte offset in the error.
                            self._error(e, offset=-2)
                    else:
                        # escape char preceding anything other than the chars
                        # in _escaped just results in that same char without
                        # the escape char
                        cc = self._escaped.get(cc, cc)
                elif cc == delim_ord:
                    break
            except IndexError:
                # We can be reasonably sure that any IndexErrors inside here
                # were caused by an out-of-bounds `buff[read_idx]`.
                self._error("Trying to read past end of buffer")

            try:
                decode_buff[insert_idx] = cc
            except IndexError:
                # Oops, that overflowed the decoding buffer, make a
                # new expanded buffer containing the existing contents.
                decode_buff = bytearray(decode_buff)
                decode_buff.extend(b"\x00" * _DECODE_BUFF_ALLOC_SIZE)
                decode_buff[insert_idx] = cc

            insert_idx += 1

        # Sync our local read index with the canonical one
        try:
            # Slice off only what we used of the working decode buffer
            return decode_buff[:insert_idx].decode('utf-8')
        except UnicodeDecodeError as exc:
            self._error(exc)

class LLSDBinaryParser(LLSDBaseParser):
    """
        Parse llsd binary to a python object.
    """
    def __init__(self, something, options=None):
        if options is None:
            options = {}
        self._keep_binary = not options.get("ignore_binary", False)

        super(LLSDBinaryParser, self).__init__(something)
        # One way of dispatching based on the next character we see would be a
        # dict lookup, and indeed that's the best way to express it in source.
        _dispatch_dict = {
            b'{': self._parse_map,
            b'[': self._parse_array,
            b'!': lambda: None,
            b'0': lambda: False,
            b'1': lambda: True,
            # 'i' = integer
            b'i': lambda: struct.unpack("!i", self._getc(4))[0],
            # 'r' = real number
            b'r': lambda: struct.unpack("!d", self._getc(8))[0],
            # 'u' = uuid
            b'u': lambda: uuid.UUID(bytes=self._getc(16)),
            # 's' = string
            b's': self._parse_string,
            # delimited/escaped string
            b"'": lambda: self._parse_string_delim(b"'"),
            b'"': lambda: self._parse_string_delim(b'"'),
            # 'l' = uri
            b'l': lambda: uri(self._parse_string()),
            # 'd' = date in seconds since epoch
            b'd': self._parse_date,
            # 'b' = binary
            # *NOTE: if not self._keep_binary, maybe have a binary placeholder
            # which has the length.
            b'b': lambda: bytes(self._parse_string_raw()) if self._keep_binary else None,
            }
        # But in fact it should be even faster to construct a list indexed by
        # ord(char). Start by filling it with the 'else' case. Use offset=-1
        # because by the time we perform this lookup, we've scanned past the
        # lookup char.
        self._dispatch = 256*[lambda: self._error("invalid binary token", -1)]
        # Now use the entries in _dispatch_dict to set the corresponding
        # entries in _dispatch.
        for c, func in _dispatch_dict.items():
            self._dispatch[ord(c)] = func
        self._depth = 0

    def parse(self):
        """
            This is the basic public interface for parsing.
            :returns: returns a python object.
        """
        try:
            return self._parse()
        except struct.error as exc:
            self._error(exc)

    def _parse(self):
        "The actual parser which is called recursively when necessary."
        if self._depth > MAX_PARSE_DEPTH:
            self._error("Parse depth exceeded maximum depth of %d." % MAX_PARSE_DEPTH)

        cc = self._getc()
        try:
            func = self._dispatch[ord(cc)]
        except IndexError:
            self._error("invalid binary token", -1)
        else:
            return func()

    def _parse_map(self):
        "Parse a single llsd map"
        rv = {}
        size = struct.unpack("!i", self._getc(4))[0]
        count = 0
        cc = self._getc()
        key = b''
        self._depth += 1
        while (cc != b'}') and (count < size):
            if cc == b'k':
                key = self._parse_string()
            elif cc in (b"'", b'"'):
                key = self._parse_string_delim(cc)
            else:
                self._error("invalid map key", -1)
            value = self._parse()
            rv[key] = value
            count += 1
            cc = self._getc()
        if cc != b'}':
            self._error("invalid map close token")
        self._depth -= 1
        return rv

    def _parse_array(self):
        "Parse a single llsd array"
        rv = []
        self._depth += 1
        size = struct.unpack("!i", self._getc(4))[0]
        for count in range(size):
            rv.append(self._parse())
        if self._getc() != b']':
            self._error("invalid array close token")
        self._depth -= 1
        return rv

    def _parse_string(self):
        try:
            return self._parse_string_raw().decode('utf-8')
        except UnicodeDecodeError as exc:
            self._error(exc)

    def _parse_string_raw(self):
        "Parse a string which has the leadings size indicator"
        try:
            size = struct.unpack("!i", self._getc(4))[0]
        except struct.error as exc:
            # convert exception class for client convenience
            self._error("struct " + str(exc))
        rv = self._getc(size)
        return rv

    def _parse_date(self):
        seconds = struct.unpack("<d", self._getc(8))[0]
        try:
            return datetime.datetime.utcfromtimestamp(seconds)
        except (OSError, OverflowError) as exc:
            # A garbage seconds value can cause utcfromtimestamp() to raise
            # OverflowError: timestamp out of range for platform time_t
            self._error(exc, -8)

def parse_binary_nohdr(something):
    # TODO ignore parser.matchseq(BINARY_HEADER)
    return LLSDBinaryParser(something).parse()

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

    else:
        lines.append(f"{indent}    └── {type(data).__name__}: {repr(data)[:50]}")
    
    return lines

