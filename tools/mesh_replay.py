#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare bounded native-mesh logical-output replay; GPU execution is explicit.

Only the two audited d8 source modules or the independent instrumenter fixture
can enter preparation. A prepared-plan SHA-256 pins every execution artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import mesh_output_instrument as instrumenter
from tools.compile_probe import driver_identity, private_output
from tools.graphics_compile_probe import controlled_driver_environment, reflect_stage
from tools.mesh_mapping_probe import BUILD_FLAGS, digest, environment, ensure_no_games, run_process
from tools.replay_contract import _identifier, _integer, _json, _object, _snapshot

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "open-ocarina.prepared-native-mesh-replay/v1"
CAPTURE_SCHEMA = "open-ocarina.mesh-logical-input/v1"
COVERAGE_SCHEMA = "open-ocarina.mesh-logical-coverage/v1"
ORIGINAL = "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3"
CONDITIONAL = "9d23b7ebf3677f53fe6901c9b94f2c4927f1f0c47ed207c04c53df9b59ae9638"
INSTRUMENTER = "69e97d192ca4607a2c7f50c4d92460c0467b5f0f076b9e3775dd04ac4fe960e7"
APPROVED_MODULES = {
    "original": (ORIGINAL, "747185e77510c027a0f23e4d598292c3d1bf47d3c95a145204497d439fae2002"),
    "conditional": (CONDITIONAL, "06ace03c7878985b3f22576356b4275c4c5bad9c6a74304b42770af8397fe558"),
    "synthetic-fixture": ("7cf8f030615526b4dc2025114a547a07be7c764f7ece3cc044aece6f9dcf13a8",
                          "e548e6d40320c28f1156e8c910fa64ee6f87b936cba15856b41648295b5828fa")}
AUDITED_DRIVER = "5ea19ac96aa52d061ecf2b8e23df7ed3d7d3b36c7d2ae7fe62f01c5a9b610aaa"
RECORD_BYTES = 44928
MAX_BYTES = 1024**3
MAX_VIEW = 512 * 1024**2
MAGIC = b"OOMESHR1"
SENTINEL, INNER_GUARD, OUTER_GUARD = 0xCDCDCDCD, 0xA11CE55A, 0xB0A7D123
EXPECTED_BINDINGS = [(0, binding, element, 6 if binding < 5 else 7)
                     for binding in range(9) for element in range(3 if binding == 8 else 1)]
LAYOUT = {"stride_bytes": 44928, "stride_words": 11232, "prefix_guard_offset": 0,
          "prefix_guard_bytes": 64, "lane_counts_offset": 64, "lane_count": 32, "lane_count_stride": 8,
          "position_offset": 320, "generic_offset": 2368, "generic_locations": [0, 1, 3, 4, 5, 6, 7, 10, 11],
          "generic_array_stride": 2048, "triangle_offset": 20800, "triangle_array_stride": 16,
          "writtenness_offset": 22848, "writtenness_words": 5504, "suffix_guard_offset": 44864,
          "suffix_guard_bytes": 64, "max_vertices": 128, "max_primitives": 128,
          "value_sentinel_word": SENTINEL, "inner_guard_word": INNER_GUARD,
          "writtenness_initial_word": 0, "writtenness_written_word": 1}


def triple(value, name, minimum=0):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must have three dimensions")
    return [_integer(item, name, minimum, 0xFFFFFFFF) for item in value]


def window(grid, origin, groups):
    grid, origin, groups = triple(grid, "native grid", 1), triple(origin, "origin"), triple(groups, "window", 1)
    if math.prod(groups) > 32 or any(start + count > limit for start, count, limit in zip(origin, groups, grid)):
        raise ValueError("Replay window must be inside the captured native grid and contain at most32 groups")
    return grid, origin, groups


