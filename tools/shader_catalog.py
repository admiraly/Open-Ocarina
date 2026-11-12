#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read Hookshot shader identities and metadata; never export shader bytes."""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

MASK64 = (1 << 64) - 1
MAX_RECORD = 64 << 20


def exact(source, count, limit=None):
    if count < 0 or (limit is not None and source.tell() + count > limit):
        raise ValueError("Field extends beyond its record")
    value = source.read(count)
    if len(value) != count:
        raise ValueError("Truncated input")
    return value


def scalar(source, fmt, limit=None):
    return struct.unpack(fmt, exact(source, struct.calcsize(fmt), limit))


def skip(source, count, limit):
    if count < 0 or source.tell() + count > limit:
        raise ValueError("Vector extends beyond its record")
    source.seek(count, 1)


def digest_range(source, count, limit):
    if count < 0 or source.tell() + count > limit:
        raise ValueError("Shader extends beyond its record")
    digest = hashlib.sha256()
    while count:
        piece = exact(source, min(count, 1 << 20), limit)
        digest.update(piece)
        count -= len(piece)
    return digest.hexdigest()


def first_stage(source, limit):
    flags, = scalar(source, "<I", limit)
    if flags & ~15:
        raise ValueError("Unknown program flags; unsupported cache layout")
    answers, = scalar(source, "<Q", limit)
    if answers > (limit - source.tell()) // 6:
        raise ValueError("Answer count exceeds record")
    variant = 0
    for _ in range(answers):
        stage, descriptor, kind, image = scalar(source, "<HHBB", limit)
        value = stage << 32 | descriptor << 16 | kind << 1 | image
        variant = ((variant ^ value) * 1099511628211) & MASK64
    questions, = scalar(source, "<Q", limit)
    skip(source, questions, limit)
    group = scalar(source, "<III", limit)
    shared_memory, topology, vertices, words, payload, task_threads = scalar(source, "<IIIIII", limit)
    stages, = scalar(source, "<Q", limit)
    if not 1 <= stages <= 8:
        raise ValueError("Invalid stage count")
    nvn_stage, vk_stage = scalar(source, "<iI", limit)
    if not 0 <= nvn_stage <= 7:
        raise ValueError("Invalid NVN stage")
    spirv_words, = scalar(source, "<Q", limit)
    if not spirv_words:
        raise ValueError("Empty SPIR-V module")
    spirv_file_offset = source.tell()
    spirv_hash = digest_range(source, spirv_words * 4, limit)
    constants_bytes, = scalar(source, "<Q", limit)
    constants_hash = digest_range(source, constants_bytes, limit)
    return {"flags": flags, "compute": bool(flags & 1), "mesh": bool(flags & 2),
            "variant": f"{variant:016x}", "answer_count": answers, "question_count": questions,
            "workgroup": list(group), "shared_memory_bytes": shared_memory,
            "stage_count": stages, "first_stage": {"nvn_stage": nvn_stage, "vk_stage": vk_stage,
            "spirv_words": spirv_words, "spirv_file_offset": spirv_file_offset, "spirv_sha256": spirv_hash,
            "constants_bytes": constants_bytes, "constants_sha256": constants_hash},
            "coverage": "First stage only; compiler-ABI-dependent Shader::Info and remaining stages are not decoded"}


