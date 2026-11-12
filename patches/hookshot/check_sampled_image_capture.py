#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check selected sampled-image payload framing, metadata, budgets and completion."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("image_buffer_checker", HERE / "check_occurrence_capture.py")
BUFFER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUFFER)
PAGE = BUFFER.PAGE
PATCH = HERE / "sampled-image-capture.patch"
CHAIN = ["compute-identity.patch", "page-table-snapshot.patch", "allocation-capture.patch", "occurrence-capture.patch", "graphics-identity.patch", PATCH.name]
COVERAGE = {"image_physical_aliases_resolved": False, "cpu_writers_quiesced": False, "temporal_state": False,
            "complete_push_constants": False, "submitted_descriptor_words_decoded": False}
TARGET = {"program": "cc62764061c44f6a", "variant": "095931037888515a", "spirv_sha256": "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b"}
integer, hex64, regular = BUFFER.integer, BUFFER.hex64, BUFFER.regular


def block(fmt):
    if 9 <= fmt <= 15: return (1, 1, 1)
    if 16 <= fmt <= 22 or 70 <= fmt <= 76: return (1, 1, 2)
    if 37 <= fmt <= 69 or 77 <= fmt <= 83 or 98 <= fmt <= 100 or fmt in (122, 123): return (1, 1, 4)
    if 91 <= fmt <= 97 or 101 <= fmt <= 103: return (1, 1, 8)
    if 107 <= fmt <= 109: return (1, 1, 16)
    if 131 <= fmt <= 146: return (4, 4, 8 if fmt <= 134 or 139 <= fmt <= 140 else 16)
    return None


def bool_field(document, key):
    if type(document.get(key)) is not bool:
        raise ValueError(f"Missing boolean: {key}")
    return document[key]


def words(value, bound=32):
    if not isinstance(value, list) or len(value) > bound:
        raise ValueError("Unbounded creation metadata")
    for word in value:
        integer(word, "creation word", 0, (1 << 32) - 1)
    return value


