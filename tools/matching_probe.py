#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile authored leaf probes and compare complete unwind-proven NSO functions."""
import argparse
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.nso_elf import read_nso
from tools.verify_bytes import verify

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SHA256 = "2591fb99008d0814f8056c3f36b36790048ed857ff0ae2bb3a4c1e37b960a4de"
MODULE_BASE = 0x80020000
PROBES = (("clear_first_byte", 0x80, 8), ("upper_two_bits", 0xC800, 12),
          ("lookup_payload", 0xA5B0, 24))
FLAGS = ("--target=aarch64-none-elf", "-O2", "-ffreestanding", "-fno-stack-protector",
         "-fno-builtin", "-fno-asynchronous-unwind-tables", "-fno-unwind-tables")


def read_memory(image, address, size):
    if address < 0 or size < 0:
        raise ValueError("Invalid mapped-memory range")
    for segment in image.segments:
        offset = address - segment.memory_offset
        if 0 <= offset and offset + size <= len(segment.data):
            return segment.data[offset:offset + size]
    raise ValueError(f"Unmapped NSO memory range at {address:#x}, size {size:#x}")


def _u32(image, address):
    return struct.unpack("<I", read_memory(image, address, 4))[0]


def _i32(image, address):
    return struct.unpack("<i", read_memory(image, address, 4))[0]


def _leb(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated CIE LEB128 field")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Oversized CIE LEB128 field")


def _validate_cie(image, address):
    length = _u32(image, address)
    if length < 8 or length > 4096 or _u32(image, address + 4) != 0:
        raise ValueError("Unsupported or malformed CIE record")
    record = read_memory(image, address + 8, length - 4)
    if record[:4] != b"\x01zR\0":
        raise ValueError("Only version-1 zR CIEs are supported by this bounded probe")
    offset = 4
    _, offset = _leb(record, offset)  # Code alignment.
    _, offset = _leb(record, offset)  # Data alignment; numeric sign is irrelevant here.
    if offset >= len(record):
        raise ValueError("Truncated CIE return-address register")
    offset += 1  # CIE version 1 stores this register in one byte.
    augmentation_size, offset = _leb(record, offset)
    if augmentation_size != 1 or offset >= len(record) or record[offset] != 0x1B:
        raise ValueError("Only pcrel/sdata4 FDE addresses are supported")


def function_extents(image, addresses):
    """Return original FDE-backed extents, not lifter-discovered instruction counts."""
    mod0 = _u32(image, 4)
    if read_memory(image, mod0, 4) != b"MOD0":
        raise ValueError("Missing MOD0 header")
    header = mod0 + _i32(image, mod0 + 16)
    if read_memory(image, header, 4) != b"\x01\x1b\x03\x3b":
        raise ValueError("Unsupported .eh_frame_hdr encoding")
    count = _u32(image, header + 8)
    table = read_memory(image, header + 12, count * 8)
    targets = set(addresses)
    found = {}
    for function_delta, fde_delta in struct.iter_unpack("<ii", table):
        start = header + function_delta
        if start not in targets:
            continue
        if start in found:
            raise ValueError(f"Duplicate unwind entry for {start:#x}")
        fde = header + fde_delta
        length = _u32(image, fde)
        if length < 12 or length > 4096:
            raise ValueError("Unsupported or malformed FDE record")
        read_memory(image, fde + 4, length)
        cie = fde + 4 - _u32(image, fde + 4)
        _validate_cie(image, cie)
        initial = fde + 8 + _i32(image, fde + 8)
        size = _i32(image, fde + 12)
        if initial != start or size <= 0 or start % 4 or size % 4:
            raise ValueError("Unwind table and FDE disagree or extent is invalid")
        text = next((segment for segment in image.segments if segment.name == ".text"), None)
        if text is None or not (text.memory_offset <= start and start + size <= text.memory_offset + len(text.data)):
            raise ValueError("Function extent is outside the NSO text segment")
        found[start] = {"memory_offset": start, "size": size,
                        "fde_memory_offset": fde, "cie_memory_offset": cie,
                        "eh_frame_hdr_memory_offset": header,
                        "boundary_source": "original .eh_frame_hdr entry and matching FDE extent"}
    if set(found) != targets:
        missing = ", ".join(hex(value) for value in sorted(targets - set(found)))
        raise ValueError(f"No original unwind boundary for {missing}")
    return found


