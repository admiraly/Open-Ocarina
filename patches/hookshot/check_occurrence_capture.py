#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify selected-occurrence allocation evidence and resolve every entry of the copied GPU table."""
import argparse
import array
import bisect
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / "occurrence-capture.patch"
SPEC = importlib.util.spec_from_file_location("oo_page_checker", HERE / "check_page_table_patch.py")
PAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAGE)
U64 = 1 << 64
COVERAGE = {"images": False, "temporal_state": False, "underlying_import_aliases_resolved": False,
            "cpu_writers_quiesced": False, "complete_physical_replay": False}


def integer(value, label, minimum=0, maximum=U64 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid integer: {label}")
    return value


def hex64(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{16}", value):
        raise ValueError(f"Invalid device identity: {label}")
    return int(value, 16)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular artifacts are required; symlinks are refused")


def resolve_table(entries, rows):
    """Match real BDA intervals plus guest offset; never round bases or extend tails."""
    indexed = sorted((hex64(row["address"], "address"), row["id"], row) for row in rows)
    starts = [item[0] for item in indexed]
    prefix = []
    for address, _, row in indexed:
        prefix.append(max(prefix[-1] if prefix else 0, address + row["size"]))
    result = dict.fromkeys(("mapped", "unresolved", "ambiguous", "partial", "missing_payload"), 0)
    samples = []
    for index, address in enumerate(entries):
        if address == 0:
            continue
        result["mapped"] += 1
        guest = 0x1000000000 + index * 4096 if index < 2097152 else 0x7F00000000 + (index - 2097152) * 4096
        hits = []
        position = bisect.bisect_right(starts, address) - 1
        while position >= 0 and prefix[position] > address:
            base, _, row = indexed[position]
            lo, hi = hex64(row["guest_lo"], "guest_lo"), hex64(row["guest_hi"], "guest_hi")
            if base and 0 <= address - base < row["size"] and lo <= guest < hi and guest - lo == address - base:
                hits.append(row)
            position -= 1
        if not hits:
            result["unresolved"] += 1
            if len(samples) < 32:
                samples.append({"table_index": index, "device_address": f"{address:016x}"})
        else:
            result["ambiguous"] += len(hits) > 1
            result["partial"] += max(row["size"] - (address - hex64(row["address"], "address")) for row in hits) < 4096
            result["missing_payload"] += not any(row["before"] and row["after"] for row in hits)
    return result, samples


def validate_snapshot(directory):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Capture directory cannot be a symlink")
    manifest = directory / "allocations.json"
    regular(manifest)
    if manifest.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Allocation manifest exceeds schema bound")
    document = json.loads(manifest.read_text())
    if document.get("schema") != "open-ocarina.gpu-allocations/v2" or document.get("replay_complete") is not False:
        raise ValueError("Allocation evidence must not claim complete replay")
    if document.get("coverage") != COVERAGE or any(type(v) is not bool for v in document["coverage"].values()):
        raise ValueError("Unsupported or overstated coverage")
    if document.get("before_position") != "immediately before selected dispatch" or document.get("after_position") != "immediately after selected dispatch":
        raise ValueError("Exact dispatch boundary declaration is missing")
    for field in ("after_recorded", "mapped_memory_invalidated", "table_exported"):
        if type(document.get(field)) is not bool:
            raise ValueError("Invalid synchronization declaration")
    if document["table_exported"] is not True or document.get("table_manifest") != "manifest.json":
        raise ValueError("Completed actual GPU table is required")
    table_report = PAGE.validate_snapshot(directory)
    table_document = json.loads((directory / "manifest.json").read_text())
    for field in ("recording_part", "recording_sequence", "completed_recording_sequence"):
        integer(document.get(field), field, 1)
        if document[field] != table_document["submission"][field]:
            raise ValueError("Allocation and page table command/timeline differ")
    for field in ("program", "variant", "spirv_sha256"):
        if document.get(field) != table_document["pipeline"][field]:
            raise ValueError("Allocation and page-table pipeline identities differ")
    occurrence = integer(document.get("selected_occurrence"), "selected_occurrence", 1, 65536)
    if document.get("occurrence_filter") != "exact program, variant and emitted SPIR-V SHA-256":
        raise ValueError("Occurrence filter identity is missing")
    if type(document.get("null_descriptor_enabled")) is not bool:
        raise ValueError("Actual enabled nullDescriptor feature state is required")
    budget = integer(document.get("budget_bytes"), "budget_bytes", 32 << 20, 8 << 30)
    rows = document.get("buffers")
    if not isinstance(rows, list) or len(rows) > 16384:
        raise ValueError("Catalog exceeds bounded size")
    if integer(document.get("candidate_count"), "candidate_count") != len(rows):
        raise ValueError("Candidate count differs from catalog")
    failures = document.get("failures")
    if not isinstance(failures, list) or len(failures) > 128 or len(failures) != len(set(failures)) or any(not isinstance(v, str) or not re.fullmatch(r"[a-z_]+", v) for v in failures):
        raise ValueError("Malformed explicit coverage failures")
    mandatory = {"images_not_captured", "temporal_state_not_captured", "underlying_import_aliases_unresolved", "cpu_writers_not_quiesced", "late_allocations_require_completed_table_resolution"}
    if not mandatory <= set(failures):
        raise ValueError("Known capture limitations were omitted")
    handles = set()
    source_bytes = 0
    payload_bytes = 0
    for index, row in enumerate(rows):
        if row.get("id") != index or type(row.get("id")) is not int:
            raise ValueError("Catalog identities must be canonical and unique")
        handle = hex64(row.get("buffer"), "buffer")
        if not handle or handle in handles:
            raise ValueError("Duplicate or null buffer ownership")
        handles.add(handle)
        address = hex64(row.get("address"), "address")
        size = integer(row.get("size"), "size", 1)
        if address and address + size > U64:
            raise ValueError("Device interval overflows")
        lo, hi = hex64(row.get("guest_lo"), "guest_lo"), hex64(row.get("guest_hi"), "guest_hi")
        if type(row.get("guest_range")) is not bool:
            raise ValueError("Guest ownership declaration is missing")
        if row["guest_range"]:
            if hi <= lo or hi - lo != size or row.get("role") == "renderer-buffer":
                raise ValueError("Guest interval and actual buffer size differ")
        elif lo or hi or row.get("role") != "renderer-buffer":
            raise ValueError("Renderer-owned buffers must not invent guest ranges")
        memory = hex64(row.get("memory"), "memory")
        integer(row.get("memory_offset"), "memory_offset")
        piece_count = integer(row.get("memory_piece_count"), "memory_piece_count")
        pieces = row.get("memory_piece_handles")
        if not isinstance(pieces, list) or len(pieces) != piece_count or len(pieces) > 16384:
            raise ValueError("Memory piece ownership list differs from count")
        for piece in pieces:
            if not hex64(piece, "memory piece"):
                raise ValueError("Null memory piece identity")
        if memory and (not pieces or pieces[0] != row["memory"]):
            raise ValueError("Primary allocation owner differs from memory pieces")
        if piece_count > 1 and "sparse_memory_piece_identity_unresolved" not in failures:
            raise ValueError("Sparse binding offsets must remain explicitly unresolved")
        usage = integer(row.get("usage"), "usage")
        if row.get("role") not in {"live-import", "retired-import", "evicted-import", "queued-import", "pool-mirror", "queued-mirror", "writeback-shadow", "queued-shadow", "renderer-buffer"}:
            raise ValueError("Unknown allocation ownership role")
        if type(row.get("copy_supported")) is not bool or row["copy_supported"] != bool(usage & 1 and size % 4 == 0):
            raise ValueError("Copy support does not match usage/alignment")
        source_bytes += size
        for phase in ("before", "after"):
            item = row.get(phase)
            if item is None:
                continue
            if not row["copy_supported"] or not document["mapped_memory_invalidated"] or (phase == "after" and not document["after_recorded"]):
                raise ValueError("Unsupported or unsynchronized payload declaration")
            name = f"buffer-{index}-{phase}.bin"
            if not isinstance(item, dict) or item.get("artifact") != name or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
                raise ValueError("Payload identity/path is malformed")
            artifact = directory / name
            regular(artifact)
            if artifact.stat().st_size != size:
                raise ValueError("Truncated or padded allocation payload")
            with artifact.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != item["sha256"]:
                raise ValueError("Allocation payload digest mismatch")
            payload_bytes += size
    if integer(document.get("candidate_bytes"), "candidate_bytes") != source_bytes or integer(document.get("required_staging_bytes"), "required_staging_bytes") != source_bytes * 2:
        raise ValueError("Candidate byte accounting differs")
    if payload_bytes > budget or (source_bytes * 2 > budget and (payload_bytes or "staging_budget_or_catalog_limit_exceeded" not in failures)):
        raise ValueError("Staging budget exceeded or missing explicit limit failure")
    descriptors = document.get("descriptors")
    if not isinstance(descriptors, list) or len(descriptors) > 65536:
        raise ValueError("Descriptor catalog is missing or unbounded")
    unresolved_descriptors = 0
    null_descriptors = 0
    missing_descriptor_payloads = 0
    owner = {row["buffer"]: row for row in rows}
    for descriptor in descriptors:
        for field in ("binding", "element", "type"):
            integer(descriptor.get(field), field, 0, (1 << 32) - 1)
        hex64(descriptor.get("buffer"), "descriptor buffer")
        offset = integer(descriptor.get("offset"), "descriptor offset")
        length = integer(descriptor.get("range"), "descriptor range", 1)
        if int(descriptor["buffer"], 16) == 0:
            if offset != 0 or length != U64 - 1 or descriptor["type"] not in (6, 7):
                raise ValueError("Unsupported null descriptor framing")
            if document["null_descriptor_enabled"]:
                null_descriptors += 1
            else:
                unresolved_descriptors += 1
                if "null_descriptor_feature_not_enabled" not in failures:
                    raise ValueError("Null descriptor without enabled feature must remain unresolved")
            continue
        row = owner.get(descriptor["buffer"])
        if row is not None and (row["before"] is None or row["after"] is None):
            missing_descriptor_payloads += 1
        if row is None or offset >= row["size"] or (length != U64 - 1 and length > row["size"] - offset):
            unresolved_descriptors += 1
    if unresolved_descriptors and not {"descriptor_owner_unknown", "descriptor_range_unresolved", "null_descriptor_feature_not_enabled"} & set(failures):
        raise ValueError("Unresolved descriptor omitted from failures")
    with (directory / "page-table.bin").open("rb") as source:
        entries = array.array("Q")
        entries.fromfile(source, PAGE.TABLE_BYTES // 8)
    if sys.byteorder != "little":
        entries.byteswap()
    resolution, samples = resolve_table(entries, rows)
    declared = document.get("table_resolution")
    if declared != resolution or not isinstance(declared, dict) or any(type(v) is not int for v in declared.values()):
        raise ValueError("Declared table resolution differs from every copied GPU entry")
    for field, reason in (("unresolved", "gpu_table_has_unresolved_pages"), ("ambiguous", "gpu_table_has_ambiguous_pages"),
                          ("partial", "gpu_table_has_partial_pages"), ("missing_payload", "gpu_table_has_missing_payloads")):
        if resolution[field] and reason not in failures:
            raise ValueError("GPU table coverage gap omitted from failures")
    return {"artifact_integrity_verified": True, "independent_gpu_attestation": False, "replay_complete": False,
            "candidate_count": len(rows), "candidate_bytes": source_bytes, "payload_bytes": payload_bytes,
            "budget_bytes": budget, "table_resolution": resolution, "unresolved_page_samples": samples,
            "unresolved_descriptor_count": unresolved_descriptors, "defined_null_descriptor_count": null_descriptors,
            "selected_occurrence": occurrence, "null_descriptor_enabled": document["null_descriptor_enabled"],
            "descriptor_payload_ranges_complete": not unresolved_descriptors and not missing_descriptor_payloads,
            "table_payload_ranges_complete": not any(resolution[k] for k in ("unresolved", "ambiguous", "partial", "missing_payload")),
            "coverage": COVERAGE, "failures": failures, "table_sha256": table_report["table_sha256"]}


def check(checkout=None, compiler=None):
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        raise ValueError("C++20 compiler is required")
    with tempfile.TemporaryDirectory(prefix="oo-allocation-check-") as temporary:
        work = Path(temporary)
        if checkout:
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(work / "index")
            if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() != PAGE.PIN:
                raise ValueError("Unexpected upstream revision")
            subprocess.run(["git", "read-tree", "HEAD"], cwd=checkout, env=env, check=True)
            for patch in (PAGE.IDENTITY, PAGE.PATCH, HERE / "allocation-capture.patch", PATCH):
                subprocess.run(["git", "apply", "--cached", str(patch.resolve())], cwd=checkout, env=env, check=True)
        headers = work / "gpu/vk"
        headers.mkdir(parents=True)
        for patch, name in ((PAGE.IDENTITY, "compute_identity.h"), (PAGE.PATCH, "page_table_capture.h"), (HERE / "allocation-capture.patch", "allocation_capture.h")):
            (headers / name).write_text(PAGE.header_from_patch(patch, name))
        subprocess.run(["git", "apply", "--include=gpu/vk/allocation_capture.h", str(PATCH)], cwd=work, check=True)
        (work / "test.cpp").write_text(r'''#include "allocation_capture.h"
#include <csignal>
int main(int argc,char** argv) {
 using namespace mkwvk;
 uint64_t parsed=0;
 for(const char* bad:{"","0","65537","-1","+2"," 2","2 ","1junk","18446744073709551616"})
    if(oo_alloc::decimal_bound(bad,1,65536,parsed))return 20;
 if(!oo_alloc::decimal_bound("65536",1,65536,parsed)||parsed!=65536)return 21;
 oo_alloc::SelectionCounter counter;oo_alloc::SelectionConfig config;config.occurrence=2;
 if(counter.observe(false,config)||counter.observed||counter.observe(true,config)||!counter.observe(true,config)||counter.observed!=2)return 22;
 config.valid=false;if(counter.observe(true,config)||counter.observed!=2)return 23;
 if(!oo_alloc::fits(8,4,16)||oo_alloc::fits(9,4,16)||oo_alloc::fits(0,UINT64_MAX,UINT64_MAX))return 1;
 oo_alloc::Row r;r.address=0x1003;r.size=4100;r.guest_lo=0x1000000000ull;r.guest_hi=r.guest_lo+r.size;
 if(oo_alloc::available(r,0x1003)!=4100||oo_alloc::available(r,0x2003)!=4||oo_alloc::available(r,0x2007))return 2;
 r.before_sha=r.after_sha="present";
 uint64_t table[]={0x1003,0x2003,0x3003};
 auto coverage=oo_alloc::table_coverage(reinterpret_cast<const uint8_t*>(table),sizeof(table),{r});
 if(coverage.mapped!=3||coverage.unresolved!=1||coverage.partial!=1||coverage.missing_payload)return 3;
 coverage=oo_alloc::table_coverage(reinterpret_cast<const uint8_t*>(table),sizeof(table),{r,r});
 if(coverage.ambiguous!=2)return 4;
 r.guest_lo+=4096;r.guest_hi+=4096;
 coverage=oo_alloc::table_coverage(reinterpret_cast<const uint8_t*>(table),sizeof(table),{r});
 if(coverage.unresolved!=3)return 5;
 oo_pt::Request request;request.submitted_sequence=7;
 if(oo_alloc::write_manifest(request,6,{}, {}, {},false,false,false,{},0))return 6;
 if(oo_alloc::save_payload("../escape",reinterpret_cast<const uint8_t*>(table),8,r.before_sha))return 7;
 if(argc==1)return 0;
 const std::string mode=argv[1];
 if(mode=="invalid")return oo_alloc::selection_config().valid||oo_alloc::config().enabled?25:0;
 if(!oo_alloc::config().enabled||oo_alloc::selection_config().occurrence!=2)return 8;
 std::vector<uint8_t> bytes(oo_pt::kTableBytes);uint64_t first=0x1003;std::memcpy(bytes.data(),&first,8);
 request.key=0xcc62764061c44f6aull;request.variant=0x095931037888515aull;
 request.shader_digest=oo_pt::config().digest;request.root_address=0x6000;request.root_push_offset=48;
 request.recording_part=3;request.logical_value=4;request.x=2;request.y=request.z=1;
 if(mode=="pending")return oo_alloc::write_manifest(request,6,{}, {}, {},false,false,false,{},0)?9:0;
#ifdef SIGXFSZ
 if(mode=="shortwrite")std::signal(SIGXFSZ,SIG_IGN);
#endif
 r.buffer=0x42;r.address=0x1003;r.size=4096;r.guest_lo=0x1000000000ull;r.guest_hi=r.guest_lo+r.size;
 r.memory=0x50;r.memory_pieces={0x50};r.piece_count=1;r.role="pool-mirror";r.copy_supported=true;r.usage=3;r.before_sha.clear();r.after_sha.clear();
 if(mode=="shortwrite")return oo_alloc::save_payload("buffer-0-before.bin",bytes.data(),4096,r.before_sha)?10:0;
 if(!oo_pt::write_completed(request,bytes.data(),bytes.size(),7))return 11;
 if(!oo_alloc::save_payload("buffer-0-before.bin",bytes.data(),4096,r.before_sha)||
    !oo_alloc::save_payload("buffer-0-after.bin",bytes.data(),4096,r.after_sha))return 12;
 std::vector<std::string> gaps={"images_not_captured","temporal_state_not_captured","underlying_import_aliases_unresolved",
    "cpu_writers_not_quiesced","late_allocations_require_completed_table_resolution"};
 oo_alloc::Row stream=r;stream.buffer=0x43;stream.address=0;stream.guest_lo=stream.guest_hi=0;stream.guest_range=false;
 stream.role="renderer-buffer";
 if(!oo_alloc::save_payload("buffer-1-before.bin",bytes.data(),4096,stream.before_sha)||
    !oo_alloc::save_payload("buffer-1-after.bin",bytes.data(),4096,stream.after_sha))return 24;
 auto totals=oo_alloc::table_coverage(bytes.data(),bytes.size(),{r,stream});
 std::vector<oo_alloc::Descriptor> descriptors={{0,0,6,0x43,0,4096},{7,1,7,0,0,UINT64_MAX},{7,2,7,0,0,UINT64_MAX}};
 if(!oo_alloc::write_manifest(request,7,{r,stream},descriptors,gaps,true,true,true,totals,8192,2,true))return 13;
 if(oo_alloc::write_manifest(request,7,{r,stream},descriptors,gaps,true,true,true,totals,8192,2,true))return 14;
 return 0;
}
''')
        executable = work / "test"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread",
                        "-I", str(headers), str(work / "test.cpp"), "-o", str(executable)], check=True, timeout=60)
        subprocess.run([str(executable)], check=True, timeout=30)
        for mode in ("pending", "valid", "shortwrite", "invalid"):
            output = work / mode
            output.mkdir()
            env = os.environ.copy()
            env["RX_VK_OO_IDENTITY_FILE"] = str(work / "identity.jsonl")
            env["RX_VK_OO_PT_SNAPSHOT_DIR"] = str(output)
            env["RX_VK_OO_PT_SNAPSHOT_SHA256"] = hashlib.sha256(b"shader").hexdigest()
            env["RX_VK_OO_ALLOC_CAPTURE"] = "1"
            env["RX_VK_OO_CAPTURE_OCCURRENCE"] = "invalid" if mode == "invalid" else "2"
            env.pop("RX_VK_OO_ALLOC_BUDGET_MIB", None)
            options = {}
            if mode == "shortwrite" and os.name == "posix":
                import resource
                def short_limit():
                    resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))
                options["preexec_fn"] = short_limit
            elif mode == "shortwrite":
                continue
            subprocess.run([str(executable), mode], env=env, check=True, timeout=30, **options)
            if mode == "valid":
                result = validate_snapshot(output)
                if (not result["table_payload_ranges_complete"] or not result["descriptor_payload_ranges_complete"]
                        or result["selected_occurrence"] != 2 or result["defined_null_descriptor_count"] != 2 or result["replay_complete"]):
                    raise ValueError("Synthetic writer claimed incorrect coverage")
            elif list(output.iterdir()):
                raise ValueError("Failed or premature capture emitted committed output")
    return {"patch_chain_verified": bool(checkout), "optimized_synthetic_helper_compiled": True, "bounded_filtered_occurrence_counter_checked": True,
            "nonguest_stream_payload_and_explicit_null_feature_checked": True,
            "unaligned_bases_partial_tails_ambiguity_guest_mismatch_and_budget_checked": True,
            "scope": "synthetic CPU helpers and patch chain; no GPU workload executed", "replay_complete": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--compiler")
    parser.add_argument("--validate-snapshot", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_snapshot(args.validate_snapshot) if args.validate_snapshot else check(args.checkout, args.compiler), indent=2))


if __name__ == "__main__":
    main()