def cache_catalog(path, program=None):
    before = path.stat()
    records = []
    scanned = 0
    with path.open("rb") as source:
        magic, reserved, stamp = scalar(source, "<4sIQ")
        if magic not in (b"HSP1", b"HSP2") or reserved != 0:
            raise ValueError("Unsupported Hookshot program cache header")
        head_size = 16 if magic == b"HSP1" else 20
        while source.tell() < before.st_size:
            if scanned >= 1000000:
                raise ValueError("Record limit exceeded")
            offset = source.tell()
            key, size = scalar(source, "<QQ", before.st_size)
            stored_flags = scalar(source, "<I", before.st_size)[0] if head_size == 20 else None
            end = source.tell() + size
            if size < 4 or size > MAX_RECORD or end > before.st_size:
                raise ValueError("Invalid or truncated record size")
            if stored_flags is not None and stored_flags & ~31:
                raise ValueError("Unknown stored record flags")
            scanned += 1
            if program is None or key == program:
                result = {"program": f"{key:016x}", "record_offset": offset, "stored_bytes": size}
                if stored_flags is not None and stored_flags & 16:
                    result["metadata_status"] = "zstd-compressed record: payload not decoded"
                    result["stored_flags"] = stored_flags
                else:
                    result.update(first_stage(source, end))
                    if stored_flags is not None and result["flags"] != stored_flags:
                        raise ValueError("Stored and payload flags disagree")
                records.append(result)
            source.seek(end)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Cache changed while reading; stop its writer and retry")
    if program is not None and not records:
        raise ValueError("Program absent from this cache revision")
    return {"schema": 1, "cache_format": magic.decode(), "cache_stamp": f"{stamp:016x}",
            "records_scanned": scanned, "records": records,
            "scope": "Metadata only; this cache is not a descriptor/input replay capture"}


def fnv_update(value, data):
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & MASK64
    return value


def seed_catalog(path, program):
    before = path.stat()
    matches = []
    with path.open("rb") as source:
        magic, programs, stages = scalar(source, "<8sII")
        if magic != b"HKSEED01" or stages > (before.st_size - 16) // 20:
            raise ValueError("Unsupported or truncated shader seed")
        for index in range(stages):
            stored_key, stage, control_bytes, code_bytes = scalar(source, "<QIII", before.st_size)
            end = source.tell() + control_bytes + code_bytes
            if end > before.st_size or control_bytes < 216 or control_bytes > (1 << 20):
                raise ValueError("Invalid seed stage extent")
            control = exact(source, control_bytes, end)
            native_length, = struct.unpack_from("<I", control, 56)
            constant_offset, = struct.unpack_from("<I", control, 64)
            native_end, = struct.unpack_from("<I", control, 68)
            native_stage, = struct.unpack_from("<H", control, 86)
            if (control[:4] != b"\x34\x12\x76\x98" or not native_length
                    or native_end > min(code_bytes, 16 << 20) or native_length > native_end
                    or constant_offset > native_end or native_stage != stage or native_stage > 7):
                raise ValueError("Invalid seed stage control")
            if stage == 5:
                value = fnv_update(0xcbf29ce484222325, control[:216])
                digest = hashlib.sha256(control[:216])
                remaining = native_end
                while remaining:
                    piece = exact(source, min(remaining, 1 << 20), end)
                    value = fnv_update(value, piece)
                    digest.update(piece)
                    remaining -= len(piece)
                if value == program:
                    matches.append({"stage_index": index, "nvn_stage": stage,
                        "program": f"{value:016x}", "stored_seed_key": f"{stored_key:016x}",
                        "native_control_code_sha256": digest.hexdigest(), "native_code_bytes": native_end})
            source.seek(end)
        # Validate every program-to-stage index without returning proprietary bytes.
        if programs > (before.st_size - source.tell()) // 4:
            raise ValueError("Invalid seed program count")
        for _ in range(programs):
            count, = scalar(source, "<I", before.st_size)
            if count > 8:
                raise ValueError("Invalid seed program stage count")
            for _ in range(count):
                stage_index, = scalar(source, "<I", before.st_size)
                if stage_index >= stages:
                    raise ValueError("Seed program refers to nonexistent stage")
        if source.tell() != before.st_size:
            raise ValueError("Unexpected trailing seed data")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Seed changed while reading")
    if not matches:
        raise ValueError("Compute program absent from this seed revision")
    return {"schema": 1, "seed_format": "HKSEED01", "stages_scanned": stages, "matches": matches,
            "scope": "Native compute identity verified by runtime hash; asset path and algorithm remain unknown"}


def program_key(value):
    if not re.fullmatch(r"[0-9a-fA-F]{16}", value):
        raise argparse.ArgumentTypeError("Expected the full 16-digit program hash")
    return int(value, 16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("cache", "seed"):
        command = sub.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--program", type=program_key, required=name == "seed")
    args = parser.parse_args()
    try:
        report = cache_catalog(args.input, args.program) if args.mode == "cache" else seed_catalog(args.input, args.program)
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, struct.error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
