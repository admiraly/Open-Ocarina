#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the page-table patch chain and its game-free completion/write helper."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
PATCH = Path(__file__).with_name("page-table-snapshot.patch")
IDENTITY = Path(__file__).with_name("compute-identity.patch")
TABLE_BYTES = 3 * 1024 * 1024 * 8


def header_from_patch(path, name):
    collecting = False
    lines = []
    for line in path.read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            collecting = line.rstrip().endswith(" b/gpu/vk/" + name)
        elif collecting and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    if not lines:
        raise ValueError("New standalone header missing from patch")
    return "".join(lines)


def validate_snapshot(directory):
    """Check artifact integrity and declared completion, not independent GPU attestation."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Snapshot directory must not be a symlink")
    manifest = directory / "manifest.json"
    binary = directory / "page-table.bin"
    if manifest.is_symlink() or binary.is_symlink() or not manifest.is_file() or not binary.is_file():
        raise ValueError("Committed manifest and regular binary artifact are required")
    if manifest.stat().st_size > 65536:
        raise ValueError("Manifest exceeds bounded schema size")
    document = json.loads(manifest.read_text())
    if document.get("schema") != "open-ocarina.gpu-page-table/v1":
        raise ValueError("Unsupported page-table snapshot schema")
    if document.get("producer") != {"stage": "page-table-only", "replay_complete": False}:
        raise ValueError("A page-table snapshot must not claim complete replay")
    coverage = {"gpu_page_table": True, "referenced_allocation_contents": False,
                "descriptor_contents": False, "gpu_outputs": False, "temporal_state": False}
    if document.get("coverage") != coverage:
        raise ValueError("Capture coverage was altered or overstated")
    pipeline = document.get("pipeline", {})
    for field, length in (("program", 16), ("variant", 16), ("spirv_sha256", 64)):
        if not re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", pipeline.get(field, "")):
            raise ValueError("Missing or malformed pipeline identity")
    submission = document.get("submission", {})
    for name in ("recording_part", "logical_value", "recording_sequence", "completed_recording_sequence"):
        if type(submission.get(name)) is not int or not 0 < submission[name] < (1 << 64):
            raise ValueError("Exact recording completion evidence is missing")
    if submission["completed_recording_sequence"] < submission["recording_sequence"]:
        raise ValueError("Snapshot was published before its recording completed")
    if document.get("command_position") != "immediately before selected compute dispatch, after descriptor and push binding":
        raise ValueError("Selected command position was not declared")
    dispatch = document.get("dispatch", {})
    if type(dispatch.get("present_observed")) is not int or dispatch["present_observed"] < 0 or "identity_event_id" not in dispatch or dispatch["identity_event_id"] is not None:
        raise ValueError("Dispatch correlation metadata is missing or overstated")
    if dispatch.get("kind") == "direct":
        grid = dispatch.get("group_count")
        if not isinstance(grid, list) or len(grid) != 3 or any(type(value) is not int or not 0 < value < (1 << 32) for value in grid):
            raise ValueError("Direct grid is malformed")
    elif dispatch.get("kind") != "indirect" or dispatch.get("group_count") is not None:
        raise ValueError("Indirect group counts must remain explicitly unknown")
    sync = document.get("synchronization", {})
    if sync.get("source") != "actual GPU guest_pages_buffer" or any(sync.get(field) is not True for field in (
            "prior_writes_to_copy_barrier", "copy_to_host_barrier", "mapped_memory_invalidated",
            "successful_exact_recording_submission", "recording_timeline_completion_checked")):
        raise ValueError("GPU copy/completion declarations are incomplete")
    table = document.get("table", {})
    if (table.get("artifact") != "page-table.bin" or type(table.get("size")) is not int
            or table["size"] != TABLE_BYTES or binary.stat().st_size != TABLE_BYTES
            or type(table.get("entry_count")) is not int or table["entry_count"] != TABLE_BYTES // 8
            or table.get("entry_bytes") != 8 or table.get("page_size") != 4096
            or table.get("byte_order") != "little"):
        raise ValueError("Unexpected table framing or truncated snapshot")
    if not re.fullmatch(r"[0-9a-f]{16}", table.get("root_device_address", "")) or int(table["root_device_address"], 16) == 0 or int(table["root_device_address"], 16) % 8:
        raise ValueError("Root device address is missing")
    if type(table.get("root_push_offset")) is not int or not 0 <= table["root_push_offset"] <= 248 or table["root_push_offset"] % 8:
        raise ValueError("Root push offset is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", table.get("sha256", "")):
        raise ValueError("Snapshot digest is missing")
    if table.get("windows") != [{"guest_base": "1000000000", "size": 8589934592, "first_entry": 0},
                                {"guest_base": "7f00000000", "size": 4294967296, "first_entry": 2097152}]:
        raise ValueError("Guest window framing differs from the pinned table layout")
    if any(type(row[name]) is not int for row in table["windows"] for name in ("size", "first_entry")):
        raise ValueError("Guest window bounds must be integers")
    with binary.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != table["sha256"]:
        raise ValueError("Page-table artifact digest mismatch")
    return {"artifact_integrity_verified": True, "declared_recording_completed": True,
            "independent_gpu_attestation": False, "replay_complete": False,
            "table_sha256": actual, "table_bytes": TABLE_BYTES,
            "pipeline": pipeline, "recording_sequence": submission["recording_sequence"],
            "scope": "GPU table artifact framing and producer declarations only; reachable payloads absent"}


def check(checkout=None, compiler=None):
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        raise ValueError("A C++20 compiler is required")
    with tempfile.TemporaryDirectory(prefix="oo-pt-check-") as temporary:
        work = Path(temporary)
        if checkout is not None:
            checkout = Path(checkout)
            if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() != PIN:
                raise ValueError("Checkout must remain at the pinned upstream revision")
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(work / "patch-index")
            subprocess.run(["git", "read-tree", "HEAD"], cwd=checkout, env=env, check=True)
            subprocess.run(["git", "apply", "--cached", str(IDENTITY.resolve())], cwd=checkout, env=env, check=True)
            subprocess.run(["git", "apply", "--cached", "--check", str(PATCH.resolve())], cwd=checkout, env=env, check=True)
        (work / "compute_identity.h").write_text(header_from_patch(IDENTITY, "compute_identity.h"))
        (work / "page_table_capture.h").write_text(header_from_patch(PATCH, "page_table_capture.h"))
        source = work / "test.cpp"
        source.write_text(r'''#include "page_table_capture.h"
#include <iostream>
#include <csignal>
int main(int argc,char**argv) {
 if(argc!=2)return 2;
 using namespace mkwvk;
 const std::string mode=argv[1];
 if(mode=="disabled")return oo_pt::config().enabled?8:0;
 oo_identity::Identity identity;identity.valid=true;identity.digest=oo_identity::sha256(reinterpret_cast<const uint8_t*>("shader"),6);
 if(!oo_pt::selected(0xcc62764061c44f6aull,0x095931037888515aull,identity))return 3;
 if(oo_pt::selected(1,0x095931037888515aull,identity))return 4;
 oo_pt::Request request;request.key=0xcc62764061c44f6aull;request.variant=0x095931037888515aull;
 request.shader_digest=oo_identity::hex(identity.digest);request.root_address=0x1000;request.root_push_offset=48;
 request.recording_part=3;request.logical_value=4;request.submitted_sequence=7;request.x=2;request.y=request.z=1;
 std::vector<uint8_t> bytes(oo_pt::kTableBytes);bytes[0]=17;bytes[4096]=23;
 uint64_t completed=7;size_t size=bytes.size();
 if(mode=="pending")completed=6;
 else if(mode=="unsubmitted")request.submitted_sequence=0;
 else if(mode=="short")size=8;
 else if(mode=="nullroot")request.root_address=0;
#ifdef SIGXFSZ
 if(mode=="shortwrite")std::signal(SIGXFSZ,SIG_IGN);
#endif
 const bool result=oo_pt::write_completed(request,bytes.data(),size,completed);
 if(mode!="valid")return result?5:0;
 if(!result||oo_pt::write_completed(request,bytes.data(),size,completed))return 6;
 return 0;
}
''')
        executable = work / "test"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                        "-pthread", str(source), "-o", str(executable)], check=True, timeout=60)
        modes = ["pending", "unsubmitted", "short", "nullroot", "disabled", "valid"]
        if os.name == "posix":
            modes.append("shortwrite")
        for mode in modes:
            output = work / mode
            output.mkdir()
            env = os.environ.copy()
            env["RX_VK_OO_IDENTITY_FILE"] = str(work / "unused-identity.jsonl")
            env.pop("RX_VK_OO_IDENTITY_PROGRAM", None)
            env.pop("RX_VK_OO_IDENTITY_VARIANT", None)
            env["RX_VK_OO_PT_SNAPSHOT_DIR"] = str(output)
            env["RX_VK_OO_PT_SNAPSHOT_SHA256"] = hashlib.sha256(b"shader").hexdigest()
            if mode == "disabled":
                env.pop("RX_VK_OO_PT_SNAPSHOT_DIR")
            options = {}
            if mode == "shortwrite":
                import resource

                def short_file_limit():
                    resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))

                options["preexec_fn"] = short_file_limit
            subprocess.run([str(executable), mode], env=env, check=True, timeout=30, **options)
            if mode == "valid":
                validate_snapshot(output)
            elif list(output.iterdir()):
                raise ValueError("An incomplete/unsubmitted snapshot produced an artifact")
    return {"patch_chain_verified": checkout is not None, "optimized_helper_compiled": True,
            "premature_unsubmitted_short_and_nullroot_refused": True,
            "complete_synthetic_snapshot_hash_verified": True, "existing_capture_not_overwritten": True,
            "disabled_capture_no_output": True, "short_write_no_committed_manifest": os.name == "posix",
            "scope": "synthetic helper and patch chain; no GPU execution or independent GPU attestation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--compiler")
    parser.add_argument("--validate-snapshot", type=Path)
    args = parser.parse_args()
    report = validate_snapshot(args.validate_snapshot) if args.validate_snapshot else check(args.checkout, args.compiler)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
