#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare owned D8 interval mesh-output replay. No device, queue, or draw.

The original mesh is instrumented by the reviewed output instrumenter. The
interval candidate is instrumented by that same assembly transform after this
tool proves binding 30 is one extra NonWritable storage buffer. Execution and
timing stay unadmitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import mesh_output_instrument as instrument
from tools import mesh_replay
from tools.compile_probe import private_output
from tools.graphics_compile_probe import controlled_driver_environment, reflect_stage
from tools.mesh_mapping_probe import BUILD_FLAGS, digest, environment, run_process

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "open-ocarina.grok-d8-interval-replay/v1"
ORIGINAL = "835aa59799c6ecb621034d946e2fa4eadb1aa2407d293321ae3e3052882bc9b3"
CANDIDATE = "c2ae946e5b4706d220e3adb940ee00e4b382f326ca4f63586d043b2990650129"
RECORDS = "40c0e1d71a67998d9da47b9aec592616d3e9cb7de1ad512887835f5b087cd09a"
INSTRUMENTED_ORIGINAL = "747185e77510c027a0f23e4d598292c3d1bf47d3c95a145204497d439fae2002"
HELPERS = (149, 620)
ENTRY = 851
RECORD_BYTES = 400
RECORD_GUARD = 0xD8A1C0DE
MAGIC = b"OOD8IV1"
OP_FUNCTION = 54
OP_FUNCTION_END = 56
OP_ENTRY = 15
OP_DECORATE = 71
DECOR_NONWRITABLE = 24
DECOR_BINDING = 33
DECOR_SET = 34


def words_of(data):
    if len(data) < 20 or len(data) % 4 or data[:4] != b"\x03\x02\x23\x07":
        raise ValueError("Expected bounded SPIR-V")
    return struct.unpack(f"<{len(data) // 4}I", data)


def instructions(words):
    at = 5
    while at < len(words):
        count = words[at] >> 16
        if not count or at + count > len(words):
            raise ValueError("Broken SPIR-V instruction")
        yield words[at:at + count]
        at += count


def function_spans(words):
    spans, current, ident = {}, None, None
    for inst in instructions(words):
        opcode = inst[0] & 0xFFFF
        if opcode == OP_FUNCTION:
            if current is not None or len(inst) < 3:
                raise ValueError("Nested or truncated function")
            current, ident = [inst], inst[2]
        elif current is not None:
            current.append(inst)
            if opcode == OP_FUNCTION_END:
                spans[ident] = tuple(current)
                current = None
    if current is not None:
        raise ValueError("Truncated function")
    return spans


def prove_candidate(original, candidate):
    """Entry 851 and every function except 149 and 620 stay word-identical."""
    left, right = function_spans(words_of(original)), function_spans(words_of(candidate))
    if set(left) != set(right) or ENTRY not in left:
        raise ValueError("Candidate function set differs from the original mesh")
    changed = []
    for ident, body in left.items():
        if right[ident] != body:
            changed.append(ident)
    if sorted(changed) != sorted(HELPERS):
        raise ValueError("Interval candidate changed a function outside helpers 149 and 620")
    if left[ENTRY] != right[ENTRY]:
        raise ValueError("Mesh entry 851 changed")
    def flat(span):
        return tuple(word for inst in span for word in inst)

    for ident in HELPERS:
        if flat(left[ident])[-5:] != flat(right[ident])[-5:]:
            raise ValueError(f"Helper {ident} miss suffix changed")
    words = words_of(candidate)
    binding = None
    nonwritable = set()
    sets = {}
    for inst in instructions(words):
        opcode = inst[0] & 0xFFFF
        if opcode == OP_DECORATE and len(inst) >= 3:
            if inst[2] == DECOR_BINDING and len(inst) >= 4 and inst[3] == 30:
                binding = inst[1]
            if inst[2] == DECOR_NONWRITABLE:
                nonwritable.add(inst[1])
            if inst[2] == DECOR_SET and len(inst) >= 4:
                sets[inst[1]] = inst[3]
    if binding is None or binding not in nonwritable or sets.get(binding) != 0:
        raise ValueError("Binding 30 is not one set-0 NonWritable variable")
    entries = [inst for inst in instructions(words_of(original)) if (inst[0] & 0xFFFF) == OP_ENTRY]
    candidate_entries = [inst for inst in instructions(words) if (inst[0] & 0xFFFF) == OP_ENTRY]
    if len(entries) != 1 or len(candidate_entries) != 1:
        raise ValueError("Expected one mesh entry point")
    old, new = entries[0], candidate_entries[0]
    if new[-1] != binding or new[1:-1] != old[1:] or (new[0] >> 16) != (old[0] >> 16) + 1 or (new[0] & 0xFFFF) != OP_ENTRY:
        raise ValueError("Entry point interface changed beyond the binding-30 variable")
    return {"changed_functions": sorted(changed), "entry": ENTRY, "binding30_id": binding,
            "miss_suffix_words": 5, "entry_function_words_identical": True}


