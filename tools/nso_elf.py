#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Map private NSO segments into an analysis ELF; no relocation or executable reconstruction."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile

HEADER_SIZE = 0x100
PAGE_SIZE = 0x1000
DEFAULT_MAX_IMAGE_BYTES = 512 * 1024 * 1024
UINT64_LIMIT = 1 << 64
ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {"local", "work", "artifacts"}


@dataclass(frozen=True)
class Segment:
    name: str
    file_offset: int
    memory_offset: int
    data: bytes
    stored_size: int
    compressed: bool

    @property
    def memory_size(self):
        return len(self.data)


@dataclass(frozen=True)
class NsoImage:
    segments: tuple[Segment, ...]
    bss_size: int
    flags: int
    build_id: str
    source_sha256: str


def _overlaps(ranges):
    """Half-open empty ranges do not overlap anything."""
    occupied = sorted((start, end, name) for start, end, name in ranges if end > start)
    for left, right in zip(occupied, occupied[1:]):
        if right[0] < left[1]:
            raise ValueError(f"Overlapping segments: {left[2]} and {right[2]}")


def _decompress(data, expected_size):
    try:
        lz4 = importlib.import_module("lz4.block")
    except ImportError as exc:
        raise ValueError("Compressed NSO requires the optional 'lz4' Python package; "
                         "install it in a local virtual environment, or supply an unpacked NSO") from exc
    try:
        result = lz4.decompress(data, uncompressed_size=expected_size)
    except Exception as exc:
        raise ValueError("Invalid LZ4-compressed NSO segment") from exc
    if len(result) != expected_size:
        raise ValueError("Decompressed NSO segment has the wrong size")
    return result


def read_nso(path, *, max_image_bytes=DEFAULT_MAX_IMAGE_BYTES):
    """Read/validate an NSO without changing it; return decompressed segment bytes.

    Flag bit 6 is preserved as uninterpreted metadata, as in the inspected loader.
    Bits 0..2 select LZ4, bits 3..5 request segment SHA-256 checks. ZBIC (bit 7)
    and other unknown flags are rejected rather than guessed.
    """
    path = Path(path)
    if max_image_bytes <= 0:
        raise ValueError("Image size limit must be positive")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Input must be a regular, non-symlink NSO file")
    with path.open("rb") as source:
        file_size = os.fstat(source.fileno()).st_size
        header = source.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE or header[:4] != b"NSO0":
            raise ValueError("Expected a complete NSO0 header")
        version, reserved, flags = struct.unpack_from("<III", header, 4)
        if version != 0 or reserved != 0:
            raise ValueError("Unsupported NSO header version/reserved field")
        if flags & 0x80:
            raise ValueError("ZBIC NSO compression (flag bit 7) is unsupported; supply a locally unpacked NSO")
        if flags & ~0x7F:
            raise ValueError("Unsupported NSO flags")
        bss_size = struct.unpack_from("<I", header, 0x3C)[0]
        specifications = []
        file_ranges, memory_ranges = [], []
        for index, name in enumerate((".text", ".rodata", ".data")):
            file_offset, memory_offset, size = struct.unpack_from("<III", header, 0x10 + 0x10 * index)
            compressed = bool(flags & (1 << index))
            stored = struct.unpack_from("<I", header, 0x60 + 4 * index)[0] if compressed else size
            memory_end = memory_offset + size + (bss_size if index == 2 else 0)
            if size > max_image_bytes or stored > max_image_bytes or memory_end > max_image_bytes:
                raise ValueError(f"{name} exceeds the configured analysis image size limit")
            if size and (file_offset < HEADER_SIZE or stored == 0):
                raise ValueError(f"Invalid {name} file range")
            if file_offset + stored > file_size:
                raise ValueError(f"Truncated {name} segment")
            if compressed and not size:
                raise ValueError(f"Empty {name} cannot be marked compressed")
            file_ranges.append((file_offset, file_offset + stored, name))
            memory_ranges.append((memory_offset, memory_end, name))
            specifications.append((index, name, file_offset, memory_offset, size, stored, compressed))
        if not specifications[0][4]:
            raise ValueError("NSO has no executable text")
        if specifications[0][3] % 4 or specifications[0][4] % 4:
            raise ValueError("AArch64 text offset and size must be instruction aligned")
        if sum(item[4] for item in specifications) + bss_size > max_image_bytes:
            raise ValueError("NSO exceeds the configured analysis image size limit")
        _overlaps(file_ranges)
        _overlaps(memory_ranges)
        source.seek(0)
        source_hash = hashlib.file_digest(source, "sha256").hexdigest()
        segments = []
        for index, name, file_offset, memory_offset, size, stored, compressed in specifications:
            source.seek(file_offset)
            data = source.read(stored)
            if len(data) != stored:
                raise ValueError("Input was truncated during conversion")
            if compressed:
                data = _decompress(data, size)
            if flags & (8 << index):
                expected = header[0xA0 + 0x20 * index:0xC0 + 0x20 * index]
                if hashlib.sha256(data).digest() != expected:
                    raise ValueError(f"{name} segment SHA-256 mismatch")
            segments.append(Segment(name, file_offset, memory_offset, data, stored, compressed))
    return NsoImage(tuple(segments), bss_size, flags, header[0x40:0x60].hex(), source_hash)