def run(reference, output, compiler="clang", objcopy="llvm-objcopy", expected_sha256=REFERENCE_SHA256):
    output = output.resolve()
    if output == ROOT or (output.is_relative_to(ROOT) and not any(
            output.is_relative_to(ROOT / name) for name in ("local", "work", "artifacts"))):
        raise ValueError("Probe outputs inside the repository must be under ignored local/, work/ or artifacts/")
    compiler_path, objcopy_path = shutil.which(compiler), shutil.which(objcopy)
    if not compiler_path or not objcopy_path:
        raise ValueError("AArch64-capable Clang and llvm-objcopy are required")
    image = read_nso(reference)
    if image.source_sha256 != expected_sha256.lower():
        raise ValueError("Reference revision hash mismatch; refusing address-based probes")
    text = next(segment for segment in image.segments if segment.name == ".text")
    if text.compressed:
        raise ValueError("Direct original-file byte verification requires an uncompressed text segment")
    extents = function_extents(image, [address for _, address, _ in PROBES])
    output.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([compiler_path, "--version"], check=True, capture_output=True,
                             text=True, timeout=15).stdout.splitlines()[0]
    results = []
    for name, address, expected_size in PROBES:
        extent = extents[address]
        if extent["size"] != expected_size:
            raise ValueError(f"Unexpected original function size for {name}")
        source = ROOT / "src" / "probes" / (name + ".c")
        obj, binary = output / (name + ".o"), output / (name + ".text")
        subprocess.run([compiler_path, *FLAGS, "-c", str(source), "-o", str(obj)], check=True,
                       capture_output=True, text=True, timeout=45)
        subprocess.run([objcopy_path, "--only-section=.text", "-O", "binary", str(obj), str(binary)],
                       check=True, capture_output=True, text=True, timeout=15)
        candidate_size = binary.stat().st_size
        file_offset = text.file_offset + address - text.memory_offset
        if candidate_size != extent["size"]:
            comparison = {"byte_match": False, "reason": "candidate text size differs from complete original extent"}
        else:
            comparison = verify(reference, binary, file_offset, 0, extent["size"], image.source_sha256)
        results.append({"probe": name, "virtual_address": hex(MODULE_BASE + address),
                        "reference_file_offset": file_offset, "candidate_text_size": candidate_size,
                        "original_boundary": extent, **comparison})
    report = {"schema": 1, "reference_module": reference.name, "reference_sha256": image.source_sha256,
              "compiler": version, "compiler_flags": list(FLAGS), "results": results,
              "all_selected_probes_matched": all(row["byte_match"] for row in results),
              "limitations": ["Leaf probes do not identify the original compiler or recover original source.",
                              "No relocated functions, global references, calls, whole-program link or runtime behavior are verified.",
                              "No project-wide reconstruction percentage is inferred from these selected probes."]}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "local" / "cpu-matching")
    parser.add_argument("--compiler", default="clang")
    parser.add_argument("--objcopy", default="llvm-objcopy")
    parser.add_argument("--reference-sha256", default=REFERENCE_SHA256)
    parser.add_argument("--require-all-matched", action="store_true")
    args = parser.parse_args()
    try:
        report = run(args.reference, args.out_dir, args.compiler, args.objcopy, args.reference_sha256)
        print(json.dumps(report, indent=2))
        return 1 if args.require_all_matched and not all(row["byte_match"] for row in report["results"]) else 0
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
