#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run one explicitly bounded headless Ghidra probe; keep all decompilation private."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {"local", "work", "artifacts"}


def private_output(path, root=ROOT):
    path = path.resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError:
        return path
    if not relative.parts or relative.parts[0] not in PRIVATE:
        raise ValueError("Probe output inside the repository must be under local/, work/ or artifacts/")
    return path


def validate_limits(address, max_bytes, decompile_seconds, timeout):
    if not 0 <= address <= 0x7FFFFFFFFFFFFFFF or address % 4:
        raise ValueError("ARM64 user-space entry address must fit a nonnegative signed 64-bit value and be 4-byte aligned")
    if not 4 <= max_bytes <= 4096 or max_bytes % 4:
        raise ValueError("--max-bytes must be a multiple of 4 from 4 to 4096")
    if address + max_bytes - 1 > 0x7FFFFFFFFFFFFFFF:
        raise ValueError("Probe window exceeds the supported user-space address range")
    if not 1 <= decompile_seconds <= 60:
        raise ValueError("--decompile-seconds must be from 1 to 60")
    if not decompile_seconds < timeout <= 600:
        raise ValueError("--timeout must exceed the decompiler limit and be at most 600 seconds")


def command(ghidra_home, elf, output, address, max_bytes, decompile_seconds):
    launcher = ghidra_home / "support" / "analyzeHeadless"
    if not launcher.is_file():
        raise ValueError("Ghidra analyzeHeadless not found; supply --ghidra-home or GHIDRA_HOME")
    return [str(launcher), str(output / "project"), "bounded-probe",
            "-import", str(elf), "-noanalysis", "-max-cpu", "2",
            "-scriptPath", str(ROOT / "tools" / "ghidra_scripts"),
            "-postScript", "BoundedProbe.java", hex(address), str(max_bytes),
            str(decompile_seconds), str(output), "-deleteProject",
            "-log", str(output / "ghidra.log"), "-scriptlog", str(output / "script.log")]


def run_probe(ghidra_home, elf, output, address, max_bytes=256, decompile_seconds=20, timeout=120):
    validate_limits(address, max_bytes, decompile_seconds, timeout)
    if elf.is_symlink():
        raise ValueError("--input must identify a regular ELF file, not a symlink")
    elf = elf.resolve()
    if not elf.is_file():
        raise ValueError("--input must identify an existing ELF file")
    with elf.open("rb") as source:
        header = source.read(20)
        if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xb7\x00":
            raise ValueError("Expected a little-endian ELF64 AArch64 input from nso_elf.py")
        source.seek(0)
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    output = private_output(output)
    if output.exists():
        raise ValueError("Output directory already exists; choose a new probe directory")
    cmd = command(ghidra_home.resolve(), elf, output, address, max_bytes, decompile_seconds)
    output.mkdir(parents=True)
    (output / "project").mkdir()
    configuration = {"schema": 1, "input_sha256": digest, "address": hex(address),
                     "max_bytes": max_bytes, "decompile_seconds": decompile_seconds,
                     "timeout_seconds": timeout, "global_analysis": False,
                     "matched_source": False}
    (output / "request.json").write_text(json.dumps(configuration, indent=2) + "\n")
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            status = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            reason = "was interrupted" if isinstance(exc, KeyboardInterrupt) else "exceeded its wall-time limit"
            raise ValueError(f"Ghidra probe {reason}; only its own process group was stopped")
    metadata = output / "probe.json"
    if status != 0 or not metadata.is_file():
        raise ValueError("Ghidra probe failed; inspect the private console.log and script.log")
    result = json.loads(metadata.read_text())
    if not result.get("decompile_completed"):
        raise ValueError("Ghidra did not complete decompilation; inspect the private probe report")
    if result.get("entry") != hex(address) or result.get("max_bytes") != max_bytes:
        raise ValueError("Ghidra result does not match the requested probe bounds")
    return {**configuration, "decompile_completed": True,
            "instruction_count": result["instruction_count"],
            "flow_outside_window": result.get("flow_outside_window"),
            "ghidra_version": result["ghidra_version"],
            "scope": "one bounded entry; complete function recovery and original source identity are not established"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ghidra-home", type=Path, default=os.environ.get("GHIDRA_HOME"))
    parser.add_argument("--input", type=Path, required=True, help="Private ELF from tools/nso_elf.py")
    parser.add_argument("--output", type=Path, required=True, help="New private output directory")
    parser.add_argument("--address", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--max-bytes", type=int, default=256)
    parser.add_argument("--decompile-seconds", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    try:
        if args.ghidra_home is None:
            raise ValueError("Set GHIDRA_HOME or pass --ghidra-home; Ghidra is an optional local tool")
        result = run_probe(args.ghidra_home, args.input, args.output, args.address,
                           args.max_bytes, args.decompile_seconds, args.timeout)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
