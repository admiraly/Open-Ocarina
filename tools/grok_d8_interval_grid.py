#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only full-grid plans for the frozen D8 interval owned replay helper.

The native helper is not rebuilt. Each window rewrites the plan header and the
initialized capture extent. Input payload bytes and descriptor rows stay
byte-identical. This module never creates a Vulkan instance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import mesh_replay

SCHEMA = "open-ocarina.grok-d8-interval-grid/v1"
MAGIC = b"OOD8IV1\x00"
GRID = (705, 1, 1)
GRANT = "root-d8-full-grid-705-serial-01"
CASES = ("original32-a", "original32-b", "candidate32")
MAX_GROUPS = 32
CAPTURE_STRIDE = mesh_replay.RECORD_BYTES
PARENT_CAP = 16 * 1024 * 1024
PARENT_SECONDS = 180
FENCE_NS = 2_000_000_000
HEADER_WORDS = 16
HEADER_BYTES = 8 + HEADER_WORDS * 4


def _sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def partition(grid=GRID):
    """Exact cover of the captured X grid by windows of at most 32 groups."""
    if tuple(grid) != GRID:
        raise ValueError("Full-grid partition requires the captured 705,1,1 grid")
    windows = []
    origin = 0
    while origin < grid[0]:
        groups = min(MAX_GROUPS, grid[0] - origin)
        windows.append({"name": f"origin-{origin:03d}-groups-{groups:02d}",
                        "origin": [origin, 0, 0], "groups": [groups, 1, 1]})
        origin += groups
    assert_partition(windows, grid)
    return windows


def assert_partition(windows, grid=GRID):
    if tuple(grid) != GRID:
        raise ValueError("Captured grid is not 705,1,1")
    cursor = 0
    seen = set()
    for window in windows:
        origin, groups = window["origin"], window["groups"]
        if origin[1:] != [0, 0] or groups[1:] != [1, 1]:
            raise ValueError("Only the X axis is partitioned")
        if not 1 <= groups[0] <= MAX_GROUPS or origin[0] != cursor:
            raise ValueError("Window is gapped, overlapped, or larger than 32 groups")
        if origin[0] + groups[0] > grid[0]:
            raise ValueError("Window extends past the captured grid")
        if groups[0] != min(MAX_GROUPS, grid[0] - origin[0]):
            raise ValueError("Window does not use the required 32-group stride")
        seen.add(origin[0])
        cursor += groups[0]
    if cursor != grid[0] or len(seen) != len(windows):
        raise ValueError("Windows do not cover every captured group exactly once")
    if windows[-1]["groups"][0] != grid[0] % MAX_GROUPS:
        raise ValueError("Tail window is not the captured remainder")
    return {"windows": len(windows), "groups": cursor, "tail_groups": windows[-1]["groups"][0]}


def parse_plan(data):
    if len(data) < HEADER_BYTES + 4 or data[:8] != MAGIC or len(data) % 4:
        raise ValueError("Plan is not a word-aligned OOD8IV1 manifest")
    header = list(struct.unpack_from("<16I", data, 8))
    version, inputs, descriptors, push, capture_size, capture_offset, capture_range = header[:7]
    groups, origin, grid = header[7:10], header[10:13], header[13:16]
    if version != 1 or inputs != 10 or descriptors != 12 or push != 0 or capture_offset != 64:
        raise ValueError("Plan header is not the owned D8 interval class")
    if tuple(grid) != GRID or math_groups(groups) > MAX_GROUPS:
        raise ValueError("Plan grid or window is outside the frozen helper contract")
    if capture_range != math_groups(groups) * CAPTURE_STRIDE or capture_size != capture_range + 128:
        raise ValueError("Capture extent disagrees with the window group count")
    if any(origin[axis] + groups[axis] > grid[axis] for axis in range(3)):
        raise ValueError("Plan window is outside the captured grid")
    offset = HEADER_BYTES
    declared = 0
    for _ in range(inputs):
        if offset + 4 > len(data):
            raise ValueError("Truncated input size")
        size = struct.unpack_from("<I", data, offset)[0]
        if not size or size > mesh_replay.MAX_VIEW or offset + 4 + size > len(data):
            raise ValueError("Input payload is outside the helper cap")
        offset += 4 + size
        declared += size
    descriptor_bytes = descriptors * 7 * 4
    middle_end = offset + descriptor_bytes + push
    if middle_end + capture_size != len(data):
        raise ValueError("Plan sections do not fill the file")
    if declared + capture_size > mesh_replay.MAX_BYTES:
        raise ValueError("Plan exceeds the one-gibibyte helper total")
    return {"header": header, "groups": groups, "origin": origin, "grid": grid,
            "middle": data[HEADER_BYTES:middle_end], "capture_size": capture_size,
            "payload_bytes": declared}


