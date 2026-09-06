"""
Minimal read-only NTFS / $MFT reader.

Why we parse this ourselves rather than use pytsk3
--------------------------------------------------
Two reasons, and neither is "not invented here". First, pytsk3 is a compiled
extension whose Windows build is fragile, and a demo that dies on a teammate's
laptop because a wheel would not build is worth nothing. Second, and more
important: the whole point of module 2 is to show *where* deleted data survives.
"A library told me" is a weaker demonstration than pointing at MFT record 41,
offset 0x158, and reading the bytes out.

Scope is deliberately narrow. Enough to enumerate MFT records, tell allocated
from unallocated, and pull resident attribute content. Not a filesystem driver.

The forensic point this exists to prove
---------------------------------------
NTFS stores small files -- roughly under 600 bytes after attribute overhead --
INSIDE their MFT record, with no data clusters allocated at all. Deleting such
a file frees no cluster; it flips one bit in the record header. The content
stays, byte for byte, until that record is reused.

So a tool that "securely deletes" by overwriting a file's clusters, or that
wipes free space cluster by cluster, does not touch it. That is not a bug in
those tools; it is a gap in what they consider to be the file.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ATTR_STANDARD_INFORMATION = 0x10
ATTR_FILE_NAME = 0x30
ATTR_DATA = 0x80
ATTR_END = 0xFFFFFFFF

FLAG_IN_USE = 0x01
FLAG_DIRECTORY = 0x02


class NotNTFS(Exception):
    pass


@dataclass
class BootSector:
    bytes_per_sector: int
    sectors_per_cluster: int
    mft_cluster: int
    record_size: int
    total_sectors: int

    @property
    def cluster_size(self) -> int:
        return self.bytes_per_sector * self.sectors_per_cluster

    @property
    def mft_offset(self) -> int:
        return self.mft_cluster * self.cluster_size


def parse_boot(data: bytes) -> BootSector:
    if data[3:11] != b"NTFS    ":
        raise NotNTFS("no NTFS OEM signature")
    bps = struct.unpack_from("<H", data, 0x0B)[0]
    spc = data[0x0D]
    total = struct.unpack_from("<Q", data, 0x28)[0]
    mft_cluster = struct.unpack_from("<Q", data, 0x30)[0]
    raw = struct.unpack_from("<b", data, 0x40)[0]
    # Positive: clusters per record. Negative: 2^-raw BYTES per record.
    rec = raw * bps * spc if raw > 0 else 1 << (-raw)
    return BootSector(bps, spc, mft_cluster, rec, total)


def apply_fixups(rec: bytearray, bps: int) -> bool:
    """
    Undo the update-sequence array.

    NTFS overwrites the last two bytes of every sector in a record with a
    sequence number, keeping the real values in a fixup array, so that a torn
    write can be detected. Read the record without reversing this and the last
    two bytes of each sector are wrong -- which silently corrupts exactly the
    resident content we are trying to recover.
    """
    off = struct.unpack_from("<H", rec, 0x04)[0]
    count = struct.unpack_from("<H", rec, 0x06)[0]
    if off == 0 or count == 0 or off + count * 2 > len(rec):
        return False
    usn = rec[off:off + 2]
    for i in range(1, count):
        pos = i * bps - 2
        if pos + 2 > len(rec):
            return False
        if rec[pos:pos + 2] != usn:
            return False                      # torn or not a valid record
        rec[pos:pos + 2] = rec[off + i * 2: off + i * 2 + 2]
    return True


@dataclass
class Attribute:
    type_id: int
    resident: bool
    name: str
    content: bytes = b""
    content_offset: int = 0          # absolute offset within the MFT record


@dataclass
class MftRecord:
    index: int
    offset: int                      # absolute byte offset in the image
    in_use: bool
    is_directory: bool
    attributes: list = field(default_factory=list)
    raw: bytes = b""

    def attr(self, type_id):
        for a in self.attributes:
            if a.type_id == type_id:
                return a
        return None

    @property
    def filename(self):
        a = self.attr(ATTR_FILE_NAME)
        if a is None or len(a.content) < 0x42:
            return None
        n = a.content[0x40]
        try:
            return a.content[0x42:0x42 + n * 2].decode("utf-16-le", "replace")
        except Exception:
            return None

    @property
    def resident_data(self):
        """Content of a resident $DATA attribute, i.e. the whole small file."""
        a = self.attr(ATTR_DATA)
        if a is None or not a.resident:
            return None
        return a.content


def parse_record(raw: bytes, index: int, offset: int, bps: int):
    if raw[:4] != b"FILE":
        return None
    rec = bytearray(raw)
    if not apply_fixups(rec, bps):
        return None
    flags = struct.unpack_from("<H", rec, 0x16)[0]
    first = struct.unpack_from("<H", rec, 0x14)[0]
    used = struct.unpack_from("<I", rec, 0x18)[0]

    out = MftRecord(index=index, offset=offset,
                    in_use=bool(flags & FLAG_IN_USE),
                    is_directory=bool(flags & FLAG_DIRECTORY),
                    raw=bytes(rec))

    pos = first
    limit = min(used, len(rec))
    while pos + 8 <= limit:
        type_id = struct.unpack_from("<I", rec, pos)[0]
        if type_id == ATTR_END:
            break
        length = struct.unpack_from("<I", rec, pos + 4)[0]
        if length < 24 or pos + length > len(rec):
            break
        non_res = rec[pos + 8]
        name_len = rec[pos + 9]
        name_off = struct.unpack_from("<H", rec, pos + 10)[0]
        name = ""
        if name_len:
            try:
                name = bytes(rec[pos + name_off: pos + name_off + name_len * 2]).decode(
                    "utf-16-le", "replace")
            except Exception:
                name = ""
        if non_res:
            out.attributes.append(Attribute(type_id, False, name))
        else:
            clen = struct.unpack_from("<I", rec, pos + 0x10)[0]
            coff = struct.unpack_from("<H", rec, pos + 0x14)[0]
            start = pos + coff
            content = bytes(rec[start:start + clen]) if start + clen <= len(rec) else b""
            out.attributes.append(Attribute(type_id, True, name, content,
                                            offset + start))
        pos += length
    return out


class NtfsImage:
    """Read-only view of an NTFS volume in a raw image file."""

    def __init__(self, path: str, volume_offset: int = 0):
        self.path = path
        self.volume_offset = volume_offset
        with open(path, "rb") as f:
            f.seek(volume_offset)
            boot = f.read(512)
        self.boot = parse_boot(boot)

    def records(self, limit: int = None):
        """Yield MftRecord for every parseable record in $MFT."""
        b = self.boot
        base = self.volume_offset + b.mft_offset
        with open(self.path, "rb") as f:
            i = 0
            while True:
                if limit is not None and i >= limit:
                    return
                off = base + i * b.record_size
                f.seek(off)
                raw = f.read(b.record_size)
                if len(raw) < b.record_size:
                    return
                if raw[:4] != b"FILE":
                    # $MFT is not necessarily contiguous; stop at the first
                    # non-record rather than guess at run lists. Enough for a
                    # freshly created volume, and we say so rather than
                    # pretending to handle fragmented $MFT.
                    return
                rec = parse_record(raw, i, off, b.bytes_per_sector)
                if rec is not None:
                    yield rec
                i += 1
