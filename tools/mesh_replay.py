#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare bounded native-mesh logical-output replay; GPU execution is explicit.

Only exact audited source pairs for an explicit mesh profile, or independently
authored fixtures, can enter preparation. Plan hashes pin execution artifacts.
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
from tools import mesh_output_profile96 as instrumenter96
from tools.compile_probe import driver_identity, private_output
from tools.graphics_compile_probe import controlled_driver_environment, reflect_stage
from tools.mesh_mapping_probe import BUILD_FLAGS, digest, environment, ensure_no_games, run_process
from tools.replay_contract import _identifier, _integer, _json, _object, _snapshot

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "open-ocarina.prepared-native-mesh-replay/v2"
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
DRIVER_PROFILES = {
    "legacy-5ea": {"library_sha256": AUDITED_DRIVER, "icd_sha256": None, "policies": ("diagnostic", "runtime")},
    "cache-safe-e5": {"library_sha256": "e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f",
                      "icd_sha256": "14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3",
                      "policies": ("native-safe",)}}
D8_PROFILE = "d8-position9-128/v1"
PROFILE96 = "6864-position10-96/v1"
DEPENDENCY_SOURCES = ("tools/mesh_replay.py", "tools/vulkan_mesh_replay.c", "tools/mesh_output_instrument.py",
                      "tools/mesh_output_profile96.py", "tools/graphics_compile_probe.py", "tools/mesh_mapping_probe.py",
                      "tools/compile_probe.py", "tools/replay_contract.py", "tools/spirv_contract.py")
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


def output_profile(name):
    if name == D8_PROFILE:
        return {"id": name, "schema": SCHEMA, "result_schema": "open-ocarina.native-mesh-replay-result/v2",
                "capture_schema": CAPTURE_SCHEMA, "coverage_schema": COVERAGE_SCHEMA,
                "program": "d8e59341cf4f629c", "variant": "0c8f08626a9d98a4", "layout": LAYOUT,
                "magic": MAGIC, "engine": instrumenter, "engine_sha256": INSTRUMENTER,
                "report_schema": "open-ocarina.mesh-output-instrument/v1", "modules": APPROVED_MODULES,
                "bindings": EXPECTED_BINDINGS, "ubo_count": 5, "kind": "captured-d8", "process_seconds": 30}
    if name == PROFILE96:
        return {"id": name, "schema": "open-ocarina.prepared-native-mesh-replay96/v1",
                "result_schema": "open-ocarina.native-mesh-replay96-result/v1",
                "capture_schema": "open-ocarina.mesh-input-profile96/v1",
                "coverage_schema": "open-ocarina.mesh-logical-coverage96/v1",
                "program": "68642bb810ee3e7c", "variant": "3f3f5b29054673da", "layout": instrumenter96.LAYOUT,
                "magic": b"OOMESH96", "engine": instrumenter96,
                "engine_sha256": "4619e081b652b9ed1b72820a22d1948907196b08de05a10cefebd15703f42f09",
                "report_schema": "open-ocarina.mesh-output-profile96/v1",
                "modules": {
                    "original": ("999c8511db93d486543421dde7350e3d0034faa834ef3edaf3553dcdc9b55d93",
                                 "2d0bd1c6dfb72398a30c6ad4539be01dae27fbc1c92e963de2b2e3db9c381682"),
                    "conditional": ("a41c6df753f30f8c0646ffa45227304e994b3fbaccdcad64927cd57f5e2327e7",
                                    "8e10dad18d4b674df682ee03c00b507457de8c691d4e72f85919ce056128e573"),
                    "synthetic-fixture": ("1944ea426326138609f4fbcbdace60ad34cd4c5d8091235c8469036d44c6b162",
                                          "9453c1a003e070a69e2dd7462aa6ef0de5c433a91e0976fffa45da9bb70cf3a7")},
                "bindings": [(0, binding, element, 6 if binding < 4 else 7)
                             for binding in range(9) for element in range(3 if binding == 8 else 1)],
                "ubo_count": 4, "kind": "captured-6864", "process_seconds": 120}
    raise ValueError("Unknown explicit mesh output profile")