def math_groups(groups):
    count = 1
    for value in groups:
        count *= value
    return count


def retarget(data, origin, groups):
    """Copy one plan, changing the window header and initialized capture only."""
    parsed = parse_plan(data)
    grid = parsed["grid"]
    origin = list(origin)
    groups = list(groups)
    if origin[1:] != [0, 0] or groups[1:] != [1, 1]:
        raise ValueError("Retarget keeps Y and Z at the captured single row")
    if not 1 <= groups[0] <= MAX_GROUPS or origin[0] + groups[0] > grid[0] or origin[0] < 0:
        raise ValueError("Retarget window is not a legal helper window")
    capture = mesh_replay.initialized_capture(groups)
    if len(capture) > MAX_GROUPS * CAPTURE_STRIDE + 128:
        raise ValueError("Initialized capture exceeds the 32-group bound")
    header = parsed["header"][:]
    header[4] = len(capture)
    header[6] = len(capture) - 128
    header[7:10] = groups
    header[10:13] = origin
    if header[5] != 64 or header[0] != 1 or header[13:16] != list(GRID):
        raise ValueError("Retarget changed the manifest class")
    body = struct.pack("<8s16I", MAGIC, *header) + parsed["middle"] + capture
    checked = parse_plan(body)
    if checked["middle"] != parsed["middle"] or checked["origin"] != origin or checked["groups"] != groups:
        raise ValueError("Retarget did not preserve the readonly input graph")
    return body