def _align(value, alignment):
    return (value + alignment - 1) & ~(alignment - 1)


def build_elf(image, base_address, entry_address=None):
    """Return (ELF bytes, metadata). The ELF is an unrelocated analysis container."""
    if not isinstance(base_address, int) or base_address < 0 or base_address % 4:
        raise ValueError("Base address must be a nonnegative, instruction-aligned integer")
    maximum_end = max(segment.memory_offset + segment.memory_size +
                      (image.bss_size if segment.name == ".data" else 0) for segment in image.segments)
    if base_address + maximum_end >= UINT64_LIMIT:
        raise ValueError("Mapped image exceeds the 64-bit address space")
    text = image.segments[0]
    entry = base_address + text.memory_offset if entry_address is None else entry_address
    if entry % 4 or not (base_address + text.memory_offset <= entry <= base_address + text.memory_offset + text.memory_size - 4):
        raise ValueError("Entry address must identify an aligned instruction inside .text")

    # ELF64 header (64 bytes), followed by three ELF64 program headers (56 each).
    output = bytearray(64 + 3 * 56)
    offsets = []
    for segment in image.segments:
        virtual_address = base_address + segment.memory_offset
        offset = _align(len(output), PAGE_SIZE) + virtual_address % PAGE_SIZE
        output.extend(bytes(offset - len(output)))
        offsets.append(offset)
        output.extend(segment.data)
    names = b"\0.text\0.rodata\0.data\0.bss\0.shstrtab\0"
    names_offset = len(output)
    output.extend(names)
    section_offset = _align(len(output), 8)
    output.extend(bytes(section_offset - len(output)))
    output.extend(bytes(6 * 64))
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    struct.pack_into("<16sHHIQQQIHHHHHH", output, 0, ident, 2, 183, 1, entry, 64,
                     section_offset, 0, 64, 56, 3, 64, 6, 5)
    segments_metadata = []
    for index, (segment, offset, permission) in enumerate(zip(image.segments, offsets, (5, 4, 6))):
        address = base_address + segment.memory_offset
        memory_size = segment.memory_size + (image.bss_size if index == 2 else 0)
        struct.pack_into("<IIQQQQQQ", output, 64 + index * 56, 1, permission, offset, address,
                         address, segment.memory_size, memory_size, PAGE_SIZE)
        struct.pack_into("<IIQQQQIIQQ", output, section_offset + (index + 1) * 64,
                         names.index(segment.name.encode()), 1, 6 if index == 0 else 2 if index == 1 else 3,
                         address, offset, segment.memory_size, 0, 0, 4 if index == 0 else 1, 0)
        segments_metadata.append({"name": segment.name, "address": address, "file_offset": offset,
                                  "file_size": segment.memory_size, "memory_size": memory_size,
                                  "permissions": ("r-x", "r--", "rw-")[index]})
    data = image.segments[2]
    struct.pack_into("<IIQQQQIIQQ", output, section_offset + 4 * 64, names.index(b".bss"), 8, 3,
                     base_address + data.memory_offset + data.memory_size, offsets[2] + data.memory_size,
                     image.bss_size, 0, 0, 1, 0)
    struct.pack_into("<IIQQQQIIQQ", output, section_offset + 5 * 64, names.index(b".shstrtab"), 3, 0,
                     0, names_offset, len(names), 0, 0, 1, 0)
    metadata = {"format": "ELF64 little-endian AArch64 analysis container", "machine": 183,
                "base_address": base_address, "entry_address": entry,
                "entry_is_conventional": entry_address is None, "segments": segments_metadata,
                "source_sha256": image.source_sha256, "source_build_id": image.build_id,
                "source_flags": image.flags, "uninterpreted_source_flags": image.flags & 0x40,
                "bss_size": image.bss_size, "elf_size": len(output),
                "elf_sha256": hashlib.sha256(output).hexdigest(),
                "limitations": ["No dynamic relocations applied or imports resolved.",
                                "No original ELF, SDK, symbols, function boundaries or entry point recovered.",
                                "Not a byte-matching or runnable native port milestone."]}
    return bytes(output), metadata


def _private_output(output):
    resolved = output.resolve()
    try:
        relative = resolved.relative_to(ROOT)
    except ValueError:
        return
    if not relative.parts or relative.parts[0] not in PRIVATE_DIRS:
        raise ValueError("Game-derived ELF outputs inside the repository must stay under local/, work/, or artifacts/")


def convert(input_path, output_path, base_address=0, *, entry_address=None,
            max_image_bytes=DEFAULT_MAX_IMAGE_BYTES):
    input_path, output_path = Path(input_path), Path(output_path)
    _private_output(output_path)
    if output_path.is_symlink() or input_path.resolve() == output_path.resolve():
        raise ValueError("Output must not be a symlink or overwrite the source NSO")
    image = read_nso(input_path, max_image_bytes=max_image_bytes)
    content, metadata = build_elf(image, base_address, entry_address)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".nso-elf-", dir=output_path.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-address", "--base", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--entry-address", type=lambda value: int(value, 0))
    parser.add_argument("--max-image-bytes", type=lambda value: int(value, 0), default=DEFAULT_MAX_IMAGE_BYTES)
    args = parser.parse_args()
    try:
        result = convert(args.input, args.output, args.base_address, entry_address=args.entry_address,
                         max_image_bytes=args.max_image_bytes)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, struct.error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