def execution_limits(profile_id):
    return {"groups_per_draw": 32, "draws_per_helper": 1, "fence_seconds": 2,
            "process_seconds": output_profile(profile_id)["process_seconds"], "max_total_input_bytes": MAX_BYTES}


def validate_driver_profile(name, policy):
    if not isinstance(name, str) or name not in DRIVER_PROFILES or policy not in DRIVER_PROFILES[name]["policies"]:
        raise ValueError("Unknown or incompatible driver profile/compiler policy")
    result = {"name": name, "library_sha256": DRIVER_PROFILES[name]["library_sha256"],
              "icd_sha256": DRIVER_PROFILES[name]["icd_sha256"], "policy": policy}
    _, controls = controlled_driver_environment(policy, {})
    if name == "cache-safe-e5":
        active = {key: value for key, value in controls["controlled_options"].items() if value is not None}
        if policy != "native-safe" or active != {"HS_SPILL_BATCH": "1", "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1"}:
            raise ValueError("Cache-safe E5 requires the exact named MAX-absent native-safe policy")
    return result


def dependency_hashes():
    return {name: digest(ROOT / name) for name in DEPENDENCY_SOURCES}


def triple(value, name, minimum=0):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must have three dimensions")
    return [_integer(item, name, minimum, 0xFFFFFFFF) for item in value]


def window(grid, origin, groups):
    grid, origin, groups = triple(grid, "native grid", 1), triple(origin, "origin"), triple(groups, "window", 1)
    if math.prod(groups) > 32 or any(start + count > limit for start, count, limit in zip(origin, groups, grid)):
        raise ValueError("Replay window must be inside the captured native grid and contain at most32 groups")
    return grid, origin, groups


def initialized_capture(groups, profile_id=D8_PROFILE):
    layout = output_profile(profile_id)["layout"]
    count = math.prod(triple(groups, "window", 1))
    if count > 32:
        raise ValueError("Capture supports at most32 workgroups")
    record = [SENTINEL] * layout["stride_words"]
    record[:16] = [INNER_GUARD] * 16
    marker, suffix = layout["writtenness_offset"] // 4, layout["suffix_guard_offset"] // 4
    record[marker:suffix] = [0] * layout["writtenness_words"]
    record[suffix:] = [INNER_GUARD] * 16
    words = [OUTER_GUARD] * 16 + record * count + [OUTER_GUARD] * 16
    return struct.pack(f"<{len(words)}I", *words)


def check_capture(data, groups, profile_id=D8_PROFILE):
    layout = output_profile(profile_id)["layout"]
    stride, marker, suffix = layout["stride_words"], layout["writtenness_offset"] // 4, layout["suffix_guard_offset"] // 4
    max_vertices, max_primitives = layout["max_vertices"], layout["max_primitives"]
    vertex_components = max_vertices * 4 * (1 + len(layout["generic_locations"]))
    count = math.prod(triple(groups, "window", 1))
    if not 1 <= count <= 32 or len(data) != layout["stride_bytes"] * count + 128:
        raise ValueError("Capture byte extent differs from the full initialized buffer")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    if words[:16] != (OUTER_GUARD,) * 16 or words[-16:] != (OUTER_GUARD,) * 16:
        raise ValueError("Outer capture guard changed")
    counts = []
    for group in range(count):
        record = words[16 + group * stride:16 + (group + 1) * stride]
        if record[:16] != (INNER_GUARD,) * 16 or record[suffix:] != (INNER_GUARD,) * 16:
            raise ValueError(f"Workgroup {group} inner capture guard changed")
        lane_rows = [record[16 + lane * 2:18 + lane * 2] for lane in range(32)]
        vertex_count, primitive_count = lane_rows[0]
        if vertex_count != max_vertices or primitive_count > max_primitives or any(row != lane_rows[0] for row in lane_rows):
            raise ValueError(f"Workgroup {group} missing/inconsistent/out-of-range per-lane output counts")
        expected_markers = (1,) * (vertex_components + primitive_count * 3) + (0,) * ((max_primitives - primitive_count) * 3)
        if record[marker:suffix] != expected_markers:
            raise ValueError(f"Workgroup {group} defined-component writtenness differs from output counts")
        for primitive in range(max_primitives):
            position = layout["triangle_offset"] // 4 + primitive * 4
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