def _tool(name, arguments):
    executable = shutil.which(name)
    if not executable:
        raise ValueError(f"Missing required CPU tool: {name}")
    return subprocess.run([executable, *arguments], check=True, text=True, capture_output=True, timeout=60)


def _function_blocks(assembly):
    lines, blocks, index = assembly.splitlines(), {}, 0
    while index < len(lines):
        line = lines[index]
        matched = next((ident for ident in HELPERS if line.strip().startswith(f"%{ident} = OpFunction ")), None)
        if matched is None:
            index += 1
            continue
        block = [line]
        index += 1
        while index < len(lines) and lines[index].strip() != "OpFunctionEnd":
            block.append(lines[index])
            index += 1
        if index == len(lines):
            raise ValueError("Truncated helper in candidate assembly")
        block.append(lines[index])
        blocks[matched] = block
        index += 1
    return blocks


def _stub_helpers(assembly):
    """Hide helper loops from the output-instrumenter audit, then restore them."""
    saved = _function_blocks(assembly)
    if set(saved) != set(HELPERS):
        raise ValueError("Candidate assembly is missing helper 149 or 620")
    lines, output, index = assembly.splitlines(), [], 0
    while index < len(lines):
        line = lines[index]
        matched = next((ident for ident in HELPERS if line.strip().startswith(f"%{ident} = OpFunction ")), None)
        if matched is None:
            output.append(line)
            index += 1
            continue
        block = saved[matched]
        head = [block[0]]
        cursor = 1
        while "OpFunctionParameter" in block[cursor]:
            head.append(block[cursor])
            cursor += 1
        output.extend(head + block[-3:])
        index += len(block)
    return "\n".join(output) + "\n", saved


def _restore_helpers(transformed, saved):
    lines, output, index = transformed.splitlines(), [], 0
    seen = set()
    while index < len(lines):
        line = lines[index]
        matched = next((ident for ident in HELPERS if line.strip().startswith(f"%{ident} = OpFunction ")), None)
        if matched is None:
            output.append(line)
            index += 1
            continue
        end = index
        while lines[end].strip() != "OpFunctionEnd":
            end += 1
        output.extend(saved[matched])
        seen.add(matched)
        index = end + 1
    if seen != set(HELPERS):
        raise ValueError("Instrumented candidate lost a helper function")
    return "\n".join(output) + "\n"


