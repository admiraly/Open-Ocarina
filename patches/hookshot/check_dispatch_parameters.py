#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify exact pushed-byte capture with synthetic inputs and a pinned isolated source index."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / "dispatch-parameters.patch"
PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
BASE = ("compute-identity.patch", "page-table-snapshot.patch", "allocation-capture.patch", "occurrence-capture.patch",
        "graphics-identity.patch", "sampled-image-capture.patch")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def header_from_patch(path=PATCH, name="dispatch_parameters.h"):
    active = False
    lines = []
    for line in Path(path).read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            active = line.rstrip().endswith(" b/gpu/vk/" + name)
        elif active and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    require(bool(lines), "New standalone header missing from patch")
    return "".join(lines)


def body(source, marker):
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def source_audit(before, after, memory):
    require(re.findall(r"\bvkCmd[A-Za-z0-9_]+\([^;]+;", before) == re.findall(r"\bvkCmd[A-Za-z0-9_]+\([^;]+;", after),
            "Original Vulkan recording commands or arguments changed")
    original_push = body(before, "void push_constants(")
    patched_push = body(after, "void push_constants(")
    call = "vkCmdPushConstants(cmd, p.layout, p.push_stages, 0, kPushSize, data);"
    require(call in original_push and call in patched_push, "Exact original Vulkan push call changed")
    require(patched_push.index("oo_dp::invalidate_push()") < patched_push.index("if (!p.uses_push)"), "Push entry must invalidate stale shadow even on no-push return")
    tail = patched_push[patched_push.index(call) + len(call):]
    require("oo_dp::note_push(" in tail and "data, Shader::Backend::SPIRV::GUEST_PAGES_PUSH_OFFSET" in tail,
            "Exact just-pushed bytes and source-defined page-root offset must be shadowed after API call")
    require("memory_guest_pages()" not in tail and "memcpy" not in tail, "Push shadow must not reconstruct bytes")
    layout = body(after, "void ensure_layout(")
    require("oo_dp::note_layout(" in layout and "for (const auto& binding : bindings)" in layout,
            "Actual ensure_layout binding inputs are not retained")
    dispatch = body(after, "void dispatch(")
    start = dispatch.index("push_constants(cmd, p, nullptr);")
    finish = dispatch.index("vkCmdDispatch(cmd, x, y, z);", start)
    middle = dispatch[start + len("push_constants(cmd, p, nullptr);"):finish]
    require("push_constants(" not in middle and "vkCmdPushConstants(" not in middle,
            "An intervening push invalidates the selected direct-dispatch association")
    require("oo_compute_identity(cmd, p, s, x, y, z, 0);" in middle, "Selected direct observer missing")
    identity = body(after, "void oo_compute_identity(")
    require(identity.index("oo_dp::note_policy(") < identity.index("memory_pt_snapshot_record("), "Subgroup policy must precede selected occurrence consumption")
    record = body(memory, "void memory_pt_snapshot_record(")
    require(record.index("if(!selected)return;") < record.index("oo_dp::select("), "Push shadow consumed before selected occurrence")
    collect = body(memory, "void memory_pt_snapshot_collect(")
    require(collect.index("completed_sequence<pending.request.submitted_sequence") < collect.index("oo_dp::write_completed("), "Export precedes exact recording completion")
    require(collect.index("oo_allocation_collect(") < collect.index("oo_dp::write_completed("), "Associated manifests must be committed before hashing")
    require(body(before, "void pin_subgroup(") == body(after, "void pin_subgroup("), "Subgroup policy changed")
    return {"exact_push_call_preserved": True, "no_intervening_direct_push": True,
            "actual_layout_inputs_retained": True, "selected_occurrence_consumption": True, "completion_gated_export": True}