def prepare_directory(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(output)
    data = source.read_bytes()
    source_plan = parse_plan(data)
    windows = partition(source_plan["grid"])
    output.mkdir(parents=True)
    rows = []
    for window in windows:
        blob = retarget(data, window["origin"], window["groups"])
        path = output / f"{window['name']}.bin"
        path.write_bytes(blob)
        digest = hashlib.sha256(blob).hexdigest()
        rows.append({**window, "plan": path.name, "sha256": digest,
                     "capture_bytes": window["groups"][0] * CAPTURE_STRIDE + 128,
                     "bytes": len(blob)})
    middle = hashlib.sha256(source_plan["middle"]).hexdigest()
    for row in rows:
        checked = parse_plan((output / row["plan"]).read_bytes())
        if hashlib.sha256(checked["middle"]).hexdigest() != middle:
            raise ValueError("A window plan changed the shared input graph")
        if checked["capture_size"] != row["capture_bytes"]:
            raise ValueError("A window plan has an inconsistent capture extent")
    manifest = {"schema": SCHEMA, "prepare_only": True, "gpu_executed": False,
                "execution_admitted": False, "timing_admitted": False,
                "historical_atomicity_certified": False, "general_equivalence_proven": False,
                "warp32_composition_admitted": False, "live_integration_admitted": False,
                "owned_replay_writers": 0, "grid": list(GRID), "partition": assert_partition(rows),
                "windows": rows, "shared_input_graph_sha256": middle,
                "source_plan_sha256": hashlib.sha256(data).hexdigest(),
                "payload_bytes": source_plan["payload_bytes"],
                "input_buffers": 10, "descriptor_rows": 12,
                "historic_two_group_window": {"origin": [128, 0, 0], "groups": [2, 1, 1],
                                              "role": "additional reference already recorded; not a full-grid substitute"},
                "limits": {"max_groups": MAX_GROUPS, "fence_timeout_ns": FENCE_NS,
                           "max_total_input_bytes": mesh_replay.MAX_BYTES,
                           "max_capture_bytes": MAX_GROUPS * CAPTURE_STRIDE + 128,
                           "parent_pipe_cap_bytes": PARENT_CAP,
                           "parent_case_timeout_seconds": PARENT_SECONDS}}
    (output / "grid-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _case_module(modules, case):
    if case == "candidate32":
        return modules["candidate"]
    if case in ("original32-a", "original32-b"):
        return modules["original"]
    raise ValueError("Unknown replay case")


def _native_checks(document, window):
    expected = window["groups"][0] * CAPTURE_STRIDE + 128
    required = {
        "gpu_executed": True, "mesh_draw_calls": 1, "requested_width": 32, "actual_width": 32,
        "width_source": "pipeline_executable_properties", "executable_stage": 128,
        "draw_mesh_tasks": window["groups"], "origin": window["origin"],
        "fence_timeout_ns": FENCE_NS, "input_bytes_unchanged": True, "control_bytes_unchanged": True,
        "robustBufferAccess2": True, "nullDescriptor": True, "capture_bytes": expected,
        "rasterizer_discard": True, "attachments": 0, "pipeline_optimization_disabled": False,
        "vendor_id": 0x8086, "device_id": 0x56A0}
    for key, value in required.items():
        if document.get(key) != value:
            raise ValueError(f"Native result {key} does not match the window contract")
    if document.get("output_profile") != "d8-interval-mesh-owned/v1":
        raise ValueError("Native result is a different manifest class")
    return expected


def execute_grid(spec, output, grant, supervisor, env_base, inspect=None):
    """Run windows strictly in order. The supervisor must reap before returning."""
    output = Path(output)
    if grant != spec.get("grant") or grant != GRANT:
        raise ValueError("Grid replay grant does not match the single full-grid lease")
    if output.exists():
        raise FileExistsError(output)
    windows = spec["windows"]
    assert_partition(windows)
    for window in windows:
        plan = Path(window["plan"])
        if _sha256(plan) != window["sha256"]:
            raise ValueError(f"Frozen window plan changed: {plan.name}")
        parsed = parse_plan(plan.read_bytes())
        if parsed["origin"] != window["origin"] or parsed["groups"] != window["groups"]:
            raise ValueError("Window plan header disagrees with the manifest")
        if parsed["capture_size"] != window["groups"][0] * CAPTURE_STRIDE + 128:
            raise ValueError("Window plan capture extent disagrees with the manifest")
    output.mkdir()
    lease = {"grant": grant, "state": "held", "retry": False, "serial": True,
             "timing_admitted": False, "fps_measured": False, "released": False}
    (output / "lease.json").write_text(json.dumps(lease, indent=2) + "\n")
    report = {"schema": SCHEMA, "accepted": False, "windows_completed": [], "stopped": None,
              "historical_atomicity_certified": False, "general_equivalence_proven": False,
              "one_frozen_input_state": True}
    failure = None
    reaping_unproven = False
    try:
        for window in windows:
            window_dir = output / window["name"]
            window_dir.mkdir()
            captures = {}
            for case in CASES:
                case_dir = window_dir / case
                if case_dir.exists():
                    raise FileExistsError(case_dir)
                case_dir.mkdir()
                command = [str(spec["helper"]), str(_case_module(spec["modules"], case)),
                           str(window["plan"]), str(case_dir / "capture.bin"), "32", "0"]
                if "--validate" in command or command[-2:] != ["32", "0"]:
                    raise ValueError("Replay command is not one width-32 helper draw")
                reaping_unproven = True
                supervised = supervisor(command, env_base, case_dir, PARENT_SECONDS, PARENT_CAP, inspect)
                reaping_unproven = supervised.get("reaped") is not True
                if not supervised.get("reaped") or supervised.get("failure") or supervised.get("returncode") != 0:
                    raise ValueError(f"Supervisor stopped the case: {supervised.get('failure') or supervised.get('returncode')}")
                document = json.loads((case_dir / "stdout.log").read_text())
                expected = _native_checks(document, window)
                data = (case_dir / "capture.bin").read_bytes()
                if len(data) != expected or len(data) > MAX_GROUPS * CAPTURE_STRIDE + 128:
                    raise ValueError("Capture file disagrees with the native byte count")
                mesh_replay.check_capture(data, window["groups"])
                captures[case] = data
            mesh_replay.compare_bytes(captures["original32-a"], captures["original32-b"])
            mesh_replay.compare_bytes(captures["original32-a"], captures["candidate32"])
            report["windows_completed"].append(window["name"])
        report["accepted"] = True
    except BaseException as error:
        failure = str(error)
        report["stopped"] = failure
        report["accepted"] = False
    finally:
        lease["state"] = "cleanup-unproven" if reaping_unproven else "released"
        lease["released"] = not reaping_unproven
        lease["accepted"] = report["accepted"]
        lease["failure"] = failure
        (output / "lease.json").write_text(json.dumps(lease, indent=2) + "\n")
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare",))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare_directory(args.source, args.output)
    print(json.dumps({"schema": SCHEMA, "prepare_only": True, "gpu_executed": False,
                      "windows": manifest["partition"]["windows"],
                      "groups": manifest["partition"]["groups"],
                      "tail_groups": manifest["partition"]["tail_groups"],
                      "shared_input_graph_sha256": manifest["shared_input_graph_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
