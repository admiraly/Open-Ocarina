#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded all-stage cache metadata with an explicitly supplied native ABI profile.

The profile must come from the cache producer's headers/compiler configuration.
Pointer-bearing Shader::Info bytes are skipped, never deserialized or executed.
No module bytes are returned. Exact framing validates parsing, not ABI provenance.
"""
import argparse
import json
from pathlib import Path
import struct
import sys

try:
    from .shader_catalog import cache_catalog, digest_range, exact, program_key, scalar, skip
except ImportError:
    from shader_catalog import cache_catalog, digest_range, exact, program_key, scalar, skip

DESCRIPTORS = ("constant_buffer", "storage_buffer", "texture_buffer", "image_buffer", "texture", "image")
VK_STAGES = {1: "vertex", 2: "tessellation_control", 4: "tessellation_evaluation",
             8: "geometry", 16: "fragment", 32: "compute", 64: "task_ext", 128: "mesh_ext"}


def validate_profile(profile, stamp):
    if not isinstance(profile, dict) or profile.get("schema") != 1 or profile.get("cache_stamp") != stamp:
        raise ValueError("ABI profile schema or cache stamp mismatch")
    sizes = profile.get("descriptor_bytes")
    if not isinstance(sizes, list) or len(sizes) != len(DESCRIPTORS):
        raise ValueError("ABI profile requires six descriptor element sizes")
    for size in [profile.get("info_bytes"), profile.get("attribute_bytes"), *sizes]:
        if type(size) is not int or not 1 <= size <= 65536:
            raise ValueError("ABI profile contains an invalid size")
    if not isinstance(profile.get("provenance"), dict) or not profile["provenance"]:
        raise ValueError("ABI profile requires provenance metadata")


def vector_skip(source, width, end):
    count, = scalar(source, "<Q", end)
    skip(source, count * width, end)
    return count


def info_skip(source, profile, end):
    skip(source, profile["info_bytes"], end)
    counts = {name: vector_skip(source, size, end)
              for name, size in zip(DESCRIPTORS, profile["descriptor_bytes"])}
    mapping_count = vector_skip(source, 2 * profile["attribute_bytes"], end)
    handles, = scalar(source, "<Q", end)
    if handles > min(65536, (end - source.tell()) // 8):
        raise ValueError("Handle program count exceeds record")
    for _ in range(handles):
        vector_skip(source, 4, end)
    return {"descriptor_record_counts": counts, "legacy_mapping_count": mapping_count,
            "handle_program_count": handles}


def stage_catalog(path, profile, program=None):
    before = path.stat()
    report = cache_catalog(path, program)
    validate_profile(profile, report["cache_stamp"])
    report["abi_profile"] = profile
    report["scope"] = "All stage metadata; caller-supplied ABI provenance is not independently authenticated"
    head_bytes = 16 if report["cache_format"] == "HSP1" else 20
    with path.open("rb") as source:
        for row in report["records"]:
            if "first_stage" not in row:
                raise ValueError("Compressed selected records require a separate trusted decompression step")
            end = row["record_offset"] + head_bytes + row["stored_bytes"]
            stages_offset = row.pop("first_stage")["spirv_file_offset"] - 16
            source.seek(stages_offset - 8 - 24)
            shared, topology, vertices, words, payload, threads = scalar(source, "<IIIIII", end)
            row["fallback_metadata"] = {"topology": topology, "vertices_per_workgroup": vertices,
                "record_words": words, "payload_words": payload, "task_threads": threads}
            source.seek(stages_offset)
            row.pop("coverage")
            row["stages"] = []
            for index in range(row["stage_count"]):
                nvn, vk = scalar(source, "<iI", end)
                if not 0 <= nvn <= 7 or vk not in VK_STAGES:
                    raise ValueError("Unsupported stage identity")
                words, = scalar(source, "<Q", end)
                if words < 5 or words * 4 > end - source.tell():
                    raise ValueError("Invalid SPIR-V module extent")
                offset = source.tell()
                if exact(source, 4, end) != b"\x03\x02\x23\x07":
                    raise ValueError("Invalid SPIR-V magic")
                source.seek(offset)
                digest = digest_range(source, words * 4, end)
                constants, = scalar(source, "<Q", end)
                constants_digest = digest_range(source, constants, end)
                row["stages"].append({"index": index, "nvn_stage": nvn, "vk_stage": vk,
                    "vk_stage_name": VK_STAGES[vk], "spirv_words": words, "spirv_file_offset": offset,
                    "spirv_sha256": digest, "constants_bytes": constants,
                    "constants_sha256": constants_digest, **info_skip(source, profile, end)})
            if source.tell() != end:
                raise ValueError("ABI/layout mismatch: unconsumed record bytes")
            row["coverage"] = "Every serialized stage consumed; exact record end validated"
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Cache changed while reading")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--abi-profile", type=Path, required=True)
    parser.add_argument("--program", type=program_key)
    args = parser.parse_args()
    try:
        result = stage_catalog(args.input, json.loads(args.abi_profile.read_text()), args.program)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, TypeError, struct.error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
