"""Read single members of a remote zip with HTTP range requests.

A zip keeps its table of contents at the end, so listing it and pulling one member costs a few
small requests instead of downloading the whole archive.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

import requests

_LOCAL_HEADER_SIZE = 30
_EXTRA_FIELD_MARGIN = 1024


class HttpRangeFile(io.RawIOBase):
    def __init__(self, url: str, session: requests.Session | None = None, timeout_s: float = 120.0):
        self.url = url
        self.session = session or requests.Session()
        self.timeout_s = timeout_s
        head = self.session.get(url, headers={"Range": "bytes=0-0"}, timeout=timeout_s)
        head.raise_for_status()
        total = head.headers.get("Content-Range", "").rsplit("/", 1)[-1]
        if head.status_code != 206 or not total.isdigit():
            raise OSError(f"server does not support range requests for {url.split('?')[0]}")
        self.size = int(total)
        self.pos = 0
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def readinto(self, buffer) -> int:
        if self.pos >= self.size or len(buffer) == 0:
            return 0
        data = self.read_range(self.pos, len(buffer))
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)

    def read_range(self, start: int, length: int) -> bytes:
        end = min(start + length, self.size) - 1
        r = self.session.get(self.url, headers={"Range": f"bytes={start}-{end}"}, timeout=self.timeout_s)
        r.raise_for_status()
        self.bytes_read += len(r.content)
        return r.content


def list_members(f: HttpRangeFile) -> list[zipfile.ZipInfo]:
    return zipfile.ZipFile(io.BufferedReader(f, buffer_size=1 << 20)).infolist()


def read_member(f: HttpRangeFile, info: zipfile.ZipInfo) -> bytes:
    """One member's bytes in a single range request, CRC-checked."""
    name_len = len(info.orig_filename.encode("utf-8" if info.flag_bits & 0x800 else "cp437"))
    blob = f.read_range(
        info.header_offset, _LOCAL_HEADER_SIZE + name_len + _EXTRA_FIELD_MARGIN + info.compress_size
    )
    name_len, extra_len = struct.unpack("<HH", blob[26:30])
    start = _LOCAL_HEADER_SIZE + name_len + extra_len
    raw = blob[start : start + info.compress_size]
    if len(raw) < info.compress_size:
        raw += f.read_range(info.header_offset + start + len(raw), info.compress_size - len(raw))
    if info.compress_type == zipfile.ZIP_STORED:
        data = raw
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompress(raw, -zlib.MAX_WBITS)
    else:
        raise OSError(f"unsupported compression {info.compress_type} for {info.filename}")
    if zlib.crc32(data) != info.CRC:
        raise OSError(f"CRC mismatch for {info.filename}")
    return data