def check_source(checkout):
    checkout = Path(checkout).resolve()
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() == PIN,
            "Source checkout is not the exact pinned revision")
    with tempfile.TemporaryDirectory(prefix="oo-dispatch-index-") as td:
        env = os.environ.copy();env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        subprocess.run(["git", "read-tree", PIN], cwd=checkout, env=env, check=True)
        hashes = []
        for name in BASE:
            patch = HERE / name
            hashes.append({"patch": name, "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()})
            subprocess.run(["git", "apply", "--cached", str(patch)], cwd=checkout, env=env, check=True)
        before = subprocess.check_output(["git", "show", ":gpu/vk/vk_draw.cpp"], cwd=checkout, env=env, text=True)
        subprocess.run(["git", "apply", "--cached", "--check", str(PATCH)], cwd=checkout, env=env, check=True)
        subprocess.run(["git", "apply", "--cached", str(PATCH)], cwd=checkout, env=env, check=True)
        after = subprocess.check_output(["git", "show", ":gpu/vk/vk_draw.cpp"], cwd=checkout, env=env, text=True)
        memory = subprocess.check_output(["git", "show", ":gpu/vk/vk_memory.cpp"], cwd=checkout, env=env, text=True)
    return {"pinned_chain_apply_check": True, "base_patches": hashes, **source_audit(before, after, memory)}


def integer(value, label, minimum=0, maximum=(1 << 64) - 1):
    require(type(value) is int and minimum <= value <= maximum, "Invalid integer: " + label)
    return value


def regular(path, maximum):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= maximum,
            "Missing, nonregular or oversized artifact: " + path.name)
    return path.read_bytes()


def validate_capture(directory):
    """Check committed parameter evidence and associations, never declare full replay."""
    directory = Path(directory)
    require(not directory.is_symlink() and directory.is_dir(), "Regular capture directory required")
    document = json.loads(regular(directory / "dispatch-parameters.json", 131072))
    require(document.get("schema") == "open-ocarina.dispatch-parameters/v1" and document.get("replay_complete") is False,
            "Unsupported or overstated dispatch schema")
    require(document.get("parameters_complete") is True, "Exact direct dispatch parameters are incomplete")
    pipeline = document["pipeline"]
    require(pipeline.get("policy_complete") is True and pipeline.get("layout_complete") is True, "Actual layout/policy missing")
    for name, width in (("program", 16), ("variant", 16), ("spirv_sha256", 64), ("cache_stamp", 16), ("layout_handle", 16)):
        require(isinstance(pipeline.get(name), str) and re.fullmatch("[0-9a-f]{%d}" % width, pipeline[name]), "Malformed pipeline identity: " + name)
    require(pipeline.get("entry_point") == "main" and pipeline.get("specialization") == [], "Unsupported entry point or specialization")
    require(len(pipeline["local_size"]) == 3, "Local size must have three axes")
    for axis in pipeline["local_size"]: integer(axis, "local size", 1, 1024)
    subgroup = pipeline["subgroup"]
    require(type(subgroup.get("require_full_subgroups")) is bool, "Full-subgroup policy missing")
    if subgroup.get("required_size") is not None:
        size = integer(subgroup["required_size"], "subgroup width", 1, 128)
        require(size & (size - 1) == 0, "Subgroup width is not a power of two")
    bindings = pipeline["descriptor_layout"]
    require(isinstance(bindings, list) and len(bindings) <= 256, "Descriptor layout exceeds bound")
    for index, binding in enumerate(bindings):
        require(binding.get("set") == 0 and type(binding.get("set")) is int and binding.get("binding") == index and type(binding.get("binding")) is int,
                "Descriptor binding order differs from actual ensure_layout")
        integer(binding.get("type"), "descriptor type", 0, (1 << 32) - 1)
        integer(binding.get("count"), "descriptor count", 1, (1 << 32) - 1)
        require(binding.get("stage_flags") == 32, "Selected compute layout has unsupported stage flags")
    push = document["push_constants"];span = pipeline["push_range"]
    require(isinstance(push, dict) and push.get("artifact") == "push-constants.bin", "Exact push artifact missing")
    size = integer(push.get("size"), "push size", 1, 256)
    offset = integer(push.get("offset"), "push offset", 0, 256 - size)
    require(size % 4 == 0 and offset % 4 == 0 and span == {"offset": offset, "size": size, "stage_flags": 32} and push.get("stage_flags") == 32,
            "Push bytes do not cover the actual complete compute range")
    payload = regular(directory / "push-constants.bin", 256)
    require(len(payload) == size and hashlib.sha256(payload).hexdigest() == push.get("sha256"), "Push payload integrity failed")
    root_offset = integer(push.get("root_push_offset"), "page-root push offset", offset, offset + size - 8)
    part = integer(document.get("recording_part"), "recording part", 1)
    logical = integer(document.get("logical_value"), "logical value", 1)
    sequence = integer(document.get("recording_sequence"), "recording sequence", 1)
    completed = integer(document.get("completed_recording_sequence"), "completion", sequence)
    occurrence = integer(document.get("selected_occurrence"), "occurrence", 1, 65536)
    dispatch = document["dispatch"]
    require(dispatch.get("kind") == "direct" and isinstance(dispatch.get("group_count"), list) and len(dispatch["group_count"]) == 3,
            "Only captured direct grids are supported")
    for axis in dispatch["group_count"]: integer(axis, "group count", 0, (1 << 32) - 1)
    associations = document.get("associations")
    require(isinstance(associations, list) and [row.get("artifact") for row in associations] == ["manifest.json", "allocations.json", "images.json"],
            "Required capture manifest associations missing")
    associated = {}
    for row in associations:
        raw = regular(directory / row["artifact"], 16 << 20)
        require(hashlib.sha256(raw).hexdigest() == row.get("sha256"), "Capture manifest association hash differs")
        associated[row["artifact"]] = json.loads(raw)
    table = associated["manifest.json"]
    require(table.get("schema") == "open-ocarina.gpu-page-table/v1", "Associated table schema differs")
    require(table["pipeline"] == {name: pipeline[name] for name in ("program", "variant", "spirv_sha256")}, "Table pipeline identity differs")
    require(table["submission"] == {"recording_part": part, "logical_value": logical, "recording_sequence": sequence, "completed_recording_sequence": completed},
            "Table recording/completion association differs")
    require(table["dispatch"]["kind"] == "direct" and table["dispatch"]["group_count"] == dispatch["group_count"], "Table direct grid differs")
    require(table["table"]["root_push_offset"] == root_offset and int(table["table"]["root_device_address"], 16) == struct.unpack_from("<Q", payload, root_offset - offset)[0],
            "Exact pushed page root differs from actual table capture")
    for name, schema in (("allocations.json", "open-ocarina.gpu-allocations/v2"), ("images.json", "open-ocarina.sampled-images/v1")):
        other = associated[name]
        require(other.get("schema") == schema and other.get("replay_complete") is False, "Associated schema/replay claim differs")
        for field in ("program", "variant", "spirv_sha256"):
            require(other.get(field) == pipeline[field], "Associated pipeline identity differs")
        for field, value in (("recording_part", part), ("recording_sequence", sequence), ("completed_recording_sequence", completed), ("selected_occurrence", occurrence)):
            require(type(other.get(field)) is int and other[field] == value, "Associated recording/occurrence differs")
    require(document.get("features", {}).get("enabled_feature_chain_complete") is False and document["features"].get("float_controls_captured") is False,
            "Uncaptured device feature/float-control gates concealed")
    gates = document.get("remaining_gates")
    require(isinstance(gates, list) and {"full_enabled_device_features", "float_controls", "underlying_import_aliases", "cpu_writers_quiesced", "temporal_state", "independent_output_comparison"} <= set(gates),
            "Known replay gates omitted")
    return {"parameters_complete": True, "same_capture_associations_checked": True, "replay_complete": False,
            "push_bytes": size, "selected_occurrence": occurrence, "remaining_gates": gates}


def check_helper(compiler=None, header=None):
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    require(bool(compiler), "C++20 compiler required")
    with tempfile.TemporaryDirectory(prefix="oo-dispatch-helper-") as td:
        work = Path(td)
        for patch, name in (("compute-identity.patch", "compute_identity.h"), ("page-table-snapshot.patch", "page_table_capture.h"),
                            ("allocation-capture.patch", "allocation_capture.h")):
            (work / name).write_text(header_from_patch(HERE / patch, name))
        (work / "dispatch_parameters.h").write_text(header if header is not None else header_from_patch())
        source = work / "test.cpp"
        source.write_text(r'''#include "dispatch_parameters.h"
int main(int argc,char**argv) {
    using namespace mkwvk::oo_dp;
    if(argc>1&&std::string(argv[1])=="disabled")return enabled()?2:0;
    auto demand=[](bool value){if(!value)std::exit(2);};
    mkwvk::oo_pt::Request request;request.key=0xcc627f2664f47f25ull;request.variant=0x6a6f3bb552de83daull;
    request.shader_digest=std::string(64,'b');request.recording_part=7;request.logical_value=9;
    request.root_address=0x1122334455667788ull;request.root_push_offset=40;request.x=3;request.y=4;request.z=5;
    Layout layout;layout.key=request.key;layout.variant=request.variant;layout.handle=10;layout.push_size=88;layout.push_stages=32;
    layout.bindings={{0,0,6,1,32},{0,7,7,3,32},{0,10,1,1,32}};note_layout(layout);
    Policy pipeline;pipeline.key=request.key;pipeline.variant=request.variant;pipeline.command=1;pipeline.layout=10;
    pipeline.cache_stamp=11;pipeline.required_size=32;pipeline.require_full=true;pipeline.null_descriptor=true;
    pipeline.local_size[0]=64;pipeline.local_size[1]=1;pipeline.local_size[2]=1;
    std::array<uint8_t,88> bytes{};for(unsigned i=0;i<88;++i)bytes[i]=uint8_t(i*7);
    for(unsigned i=0;i<8;++i)bytes[40+i]=uint8_t(request.root_address>>(8*i));
    auto push=[&]{note_push(1,request.key,request.variant,10,7,9,32,0,bytes.size(),bytes.data(),40);note_policy(pipeline);};
    push();const auto saved=bytes;auto capture=select(1,request,64);demand(capture.push_complete);bytes.fill(255);
    demand(capture.pushed.bytes==std::vector<uint8_t>(saved.begin(),saved.end()));
    demand(!select(1,request,64).push_complete); // shadow consumed exactly once
    push();invalidate_push();demand(!select(1,request,64).push_complete);
    push();demand(!select(2,request,64).push_complete);
    push();auto wrong=request;wrong.recording_part++;demand(!select(1,wrong,64).push_complete);
    push();wrong=request;wrong.logical_value++;demand(!select(1,wrong,64).push_complete);
    push();wrong=request;wrong.root_address++;demand(!select(1,wrong,64).push_complete);
    push();wrong=request;wrong.root_push_offset=48;demand(!select(1,wrong,64).push_complete);
    push();auto wrong_policy=pipeline;wrong_policy.layout++;note_policy(wrong_policy);demand(!select(1,request,64).push_complete);
    demand(!write_completed(capture,request,999));request.submitted_sequence=15;
    demand(!write_completed(capture,request,14));wrong=request;wrong.root_address++;demand(!write_completed(capture,wrong,15));
    if(argc>1&&std::string(argv[1])=="indirect") {capture.request.indirect=true;request.indirect=true;}
    if(argc>1&&std::string(argv[1])=="missing") {capture.push_complete=false;capture.layout_complete=false;}
    demand(write_completed(capture,request,15));demand(!write_completed(capture,request,15));return 0;
}
''')
        executable = work / "check"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread", str(source), "-o", str(executable)], check=True, timeout=60)
        base = {key: value for key, value in os.environ.items() if not key.startswith("RX_VK_OO_")}
        reports = []
        for mode in ("direct", "indirect", "missing"):
            output = work / mode;output.mkdir()
            env = dict(base, RX_VK_OO_IDENTITY_FILE=str(work / "unused.jsonl"), RX_VK_OO_IDENTITY_PROGRAM="cc627f2664f47f25",
                       RX_VK_OO_IDENTITY_VARIANT="6a6f3bb552de83da", RX_VK_OO_PT_SNAPSHOT_DIR=str(output),
                       RX_VK_OO_ALLOC_CAPTURE="1", RX_VK_OO_DISPATCH_PARAMETERS="1", RX_VK_OO_PT_SNAPSHOT_SHA256="b"*64)
            subprocess.run([str(executable), mode], env=env, check=True, timeout=10)
            document = json.loads((output / "dispatch-parameters.json").read_text())
            require(document["replay_complete"] is False, "Replay completeness overstated")
            require(document["recording_sequence"] == 15 and document["logical_value"] == 9, "Logical submission confused with recording completion")
            require(document["selected_occurrence"] == 64, "Selected occurrence lost")
            require(document["pipeline"]["descriptor_layout"][1] == {"set":0,"binding":7,"type":7,"count":3,"stage_flags":32}, "Actual layout metadata lost")
            require(document["pipeline"]["subgroup"] == {"required_size":32,"require_full_subgroups":True}, "Subgroup request lost")
            if mode == "missing":
                require(document["push_constants"] is None and not document["parameters_complete"], "Missing push fabricated")
                require(not (output / "push-constants.bin").exists(), "Missing push bytes exported")
            else:
                expected = bytearray(i*7 % 256 for i in range(88));expected[40:48] = struct.pack("<Q", 0x1122334455667788)
                blob = (output / "push-constants.bin").read_bytes()
                require(blob == expected, "Host push bytes reconstructed or stale")
                require(document["push_constants"]["sha256"] == hashlib.sha256(blob).hexdigest(), "Push digest differs")
                require(document["push_constants"]["root_push_offset"] == 40, "Page root offset was hardcoded")
                require(document["parameters_complete"] == (mode == "direct"), "Indirect grid treated as known")
            require(document["dispatch"]["group_count"] == ([3,4,5] if mode != "indirect" else None), "Dispatch grid declaration differs")
            require(all(row["sha256"] is None for row in document["associations"]), "Missing associations fabricated")
            reports.append(mode)
        subprocess.run([str(executable), "disabled"], env=base, check=True, timeout=10)
    return {"optimized_helper_compile": True, "exact_bytes_and_digest": True, "fresh_single_use_shadow": True,
            "command_layout_recording_and_root_mismatch_refused": True, "same_recording_completion_checked": True,
            "unknown_indirect_and_missing_metadata": True, "synthetic_modes": reports, "gpu_execution": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--compiler")
    parser.add_argument("--capture-dir", type=Path)
    args = parser.parse_args()
    try:
        report = validate_capture(args.capture_dir) if args.capture_dir else check_helper(args.compiler)
        if args.checkout:
            report.update(check_source(args.checkout))
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