def capture_inputs(path, driver_policy, mode="historical-draw", profile_id=D8_PROFILE):
    """Keep historical closure separate from an explicitly chosen readonly graph."""
    if mode not in ("historical-draw", "frozen-logical-readonly"):
        raise ValueError("An explicit supported replay evidence mode is required")
    profile = output_profile(profile_id)
    if path.is_symlink():
        raise ValueError("Capture manifest must not be a symlink")
    root = path.resolve().parent
    document = _json(path)
    if not isinstance(document, dict) or document.get("schema") != profile["capture_schema"]:
        raise ValueError("Unsupported mesh logical input capture schema")
    if profile_id == PROFILE96 and document.get("profile") != PROFILE96:
        raise ValueError("Explicit profile96 capture identity is required")
    if profile_id == PROFILE96 and any(document.get(name) is not False for name in
            ("runtime_execution_authorized", "equivalence_proven", "replay_complete")):
        raise ValueError("Profile96 producer flags must retain their explicit unproven/unauthorized state")
    event = document.get("event")
    if not isinstance(event, dict) or event.get("program") != profile["program"] or \
            event.get("variant") != profile["variant"] or event.get("spirv_sha256") != profile["modules"]["original"][0]:
        raise ValueError("Captured event differs from the exact audited mesh profile")
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
    if sorted(signature) != profile["bindings"]:
        raise ValueError("Descriptor slots differ from the audited profile input layout")
    chosen = {"kind": profile["kind"], "output_profile": profile_id, "mode": mode, "event": event, "event_sha256": event_key, "grid": grid,
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
    if not isinstance(evidence, dict) or evidence.get("schema") != profile["coverage_schema"] or evidence.get("event_sha256") != event_key:
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


def validate_instrumentation(report, module, expected_kind, profile_id=D8_PROFILE):
    profile = output_profile(profile_id)
    engine = profile["engine"]
    if report.get("schema") != profile["report_schema"] or report.get("source_kind") != expected_kind:
        raise ValueError("Unexpected stage-preserving instrumentation profile")
    if profile_id == PROFILE96 and (report.get("output_profile") != PROFILE96 or
            report.get("frozen_infrastructure_sha256") != INSTRUMENTER or
            report.get("resource_auditor_sha256") != digest(ROOT / "tools" / "spirv_contract.py")):
        raise ValueError("Profile96 runtime audit/dependency identity differs")
    required = ("structural_validation_passed", "all_original_output_stores_preserved", "all_original_set_mesh_outputs_preserved",
                "all_original_control_barriers_preserved", "original_input_bindings_readonly")
    if any(report.get(name) is not True for name in required) or report.get("original_push_constant_bytes") != 0:
        raise ValueError("Instrumentation did not establish the bounded readonly output contract")
    approved_source, approved_module = profile["modules"][expected_kind]
    if report.get("source_module_sha256") != approved_source or report.get("instrumented_sha256") != approved_module or digest(module) != approved_module:
        raise ValueError("Module differs from the reviewed source/derivative digest pair")
    if report.get("record_layout") != profile["layout"]:
        raise ValueError("Instrumented record layout/module digest differs")
    if report.get("instrumenter_source_sha256") != profile["engine_sha256"] or digest(Path(engine.__file__)) != profile["engine_sha256"]:
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
    expected = [{"set": 0, "binding": binding, "type": 6 if binding < profile["ubo_count"] else 7,
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


def write_native_plan(path, state, origin, groups, profile_id=D8_PROFILE):
    profile = output_profile(profile_id)
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
    initial = initialized_capture(groups, profile_id)
    if len(unique) > 11 or sum(row["range_bytes"] for row in unique) + len(initial) > MAX_BYTES:
        raise ValueError("Bounded replay input budget exceeded")
    with path.open("xb") as target:
        header = [1, len(unique), len(descriptors), 0, len(initial), 64, len(initial) - 128, *groups, *origin, *grid]
        target.write(struct.pack("<8s16I", profile["magic"], *header))
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
            fixture=False, mode=None, driver_policy=None, driver_profile=None, profile_id=D8_PROFILE,
            device=0, null_table_tail=False):
    """CPU-only preparation; even native plan checks stop before vkCreateInstance."""
    _integer(device, "device index", 0, 255)
    profile = output_profile(profile_id)
    engine = profile["engine"]
    selected_driver = validate_driver_profile(driver_profile, driver_policy)
    initial_dependencies = dependency_hashes()
    origin = [0, 0, 0] if origin is None else origin
    groups = [1, 1, 1] if groups is None else groups
    if fixture:
        if capture is not None or original is not None or conditional is not None or mode is not None:
            raise ValueError("The independent fixture cannot accept captured or arbitrary shader inputs")
        state = {"kind": "synthetic-fixture", "output_profile": profile_id, "mode": "synthetic-fixture", "event": None,
                 "grid": [512, 512, 512], "bindings": [], "historical_draw_reproduced": False}
    else:
        if null_table_tail:
            raise ValueError("Captured descriptor states cannot be overridden by fixture options")
        if capture is None or original is None or conditional is None or mode is None:
            raise ValueError("Real-module preparation requires capture, both pinned sources and an explicit evidence mode")
        for path, expected in ((original, profile["modules"]["original"][0]), (conditional, profile["modules"]["conditional"][0])):
            if path.is_symlink() or not path.is_file() or not 20 <= path.stat().st_size <= 2 * 1024**2 or digest(path) != expected:
                raise ValueError("Only the exact reviewed bounded original and conditional profile sources are supported")
        state = capture_inputs(capture, driver_policy, mode, profile_id)
    window(state["grid"], origin, groups)
    output = private_output(output_path)
    output.mkdir(parents=True)
    retained = []
    for index, (source, sha256) in enumerate(state.get("retained_artifacts", [])):
        name = f"evidence-{index}{'.json' if source.suffix == '.json' else '.bin'}"
        freeze_copy(source, output / name, sha256)
        retained.append(name)
    if fixture:
        report = engine.prepare_fixture(output / "original")
        state["fixture_source_sha256"] = report["source_module_sha256"]
        for _, binding, element, descriptor_type in profile["bindings"]:
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
            report = engine.prepare(source, output / name)
            modules[name] = (output / name / "instrumented.spv", report)
    for name, (module, report) in modules.items():
        validate_instrumentation(report, module, "synthetic-fixture" if fixture else name, profile_id)
    helper = build_helper(output)
    input_info = write_native_plan(output / "inputs.bin", state, origin, groups, profile_id)
    if fixture:
        expected_body = engine.fixture_expected_capture(origin, groups)
        expected = initialized_capture(groups, profile_id)[:64] + expected_body + initialized_capture(groups, profile_id)[-64:]
        check_capture(expected, groups, profile_id)
        (output / "expected.bin").write_bytes(expected)
    cases = [{"name": "original32-a", "module": "original/instrumented.spv", "width": 32},
             {"name": "original32-b", "module": "original/instrumented.spv", "width": 32},
             {"name": "conditional8", "module": "original/instrumented.spv" if fixture else "conditional/instrumented.spv", "width": 8},
             {"name": "conditional16", "module": "original/instrumented.spv" if fixture else "conditional/instrumented.spv", "width": 16}]
    # Native framing validation is CPU-only and returns before Vulkan instance creation.
    native_result = json.loads(run_process([str(helper), str(modules["original"][0]), str(output / "inputs.bin"),
                                           str(output / "unused-output.bin"), "32", str(device), "--validate"],
                                          environment(), output, "validate-native-plan", 15))
    if native_result.get("output_profile") != profile_id or native_result.get("gpu_executed") is not False or native_result.get("native_plan_validated") is not True:
        raise ValueError("Unexpected CPU-only native validation result")
    _, policy = controlled_driver_environment(driver_policy, {})
    artifacts = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.suffix not in (".stdout", ".stderr", ".spvasm"):
            artifacts[path.relative_to(output).as_posix()] = {"size": path.stat().st_size, "sha256": digest(path)}
            path.chmod(0o555 if path == helper else 0o444)
    sources = dependency_hashes()
    if sources != initial_dependencies:
        raise ValueError("Correctness/execution dependency source changed during preparation")
    summary = {key: value for key, value in state.items() if key not in ("bindings", "source_manifest", "retained_artifacts")}
    summary["retained_artifacts"] = retained
    summary["bindings"] = [{key: value for key, value in row.items() if key != "path"} for row in state["bindings"]]
    plan = {"schema": profile["schema"], "output_profile": profile_id, "prepare_only": True, "gpu_executed": False, "mode": state["mode"],
            "kind": state["kind"], "origin": origin, "groups": groups, "native_grid": state["grid"],
            "device_index": device, "driver_policy": policy, "source_capture": summary,
            "driver_profile": selected_driver, "expected_driver_library_sha256": selected_driver["library_sha256"],
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
            "limits": execution_limits(profile_id),
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
    profile = output_profile(plan.get("output_profile"))
    if plan.get("schema") != profile["schema"] or plan.get("prepare_only") is not True or plan.get("gpu_executed") is not False:
        raise ValueError("Unexpected prepared native-mesh replay plan")
    limits = plan.get("limits")
    if not isinstance(limits, dict) or any(type(value) is not int for value in limits.values()) or limits != execution_limits(profile["id"]):
        raise ValueError("Prepared execution limits differ from the exact mesh profile budget")
    selected_driver = validate_driver_profile(plan.get("driver_profile", {}).get("name"), plan.get("driver_policy", {}).get("name"))
    if plan.get("driver_profile") != selected_driver or plan.get("expected_driver_library_sha256") != selected_driver["library_sha256"]:
        raise ValueError("Prepared driver profile/hash/policy identity differs")
    window(plan["native_grid"], plan["origin"], plan["groups"])
    for name, artifact in plan["artifacts"].items():
        _snapshot(path, {"path": name, **artifact}, maximum=MAX_BYTES + 2 * 1024**2)
    for name, expected in plan["sources"].items():
        if name not in DEPENDENCY_SOURCES or digest(ROOT / name) != expected:
            raise ValueError("Reviewed implementation source changed")
    if set(plan["sources"]) != set(DEPENDENCY_SOURCES):
        raise ValueError("Incomplete source identity graph")
    if plan["kind"] not in ("synthetic-fixture", profile["kind"]) or \
            (plan["kind"] == "synthetic-fixture") != (plan["mode"] == "synthetic-fixture"):
        raise ValueError("Prepared module kind and evidence mode disagree")
    expected_names = {"original"} if plan["kind"] == "synthetic-fixture" else {"original", "conditional"}
    if set(plan["instrumentation"]) != expected_names or plan["mode"] not in ("synthetic-fixture", "historical-draw", "frozen-logical-readonly"):
        raise ValueError("Unsupported prepared module/evidence profile")
    for name, report in plan["instrumentation"].items():
        validate_instrumentation(report, path / name / "instrumented.spv", "synthetic-fixture" if plan["kind"] == "synthetic-fixture" else name, profile["id"])
        if digest(path / name / "source.spv") != report["source_module_sha256"] or _json(path / name / "report.json") != report:
            raise ValueError("Prepared source/audit artifacts differ")
    expected_cases = [("original32-a", 32), ("original32-b", 32), ("conditional8", 8), ("conditional16", 16)]
    if [(case.get("name"), case.get("width")) for case in plan["cases"]] != expected_cases:
        raise ValueError("Unexpected execution case list")
    for case in plan["cases"]:
        expected_module = "original/instrumented.spv" if case["width"] == 32 or plan["kind"] == "synthetic-fixture" else "conditional/instrumented.spv"
        if case["module"] != expected_module or case["module"] not in plan["artifacts"]:
            raise ValueError("Execution module is absent from the frozen artifact graph")
    with (path / "inputs.bin").open("rb") as native:
        header = native.read(72)
    if len(header) != 72:
        raise ValueError("Native control header is incomplete")
    native_header = struct.unpack("<8s16I", header)
    expected_capture_size = profile["layout"]["stride_bytes"] * math.prod(plan["groups"]) + 128
    if native_header[0] != profile["magic"] or native_header[1] != 1 or native_header[5:8] != (expected_capture_size, 64, expected_capture_size - 128):
        raise ValueError("Native control magic/profile/record extent differs")
    if native_header[8:11] != tuple(plan["groups"]) or native_header[11:14] != tuple(plan["origin"]) or native_header[14:17] != tuple(plan["native_grid"]):
        raise ValueError("Native control launch coordinates differ from the reviewed plan")
    if (path / "execution-started.json").exists() or (path / "execution-report.json").exists():
        raise ValueError("Prepared plan has already been used; choose a fresh preparation")
    return path, plan


def execute(path, expected_sha256, driver):
    output, plan = checked_prepared(path, expected_sha256)
    profile = output_profile(plan["output_profile"])
    identity = driver_identity(driver)
    selected_driver = plan["driver_profile"]
    if identity["library_sha256"] != selected_driver["library_sha256"] or \
            (selected_driver["icd_sha256"] is not None and identity["icd_sha256"] != selected_driver["icd_sha256"]):
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
                                          str(plan["device_index"])], env, output, case["name"], profile["process_seconds"]))
        if metadata.get("output_profile") != profile["id"] or metadata.get("gpu_executed") is not True or metadata.get("actual_width") != case["width"] or \
                metadata.get("requested_width") != case["width"] or metadata.get("executable_stage") != 128 or \
                metadata.get("width_source") != "pipeline_executable_properties" or metadata.get("mesh_draw_calls") != 1 or \
                metadata.get("draw_mesh_tasks") != plan["groups"] or metadata.get("origin") != plan["origin"] or \
                metadata.get("input_bytes_unchanged") is not True or metadata.get("control_bytes_unchanged") is not True:
            raise ValueError("Native helper metadata differs from the reviewed bounded case")
        data = result_path.read_bytes()
        validation = check_capture(data, plan["groups"], profile["id"])
        if plan["kind"] == "synthetic-fixture":
            compare_bytes((output / "expected.bin").read_bytes(), data)
        if reference is None:
            reference = data
        else:
            compare_bytes(reference, data)
        observations.append({"case": case, "metadata": metadata, "capture": validation})
    report = {"schema": profile["result_schema"], "output_profile": profile["id"], "reviewed_plan_sha256": expected_sha256,
              "kind": plan["kind"], "mode": plan["mode"], "driver": identity, "driver_profile": selected_driver, "driver_policy": policy,
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
        command.add_argument("--driver-policy", choices=("diagnostic", "runtime", "native-safe"), required=True)
        command.add_argument("--driver-profile", choices=tuple(DRIVER_PROFILES), required=True)
        command.add_argument("--profile-id", choices=(D8_PROFILE, PROFILE96), default=D8_PROFILE)
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