def initialized_capture(groups):
    count = math.prod(triple(groups, "window", 1))
    if count > 32:
        raise ValueError("Capture supports at most32 workgroups")
    record = [SENTINEL] * (RECORD_BYTES // 4)
    record[:16] = [INNER_GUARD] * 16
    record[5712:11216] = [0] * 5504
    record[11216:] = [INNER_GUARD] * 16
    words = [OUTER_GUARD] * 16 + record * count + [OUTER_GUARD] * 16
    return struct.pack(f"<{len(words)}I", *words)


def check_capture(data, groups):
    count = math.prod(triple(groups, "window", 1))
    if not 1 <= count <= 32 or len(data) != RECORD_BYTES * count + 128:
        raise ValueError("Capture byte extent differs from the full initialized buffer")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    if words[:16] != (OUTER_GUARD,) * 16 or words[-16:] != (OUTER_GUARD,) * 16:
        raise ValueError("Outer capture guard changed")
    counts = []
    for group in range(count):
        record = words[16 + group * 11232:16 + (group + 1) * 11232]
        if record[:16] != (INNER_GUARD,) * 16 or record[11216:] != (INNER_GUARD,) * 16:
            raise ValueError(f"Workgroup {group} inner capture guard changed")
        lane_rows = [record[16 + lane * 2:18 + lane * 2] for lane in range(32)]
        vertex_count, primitive_count = lane_rows[0]
        if vertex_count != 128 or primitive_count > 128 or any(row != lane_rows[0] for row in lane_rows):
            raise ValueError(f"Workgroup {group} missing/inconsistent/out-of-range per-lane output counts")
        expected_markers = (1,) * (5120 + primitive_count * 3) + (0,) * ((128 - primitive_count) * 3)
        if record[5712:11216] != expected_markers:
            raise ValueError(f"Workgroup {group} defined-component writtenness differs from output counts")
        for primitive in range(128):
            position = 5200 + primitive * 4
            if record[position + 3] != SENTINEL:
                raise ValueError(f"Workgroup {group} triangle padding changed")
            indices = record[position:position + 3]
            if primitive >= primitive_count:
                if indices != (SENTINEL,) * 3:
                    raise ValueError(f"Workgroup {group} non-emitted primitive storage changed")
            elif any(index >= vertex_count for index in indices):
                raise ValueError(f"Workgroup {group} emitted index exceeds vertex count")
        counts.append({"vertices": vertex_count, "primitives": primitive_count})
    return {"sha256": hashlib.sha256(data).hexdigest(), "counts": counts, "all_guards_and_writtenness_valid": True}


def compare_bytes(reference, alternative):
    if len(reference) != len(alternative):
        raise ValueError("Full capture lengths differ")
    if reference != alternative:
        first = next(i for i, (left, right) in enumerate(zip(reference, alternative)) if left != right)
        raise ValueError(f"Full initialized capture differs at byte {first}")
    return True


def verify_overlap(bindings, views):
    """Check captured bytes agree over every declared canonical backing overlap."""
    pieces = []
    for row in bindings:
        key = (row["binding"], row["element"])
        view = views[key]
        cursor = 0
        for piece in view["pieces"]:
            _object(piece, ("view_offset", "size", "backing_id", "generation", "backing_offset"))
            offset = _integer(piece["view_offset"], "piece view offset")
            size = _integer(piece["size"], "piece size", 1, row["range_bytes"])
            _identifier(piece["backing_id"])
            _integer(piece["generation"], "backing generation")
            backing = _integer(piece["backing_offset"], "backing offset")
            if offset != cursor or offset + size > row["range_bytes"] or backing + size >= 1 << 64:
                raise ValueError("Ownership pieces do not cover the exact descriptor range")
            cursor += size
            pieces.append((row, piece))
        if cursor != row["range_bytes"]:
            raise ValueError("Incomplete ownership interval coverage")
    for index, (left, a) in enumerate(pieces):
        for right, b in pieces[index + 1:]:
            if (a["backing_id"], a["generation"]) != (b["backing_id"], b["generation"]):
                continue
            low = max(a["backing_offset"], b["backing_offset"])
            high = min(a["backing_offset"] + a["size"], b["backing_offset"] + b["size"])
            if low >= high:
                continue
            with left["path"].open("rb") as one, right["path"].open("rb") as two:
                one.seek(a["view_offset"] + low - a["backing_offset"])
                two.seek(b["view_offset"] + low - b["backing_offset"])
                remaining = high - low
                while remaining:
                    length = min(remaining, 1024**2)
                    first, second = one.read(length), two.read(length)
                    if len(first) != length or len(second) != length or first != second:
                        raise ValueError("Aliased readonly input snapshots disagree")
                    remaining -= length


def capture_inputs(path, driver_policy, mode="historical-draw"):
    """Keep historical closure separate from an explicitly chosen readonly graph."""
    if mode not in ("historical-draw", "frozen-logical-readonly"):
        raise ValueError("An explicit supported replay evidence mode is required")
    if path.is_symlink():
        raise ValueError("Capture manifest must not be a symlink")
    root = path.resolve().parent
    document = _json(path)
    if not isinstance(document, dict) or document.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("Unsupported mesh logical input capture schema")
    event = document.get("event")
    if not isinstance(event, dict) or event.get("program") != "d8e59341cf4f629c" or \
            event.get("variant") != "0c8f08626a9d98a4" or event.get("spirv_sha256") != ORIGINAL:
        raise ValueError("Captured event differs from the exact audited d8 mesh stage")
    if mode == "historical-draw" and event.get("actual_site_confirmed") is not True:
        raise ValueError("Actual native command site was not confirmed")
    for name in ("selected_occurrence", "recording_sequence", "completed_recording_sequence", "recording_part", "logical_value"):
        if mode == "historical-draw" or name in event:
            _integer(event.get(name), name)
    if mode == "historical-draw" and event["recording_sequence"] != event["completed_recording_sequence"]:
        raise ValueError("Selected recording sequence did not complete")
    if mode == "historical-draw" and document.get("subgroup", {}).get("required_size") != 32:
        raise ValueError("Historical original mesh subgroup32 policy was not observed")
    event_key = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    launch = document.get("launch")
    if not isinstance(launch, dict) or launch.get("command") != "vkCmdDrawMeshTasksIndirectEXT" or \
            launch.get("draw_count") != 1 or launch.get("stride") != 12:
        raise ValueError("Expected the exact converted single native mesh indirect draw")
    launch_path = _snapshot(root, launch.get("snapshot"), 12, 12)
    grid = list(struct.unpack("<3I", launch_path.read_bytes()))
    triple(grid, "GPU-visible native grid", 1)
    bindings = document.get("bindings")
    if not isinstance(bindings, list) or len(bindings) != 11:
        raise ValueError("All eleven exact UBO/SSBO descriptor elements are required")
    signature, resolved = [], []
    for row in bindings:
        if not isinstance(row, dict):
            raise ValueError("Invalid descriptor row")
        slot = tuple(_integer(row.get(name), name, 0, 8) for name in ("set", "binding", "element", "type"))
        signature.append(slot)
        if "null_descriptor" in row and type(row["null_descriptor"]) is not bool:
            raise ValueError("Null descriptor state must be explicit boolean metadata")
        if row.get("null_descriptor") is True:
            if slot not in ((0, 8, 1, 7), (0, 8, 2, 7)) or document.get("features", {}).get("nullDescriptor") is not True:
                raise ValueError("Null descriptor is outside the reviewed slots or feature profile")
            if row.get("snapshot") is not None or row.get("range_bytes") is not None or \
                    row.get("original_buffer_offset") != 0 or row.get("descriptor_range") != (1 << 64) - 1 or \
                    row.get("buffer_handle") != "0000000000000000" or any(row.get(name) is not None for name in
                    ("allocation_row", "buffer_lifetime", "backing_generation")):
                raise ValueError("Null descriptor must retain actual null/offset0/WHOLE_SIZE framing without fabricated ownership or bytes")
            resolved.append(dict(row))
            continue
        size = _integer(row.get("range_bytes"), "descriptor range", 1, MAX_VIEW)
        data_path = _snapshot(root, row.get("snapshot"), size, MAX_VIEW)
        offset = _integer(row.get("original_buffer_offset"), "original descriptor offset")
        if offset + size >= 1 << 64:
            raise ValueError("Original descriptor range overflows")
        resolved.append({**row, "path": data_path})
    if sorted(signature) != EXPECTED_BINDINGS:
        raise ValueError("Descriptor slots differ from the audited d8 input layout")
    chosen = {"kind": "captured-d8", "mode": mode, "event": event, "event_sha256": event_key, "grid": grid,
              "bindings": sorted(resolved, key=lambda row: (row["binding"], row["element"])),
              "source_manifest": path.resolve(), "source_manifest_sha256": digest(path),
              "source_features": document.get("features", {}),
              "launch_sha256": launch["snapshot"]["sha256"],
              "retained_artifacts": [(path.resolve(), digest(path)), (launch_path, launch["snapshot"]["sha256"])]}
    if mode == "frozen-logical-readonly":
        chosen.update(historical_draw_reproduced=False, historical_atomicity="not-established",
                      historical_backing_provenance="not-established", coverage_sha256=None,
                      evidence_scope="Explicit chosen immutable descriptor graph; no historical-draw reproduction claim")
        return chosen
    coverage_path = _snapshot(root, document.get("coverage_evidence"), maximum=2 * 1024**2)
    evidence = _json(coverage_path)
    if not isinstance(evidence, dict) or evidence.get("schema") != COVERAGE_SCHEMA or evidence.get("event_sha256") != event_key:
        raise ValueError("Coverage certificate is missing or belongs to another complete event")
    required = ("ownership_complete", "cpu_writers_quiescent", "import_aliases_resolved", "ordered_gpu_before_snapshot")
    if any(evidence.get(name) is not True for name in required) or evidence.get("unresolved") != []:
        raise ValueError("Real mesh input closure remains incomplete: ownership, CPU writers, aliases or snapshot order")
    if evidence.get("driver_policy") != driver_policy or evidence.get("launch_sha256") != launch["snapshot"]["sha256"]:
        raise ValueError("Coverage policy or captured indirect bytes differ")
    _identifier(evidence.get("source_revision"))
    if not isinstance(evidence.get("source_binary_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", evidence["source_binary_sha256"]):
        raise ValueError("Capture producer binary is not hash pinned")
    expected_slots = sorted([row["set"], row["binding"], row["element"], row["type"], row["range_bytes"],
                             None if row.get("null_descriptor") else row["snapshot"]["sha256"]]
                            for row in resolved)
    if evidence.get("slots") != expected_slots:
        raise ValueError("Coverage certificate does not enumerate exact input ranges/digests")
    proofs = evidence.get("proof_sources")
    if not isinstance(proofs, list) or len(proofs) < 2:
        raise ValueError("Backing provenance and quiescence proof sources must be retained")
    chosen["retained_artifacts"].append((coverage_path, digest(coverage_path)))
    for proof in proofs:
        proof_path = _snapshot(root, proof, maximum=16 * 1024**2)
        chosen["retained_artifacts"].append((proof_path, proof["sha256"]))
    views = evidence.get("views")
    data_bindings = [row for row in resolved if not row.get("null_descriptor")]
    if not isinstance(views, list) or len(views) != len(data_bindings):
        raise ValueError("Canonical backing ownership must cover each descriptor view")
    by_slot = {}
    for view in views:
        if not isinstance(view, dict):
            raise ValueError("Invalid ownership view")
        key = (view.get("binding"), view.get("element"))
        _identifier(view.get("buffer_lifetime"))
        if key in by_slot or not isinstance(view.get("pieces"), list) or not view["pieces"]:
            raise ValueError("Duplicate or missing ownership pieces")
        by_slot[key] = view
    if set(by_slot) != {(row["binding"], row["element"]) for row in data_bindings}:
        raise ValueError("Ownership view slots differ from the input descriptors")
    verify_overlap(data_bindings, by_slot)
    chosen.update(coverage_sha256=digest(coverage_path), historical_draw_reproduced="not-proven-by-validation",
                  evidence_scope="Identity-bound producer assertions and retained proof artifacts; parent review still required")
    return chosen


def validate_instrumentation(report, module, expected_kind):
    if report.get("schema") != "open-ocarina.mesh-output-instrument/v1" or report.get("source_kind") != expected_kind:
        raise ValueError("Unexpected stage-preserving instrumentation profile")
    required = ("structural_validation_passed", "all_original_output_stores_preserved", "all_original_set_mesh_outputs_preserved",
                "all_original_control_barriers_preserved", "original_input_bindings_readonly")
    if any(report.get(name) is not True for name in required) or report.get("original_push_constant_bytes") != 0:
        raise ValueError("Instrumentation did not establish the bounded readonly output contract")
    approved_source, approved_module = APPROVED_MODULES[expected_kind]
    if report.get("source_module_sha256") != approved_source or report.get("instrumented_sha256") != approved_module or digest(module) != approved_module:
        raise ValueError("Module differs from the reviewed source/derivative digest pair")
    if report.get("record_layout") != LAYOUT:
        raise ValueError("Instrumented record layout/module digest differs")
    if report.get("instrumenter_source_sha256") != INSTRUMENTER or digest(Path(instrumenter.__file__)) != INSTRUMENTER:
        raise ValueError("Instrumenter source changed during preparation")
    if report.get("capture_descriptor") != {"set": 1, "binding": 0, "type": "storage_buffer"}:
        raise ValueError("Unexpected capture descriptor")
    control = report.get("control_descriptor", {})
    if any(control.get(key) != value for key, value in {"set": 1, "binding": 1, "type": "storage_buffer", "byte_size": 32, "readonly": True}.items()):
        raise ValueError("Unexpected origin control descriptor")
    if report.get("workgroup_origin_substitution", {}).get("enabled") is not True:
        raise ValueError("Original WorkgroupId origin preservation was not established")
    reflected = reflect_stage(module.read_bytes(), "mesh")
    if reflected["local_size"] != [32, 1, 1] or reflected["push_constant_bytes"] != 0:
        raise ValueError("Instrumented shader launch/push contract changed")
    expected = [{"set": 0, "binding": binding, "type": 6 if binding < 5 else 7,
                 "count": 3 if binding == 8 else 1} for binding in range(9)]
    expected += [{"set": 1, "binding": binding, "type": 7, "count": 1} for binding in (0, 1)]
    if reflected["bindings"] != expected:
        raise ValueError("Instrumented descriptor reflection differs from the exact supported layout")


def freeze_copy(source, destination, expected_sha256):
    with source.open("rb") as before, destination.open("xb") as after:
        shutil.copyfileobj(before, after, 1024**2)
    if digest(destination) != expected_sha256:
        raise ValueError("Input changed while freezing the replay artifacts")
    destination.chmod(0o444)


def write_native_plan(path, state, origin, groups):
    grid, origin, groups = window(state["grid"], origin, groups)
    unique, indices, descriptors = [], {}, []
    for row in state["bindings"]:
        if row.get("null_descriptor") is True:
            descriptors.append([0, row["binding"], row["element"], row["type"], 0xFFFFFFFF, 0])
            continue
        key = (row["snapshot"]["sha256"], row["range_bytes"])
        if key not in indices:
            indices[key] = len(unique)
            unique.append(row)
        descriptors.append([0, row["binding"], row["element"], row["type"], indices[key], row["range_bytes"]])
    initial = initialized_capture(groups)
    if len(unique) > 11 or sum(row["range_bytes"] for row in unique) + len(initial) > MAX_BYTES:
        raise ValueError("Bounded replay input budget exceeded")
    with path.open("xb") as target:
        header = [1, len(unique), len(descriptors), 0, len(initial), 64, len(initial) - 128, *groups, *origin, *grid]
        target.write(struct.pack("<8s16I", MAGIC, *header))
        for row in unique:
            target.write(struct.pack("<I", row["range_bytes"]))
            sha = hashlib.sha256()
            with row["path"].open("rb") as source:
                while chunk := source.read(1024**2):
                    sha.update(chunk)
                    target.write(chunk)
            if sha.hexdigest() != row["snapshot"]["sha256"]:
                raise ValueError("Descriptor snapshot changed while preparing native input bytes")
        for descriptor in descriptors:
            target.write(struct.pack("<6I", *descriptor))
        target.write(initial)
    path.chmod(0o444)
    return {"unique_input_buffers": len(unique), "descriptor_elements": len(descriptors), "capture_bytes": len(initial),
            "null_descriptor_elements": sum(row.get("null_descriptor") is True for row in state["bindings"]),
            "readonly_view_rebasing": "exact-range immutable copies at offset0; captured alias provenance retained",
            "input_layout": descriptors}


def build_helper(output):
    env = environment()
    flags = run_process(["pkg-config", "--cflags", "--libs", "vulkan"], env, output, "pkg-config", 15)
    import shlex
    helper = output / "mesh-replay-helper"
    source = ROOT / "tools" / "vulkan_mesh_replay.c"
    run_process(["cc", *BUILD_FLAGS, str(source), *shlex.split(flags), "-o", str(helper)], env, output, "build-helper", 60)
    return helper


def prepare(output_path, *, capture=None, original=None, conditional=None, origin=None, groups=None,
            fixture=False, mode=None, driver_policy="runtime", device=0, null_table_tail=False):
    """CPU-only preparation; even native plan checks stop before vkCreateInstance."""
    _integer(device, "device index", 0, 255)
    origin = [0, 0, 0] if origin is None else origin
    groups = [1, 1, 1] if groups is None else groups
    if fixture:
        if capture is not None or original is not None or conditional is not None or mode is not None:
            raise ValueError("The independent fixture cannot accept captured or arbitrary shader inputs")
        state = {"kind": "synthetic-fixture", "mode": "synthetic-fixture", "event": None,
                 "grid": [512, 512, 512], "bindings": [], "historical_draw_reproduced": False}
    else:
        if null_table_tail:
            raise ValueError("Captured descriptor states cannot be overridden by fixture options")
        if capture is None or original is None or conditional is None or mode is None:
            raise ValueError("Real-module preparation requires capture, both pinned sources and an explicit evidence mode")
        for path, expected in ((original, ORIGINAL), (conditional, CONDITIONAL)):
            if path.is_symlink() or not path.is_file() or not 20 <= path.stat().st_size <= 2 * 1024**2 or digest(path) != expected:
                raise ValueError("Only the exact reviewed bounded original and conditional d8 sources are supported")
        state = capture_inputs(capture, driver_policy, mode)
    window(state["grid"], origin, groups)
    output = private_output(output_path)
    output.mkdir(parents=True)
    retained = []
    for index, (source, sha256) in enumerate(state.get("retained_artifacts", [])):
        name = f"evidence-{index}{'.json' if source.suffix == '.json' else '.bin'}"
        freeze_copy(source, output / name, sha256)
        retained.append(name)
    if fixture:
        report = instrumenter.prepare_fixture(output / "original")
        state["fixture_source_sha256"] = report["source_module_sha256"]
        for _, binding, element, descriptor_type in EXPECTED_BINDINGS:
            if null_table_tail and binding == 8 and element in (1, 2):
                state["bindings"].append({"set": 0, "binding": binding, "element": element, "type": 7,
                                          "null_descriptor": True, "range_bytes": None, "snapshot": None,
                                          "original_buffer_offset": 0, "descriptor_range": (1 << 64) - 1})
                continue
            size = 64 if descriptor_type == 6 else 4
            data = bytes(size)
            filename = output / f"fixture-input-{binding}-{element}.bin"
            filename.write_bytes(data)
            state["bindings"].append({"set": 0, "binding": binding, "element": element, "type": descriptor_type,
                                      "range_bytes": size, "path": filename,
                                      "snapshot": {"size": size, "sha256": digest(filename)}})
        modules = {"original": (output / "original" / "instrumented.spv", report)}
    else:
        modules = {}
        for name, source in (("original", original), ("conditional", conditional)):
            report = instrumenter.prepare(source, output / name)
            modules[name] = (output / name / "instrumented.spv", report)
    for name, (module, report) in modules.items():
        validate_instrumentation(report, module, "synthetic-fixture" if fixture else name)
    helper = build_helper(output)
    input_info = write_native_plan(output / "inputs.bin", state, origin, groups)
    if fixture:
        expected_body = instrumenter.fixture_expected_capture(origin, groups)
        expected = initialized_capture(groups)[:64] + expected_body + initialized_capture(groups)[-64:]
        check_capture(expected, groups)
        (output / "expected.bin").write_bytes(expected)
    cases = [{"name": "original32-a", "module": "original/instrumented.spv", "width": 32},
             {"name": "original32-b", "module": "original/instrumented.spv", "width": 32},
             {"name": "conditional8", "module": "original/instrumented.spv" if fixture else "conditional/instrumented.spv", "width": 8},
             {"name": "conditional16", "module": "original/instrumented.spv" if fixture else "conditional/instrumented.spv", "width": 16}]
    # Native framing validation is CPU-only and returns before Vulkan instance creation.
    native_result = json.loads(run_process([str(helper), str(modules["original"][0]), str(output / "inputs.bin"),
                                           str(output / "unused-output.bin"), "32", str(device), "--validate"],
                                          environment(), output, "validate-native-plan", 15))
    if native_result.get("gpu_executed") is not False or native_result.get("native_plan_validated") is not True:
        raise ValueError("Unexpected CPU-only native validation result")
    _, policy = controlled_driver_environment(driver_policy, {})
    artifacts = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.suffix not in (".stdout", ".stderr", ".spvasm"):
            artifacts[path.relative_to(output).as_posix()] = {"size": path.stat().st_size, "sha256": digest(path)}
            path.chmod(0o555 if path == helper else 0o444)
    sources = {"tools/mesh_replay.py": digest(Path(__file__)),
               "tools/vulkan_mesh_replay.c": digest(ROOT / "tools" / "vulkan_mesh_replay.c"),
               "tools/mesh_output_instrument.py": digest(Path(instrumenter.__file__))}
    summary = {key: value for key, value in state.items() if key not in ("bindings", "source_manifest", "retained_artifacts")}
    summary["retained_artifacts"] = retained
    summary["bindings"] = [{key: value for key, value in row.items() if key != "path"} for row in state["bindings"]]
    plan = {"schema": SCHEMA, "prepare_only": True, "gpu_executed": False, "mode": state["mode"],
            "kind": state["kind"], "origin": origin, "groups": groups, "native_grid": state["grid"],
            "device_index": device, "driver_policy": policy, "source_capture": summary,
            "expected_driver_library_sha256": AUDITED_DRIVER,
            "artifacts": artifacts, "sources": sources, "cases": cases, "input_info": input_info,
            "instrumentation": {name: report for name, (_, report) in modules.items()},
            "helper_build_flags": BUILD_FLAGS, "native_plan_validation": native_result,
            "enabled_feature_profile": {"meshShader": True, "dynamicRendering": True, "subgroupSizeControl": True,
                "pipelineExecutableInfo": True, "robustBufferAccess": True, "robustBufferAccess2": True,
                "vertexPipelineStoresAndAtomics": True, "shaderInt16": True, "shaderInt64": True,
                "shaderStorageBufferArrayDynamicIndexing": True, "shaderImageGatherExtended": True,
                "uniformBufferStandardLayout": True, "shaderFloat16": True, "shaderInt8": True,
                "nullDescriptor_required": input_info["null_descriptor_elements"] > 0,
                "signed_zero_inf_nan_preserve_float32_required": True},
            "limits": {"groups_per_draw": 32, "draws_per_helper": 1, "fence_seconds": 2, "process_seconds": 30,
                       "max_total_input_bytes": MAX_BYTES},
            "claims": {"real_game_rendering_equivalence": False, "fps_gain": False,
                       "historical_draw_reproduced": False, "conditional_transform_equivalence_proven": False}}
    if not fixture and digest(capture) != state["source_manifest_sha256"]:
        raise ValueError("Capture manifest changed during preparation")
    destination = output / "prepared-plan.json"
    destination.write_text(json.dumps(plan, indent=2) + "\n")
    destination.chmod(0o444)
    return plan


def checked_prepared(path, expected_sha256):
    if path.is_symlink():
        raise ValueError("Prepared directory must not be a symlink")
    path = path.resolve()
    if path.is_relative_to(ROOT) and path.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",)):
        raise ValueError("Prepared output must remain private")
    manifest = path / "prepared-plan.json"
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or digest(manifest) != expected_sha256:
        raise ValueError("Prepared plan differs from the explicitly reviewed SHA-256")
    plan = _json(manifest)
    if plan.get("schema") != SCHEMA or plan.get("prepare_only") is not True or plan.get("gpu_executed") is not False:
        raise ValueError("Unexpected prepared native-mesh replay plan")
    window(plan["native_grid"], plan["origin"], plan["groups"])
    for name, artifact in plan["artifacts"].items():
        _snapshot(path, {"path": name, **artifact}, maximum=MAX_BYTES + 2 * 1024**2)
    for name, expected in plan["sources"].items():
        if name not in ("tools/mesh_replay.py", "tools/vulkan_mesh_replay.c", "tools/mesh_output_instrument.py") or digest(ROOT / name) != expected:
            raise ValueError("Reviewed implementation source changed")
    if set(plan["sources"]) != {"tools/mesh_replay.py", "tools/vulkan_mesh_replay.c", "tools/mesh_output_instrument.py"}:
        raise ValueError("Incomplete source identity graph")
    if plan["kind"] not in ("synthetic-fixture", "captured-d8") or \
            (plan["kind"] == "synthetic-fixture") != (plan["mode"] == "synthetic-fixture"):
        raise ValueError("Prepared module kind and evidence mode disagree")
    expected_names = {"original"} if plan["kind"] == "synthetic-fixture" else {"original", "conditional"}
    if set(plan["instrumentation"]) != expected_names or plan["mode"] not in ("synthetic-fixture", "historical-draw", "frozen-logical-readonly"):
        raise ValueError("Unsupported prepared module/evidence profile")
    for name, report in plan["instrumentation"].items():
        validate_instrumentation(report, path / name / "instrumented.spv", "synthetic-fixture" if plan["kind"] == "synthetic-fixture" else name)
        if digest(path / name / "source.spv") != report["source_module_sha256"] or _json(path / name / "report.json") != report:
            raise ValueError("Prepared source/audit artifacts differ")
    expected_cases = [("original32-a", 32), ("original32-b", 32), ("conditional8", 8), ("conditional16", 16)]
    if [(case.get("name"), case.get("width")) for case in plan["cases"]] != expected_cases:
        raise ValueError("Unexpected execution case list")
    for case in plan["cases"]:
        expected_module = "original/instrumented.spv" if case["width"] == 32 or plan["kind"] == "synthetic-fixture" else "conditional/instrumented.spv"
        if case["module"] != expected_module or case["module"] not in plan["artifacts"]:
            raise ValueError("Execution module is absent from the frozen artifact graph")
    if (path / "execution-started.json").exists() or (path / "execution-report.json").exists():
        raise ValueError("Prepared plan has already been used; choose a fresh preparation")
    return path, plan


def execute(path, expected_sha256, driver):
    output, plan = checked_prepared(path, expected_sha256)
    identity = driver_identity(driver)
    if identity["library_sha256"] != AUDITED_DRIVER or plan.get("expected_driver_library_sha256") != AUDITED_DRIVER:
        raise ValueError("Execution driver differs from the explicitly audited library")
    env, policy = controlled_driver_environment(plan["driver_policy"]["name"], environment())
    if policy != plan["driver_policy"]:
        raise ValueError("Driver compiler policy changed")
    env.update(VK_ICD_FILENAMES=str(driver.resolve()), VK_DRIVER_FILES=str(driver.resolve()),
               MESA_SHADER_CACHE_DISABLE="true", MESA_SHADER_CACHE_DIR=str(output / "cache"))
    ensure_no_games()
    marker = {"reviewed_plan_sha256": expected_sha256, "driver": identity, "mode": plan["mode"]}
    with (output / "execution-started.json").open("x") as target:
        json.dump(marker, target)
    observations, reference = [], None
    for case in plan["cases"]:
        ensure_no_games()
        result_path = output / f"{case['name']}.bin"
        metadata = json.loads(run_process([str(output / "mesh-replay-helper"), str(output / case["module"]),
                                          str(output / "inputs.bin"), str(result_path), str(case["width"]),
                                          str(plan["device_index"])], env, output, case["name"], 30))
        if metadata.get("gpu_executed") is not True or metadata.get("actual_width") != case["width"] or \
                metadata.get("requested_width") != case["width"] or metadata.get("executable_stage") != 128 or \
                metadata.get("width_source") != "pipeline_executable_properties" or metadata.get("mesh_draw_calls") != 1 or \
                metadata.get("draw_mesh_tasks") != plan["groups"] or metadata.get("origin") != plan["origin"] or \
                metadata.get("input_bytes_unchanged") is not True or metadata.get("control_bytes_unchanged") is not True:
            raise ValueError("Native helper metadata differs from the reviewed bounded case")
        data = result_path.read_bytes()
        validation = check_capture(data, plan["groups"])
        if plan["kind"] == "synthetic-fixture":
            compare_bytes((output / "expected.bin").read_bytes(), data)
        if reference is None:
            reference = data
        else:
            compare_bytes(reference, data)
        observations.append({"case": case, "metadata": metadata, "capture": validation})
    report = {"schema": "open-ocarina.native-mesh-replay-result/v1", "reviewed_plan_sha256": expected_sha256,
              "kind": plan["kind"], "mode": plan["mode"], "driver": identity, "driver_policy": policy,
              "all_full_buffers_identical": True, "baseline_repeat_identical": True,
              "cases": observations, "historical_draw_reproduced": False, "real_game_rendering_equivalence": False,
              "conditional_transform_equivalence_proven": False, "fps_gain": False,
              "scope": "Bounded instrumented native-mesh logical outputs for the chosen immutable input graph"}
    (output / "execution-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def parse_triple(value):
    try:
        result = [int(item, 0) for item in value.split(",")]
        return triple(result, "command-line dimensions")
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("fixture", "prepare"):
        command = commands.add_parser(name, help="CPU-only preparation and native framing validation")
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--origin", type=parse_triple, default=[0, 0, 0])
        command.add_argument("--groups", type=parse_triple, default=[1, 1, 1])
        command.add_argument("--driver-policy", choices=("diagnostic", "runtime"), default="runtime")
        command.add_argument("--device", type=int, default=0)
        if name == "fixture":
            command.add_argument("--null-table-tail", action="store_true", help="Exercise actual null-descriptor framing at binding8 elements1/2")
        if name == "prepare":
            command.add_argument("--capture", type=Path, required=True)
            command.add_argument("--original", type=Path, required=True)
            command.add_argument("--conditional", type=Path, required=True)
            command.add_argument("--mode", choices=("historical-draw", "frozen-logical-readonly"), required=True)
    command = commands.add_parser("execute", help="GPU operation requiring reviewed plan and exclusive device ownership")
    command.add_argument("--prepared", type=Path, required=True)
    command.add_argument("--plan-sha256", required=True)
    command.add_argument("--driver", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "execute":
            report = execute(args.prepared, args.plan_sha256, args.driver)
        else:
            options = vars(args).copy()
            options.pop("command")
            output = options.pop("output")
            report = prepare(output, fixture=args.command == "fixture", **options)
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