def instrument_candidate(candidate, output):
    real = instrument.analyze_assembly

    def wrapped(assembly):
        summary = real(assembly)
        extra = [row for row in summary["descriptors"] if row["binding"] == 30]
        base = [row for row in summary["descriptors"] if row["binding"] != 30]
        if len(extra) != 1 or extra[0]["set"] != 0 or extra[0]["descriptor_count"] != 1:
            raise ValueError("Candidate descriptor contract is not base plus binding 30")
        if extra[0]["descriptor_type"] != "storage_buffer" or extra[0]["stores"] or extra[0]["image_writes"]:
            raise ValueError("Binding 30 is not a load-only storage buffer")
        return {**summary, "descriptors": base}

    assembly = _tool("spirv-dis", ["--raw-id", str(candidate)]).stdout
    wrapped(assembly)
    stubbed, saved = _stub_helpers(assembly)
    instrument.analyze_assembly = wrapped
    try:
        transformed, metadata = instrument.instrument_assembly(stubbed, "original")
    finally:
        instrument.analyze_assembly = real
    transformed = _restore_helpers(transformed, saved)
    if metadata["source_kind"] != "original" or metadata["original_barriers"] != 2:
        raise ValueError("Candidate instrumentation did not keep the original entry barrier inventory")
    output.mkdir(parents=True, exist_ok=True)
    text_path = output / "instrumented.spvasm"
    text_path.write_text(transformed)
    binary = output / "instrumented.spv"
    _tool("spirv-as", ["--target-env", "spv1.6", str(text_path), "-o", str(binary)])
    _tool("spirv-val", ["--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(binary)])
    metadata = {**metadata, "source_kind": "interval-candidate", "binding30_preserved": True,
                "runtime_execution_authorized": False, "equivalence_proven": False}
    return binary, metadata


def expected_bindings(include_record):
    rows = [{"set": 0, "binding": binding, "type": 6 if binding < 5 else 7, "count": 3 if binding == 8 else 1}
            for binding in range(9)]
    if include_record:
        rows.append({"set": 0, "binding": 30, "type": 7, "count": 1})
    rows += [{"set": 1, "binding": binding, "type": 7, "count": 1} for binding in (0, 1)]
    return rows


def element_count(rows):
    return sum(row["count"] for row in rows)


def guarded_record(payload):
    if len(payload) != RECORD_BYTES:
        raise ValueError("Interval record must be the pinned 400-byte table")
    guard = struct.pack("<16I", *([RECORD_GUARD] * 16))
    return guard + payload + guard


def write_plan(path, state, record_path):
    unique, indices, descriptors = [], {}, []
    for row in state["bindings"]:
        if row.get("null_descriptor") is True:
            descriptors.append([0, row["binding"], row["element"], row["type"], 0xFFFFFFFF, 0, 0])
            continue
        key = (row["snapshot"]["sha256"], row["range_bytes"])
        if key not in indices:
            indices[key] = len(unique)
            unique.append(row)
        descriptors.append([0, row["binding"], row["element"], row["type"], indices[key], row["range_bytes"], 0])
    record = guarded_record(record_path.read_bytes())
    record_index = len(unique)
    descriptors.append([0, 30, 0, 7, record_index, RECORD_BYTES, 64])
    groups, origin, grid = state["groups"], state["origin"], state["grid"]
    initial = mesh_replay.initialized_capture(groups)
    header = [1, len(unique) + 1, len(descriptors), 0, len(initial), 64, len(initial) - 128, *groups, *origin, *grid]
    with path.open("xb") as target:
        target.write(struct.pack("<8s16I", MAGIC, *header))
        for row in unique:
            target.write(struct.pack("<I", row["range_bytes"]))
            target.write(row["path"].read_bytes())
        target.write(struct.pack("<I", len(record)))
        target.write(record)
        for descriptor in descriptors:
            target.write(struct.pack("<7I", *descriptor))
        target.write(initial)
    identities = [{"binding": item[1], "element": item[2], "input": None if item[4] == 0xFFFFFFFF else item[4],
                   "offset": item[6], "range": item[5], "null": item[4] == 0xFFFFFFFF} for item in descriptors]
    return {"descriptor_elements": len(descriptors), "set0_bindings": 10, "owned_elements": len(descriptors) + 2,
            "record_input": record_index, "record_offset": 64, "record_range": RECORD_BYTES,
            "identities": identities, "same_buffer_identity_for_both_modules": True}


def build_helper(output):
    flags = run_process(["pkg-config", "--cflags", "--libs", "vulkan"], environment(), output, "pkg-config", 15)
    import shlex
    helper = output / "d8-interval-replay-helper"
    source = ROOT / "tools" / "vulkan_d8_interval_replay.c"
    run_process(["cc", *BUILD_FLAGS, str(source), *shlex.split(flags), "-o", str(helper)],
                environment(), output, "build-helper", 60)
    return helper


def prepare(output_path, capture, original, candidate, records, origin, groups):
    if digest(original) != ORIGINAL or digest(candidate) != CANDIDATE or digest(records) != RECORDS:
        raise ValueError("Original, candidate, or record digest differs from the reviewed pins")
    proof = prove_candidate(original.read_bytes(), candidate.read_bytes())
    state = mesh_replay.capture_inputs(capture, "native-safe", "frozen-logical-readonly")
    grid, origin, groups = mesh_replay.window(state["grid"], origin, groups)
    state["grid"], state["origin"], state["groups"] = grid, origin, groups
    output = private_output(output_path)
    output.mkdir(parents=True)
    original_report = instrument.prepare(original, output / "original")
    if original_report["instrumented_sha256"] != INSTRUMENTED_ORIGINAL:
        raise ValueError("Original instrumentation differs from the reviewed derivative")
    _tool("spirv-val", ["--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(candidate)])
    (output / "candidate").mkdir()
    (output / "candidate" / "source.spv").write_bytes(candidate.read_bytes())
    instrumented, candidate_meta = instrument_candidate(candidate, output / "candidate")
    reflected = reflect_stage(instrumented.read_bytes(), "mesh")
    if reflected["bindings"] != expected_bindings(True) or reflected["local_size"] != [32, 1, 1]:
        raise ValueError("Candidate instrumented reflection differs from the mesh-only owned layout")
    original_reflected = reflect_stage((output / "original" / "instrumented.spv").read_bytes(), "mesh")
    if original_reflected["bindings"] != expected_bindings(False):
        raise ValueError("Original instrumented reflection gained or lost a descriptor")
    if element_count(expected_bindings(True)) != 14 or element_count(expected_bindings(False)) != 13:
        raise ValueError("Element count is not the mesh-only owned union")
    # The original instrumented module has no binding 30. Both replay cases still bind it.
    copied = {}
    for row in state["bindings"]:
        if row.get("null_descriptor"):
            continue
        key = (row["snapshot"]["sha256"], row["range_bytes"])
        if key not in copied:
            destination = output / "inputs" / f"{row['binding']}-{row['element']}.bin"
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(row["path"].read_bytes())
            copied[key] = destination
        row["path"] = copied[key]
    record_copy = output / "records.bin"
    record_copy.write_bytes(records.read_bytes())
    helper = build_helper(output)
    info = write_plan(output / "inputs.bin", state, record_copy)
    native = json.loads(run_process([str(helper), str(output / "original" / "instrumented.spv"),
                                     str(output / "inputs.bin"), str(output / "unused-output.bin"),
                                     "32", "0", "--validate"], environment(), output, "validate-original", 20))
    native_candidate = json.loads(run_process([str(helper), str(instrumented), str(output / "inputs.bin"),
                                               str(output / "unused-output.bin"), "32", "0", "--validate"],
                                              environment(), output, "validate-candidate", 20))
    for result in (native, native_candidate):
        if result.get("gpu_executed") is not False or result.get("queue_retrieved") is not False or result.get("owned_elements") != 14:
            raise ValueError("CPU validation touched a device or changed the element count")
    refused = subprocess.run([str(helper), str(output / "original" / "instrumented.spv"), str(output / "inputs.bin"),
                              str(output / "unused-output.bin"), "32", "0"], capture_output=True, text=True, timeout=20)
    if refused.returncode != 4:
        raise ValueError("Unauthenticated helper did not refuse before device creation")
    _, policy = controlled_driver_environment("native-safe", {})
    plan = {"schema": SCHEMA, "prepare_only": True, "gpu_executed": False, "queue_retrieved": False,
            "timing_admitted": False, "execution_admitted": False, "mode": "frozen-logical-readonly",
            "historical_atomicity_certified": False, "owned_replay_writers": 0,
            "origin": origin, "groups": groups, "native_grid": grid,
            "cases": [{"name": "original32-a", "module": "original/instrumented.spv", "width": 32},
                      {"name": "original32-b", "module": "original/instrumented.spv", "width": 32},
                      {"name": "candidate32", "module": "candidate/instrumented.spv", "width": 32}],
            "comparison": {"baselines": ["original32-a", "original32-b"], "candidate": "candidate32",
                           "scope": "every capture byte, count row, topology index, attribute word, writtenness word, and guard",
                           "partial_samples": False, "float_tolerance": None},
            "descriptor_identity": info, "candidate_proof": proof, "candidate_instrumentation": candidate_meta,
            "original_instrumented_sha256": INSTRUMENTED_ORIGINAL,
            "candidate_instrumented_sha256": digest(instrumented),
            "driver_policy": policy, "driver_profile": "cache-safe-e5",
            "layout": {"profile": "mesh-only-owned-sets", "set0_bindings": 10, "set0_elements": 12,
                       "owned_elements": 14, "common33_elements": 33, "known_a770_max_push_descriptors": 32,
                       "push_constants": 0, "fits_push_budget_by_element_count": True,
                       "uses_push_descriptors": False},
            "composition": {"admitted": False, "hash_guard_relaxed": False,
                            "reason": "warp32 remains pinned to the original module; entry 851 identity is not a composition proof"},
            "native_validation": {"original": native, "candidate": native_candidate},
            "pins": {"original": ORIGINAL, "candidate": CANDIDATE, "records": RECORDS}}
    destination = output / "prepared-plan.json"
    destination.write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare",))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--origin", default="128,0,0")
    parser.add_argument("--groups", default="2,1,1")
    args = parser.parse_args()
    origin = [int(part) for part in args.origin.split(",")]
    groups = [int(part) for part in args.groups.split(",")]
    plan = prepare(args.output, args.capture, args.original, args.candidate, args.records, origin, groups)
    print(json.dumps({"schema": SCHEMA, "prepare_only": True, "gpu_executed": plan["gpu_executed"],
                      "owned_elements": plan["layout"]["owned_elements"],
                      "candidate_instrumented_sha256": plan["candidate_instrumented_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