def plan(image, view, layout):
    if not image["complete"] or not view["complete"]: return None, "unsupported_create_info_pnext_or_ownership"
    if not hex64(image["handle"], "image") or view["image"] != image["handle"]: return None, "image_view_owner_unknown"
    if layout != 1: return None, "descriptor_layout_not_general"
    if image["flags"] & ~(8 | 16 | 128 | 256): return None, "unsupported_image_flags_sparse_alias_or_protected"
    if image["samples"] != 1 or image["type"] > 1 or image["extent"][2] != 1: return None, "unsupported_multisample_or_three_dimensional_image"
    if view["aspect"] != 1: return None, "unsupported_noncolor_aspect"
    if not image["usage"] & 1 or not image["usage"] & 4: return None, "image_transfer_or_sample_usage_missing"
    usage = view["usage"] if view["explicit_usage"] else image["usage"]
    if not usage & 4 or usage & image["usage"] != usage: return None, "view_usage_conflicts_with_image"
    if view["format"] != image["format"] and not image["flags"] & 8: return None, "view_format_conflicts_with_image"
    if image["type"] == 0 and image["extent"][1] != 1: return None, "invalid_image_or_view_bounds"
    if view["flags"] or not all(image["extent"][:2]) or not image["mip_levels"] or not image["array_layers"] or image["mip_levels"] > 32: return None, "invalid_image_or_view_bounds"
    if (image["type"] == 0 and view["type"] not in (0, 4)) or (image["type"] == 1 and view["type"] not in (1, 3, 5, 6)): return None, "unsupported_view_type"
    base, layer = view["base_mip"], view["base_layer"]
    if base >= image["mip_levels"] or layer >= image["array_layers"]: return None, "view_subresource_out_of_bounds"
    if image["mip_levels"] > max(image["extent"][:2]).bit_length(): return None, "invalid_image_or_view_bounds"
    count = image["mip_levels"] - base if view["mip_count"] == (1 << 32) - 1 else view["mip_count"]
    layers = image["array_layers"] - layer if view["layer_count"] == (1 << 32) - 1 else view["layer_count"]
    if not 0 < count <= image["mip_levels"] - base or not 0 < layers <= image["array_layers"] - layer: return None, "view_subresource_out_of_bounds"
    if view["type"] in (0, 1) and layers != 1: return None, "view_subresource_out_of_bounds"
    if view["type"] in (3, 6) and (not image["flags"] & 16 or image["extent"][0] != image["extent"][1] or layers % 6 or (view["type"] == 3 and layers != 6)): return None, "view_subresource_out_of_bounds"
    framing = block(image["format"])
    if framing is None: return None, "unsupported_backing_format"
    bw, bh, bpb = framing
    offset, mips = 0, []
    for level in range(base, base + count):
        width, height = max(1, image["extent"][0] >> level), max(1, image["extent"][1] >> level)
        alignment = max(4, bpb)
        offset = (offset + alignment - 1) // alignment * alignment
        size = ((width + bw - 1) // bw) * ((height + bh - 1) // bh) * bpb * layers
        if offset + size >= 1 << 64: return None, "image_footprint_overflow"
        mips.append({"level": level, "extent": [width, height, 1], "offset": offset, "bytes": size})
        offset += size
    return {"supported": True, "reason": "", "bytes": (offset + 3) // 4 * 4,
            "block": list(framing), "layers": layers, "mips": mips}, None


def validate_images(directory, allocation=None):
    directory = Path(directory)
    if directory.is_symlink(): raise ValueError("Symlink capture directory refused")
    manifest = directory / "images.json"
    regular(manifest)
    if manifest.stat().st_size > 4 << 20: raise ValueError("Image manifest exceeds bound")
    document = json.loads(manifest.read_text())
    if document.get("schema") != "open-ocarina.sampled-images/v1" or document.get("replay_complete") is not False:
        raise ValueError("Image evidence must never claim complete replay")
    if document.get("coverage") != COVERAGE or any(type(v) is not bool for v in document["coverage"].values()):
        raise ValueError("Overstated image/replay coverage")
    complete = bool_field(document, "image_inputs_complete")
    invalidated = bool_field(document, "mapped_memory_invalidated")
    for key, value in TARGET.items():
        if document.get(key) != value: raise ValueError("Read-only image contract requires the exact target identity")
    for key in ("recording_part", "recording_sequence", "completed_recording_sequence"):
        integer(document.get(key), key, 1)
    if document["completed_recording_sequence"] < document["recording_sequence"]: raise ValueError("Image export precedes exact recording completion")
    integer(document.get("selected_occurrence"), "selected_occurrence", 1, 65536)
    if document.get("command_position") != "before selected compute dispatch": raise ValueError("Image command boundary is missing")
    image_budget = integer(document.get("image_budget_bytes"), "image_budget", 8 << 20, 4 << 30)
    budget = integer(document.get("aggregate_budget_bytes"), "aggregate_budget", 32 << 20, 8 << 30)
    buffers = integer(document.get("buffer_staging_bytes"), "buffer_staging")
    if allocation is not None:
        for key in (*TARGET, "recording_part", "recording_sequence", "completed_recording_sequence", "selected_occurrence"):
            if document[key] != allocation[key]: raise ValueError("Image and buffer capture identities/timelines differ")
        if budget != allocation["budget_bytes"] or buffers != allocation["required_staging_bytes"]: raise ValueError("Aggregate buffer staging accounting differs")
    failures = document.get("failures")
    mandatory = {"image_physical_aliases_unresolved", "cpu_writers_not_quiesced", "temporal_state_not_captured"}
    if not isinstance(failures, list) or not mandatory <= set(failures) or len(failures) > 64:
        raise ValueError("Missing explicit image capture gaps")
    rows = document.get("images")
    if not isinstance(rows, list) or len(rows) > 64: raise ValueError("Image descriptors missing or unbounded")
    identities, owners = set(), {}
    planned_bytes, copied_bytes = 0, 0
    complete_rows = 0
    for index, row in enumerate(rows):
        if type(row.get("id")) is not int or row["id"] != index: raise ValueError("Noncanonical image row identity")
        for key in ("binding", "element", "descriptor_type", "descriptor_layout"): integer(row.get(key), key, 0, (1 << 32) - 1)
        identity = (row["binding"], row["element"])
        if identity in identities: raise ValueError("Duplicate image descriptor")
        identities.add(identity)
        hex64(row.get("image_view"), "image_view"); hex64(row.get("sampler_handle"), "sampler")
        image, view, sampler = row.get("image", {}), row.get("view", {}), row.get("sampler", {})
        for key in ("handle", "memory"): hex64(image.get(key), key)
        integer(image.get("memory_offset"), "memory_offset")
        for key in ("flags", "type", "format", "mip_levels", "array_layers", "samples", "tiling", "usage", "sharing_mode", "initial_layout"):
            integer(image.get(key), key, 0, (1 << 32) - 1)
        if len(words(image.get("extent"))) != 3: raise ValueError("Invalid image extent")
        for key in ("queue_families", "pnext_types", "format_list"): words(image.get(key))
        bool_field(image, "complete")
        if image["complete"] and any(t != 1000147000 for t in image["pnext_types"]): raise ValueError("Unsupported image pNext claimed complete")
        if int(image["handle"], 16):
            if image["handle"] in owners and owners[image["handle"]] != image: raise ValueError("Aliased views disagree on backing image creation facts")
            owners[image["handle"]] = image
        for key in ("handle", "image"): hex64(view.get(key), key)
        for key in ("flags", "type", "format", "aspect", "base_mip", "mip_count", "base_layer", "layer_count", "usage"):
            integer(view.get(key), key, 0, (1 << 32) - 1)
        if len(words(view.get("components"))) != 4: raise ValueError("Invalid component mapping")
        words(view.get("pnext_types"));bool_field(view, "complete");bool_field(view, "explicit_usage")
        if view["complete"] and (view["pnext_types"] != ([1000117002] if view["explicit_usage"] else [])):
            raise ValueError("View usage extension framing differs")
        if int(view["handle"], 16) and view["handle"] != row["image_view"]: raise ValueError("Submitted view identity differs from creation")
        hex64(sampler.get("handle"), "sampler owner");bool_field(sampler, "complete")
        if len(words(sampler.get("core_words"))) != 16: raise ValueError("Sampler core creation words are incomplete")
        extensions = sampler.get("pnext")
        if not isinstance(extensions, list) or len(extensions) > 16: raise ValueError("Sampler extension chain is unbounded")
        for extension in extensions:
            kind = integer(extension.get("type"), "sampler extension", 0, (1 << 32) - 1)
            value = words(extension.get("words"))
            expected = {1000130001: 1, 1000287000: 5}.get(kind)
            if sampler["complete"] and (expected is None or len(value) != expected): raise ValueError("Unsupported sampler pNext claimed complete")
        row_failures = row.get("failures")
        if not isinstance(row_failures, list) or len(row_failures) > 32: raise ValueError("Missing explicit image row failures")
        if (not int(sampler["handle"], 16) or sampler["handle"] != row["sampler_handle"]) and "sampler_owner_unknown" not in row_failures:
            raise ValueError("Missing explicit sampler ownership gap")
        if not sampler["complete"] and "sampler_pnext_unsupported" not in row_failures:
            raise ValueError("Unsupported sampler extension gap was omitted")
        expected, reason = plan(image, view, row["descriptor_layout"])
        copy = row.get("copy", {})
        if type(copy.get("supported")) is not bool: raise ValueError("Missing image copy support declaration")
        if expected is not None:
            if copy != expected: raise ValueError("Image block/mip/layer footprint differs from exact creation bounds")
            planned_bytes += copy["bytes"]
        elif copy["supported"] or copy.get("reason") != reason or reason not in row_failures:
            raise ValueError("Unsupported image framing was silently accepted")
        payload = row.get("before")
        if payload is not None:
            if expected is None or not invalidated: raise ValueError("Unsupported or uninvalidated image payload")
            name = f"image-{index}-before.bin"
            if not isinstance(payload, dict) or payload.get("artifact") != name: raise ValueError("Escaping or incorrect image artifact path")
            artifact = directory / name;regular(artifact)
            if artifact.stat().st_size != copy["bytes"]: raise ValueError("Image payload truncated or padded beyond declared plan")
            with artifact.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
                end = 0
                for mip in copy["mips"]:
                    source.seek(end)
                    if any(source.read(mip["offset"] - end)): raise ValueError("Mip alignment padding was not GPU-zero initialized")
                    end = mip["offset"] + mip["bytes"]
                source.seek(end)
                if any(source.read()): raise ValueError("Image tail padding was not GPU-zero initialized")
            if digest != payload.get("sha256"): raise ValueError("Image payload digest mismatch")
            copied_bytes += copy["bytes"]
        good = (expected is not None and payload is not None and not row_failures and row["descriptor_type"] == 1
                and row["element"] == 0 and row["binding"] in (8, 9, 10, 11)
                and sampler["complete"] and sampler["handle"] == row["sampler_handle"] and int(sampler["handle"], 16) != 0
                and view["handle"] == row["image_view"] and int(view["handle"], 16) != 0)
        complete_rows += good
    if copied_bytes > image_budget or buffers + copied_bytes > budget: raise ValueError("Image or aggregate staging budget exceeded")
    if (planned_bytes > image_budget or buffers + planned_bytes > budget) and "image_or_aggregate_staging_budget_exceeded" not in failures:
        raise ValueError("Image budget gap was omitted")
    actual_complete = (invalidated and len(rows) == 4 and identities == {(b, 0) for b in (8, 9, 10, 11)}
                       and complete_rows == 4 and set(failures) == mandatory)
    if complete != actual_complete: raise ValueError("Image input coverage overstated or inconsistent")
    return {"artifact_integrity_verified": True, "independent_gpu_attestation": False, "image_inputs_complete": complete,
            "replay_complete": False, "selected_occurrence": document["selected_occurrence"], "descriptor_count": len(rows),
            "unique_image_count": len(owners), "image_payload_bytes": copied_bytes, "coverage": COVERAGE,
            "linked_buffer_capture_checked": allocation is not None, "failures": failures}


def validate_snapshot(directory):
    buffer_report = BUFFER.validate_snapshot(directory)
    allocation = json.loads((Path(directory) / "allocations.json").read_text())
    report = validate_images(directory, allocation)
    report["buffer_table_ranges_complete"] = buffer_report["table_payload_ranges_complete"]
    report["buffer_descriptor_ranges_complete"] = buffer_report["descriptor_payload_ranges_complete"]
    return report


def check(checkout=None, compiler=None):
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    if not compiler: raise ValueError("C++20 compiler required")
    with tempfile.TemporaryDirectory(prefix="oo-image-check-") as temporary:
        work = Path(temporary)
        if checkout:
            env = os.environ.copy();env["GIT_INDEX_FILE"] = str(work / "index")
            if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() != PAGE.PIN: raise ValueError("Unexpected upstream revision")
            subprocess.run(["git", "read-tree", "HEAD"], cwd=checkout, env=env, check=True)
            for name in CHAIN: subprocess.run(["git", "apply", "--cached", str(HERE / name)], cwd=checkout, env=env, check=True)
        headers = work / "gpu/vk";headers.mkdir(parents=True)
        for name, patch in (("compute_identity.h", PAGE.IDENTITY), ("page_table_capture.h", PAGE.PATCH), ("allocation_capture.h", HERE / "allocation-capture.patch")):
            (headers / name).write_text(PAGE.header_from_patch(patch, name))
        subprocess.run(["git", "apply", "--include=gpu/vk/allocation_capture.h", str(HERE / "occurrence-capture.patch")], cwd=work, check=True)
        (headers / "image_capture.h").write_text(PAGE.header_from_patch(PATCH, "image_capture.h"))
        source = work / "test.cpp"
        source.write_text(r'''#include "image_capture.h"
int main() {
 using namespace mkwvk;
 oo_img::Image image;image.handle=0x42;image.memory=0x80;image.type=1;image.format=9;image.width=5;image.height=3;image.depth=1;image.mips=3;image.layers=2;image.samples=1;image.usage=5;
 oo_img::View view;view.handle=0x50;view.image=0x42;view.type=5;view.format=9;view.aspect=1;view.mip_count=3;view.layer_count=2;
 auto planned=oo_img::plan(image,view,1);if(!planned.supported||planned.bytes!=40||planned.mips[1].offset!=32||planned.mips[2].bytes!=2)return 1;
 auto bc=image;bc.format=137;view.format=137;auto compressed=oo_img::plan(bc,view,1);if(!compressed.supported||compressed.bytes!=128)return 2;
 view.format=9;
 for(uint32_t bad:{0u,2u,5u})if(oo_img::plan(image,view,bad).supported)return 3;
 auto sparse=image;sparse.flags=1;if(oo_img::plan(sparse,view,1).supported)return 4;
 auto depth=view;depth.aspect=2;if(oo_img::plan(image,depth,1).supported)return 5;
 uint64_t sum=0;if(oo_img::add(UINT64_MAX,1,sum)||oo_img::mul(UINT64_MAX,2,sum))return 6;
 oo_pt::Request request;request.key=0xcc62764061c44f6aull;request.variant=0x095931037888515aull;request.shader_digest="b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b";
 request.recording_part=3;request.submitted_sequence=7;
 if(oo_img::write_manifest(request,6,64,0,{}, {},false,false))return 7;
 std::vector<oo_img::Row> rows;
 std::vector<uint8_t> bytes(planned.bytes);for(const auto& mip:planned.mips)for(uint64_t n=0;n<mip.bytes;++n)bytes[mip.offset+n]=17;
 for(uint32_t i=0;i<4;++i){oo_img::Row row;row.binding=8+i;row.type=1;row.layout=1;row.image_view=0x50+i;row.sampler_handle=0x90;row.image=image;row.view=view;row.view.handle=row.image_view;row.sampler.handle=0x90;row.copy=planned;
 if(!oo_alloc::save_payload("image-"+std::to_string(i)+"-before.bin",bytes.data(),bytes.size(),row.payload_sha))return 8;rows.push_back(row);}
 if(!oo_img::write_manifest(request,7,64,0,rows,{"image_physical_aliases_unresolved","cpu_writers_not_quiesced","temporal_state_not_captured"},true,true))return 9;
 return 0;
}
''')
        executable = work / "test"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread", "-I", str(headers), str(source), "-o", str(executable)], check=True, timeout=60)
        output = work / "capture";output.mkdir()
        env = os.environ.copy();env.update(RX_VK_OO_IDENTITY_FILE=str(work / "identity.jsonl"), RX_VK_OO_PT_SNAPSHOT_DIR=str(output), RX_VK_OO_ALLOC_CAPTURE="1", RX_VK_OO_IMAGES="1")
        for key in ("RX_VK_OO_IMAGE_BUDGET_MIB", "RX_VK_OO_ALLOC_BUDGET_MIB", "RX_VK_OO_CAPTURE_OCCURRENCE"): env.pop(key, None)
        subprocess.run([str(executable)], env=env, check=True, timeout=30)
        report = validate_images(output)
        if not report["image_inputs_complete"] or report["unique_image_count"] != 1: raise ValueError("Synthetic alias/view image capture failed")
    return {"patch_chain_verified": bool(checkout), "optimized_synthetic_helper_compiled": True,
            "mip_layer_block_padding_and_unsupported_layout_aspect_flags_checked": True,
            "synthetic_gpu_evidence_framing_checked": True, "gpu_workload_executed": False, "replay_complete": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path);parser.add_argument("--compiler");parser.add_argument("--validate-snapshot", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_snapshot(args.validate_snapshot) if args.validate_snapshot else check(args.checkout, args.compiler), indent=2))


if __name__ == "__main__": main()
