#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record hashes and NSO header metadata without exporting executable bytes."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def describe(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular module file: {path.name}")
    with path.open("rb") as source:
        header = source.read(256)
        if len(header) != 256 or header[:4] != b"NSO0":
            raise ValueError(f"Not an NSO module: {path.name}")
        source.seek(0)
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    flags = struct.unpack_from("<I", header, 12)[0]
    sections = {}
    for index, (name, offset) in enumerate((("text", 16), ("rodata", 32), ("data", 48))):
        file_offset, memory_offset, memory_size = struct.unpack_from("<III", header, offset)
        stored_size = struct.unpack_from("<I", header, 96 + 4 * index)[0] if flags & (1 << index) else memory_size
        if memory_size and not stored_size:
            raise ValueError(f"Empty compressed {name} section in {path.name}")
        if file_offset + stored_size > path.stat().st_size:
            raise ValueError(f"Truncated {name} section in {path.name}")
        sections[name] = {"file_offset": file_offset, "memory_offset": memory_offset,
                          "memory_size": memory_size, "stored_size": stored_size,
                          "compressed": bool(flags & (1 << index))}
    return {"module": path.name, "size": path.stat().st_size, "sha256": digest,
            "build_id": header[64:96].hex(), "sections": sections}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        directory = args.game_dir / ".hookshot_exefs"
        if directory.is_symlink():
            raise ValueError("Expected an ordinary .hookshot_exefs directory")
        modules = [describe(directory / name) for name in ("main", "sdk", "subsdk0", "rtld")]
        print(json.dumps({"schema": 1, "modules": modules,
                          "matching_status": "not assessed; hashes and metadata do not establish source matching"}, indent=2))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
