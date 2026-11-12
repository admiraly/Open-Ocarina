#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare a bounded physical-memory replay; Vulkan execution is explicit and digest-authorized."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import runpy
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.compile_probe import reflect
from tools.replay_contract import _json, _object, _integer, _identifier, _snapshot, _triple

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "open-ocarina.physical-compute-replay/v1"
TARGET = ("cc62764061c44f6a", "095931037888515a", "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b")
MAX_ALLOCATIONS = 128
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_TABLE_BYTES = 32 * 1024**2
FORMATS = {9: 1, 16: 2, 37: 4, 43: 4, 70: 2, 76: 2, 83: 4, 97: 8, 98: 4, 100: 4, 103: 8, 109: 16, 122: 4}
MAGIC = b"OOPHYR1\0"

FIXTURE_SOURCE = """#version 460
#extension GL_EXT_buffer_reference : require
#extension GL_EXT_shader_explicit_arithmetic_types_int64 : require
layout(local_size_x=64) in;
layout(buffer_reference,std430,buffer_reference_align=8) readonly buffer Table { uint64_t entry[]; };
layout(buffer_reference,std430,buffer_reference_align=4) readonly buffer Input { uint value[]; };
layout(set=0,binding=0,std430) buffer Output { uint value[]; } output_data;
layout(set=0,binding=1,std430) readonly buffer Alias { uint value[]; } alias_data;
layout(set=0,binding=2) uniform usampler2D pixels;
layout(push_constant) uniform Push { layout(offset=48) uint64_t table_root; } push_data;
void main() {
 uint index=gl_GlobalInvocationID.x;
 Table table=Table(push_data.table_root);
 Input input_data=Input(table.entry[index/1024u]);
 output_data.value[index]=alias_data.value[index]+input_data.value[index%1024u]+texelFetch(pixels,ivec2(index,0),0).x;
}
"""


def _id_rows(rows, name, maximum=256):
    if not isinstance(rows, list) or len(rows) > maximum:
        raise ValueError(f"Invalid {name} list")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"Invalid {name} row")
        key = _identifier(row.get("id"))
        if key in result:
            raise ValueError(f"Duplicate {name} identity")
        result[key] = row
    return result


def _backing(value):
    _object(value, ("id", "generation", "offset", "size"))
    _identifier(value["id"])
    _integer(value["generation"], "backing generation")
    _integer(value["offset"], "backing offset")
    _integer(value["size"], "backing size", 1, MAX_TOTAL_BYTES)
    if value["offset"] + value["size"] >= 1 << 64:
        raise ValueError("Backing interval overflows")
    return value


def _artifact(root, value, size=None, maximum=MAX_TOTAL_BYTES):
    path = _snapshot(root, value, size, maximum)
    return {"path": path, "size": value["size"], "sha256": value["sha256"]}


def _float_word(value):
    return struct.unpack("<f", struct.pack("<I", value))[0]


def source_independence(document, *, check_ledger=True):
    """Prove that flat owned copies preserve all source binding aliases.

    Equal bytes/guest addresses/generation counters never establish identity.
    External leases are compared at the common validated event endpoint; the
    raw checker rejects intervening overlapping invalidations.
    """
    if check_ledger:
        checker = runpy.run_path(str(ROOT / "patches/hookshot/check_binding_provenance.py"))
        result = checker["validate_document"](document)
        if not result["backing_provenance_complete"]:
            raise ValueError("Complete original backing provenance is required")
    memories = {row["lifetime_id"]: row for row in document["memories"]}
    intervals = {}
    for obj in document["objects"]:
        if not obj["tracked"]:
            raise ValueError("Untracked original object cannot be linearized")
        for edge in obj["edges"]:
            memory = memories[edge["memory_lifetime_id"]]
            size = min(edge["size"], obj["size"] - edge["resource_offset"])
            if size <= 0:
                continue
            if memory["kind"] == "vma":
                key, base = ("vk-memory", memory["lifetime_id"]), 0
            else:
                lease = memory["validated"]
                key, base = ("external", lease["kind"], lease["backing_id"]), lease["backing_offset"]
            lo = base + edge["memory_offset"]
            intervals.setdefault(key, []).append((lo, lo + size, obj["lifetime_id"]))
    for rows in intervals.values():
        rows.sort()
        for previous, current in zip(rows, rows[1:]):
            if current[0] < previous[1]:
                raise ValueError("Original backing intervals overlap; independent owned copies would lose aliases")
    return {"canonical_backing_groups": len(intervals), "committed_segments": sum(map(len, intervals.values())),
            "overlapping_intervals": 0, "objects": len(document["objects"])}


def _normalized_source(root, backing, event, pipeline, allocations, views, images, image_views, samplers, bindings, push, table, grid):
    """Recompute the supported disjoint-original-object normalization gate."""
    source_name = _identifier(backing.get("source_directory"))
    source = root / source_name
    refs = backing.get("source_manifests")
    names = ("manifest.json", "allocations.json", "images.json", "dispatch-parameters.json", "binding-provenance.json", "launch.json")
    _object(refs, names)
    documents = {}
    for name in names:
        artifact = _artifact(root, refs[name], maximum=64 * 1024**2)
        if artifact["path"] != (source / name).resolve():
            raise ValueError("Original evidence path differs from its pinned source bundle")
        documents[name] = _json(artifact["path"], maximum=64 * 1024**2)
    ledger = documents["binding-provenance.json"]
    independence = source_independence(ledger)
    for checker_name, function in (("check_binding_provenance.py", "validate_capture"),
                                   ("check_dispatch_parameters.py", "validate_capture"),
                                   ("check_sampled_image_capture.py", "validate_snapshot")):
        runpy.run_path(str(ROOT / "patches/hookshot" / checker_name))[function](source)
    parameters = documents["dispatch-parameters.json"]
    if (parameters["recording_sequence"] != event["recording_sequence"] or parameters["selected_occurrence"] != event["selected_occurrence"] or
        parameters["dispatch"] != grid or parameters["pipeline"]["local_size"] != pipeline["local_size"] or
        parameters["pipeline"]["push_range"] != {"offset": 0, "size": pipeline["push_range_bytes"], "stage_flags": 32} or
        parameters["pipeline"]["spirv_sha256"] != pipeline["spirv"]["sha256"] or
        parameters["push_constants"]["sha256"] != push["sha256"] or
        documents["manifest.json"]["table"]["sha256"] != table["snapshot"]["sha256"] or
        parameters["pipeline"]["subgroup"] != {"required_size": 32, "require_full_subgroups": True}):
        raise ValueError("Normalized graph differs from its exact selected source dispatch")
    receipt = documents["launch.json"]
    if receipt.get("renderer_sha256") != backing["source_binary_sha256"]:
        raise ValueError("Frozen producer receipt differs from the declared renderer")
    objects = {(row["kind"], row["handle"]): row for row in ledger["objects"]}
    sources = documents["allocations.json"]["buffers"]
    origins = {row["view_id"]: row for row in backing["buffer_origins"]}
    by_handle = {}
    for view in views.values():
        origin = origins[view["id"]]
        row = sources[origin["catalog_id"]]
        obj = objects[("buffer", row["buffer"])]
        allocation = allocations[view["allocation"]]
        if (origin["buffer_lifetime"] != str(obj["lifetime_id"]) or view["buffer_offset"] or view["offset"] or
            view["size"] != row["size"] or view["buffer_size"] != row["size"] or allocation["size"] != row["size"] or
            view["device_address"] != int(row["address"], 16) or allocation["initial"]["sha256"] != row["before"]["sha256"] or
            allocation["backing"] != {"id": "owned-buffer-" + str(obj["lifetime_id"]), "generation": 1, "offset": 0, "size": row["size"]}):
            raise ValueError("Owned buffer does not preserve its complete original owner")
        if row["buffer"] in by_handle and by_handle[row["buffer"]] != view["allocation"]:
            raise ValueError("One original owner was split into independent owned allocations")
        by_handle[row["buffer"]] = view["allocation"]
    normalized_slots = {(row["binding"], row["element"]): row for row in bindings}
    for raw in documents["allocations.json"]["descriptors"]:
        normalized = normalized_slots[(raw["binding"], raw["element"])]
        view = views.get(normalized["view"])
        if raw["buffer"] == "0000000000000000":
            if view is not None:
                raise ValueError("Original null descriptor changed")
        elif (view is None or by_handle.get(raw["buffer"]) != view["allocation"] or
              normalized["offset"] != raw["offset"] or normalized["range"] != raw["range"] or normalized["type"] != raw["type"]):
            raise ValueError("Original descriptor owner/range changed during normalization")
    raw_images = documents["images.json"]["images"]
    image_origins = {row["image_id"]: row for row in backing["image_origins"]}
    for image in images.values():
        origin = image_origins[image["id"]]
        raw = raw_images[origin["catalog_id"]]
        obj = objects[("image", raw["image"]["handle"])]
        original = raw["image"]
        if (origin["image_lifetime"] != str(obj["lifetime_id"]) or image["backing"]["id"] != "owned-image-" + str(obj["lifetime_id"]) or
            image["backing"]["size"] != obj["size"] or any(image[key] != original[key] for key in ("format", "type", "extent", "mip_levels", "array_layers")) or
            image["mip_levels"] != 1 or raw["view"]["base_mip"] or raw["view"]["base_layer"] or raw["view"]["layer_count"] != image["array_layers"]):
            raise ValueError("Unsupported original sampled-image normalization")
        descriptor = normalized_slots[(raw["binding"], raw["element"])]
        normalized_view = image_views[descriptor["image_view"]]
        normalized_sampler = samplers[descriptor["sampler"]]
        if (normalized_view["image"] != image["id"] or any(normalized_view[key] != raw["view"][key] for key in
            ("type", "format", "components", "base_mip", "mip_count", "base_layer", "layer_count")) or
            normalized_sampler["core_words"] != raw["sampler"]["core_words"] or normalized_sampler["pnext"] != raw["sampler"]["pnext"]):
            raise ValueError("Original sampled descriptor view/sampler changed during normalization")
        digest = hashlib.sha256()
        for layer in range(image["array_layers"]):
            digest.update(image["subresource_artifacts"][(0, layer)]["path"].read_bytes())
        if digest.hexdigest() != raw["before"]["sha256"]:
            raise ValueError("Owned sampled bytes differ from the complete original view payload")
    return independence


def _evidence(root, references, event, pipeline, allocations, views, images, image_views, samplers, bindings, push, table, mode, grid):
    required = ("coverage", "backing", "quiescence", "temporal", "dispatch")
    _object(references, required)
    evidence = {}
    for name in required:
        artifact = _artifact(root, references[name], maximum=2 * 1024**2)
        item = _json(artifact["path"])
        if not isinstance(item, dict) or item.get("schema") != f"open-ocarina.physical-{name}/v1" or item.get("event_id") != event["id"]:
            raise ValueError(f"Missing identity-bound {name} evidence")
        evidence[name] = item
    coverage = evidence["coverage"]
    slots = sorted([0, row["binding"], row["element"]] for row in bindings)
    if (coverage.get("spirv_sha256") != pipeline["spirv"]["sha256"] or
        coverage.get("descriptor_slots") != slots or coverage.get("allocation_ids") != sorted(allocations) or
        coverage.get("buffer_view_ids") != sorted(views) or coverage.get("image_view_ids") != sorted(image_views) or coverage.get("push_sha256") != push["sha256"] or
        coverage.get("table_sha256") != table["snapshot"]["sha256"] or coverage.get("unresolved") != []):
        raise ValueError("Coverage evidence does not enumerate the complete declared input graph")
    backing = evidence["backing"]
    _identifier(backing.get("source_revision"))
    if not isinstance(backing.get("source_binary_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", backing["source_binary_sha256"]):
        raise ValueError("Backing producer binary must be hash pinned")
    lease = _identifier(backing.get("lease_id"))
    if backing.get("registry_start_sequence") != 0:
        raise ValueError("Backing provenance must begin before the first import")
    if pipeline["program"] == TARGET[0] and backing.get("normalization") != "disjoint-original-objects-v1":
        raise ValueError("Target inputs require independently checked raw source normalization")
    if backing.get("normalization") == "disjoint-original-objects-v1":
        _normalized_source(root, backing, event, pipeline, allocations, views, images, image_views, samplers, bindings, push, table, grid)
    elif backing.get("normalization") is not None:
        raise ValueError("Unsupported source normalization")
    origins = backing.get("buffer_origins")
    if not isinstance(origins, list) or {item.get("view_id") for item in origins} != set(views) or len(origins) != len(views):
        raise ValueError("Backing evidence must cover every buffer view once")
    for item in origins:
        view = views[item["view_id"]]
        allocation = allocations[view["allocation"]]
        _identifier(item.get("buffer_lifetime"))
        pieces = item.get("pieces")
        if not isinstance(pieces, list) or not pieces:
            raise ValueError("Missing committed buffer bind pieces")
        start, end = view["buffer_offset"], view["buffer_offset"] + view["size"]
        cursor = start
        for piece in sorted(pieces, key=lambda row: row.get("buffer_offset", -1)):
            _integer(piece.get("buffer_offset"), "bind offset")
            _integer(piece.get("size"), "bind size", 1, MAX_TOTAL_BYTES)
            _identifier(piece.get("memory_lifetime"))
            _integer(piece.get("bind_sequence"), "committed bind sequence")
            lo, hi = max(start, piece["buffer_offset"]), min(end, piece["buffer_offset"] + piece["size"])
            if lo >= hi:
                continue
            expected_offset = allocation["backing"]["offset"] + view["offset"] + lo - start
            if (lo != cursor or piece.get("backing_id") != allocation["backing"]["id"] or
                piece.get("generation") != allocation["backing"]["generation"] or
                piece.get("backing_offset", -1) + lo - piece["buffer_offset"] != expected_offset):
                raise ValueError("Bind pieces do not preserve the canonical backing interval")
            cursor = hi
        if cursor != end:
            raise ValueError("Buffer provenance has a bind gap")
    image_origins = backing.get("image_origins")
    if not isinstance(image_origins, list) or len(image_origins) != len(images) or {row.get("image_id") for row in image_origins} != set(images):
        raise ValueError("Image backing evidence is incomplete")
    for item in image_origins:
        if item.get("backing") != images[item["image_id"]]["backing"]:
            raise ValueError("Image backing identity differs from provenance")
        _identifier(item.get("image_lifetime"))
        _identifier(item.get("memory_lifetime"))
        _integer(item.get("bind_sequence"), "image bind sequence")
    q = evidence["quiescence"]
    expected_backings = sorted({row["backing"]["id"] for row in list(allocations.values()) + list(images.values())})
    if mode == "historical-dispatch":
        if (q.get("lease_id") != lease or q.get("backing_ids") != expected_backings or
            q.get("before_epoch") != event["before_epoch"] or q.get("after_epoch") != event["after_epoch"] or
            q.get("cpu_write_events") != [] or q.get("backing_mutation_events") != []):
            raise ValueError("No complete writer/backing lease covers both historical snapshots")
    elif q.get("status") != "historical-atomicity-not-established" or q.get("owned_input_state") != "immutable-artifacts":
        raise ValueError("Frozen diagnostics must explicitly retain historical atomicity uncertainty")
    t = evidence["temporal"]
    if t.get("initial_epoch") != event["before_epoch"] or t.get("external_dependencies") != [] or t.get("method") != "complete-frozen-initial-state":
        raise ValueError("External temporal dependencies remain unsupported")
    d = evidence["dispatch"]
    for key in ("recording_sequence", "selected_occurrence", "before_epoch", "after_epoch"):
        if d.get(key) != event[key]:
            raise ValueError("Dispatch evidence does not identify the selected snapshot positions")
    if d.get("before_position") != "immediately-before-dispatch" or d.get("after_position") != "immediately-after-dispatch" or d.get("completed_recording_sequence", -1) < event["recording_sequence"]:
        raise ValueError("Dispatch completion/position evidence is incomplete")


def validate(capture, *, validate_spirv=True):
    capture = Path(capture).resolve()
    manifest_digest = hashlib.sha256((capture / "manifest.json").read_bytes()).hexdigest()
    manifest = _json(capture / "manifest.json", maximum=128 * 1024**2)
    if hashlib.sha256((capture / "manifest.json").read_bytes()).hexdigest() != manifest_digest:
        raise ValueError("Manifest changed during parsing")
    _object(manifest, ("schema", "kind", "mode", "expected_source", "event", "pipeline", "dispatch", "push_constants", "physical_table",
                       "allocations", "views", "bindings", "images", "image_views", "samplers", "evidence"))
    if manifest["schema"] != SCHEMA:
        raise ValueError("Unsupported capture: raw page-table/allocation captures are not physical replay contracts")
    if manifest["kind"] not in ("target", "synthetic"):
        raise ValueError("Unsupported capture kind")
    if manifest["mode"] not in ("frozen-diagnostic", "historical-dispatch") or manifest["expected_source"] not in ("none", "captured-after", "owned-frozen-baseline", "authored-synthetic-reference"):
        raise ValueError("Replay evidence scope must be explicit")
    if manifest["mode"] == "historical-dispatch" and manifest["expected_source"] != "captured-after":
        raise ValueError("Historical replay needs complete captured AFTER expectations")
    if manifest["mode"] == "historical-dispatch":
        raise ValueError("Historical-dispatch execution is not implemented: full device/float/atomicity proof remains separate")
    if manifest["kind"] != "synthetic" and manifest["expected_source"] == "authored-synthetic-reference":
        raise ValueError("Authored synthetic reference cannot certify a game capture")
    event = manifest["event"]
    _object(event, ("id", "recording_sequence", "selected_occurrence", "before_epoch", "after_epoch"))
    _identifier(event["id"])
    for name in event.keys() - {"id"}:
        _integer(event[name], name, 1)
    if event["before_epoch"] >= event["after_epoch"]:
        raise ValueError("Snapshot epochs are not ordered")
    pipeline = manifest["pipeline"]
    _object(pipeline, ("program", "variant", "spirv", "local_size", "push_range_bytes", "root_push_offset", "device", "source"))
    shader = _artifact(capture, pipeline["spirv"], maximum=16 * 1024**2)
    layout = reflect(shader["path"].read_bytes())
    if any(row["set"] != 0 for row in layout["bindings"]):
        raise ValueError("Only the reviewed set-zero descriptor model is supported")
    if pipeline["local_size"] != layout["workgroup"] or pipeline["root_push_offset"] != 48:
        raise ValueError("Shader workgroup or supported root offset differs")
    _integer(pipeline["push_range_bytes"], "push range", layout["push_constant_bytes"], 128)
    if pipeline["push_range_bytes"] % 4 or pipeline["root_push_offset"] + 8 > pipeline["push_range_bytes"]:
        raise ValueError("Invalid complete push range")
    if manifest["kind"] == "target":
        if (pipeline["program"], pipeline["variant"], shader["sha256"]) != TARGET or pipeline["source"] is not None:
            raise ValueError("Only the exactly reviewed target shader is supported")
    else:
        source = _artifact(capture, pipeline["source"], maximum=16384)
        if source["path"].read_bytes() != FIXTURE_SOURCE.encode():
            raise ValueError("Synthetic mode requires the independently authored fixture source")
    device = pipeline["device"]
    _object(device, ("vendor_id", "device_id"))
    for value in device.values():
        _integer(value, "device identity", 0, 0xffffffff)
    if manifest["kind"] == "target" and (not device["vendor_id"] or not device["device_id"]):
        raise ValueError("Target replay requires captured device identity")
    dispatch = manifest["dispatch"]
    _object(dispatch, ("kind", "group_count"))
    if dispatch["kind"] != "direct":
        raise ValueError("Only complete direct dispatches are supported")
    groups = _triple(dispatch["group_count"], "dispatch group count")
    if manifest["kind"] == "synthetic" and groups != [1, 1, 1]:
        raise ValueError("The authored fixture has exactly one bounded workgroup")
    if math.prod(groups) > 65536 or math.prod(groups) * math.prod(layout["workgroup"]) > 4 * 1024**2:
        raise ValueError("Dispatch exceeds bounded replay limits")
    push = _artifact(capture, manifest["push_constants"], pipeline["push_range_bytes"], 128)
    table = manifest["physical_table"]
    _object(table, ("snapshot", "entry_count", "page_size", "root_address", "mappings"))
    _integer(table["entry_count"], "table entries", 1, MAX_TABLE_BYTES // 8)
    if manifest["kind"] == "target" and table["entry_count"] != 3 * 1024**2:
        raise ValueError("Target requires the complete reviewed guest-table address domain")
    if table["page_size"] != 4096:
        raise ValueError("Only the reviewed4096-byte guest page model is supported")
    _integer(table["root_address"], "original table root", 1)
    table_artifact = _artifact(capture, table["snapshot"], table["entry_count"] * 8, MAX_TABLE_BYTES)
    if struct.unpack_from("<Q", push["path"].read_bytes(), pipeline["root_push_offset"])[0] != table["root_address"]:
        raise ValueError("Captured push root differs from the captured table root")
    allocations = _id_rows(manifest["allocations"], "allocation", MAX_ALLOCATIONS)
    if not allocations:
        raise ValueError("A replay cannot pass with no compared allocations")
    total = 0
    for row in allocations.values():
        _object(row, ("id", "size", "backing", "initial", "expected"))
        _integer(row["size"], "allocation size", 1, MAX_TOTAL_BYTES)
        if _backing(row["backing"])["size"] != row["size"]:
            raise ValueError("Canonical backing size must equal the complete allocation")
        total += row["size"]
        row["initial_artifact"] = _artifact(capture, row["initial"], row["size"])
        if manifest["expected_source"] == "none":
            if row["expected"] is not None:
                raise ValueError("Unreferenced expected payload cannot silently become a baseline")
            row["expected_artifact"] = None
        else:
            row["expected_artifact"] = _artifact(capture, row["expected"], row["size"])
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Canonical allocations exceed the aggregate replay budget")
    views = _id_rows(manifest["views"], "view")
    for row in views.values():
        _object(row, ("id", "allocation", "offset", "size", "buffer_offset", "buffer_size", "device_address"))
        if row["allocation"] not in allocations:
            raise ValueError("Unknown canonical allocation")
        _integer(row["offset"], "view offset")
        _integer(row["buffer_offset"], "original buffer offset")
        _integer(row["buffer_size"], "true original buffer size", 1, MAX_TOTAL_BYTES)
        _integer(row["device_address"], "original buffer device address")
        _integer(row["size"], "view size", 1, MAX_TOTAL_BYTES)
        if row["offset"] + row["size"] > allocations[row["allocation"]]["size"] or row["buffer_offset"] + row["size"] > row["buffer_size"]:
            raise ValueError("View exceeds its canonical allocation")
    mappings = table["mappings"]
    if not isinstance(mappings, list) or len(mappings) > table["entry_count"]:
        raise ValueError("Invalid physical mapping list")
    mapping_by_index = {}
    raw_entries = struct.unpack(f"<{table['entry_count']}Q", table_artifact["path"].read_bytes())
    for row in mappings:
        _object(row, ("index", "original_address", "allocation", "offset", "view"))
        index = _integer(row["index"], "table index", 0, table["entry_count"] - 1)
        _integer(row["original_address"], "original mapped address", 1)
        _integer(row["offset"], "canonical page offset")
        if index in mapping_by_index or row["allocation"] not in allocations:
            raise ValueError("Duplicate physical mapping or unknown allocation")
        if row["view"] not in views:
            raise ValueError("Physical page lacks a proven original buffer owner")
        owner = views[row["view"]]
        delta = row["original_address"] - owner["device_address"] - owner["buffer_offset"]
        if not owner["device_address"] or delta < 0 or delta + 4096 > owner["size"] or row["allocation"] != owner["allocation"] or row["offset"] != owner["offset"] + delta:
            raise ValueError("Physical page mapping does not preserve its true owner canonical interval")
        if row["original_address"] % 16 or row["offset"] % 16 or row["original_address"] != raw_entries[index] or row["offset"] + 4096 > allocations[row["allocation"]]["size"]:
            raise ValueError("Physical page mapping differs or exceeds captured canonical memory")
        mapping_by_index[index] = row
    if set(mapping_by_index) != {i for i, address in enumerate(raw_entries) if address}:
        raise ValueError("Every nonzero table entry must be relocated exactly once")
    if manifest["kind"] == "synthetic" and (table["entry_count"] != 1 or len(mappings) != 1 or mappings[0]["index"] != 0):
        raise ValueError("The finite fixture requires exactly one valid input page")
    target_certificate = None
    if manifest["kind"] == "target":
        from tools.physical_span_contract import validate_target_access_spans
        owners = {}
        for owner in views.values():
            address = owner["device_address"]
            if not address:
                continue
            if address in owners and owners[address] != owner["buffer_size"]:
                raise ValueError("Conflicting true original owner extents")
            owners[address] = owner["buffer_size"]
        target_certificate = validate_target_access_spans(shader["path"].read_bytes(), table_artifact["path"].read_bytes(),
            [{"address": address, "size": size} for address, size in sorted(owners.items())])
    images = _id_rows(manifest["images"], "image", 64)
    image_views = _id_rows(manifest["image_views"], "image view", 64)
    samplers = _id_rows(manifest["samplers"], "sampler", 64)
    for row in images.values():
        _object(row, ("id", "format", "type", "extent", "mip_levels", "array_layers", "backing", "subresources"))
        if row["format"] not in FORMATS or row["type"] not in (0, 1):
            raise ValueError("Unsupported sampled image format/type")
        extent = _triple(row["extent"], "image extent")
        if max(extent) > 16384 or extent[2] != 1 or (row["type"] == 0 and extent[1] != 1):
            raise ValueError("Unsupported image dimensions")
        _integer(row["mip_levels"], "mip levels", 1, min(15, max(extent).bit_length()))
        _integer(row["array_layers"], "image layers", 1, 256)
        _backing(row["backing"])
        if not isinstance(row["subresources"], list) or len(row["subresources"]) > 4096:
            raise ValueError("Invalid image subresources")
        subresources = {}
        for sub in row["subresources"]:
            _object(sub, ("mip", "layer", "initial"))
            mip = _integer(sub["mip"], "image mip", 0, row["mip_levels"] - 1)
            layer = _integer(sub["layer"], "image layer", 0, row["array_layers"] - 1)
            key = (mip, layer)
            if key in subresources:
                raise ValueError("Duplicate image subresource")
            size = max(1, extent[0] >> mip) * max(1, extent[1] >> mip) * FORMATS[row["format"]]
            subresources[key] = _artifact(capture, sub["initial"], size, 256 * 1024**2)
        row["subresource_artifacts"] = subresources
    if sum(item["size"] for row in images.values() for item in row["subresource_artifacts"].values()) > 1024**3:
        raise ValueError("Sampled image payloads exceed the replay budget")
    objects = [("buffer", row["id"], row["backing"]) for row in allocations.values()] + [("image", row["id"], row["backing"]) for row in images.values()]
    for i, (kind, identity, backing) in enumerate(objects):
        for other_kind, other_id, other in objects[i + 1:]:
            if backing["id"] == other["id"] and max(backing["offset"], other["offset"]) < min(backing["offset"] + backing["size"], other["offset"] + other["size"]):
                raise ValueError("Overlapping canonical objects must be merged; image/buffer memory aliasing is unsupported")
    for row in image_views.values():
        _object(row, ("id", "image", "type", "format", "components", "base_mip", "mip_count", "base_layer", "layer_count"))
        if row["image"] not in images:
            raise ValueError("Unknown sampled image")
        image = images[row["image"]]
        if row["format"] != image["format"] or row["type"] not in (0, 1, 3, 4, 5, 6):
            raise ValueError("Unsupported reinterpretation or view type")
        if (image["type"] == 0 and row["type"] not in (0, 4)) or (image["type"] == 1 and row["type"] not in (1, 3, 5, 6)):
            raise ValueError("Image and view dimensions differ")
        if not isinstance(row["components"], list) or len(row["components"]) != 4 or any(type(v) is not int or not 0 <= v <= 6 for v in row["components"]):
            raise ValueError("Unsupported image swizzle")
        for base, count, limit in (("base_mip", "mip_count", image["mip_levels"]), ("base_layer", "layer_count", image["array_layers"])):
            _integer(row[base], base, 0, limit - 1)
            _integer(row[count], count, 1, limit)
            if row[base] + row[count] > limit:
                raise ValueError("Image view exceeds backing subresources")
        for mip in range(row["base_mip"], row["base_mip"] + row["mip_count"]):
            for layer in range(row["base_layer"], row["base_layer"] + row["layer_count"]):
                if (mip, layer) not in image["subresource_artifacts"]:
                    raise ValueError("A view exposes an uncaptured sampled subresource")
        if row["type"] in (0, 1) and row["layer_count"] != 1:
            raise ValueError("Non-array view must expose one layer")
        if row["type"] in (3, 6) and (image["extent"][0] != image["extent"][1] or row["base_layer"] % 6 or row["layer_count"] % 6 or (row["type"] == 3 and row["layer_count"] != 6)):
            raise ValueError("Invalid cube view geometry")
    for row in samplers.values():
        _object(row, ("id", "core_words", "pnext"))
        words = row["core_words"]
        if row["pnext"] != [] or not isinstance(words, list) or len(words) != 16:
            raise ValueError("Sampler extensions are not supported by this executor")
        for value in words:
            _integer(value, "sampler word", 0, 0xffffffff)
        if words[0] or any(words[i] not in (0, 1) for i in (1, 2, 3, 8, 10)) or any(words[i] > 3 for i in (4, 5, 6)) or words[11] > 7 or words[14] > 5 or words[15] or words[10]:
            raise ValueError("Unsupported core sampler behavior")
        if not all(math.isfinite(_float_word(words[i])) for i in (7, 9, 12, 13)) or _float_word(words[12]) > _float_word(words[13]) or _float_word(words[9]) < 1:
            raise ValueError("Invalid sampler floating-point parameters")
    bindings = manifest["bindings"]
    if not isinstance(bindings, list) or not bindings or len(bindings) > 256:
        raise ValueError("Invalid descriptor list")
    expected_slots = {(row["binding"], element): row["type"] for row in layout["bindings"] for element in range(row["count"])}
    seen = set()
    used_views, used_image_views, used_samplers = set(), set(), set()
    for row in bindings:
        _object(row, ("binding", "element", "type", "view", "offset", "range", "image_view", "sampler"))
        slot = (row["binding"], row["element"])
        if slot in seen or expected_slots.get(slot) != row["type"] or row["type"] not in (1, 6, 7):
            raise ValueError("Descriptors do not match the exact shader layout")
        seen.add(slot)
        if row["type"] == 1:
            if row["view"] is not None or row["offset"] or row["range"] or row["image_view"] not in image_views or row["sampler"] not in samplers:
                raise ValueError("Incomplete sampled image/sampler descriptor")
            used_image_views.add(row["image_view"]); used_samplers.add(row["sampler"])
            view = image_views[row["image_view"]]
            if manifest["kind"] == "target" and (view["type"] != (5 if row["binding"] == 10 else 1) or view["format"] == 98):
                raise ValueError("Sampled view disagrees with the reviewed target dimensional/numeric type")
            if manifest["kind"] == "synthetic" and (view["type"] != 1 or view["format"] != 98 or images[view["image"]]["extent"] != [64, 1, 1]):
                raise ValueError("Fixture sampled image must contain64 unsigned pixels")
            if manifest["kind"] == "synthetic" and (view["base_mip"], view["mip_count"], view["base_layer"], view["layer_count"]) != (0, 1, 0, 1):
                raise ValueError("Fixture image view must expose its finite64-pixel source")
        else:
            if row["image_view"] is not None or row["sampler"] is not None:
                raise ValueError("Buffer descriptor cannot name an image")
            if row["view"] is None:
                if row["offset"] != 0 or row["range"] != 0:
                    raise ValueError("Null buffer descriptors must have empty normalized ranges")
                continue
            if row["view"] not in views:
                raise ValueError("Descriptor refers to a missing buffer view")
            view = views[row["view"]]
            _integer(row["offset"], "descriptor offset")
            _integer(row["range"], "descriptor range", 1, MAX_TOTAL_BYTES)
            if row["offset"] + row["range"] > view["size"]:
                raise ValueError("Descriptor range exceeds its canonical view")
            if manifest["kind"] == "synthetic" and row["range"] < 256:
                raise ValueError("Fixture buffer descriptors must cover all64 invocations")
            used_views.add(row["view"])
    if seen != set(expected_slots) or used_views | {row["view"] for row in mappings} != set(views) or used_image_views != set(image_views) or used_samplers != set(samplers):
        raise ValueError("Resource or descriptor enumeration is incomplete")
    if {row["image"] for row in image_views.values()} != set(images):
        raise ValueError("Unclassified sampled image")
    _evidence(capture, manifest["evidence"], event, pipeline, allocations, views, images, image_views, samplers, bindings, push, table, manifest["mode"], dispatch)
    if validate_spirv:
        subprocess.run(["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(shader["path"])],
                       check=True, capture_output=True, timeout=30)
        if manifest["kind"] == "synthetic":
            import tempfile
            with tempfile.TemporaryDirectory() as temporary:
                rebuilt = Path(temporary) / "fixture.spv"
                subprocess.run(["glslangValidator", "-V", "--target-env", "vulkan1.3", "-S", "comp", str(source["path"]), "-o", str(rebuilt)], check=True, capture_output=True, timeout=30)
                if hashlib.sha256(rebuilt.read_bytes()).hexdigest() != shader["sha256"]:
                    raise ValueError("Synthetic SPIR-V differs from the compiled independently authored fixture")
    return {"manifest": manifest, "capture": capture, "source_manifest_sha256": manifest_digest, "shader": shader, "push": push, "table": table_artifact,
            "layout": layout, "allocations": allocations, "views": views, "images": images,
            "image_views": image_views, "samplers": samplers, "mappings": mappings,
            "target_access_certificate": target_certificate,
            "producer_completeness_independently_proven": False}


def _private_new(path):
    path = Path(path).resolve()
    if path.is_relative_to(ROOT) and (path == ROOT or path.relative_to(ROOT).parts[0] not in ("local", "work", "artifacts")):
        raise ValueError("Output must be outside the tracked tree or under ignored private storage")
    if path.exists():
        raise ValueError("Choose a new private output directory")
    return path


def prepare(capture, output, *, policy=32, device=0, validate_spirv=True):
    if policy not in (8, 16, 32) or type(device) is not int or not 0 <= device <= 255:
        raise ValueError("Invalid bounded replay policy/device")
    state = validate(capture, validate_spirv=validate_spirv)
    output = _private_new(output)
    output.mkdir(parents=True)
    (output / "inputs").mkdir()
    frozen = {}

    def freeze(artifact):
        digest = artifact["sha256"]
        if digest in frozen:
            return frozen[digest]
        destination = output / "inputs" / (digest + ".bin")
        sha = hashlib.sha256()
        with artifact["path"].open("rb") as source, destination.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                sha.update(chunk); target.write(chunk)
        if destination.stat().st_size != artifact["size"] or sha.hexdigest() != digest:
            raise ValueError("Capture changed while freezing the approved plan")
        destination.chmod(0o444)
        frozen[digest] = destination
        return destination

    binary = bytearray(MAGIC)

    def u32(*values): binary.extend(struct.pack("<" + "I" * len(values), *values))
    def u64(*values): binary.extend(struct.pack("<" + "Q" * len(values), *values))
    def string(value):
        data = str(value).encode()
        if not data or len(data) > 4095 or b"\0" in data:
            raise ValueError("Invalid native control string")
        u32(len(data)); binary.extend(data)

    manifest = state["manifest"]
    u32(2, policy, device, manifest["pipeline"]["device"]["vendor_id"], manifest["pipeline"]["device"]["device_id"])
    binary.extend(bytes.fromhex(state["source_manifest_sha256"]))
    string(freeze(state["shader"])); string(freeze(state["push"]))
    u32(state["push"]["size"], 48, *manifest["dispatch"]["group_count"])
    allocations = list(state["allocations"].values())
    indices = {row["id"]: i for i, row in enumerate(allocations)}
    u32(len(allocations))
    for row in allocations:
        string(row["id"]); u64(row["size"])
        string(freeze(row["initial_artifact"])); string(freeze(row["expected_artifact"]) if row["expected_artifact"] else "-")
    string(freeze(state["table"])); u32(manifest["physical_table"]["entry_count"], len(state["mappings"]))
    for row in state["mappings"]:
        u32(row["index"], indices[row["allocation"]]); u64(row["offset"])
    images = list(state["images"].values()); image_indices = {row["id"]: i for i, row in enumerate(images)}
    u32(len(images))
    for row in images:
        u32(row["format"], row["type"], *row["extent"], row["mip_levels"], row["array_layers"], len(row["subresource_artifacts"]))
        for (mip, layer), artifact in sorted(row["subresource_artifacts"].items()):
            u32(mip, layer); u64(artifact["size"]); string(freeze(artifact))
    image_views = list(state["image_views"].values()); view_indices = {row["id"]: i for i, row in enumerate(image_views)}
    u32(len(image_views))
    for row in image_views:
        u32(image_indices[row["image"]], row["type"], row["format"], *row["components"], row["base_mip"], row["mip_count"], row["base_layer"], row["layer_count"])
    samplers = list(state["samplers"].values()); sampler_indices = {row["id"]: i for i, row in enumerate(samplers)}
    u32(len(samplers))
    for row in samplers: u32(*row["core_words"])
    bindings = manifest["bindings"]
    u32(len(bindings))
    for row in bindings:
        if row["type"] == 1:
            allocation, offset, size = 0, 0, 0
            image_view, sampler = view_indices[row["image_view"]], sampler_indices[row["sampler"]]
            null = 0
        elif row["view"] is None:
            allocation, offset, size, image_view, sampler, null = 0, 0, 0, 0, 0, 1
        else:
            view = state["views"][row["view"]]
            allocation, offset, size = indices[view["allocation"]], view["offset"] + row["offset"], row["range"]
            image_view, sampler, null = 0, 0, 0
        u32(row["binding"], row["element"], row["type"], null, allocation, image_view, sampler); u64(offset, size)
    plan = output / "plan.bin"; plan.write_bytes(binary); plan.chmod(0o444)
    if hashlib.sha256((state["capture"] / "manifest.json").read_bytes()).hexdigest() != state["source_manifest_sha256"]:
        raise ValueError("Manifest changed while freezing its input graph")
    summary = {"schema": "open-ocarina.prepared-physical-replay/v1", "source_manifest_sha256": state["source_manifest_sha256"],
        "plan_sha256": hashlib.sha256(binary).hexdigest(), "policy": policy, "device_index": device,
        "kind": manifest["kind"], "mode": manifest["mode"], "expected_source": manifest["expected_source"],
        "live_snapshot_atomicity": "producer-evidence-only" if manifest["mode"] == "historical-dispatch" else "not-established",
        "event_id": manifest["event"]["id"], "shader_sha256": state["shader"]["sha256"],
        "allocation_count": len(allocations), "allocation_bytes": sum(row["size"] for row in allocations),
        "allocation_graph": [{"id": row["id"], "size": row["size"]} for row in allocations],
        "image_count": len(images), "frozen_artifact_count": len(frozen), "dispatch_executed": False,
        "target_access_certificate": state["target_access_certificate"],
        "producer_completeness_independently_proven": False,
        "limitations": ["Producer evidence is cross-checked for consistency, not independently proven truthful.",
                        "Execution needs separate exact-plan authorization and a GPU lease.",
                        "Owned diagnostic preparation does not prove historical device-state or CPU atomicity equivalence."]}
    (output / "plan.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def normalize(capture, output, spirv, receipt, *, vendor_id, device_id):
    """Normalize the supported disjoint capture into an owned frozen diagnostic.

    This is CPU/file work. It retains the complete raw producer bundle privately,
    recomputes bind independence, and never upgrades historical atomicity claims.
    """
    capture, spirv, receipt = Path(capture).resolve(), Path(spirv).resolve(), Path(receipt).resolve()
    raw = {name: _json(capture / name, maximum=64 * 1024**2) for name in
           ("manifest.json", "allocations.json", "images.json", "dispatch-parameters.json", "binding-provenance.json")}
    proof = source_independence(raw["binding-provenance.json"])
    output = _private_new(output); output.mkdir(parents=True)
    source = output / "source"; source.mkdir()
    # Hard links preserve the read-only source bytes without duplicating gigabytes.
    # No permissions are changed on links; prepare later copies its own frozen data.
    for path in capture.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Regular flat capture artifacts are required")
        os.link(path, source / path.name)
    (source / "launch.json").write_bytes(receipt.read_bytes())

    def reference(path):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"path": path.relative_to(output).as_posix(), "size": path.stat().st_size, "sha256": digest}

    def artifact(name, data):
        path = output / name; path.write_bytes(data)
        return reference(path)

    def snapshot(row, size):
        path = source / row["artifact"]
        result = reference(path)
        if result["size"] != size or result["sha256"] != row["sha256"]:
            raise ValueError("Original payload differs from its captured digest/extent")
        return result

    ledger = raw["binding-provenance.json"]
    objects = {(row["kind"], row["handle"]): row for row in ledger["objects"]}
    buffers = raw["allocations.json"]["buffers"]
    params = raw["dispatch-parameters.json"]
    table_info = raw["manifest.json"]["table"]
    table = snapshot(table_info, table_info["size"])
    pages = struct.unpack(f"<{table_info['entry_count']}Q", (output / table["path"]).read_bytes())
    ordered = sorted((int(row["address"], 16), row) for row in buffers if int(row["address"], 16))
    owners = {}
    import bisect
    starts = [row[0] for row in ordered]
    mappings = []
    for index, address in enumerate(pages):
        if not address:
            continue
        position = bisect.bisect_right(starts, address) - 1
        if position < 0:
            raise ValueError("Table page has no true original buffer owner")
        base, row = ordered[position]
        if address + 4096 > base + row["size"]:
            raise ValueError("Table page exceeds its true original owner")
        owners[row["buffer"]] = row
        identity = "buffer-" + str(row["id"])
        mappings.append({"index": index, "original_address": address, "allocation": identity, "offset": address - base, "view": identity})
    for descriptor in raw["allocations.json"]["descriptors"]:
        if int(descriptor["buffer"], 16):
            selected = [row for row in buffers if row["buffer"] == descriptor["buffer"]]
            if len(selected) != 1:
                raise ValueError("Descriptor owner is missing or ambiguous")
            owners[descriptor["buffer"]] = selected[0]
    allocations, views, buffer_origins, by_handle = [], [], [], {}
    backing = lambda name, size: {"id": name, "generation": 1, "offset": 0, "size": size}
    for row in sorted(owners.values(), key=lambda row: row["id"]):
        obj = objects[("buffer", row["buffer"])]; identity = "buffer-" + str(row["id"])
        canonical = backing("owned-buffer-" + str(obj["lifetime_id"]), row["size"])
        allocations.append({"id": identity, "size": row["size"], "backing": canonical, "initial": snapshot(row["before"], row["size"]), "expected": None})
        views.append({"id": identity, "allocation": identity, "offset": 0, "size": row["size"], "buffer_offset": 0,
                      "buffer_size": row["size"], "device_address": int(row["address"], 16)})
        by_handle[row["buffer"]] = identity
        buffer_origins.append({"view_id": identity, "catalog_id": row["id"], "buffer_lifetime": str(obj["lifetime_id"]),
            "pieces": [{"buffer_offset": 0, "size": row["size"], "backing_id": canonical["id"], "generation": 1,
                        "backing_offset": 0, "memory_lifetime": "owned-memory-" + str(obj["lifetime_id"]), "bind_sequence": obj["created_sequence"]}]})
    bindings = []
    for row in raw["allocations.json"]["descriptors"]:
        view = by_handle.get(row["buffer"])
        bindings.append({"binding": row["binding"], "element": row["element"], "type": row["type"], "view": view,
            "offset": row["offset"] if view else 0, "range": row["range"] if view else 0, "image_view": None, "sampler": None})
    images, image_views, samplers, image_origins = [], [], [], []
    for row in raw["images.json"]["images"]:
        image, view, sampler = row["image"], row["view"], row["sampler"]
        if (image["mip_levels"] != 1 or view["base_mip"] or view["mip_count"] != 1 or view["base_layer"] or view["layer_count"] != image["array_layers"] or
            row["copy"]["mips"][0]["offset"] != 0 or image["format"] not in FORMATS):
            raise ValueError("Normalizer currently supports complete one-mip uncompressed images")
        obj = objects[("image", image["handle"])]; identity = "image-" + str(row["id"])
        view_id, sampler_id = identity + "-view", identity + "-sampler"
        data_ref = snapshot(row["before"], row["copy"]["bytes"])
        data = (output / data_ref["path"]).read_bytes()
        layer_size = image["extent"][0] * image["extent"][1] * FORMATS[image["format"]]
        if len(data) != layer_size * image["array_layers"]:
            raise ValueError("Image payload contains unsupported padding or incomplete layers")
        subresources = [{"mip": 0, "layer": layer, "initial": artifact(identity + f"-layer-{layer}.bin", data[layer * layer_size:(layer + 1) * layer_size])}
                        for layer in range(image["array_layers"])]
        canonical = backing("owned-image-" + str(obj["lifetime_id"]), obj["size"])
        images.append({"id": identity, **{key: image[key] for key in ("format", "type", "extent", "mip_levels", "array_layers")}, "backing": canonical, "subresources": subresources})
        image_views.append({"id": view_id, "image": identity, **{key: view[key] for key in ("type", "format", "components", "base_mip", "mip_count", "base_layer", "layer_count")}})
        samplers.append({"id": sampler_id, "core_words": sampler["core_words"], "pnext": sampler["pnext"]})
        bindings.append({"binding": row["binding"], "element": row["element"], "type": 1, "view": None, "offset": 0, "range": 0, "image_view": view_id, "sampler": sampler_id})
        edge = obj["edges"][0]
        image_origins.append({"image_id": identity, "catalog_id": row["id"], "image_lifetime": str(obj["lifetime_id"]),
            "memory_lifetime": str(edge["memory_lifetime_id"]), "bind_sequence": edge["bind_sequence"], "backing": canonical})
    shader = artifact("target.spv", spirv.read_bytes())
    push = snapshot(params["push_constants"], params["push_constants"]["size"])
    event = {"id": "frozen-recording-" + str(params["recording_sequence"]) + "-occurrence-" + str(params["selected_occurrence"]),
             "recording_sequence": params["recording_sequence"], "selected_occurrence": params["selected_occurrence"], "before_epoch": 1, "after_epoch": 2}
    receipt_document = _json(receipt)
    coverage = {"spirv_sha256": shader["sha256"], "descriptor_slots": sorted([0, row["binding"], row["element"]] for row in bindings),
        "allocation_ids": sorted(row["id"] for row in allocations), "buffer_view_ids": sorted(row["id"] for row in views),
        "image_view_ids": sorted(row["id"] for row in image_views), "push_sha256": push["sha256"], "table_sha256": table["sha256"], "unresolved": []}
    backing_evidence = {"source_revision": "externally-frozen-capture-v1", "source_binary_sha256": receipt_document["renderer_sha256"], "lease_id": "owned-frozen-inputs", "registry_start_sequence": 0,
        "normalization": "disjoint-original-objects-v1", "source_directory": "source", "source_manifests": {name: reference(source / name) for name in (*raw.keys(), "launch.json")},
        "buffer_origins": buffer_origins, "image_origins": image_origins}
    evidence_rows = {"coverage": coverage, "backing": backing_evidence,
        "quiescence": {"status": "historical-atomicity-not-established", "owned_input_state": "immutable-artifacts"},
        "temporal": {"initial_epoch": 1, "external_dependencies": [], "method": "complete-frozen-initial-state"},
        "dispatch": {**{key: value for key, value in event.items() if key != "id"}, "before_position": "immediately-before-dispatch", "after_position": "immediately-after-dispatch",
                     "completed_recording_sequence": params["completed_recording_sequence"]}}
    evidence = {name: artifact(name + ".json", (json.dumps({"schema": f"open-ocarina.physical-{name}/v1", "event_id": event["id"], **row}, sort_keys=True) + "\n").encode())
                for name, row in evidence_rows.items()}
    manifest = {"schema": SCHEMA, "kind": "target", "mode": "frozen-diagnostic", "expected_source": "none", "event": event,
        "pipeline": {"program": params["pipeline"]["program"], "variant": params["pipeline"]["variant"], "spirv": shader, "local_size": params["pipeline"]["local_size"],
                     "push_range_bytes": params["pipeline"]["push_range"]["size"], "root_push_offset": params["push_constants"]["root_push_offset"],
                     "device": {"vendor_id": vendor_id, "device_id": device_id}, "source": None},
        "dispatch": params["dispatch"], "push_constants": push,
        "physical_table": {"snapshot": table, "entry_count": table_info["entry_count"], "page_size": table_info["page_size"], "root_address": int(table_info["root_device_address"], 16), "mappings": mappings},
        "allocations": allocations, "views": views, "bindings": bindings, "images": images, "image_views": image_views, "samplers": samplers, "evidence": evidence}
    (output / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n")
    state = validate(output)
    return {"normalized": True, "dispatch_executed": False, "source_independence": proof, "allocations": len(allocations),
            "allocation_bytes": sum(row["size"] for row in allocations), "target_access_certificate": state["target_access_certificate"]}


def control_identity(data):
    """Read only identity/complete allocation extents from the approved native frame."""
    if data[:8] != MAGIC or len(data) < 60 or struct.unpack_from("<I", data, 8)[0] != 2:
        raise ValueError("Unsupported native plan framing")
    width = struct.unpack_from("<I", data, 12)[0]
    manifest_digest = data[28:60].hex()
    offset = 60

    def string():
        nonlocal offset
        count = struct.unpack_from("<I", data, offset)[0]; offset += 4
        if not 1 <= count <= 4095 or offset + count > len(data) or b"\0" in data[offset:offset + count]:
            raise ValueError("Malformed native plan string")
        result = data[offset:offset + count].decode(); offset += count
        return result

    string(); string(); offset += 20
    count = struct.unpack_from("<I", data, offset)[0]; offset += 4
    if not 1 <= count <= MAX_ALLOCATIONS:
        raise ValueError("Invalid native allocation graph")
    graph = []
    for _ in range(count):
        identity = _identifier(string()); size = struct.unpack_from("<Q", data, offset)[0]; offset += 8
        _integer(size, "native canonical size", 1, MAX_TOTAL_BYTES)
        string(); string(); graph.append({"id": identity, "size": size})
    return width, manifest_digest, graph


def build(output, cc="cc"):
    output = Path(output).resolve()
    if not output.is_file() and not output.parent.is_dir():
        output.parent.mkdir(parents=True)
    if output == ROOT or (output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[0] not in ("local", "work", "artifacts")):
        raise ValueError("Native helper output must remain private")
    if output.exists():
        raise ValueError("Choose a new native binary path; frozen builds are not overwritten")
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"], check=True, capture_output=True, text=True, timeout=15).stdout)
    subprocess.run([cc, "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", str(ROOT / "tools" / "vulkan_physical_replay.c"), *flags, "-o", str(output)],
                   check=True, capture_output=True, text=True, timeout=60)
    return {"binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "flags": ["-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"], "dispatch_executed": False}


def make_fixture(output):
    """Create/compile independently authored input data; never create a Vulkan instance."""
    output = _private_new(output); output.mkdir(parents=True)

    def artifact(name, data):
        (output / name).write_bytes(data)
        return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def evidence(name, value):
        return artifact(name + ".json", (json.dumps(value, sort_keys=True) + "\n").encode())

    source = artifact("fixture.comp", FIXTURE_SOURCE.encode())
    shader = output / "fixture.spv"
    subprocess.run(["glslangValidator", "-V", "--target-env", "vulkan1.3", "-S", "comp", str(output / source["path"]), "-o", str(shader)], check=True, capture_output=True, timeout=30)
    spirv = artifact("fixture.spv", shader.read_bytes())
    input_data = bytearray(b"\xa5" * 4096); initial = bytearray(b"\xc3" * 4096); expected = bytearray(initial)
    for i in range(64):
        struct.pack_into("<I", input_data, 4 * i, 1000 + i); struct.pack_into("<I", initial, 4 * i, 100 + i); struct.pack_into("<I", expected, 4 * i, 1110 + 3 * i)
    push = bytearray(88); struct.pack_into("<Q", push, 48, 0x200000)
    push_artifact = artifact("push.bin", push); table = artifact("table.bin", struct.pack("<Q", 0x100000))
    before = artifact("input.bin", input_data); output_before = artifact("output-before.bin", initial); output_after = artifact("output-after.bin", expected)
    pixels = artifact("pixels.bin", struct.pack("<64I", *(10 + i for i in range(64))))
    event = {"id": "authored-fixture", "recording_sequence": 1, "selected_occurrence": 1, "before_epoch": 1, "after_epoch": 2}
    backing = lambda name, size: {"id": name, "generation": 1, "offset": 0, "size": size}
    allocations = [{"id": "input", "size": 4096, "backing": backing("owned-input", 4096), "initial": before, "expected": before},
                   {"id": "output", "size": 4096, "backing": backing("owned-output", 4096), "initial": output_before, "expected": output_after}]
    views = [{"id": name, "allocation": "output", "offset": 0, "size": 4096, "buffer_offset": 0, "buffer_size": 4096, "device_address": 0} for name in ("write-view", "alias-view")]
    views.append({"id": "physical-input", "allocation": "input", "offset": 0, "size": 4096, "buffer_offset": 0, "buffer_size": 4096, "device_address": 0x100000})
    images = [{"id": "pixels", "format": 98, "type": 1, "extent": [64, 1, 1], "mip_levels": 1, "array_layers": 1,
               "backing": backing("owned-image", 256), "subresources": [{"mip": 0, "layer": 0, "initial": pixels}]}]
    image_views = [{"id": "pixels-view", "image": "pixels", "type": 1, "format": 98, "components": [0, 0, 0, 0], "base_mip": 0, "mip_count": 1, "base_layer": 0, "layer_count": 1}]
    samplers = [{"id": "nearest", "core_words": [0, 0, 0, 0, 2, 2, 2, 0, 0, 0x3f800000, 0, 7, 0, 0, 0, 0], "pnext": []}]
    bindings = [{"binding": i, "element": 0, "type": 7, "view": view, "offset": 0, "range": 4096, "image_view": None, "sampler": None} for i, view in enumerate(("write-view", "alias-view"))]
    bindings.append({"binding": 2, "element": 0, "type": 1, "view": None, "offset": 0, "range": 0, "image_view": "pixels-view", "sampler": "nearest"})
    coverage = {"schema": "open-ocarina.physical-coverage/v1", "event_id": event["id"], "spirv_sha256": spirv["sha256"], "descriptor_slots": [[0, i, 0] for i in range(3)],
                "allocation_ids": ["input", "output"], "buffer_view_ids": sorted(row["id"] for row in views), "image_view_ids": ["pixels-view"], "push_sha256": push_artifact["sha256"], "table_sha256": table["sha256"], "unresolved": []}
    backing_evidence = {"schema": "open-ocarina.physical-backing/v1", "event_id": event["id"], "source_revision": "authored-fixture-v1", "source_binary_sha256": spirv["sha256"], "lease_id": "owned-fixture-lease", "registry_start_sequence": 0,
        "buffer_origins": [{"view_id": row["id"], "buffer_lifetime": "owned-" + row["id"], "pieces": [{"buffer_offset": 0, "size": 4096, "backing_id": "owned-" + row["allocation"], "generation": 1, "backing_offset": 0, "memory_lifetime": "owned-" + row["allocation"] + "-memory", "bind_sequence": 1}]} for row in views],
        "image_origins": [{"image_id": "pixels", "image_lifetime": "owned-pixels", "memory_lifetime": "owned-image-memory", "bind_sequence": 1, "backing": images[0]["backing"]}]}
    quiescence = {"schema": "open-ocarina.physical-quiescence/v1", "event_id": event["id"], "status": "historical-atomicity-not-established", "owned_input_state": "immutable-artifacts"}
    temporal = {"schema": "open-ocarina.physical-temporal/v1", "event_id": event["id"], "initial_epoch": 1, "external_dependencies": [], "method": "complete-frozen-initial-state"}
    dispatch_evidence = {"schema": "open-ocarina.physical-dispatch/v1", "event_id": event["id"], **{k: v for k, v in event.items() if k != "id"}, "before_position": "immediately-before-dispatch", "after_position": "immediately-after-dispatch", "completed_recording_sequence": 1}
    manifest = {"schema": SCHEMA, "kind": "synthetic", "mode": "frozen-diagnostic", "expected_source": "authored-synthetic-reference", "event": event,
        "pipeline": {"program": "0000000000000000", "variant": "0000000000000001", "spirv": spirv, "local_size": [64, 1, 1], "push_range_bytes": 88, "root_push_offset": 48, "device": {"vendor_id": 0, "device_id": 0}, "source": source},
        "dispatch": {"kind": "direct", "group_count": [1, 1, 1]}, "push_constants": push_artifact,
        "physical_table": {"snapshot": table, "entry_count": 1, "page_size": 4096, "root_address": 0x200000, "mappings": [{"index": 0, "original_address": 0x100000, "allocation": "input", "offset": 0, "view": "physical-input"}]},
        "allocations": allocations, "views": views, "bindings": bindings, "images": images, "image_views": image_views, "samplers": samplers,
        "evidence": {name: evidence(name, value) for name, value in (("coverage", coverage), ("backing", backing_evidence), ("quiescence", quiescence), ("temporal", temporal), ("dispatch", dispatch_evidence))}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"schema": SCHEMA, "kind": "synthetic", "fixture_written": True, "shader_sha256": spirv["sha256"], "dispatch_executed": False}


def execute(prepared, output, helper, helper_sha256, driver, authorization, *, alternative_authorization=None, timeout=90):
    """Explicit lease/approval boundary; callers must obtain GPU authorization separately."""
    from tools.compile_probe import driver_identity
    prepared = Path(prepared).resolve(); helper = Path(helper).resolve(); driver = Path(driver).resolve()
    summary = _json(prepared / "plan.json"); control = prepared / "plan.bin"
    digest = hashlib.sha256(control.read_bytes()).hexdigest()
    if summary.get("plan_sha256") != digest or authorization != digest:
        raise ValueError("Exact prepared-plan digest authorization is required")
    inputs = list((prepared / "inputs").iterdir())
    if len(inputs) != summary.get("frozen_artifact_count"):
        raise ValueError("Frozen input graph changed after approval")
    for item in inputs:
        if item.is_symlink() or not item.is_file() or not re.fullmatch(r"[0-9a-f]{64}\.bin", item.name):
            raise ValueError("Unexpected frozen input artifact")
        with item.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != item.stem:
                raise ValueError("Frozen input changed after approval")
    width, source_digest, graph = control_identity(control.read_bytes())
    if summary.get("policy") != width or summary.get("source_manifest_sha256") != source_digest or summary.get("allocation_graph") != graph:
        raise ValueError("Prepared policy identity differs")
    if width != 32 and alternative_authorization != hashlib.sha256(f"alternative:{width}:{digest}".encode()).hexdigest():
        raise ValueError("Alternative subgroup execution needs additional exact-policy authorization")
    if not helper.is_file() or hashlib.sha256(helper.read_bytes()).hexdigest() != helper_sha256:
        raise ValueError("Native helper digest differs from the reviewed build")
    if not 1 <= timeout <= 120:
        raise ValueError("Replay wall timeout must be 1..120 seconds")
    selected_driver = driver_identity(driver)
    output = _private_new(output); output.mkdir(parents=True)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(("HS_", "VK_", "MESA_", "INTEL_")) or key == "shader_spilling_rate":
            environment.pop(key)
    environment.update(VK_DRIVER_FILES=str(driver), MESA_SHADER_CACHE_DIR=str(output / "shader-cache"), HS_SPILL_BATCH="1", shader_spilling_rate="1")
    with (output / "native.json").open("w") as out, (output / "native.stderr").open("w") as err:
        child = subprocess.Popen([str(helper), str(control), str(output), "--execute-approved-plan"], env=environment, stdout=out, stderr=err, start_new_session=True)
        try:
            status = child.wait(timeout=timeout)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            os.killpg(child.pid, signal.SIGTERM)
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL); child.wait()
            raise ValueError("Bounded replay interrupted/timed out; only its process group stopped")
    if status not in (0, 1):
        raise ValueError("Native replay failed; inspect private stderr")
    if driver_identity(driver) != selected_driver:
        raise ValueError("Selected driver changed during the replay trial")
    result = _json(output / "native.json")
    if result.get("dispatch_count") != 1 or result.get("actual_subgroup_size") != width or result.get("source_manifest_sha256") != source_digest or len(result.get("allocations", [])) != len(graph):
        raise ValueError("Native dispatch or subgroup identity differs from approved plan")
    artifacts = []
    for index, row in enumerate(result["allocations"]):
        if row.get("index") != index or row.get("size") != graph[index]["size"]:
            raise ValueError("Native output canonical extent differs from approved plan")
        path = output / f"allocation-{row['index']}.bin"
        if path.stat().st_size != row["size"]:
            raise ValueError("Native output omitted part of a canonical allocation")
        with path.open("rb") as source: sha = hashlib.file_digest(source, "sha256").hexdigest()
        artifacts.append({"path": path.name, "size": row["size"], "sha256": sha})
    report = {"schema": "open-ocarina.physical-replay-result/v1", "plan_sha256": digest, "source_manifest_sha256": summary["source_manifest_sha256"],
        "native_binary_sha256": helper_sha256, "driver": selected_driver, "policy": width, "dispatch_executed": True,
        "mode": summary["mode"], "expected_source": summary["expected_source"], "allocation_graph": graph, "allocations": artifacts, "native": result,
        "captured_after_agreement": result["comparison_passed"] if summary["expected_source"] == "captured-after" else None,
        "baseline_reproducibility": "not-established-by-one-dispatch", "live_snapshot_atomicity": summary["live_snapshot_atomicity"],
        "limitations": ["A frozen diagnostic does not establish atomicity or equivalence of a historical live dispatch.", "No rendering or performance claim follows from one dispatch."]}
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def compare(left, right):
    left, right = Path(left).resolve(), Path(right).resolve()
    a, b = _json(left / "result.json"), _json(right / "result.json")
    if any(row.get("schema") != "open-ocarina.physical-replay-result/v1" or row.get("dispatch_executed") is not True for row in (a, b)):
        raise ValueError("Comparison requires actual owned replay result records")
    if a.get("driver", {}).get("library_sha256") != b.get("driver", {}).get("library_sha256") or a.get("native_binary_sha256") != b.get("native_binary_sha256"):
        raise ValueError("Replay implementation or driver differs; compare only explicit policy variants")
    for key in ("device_uuid", "driver_uuid", "device_vendor", "device_id"):
        if not a.get("native", {}).get(key) or a["native"][key] != b.get("native", {}).get(key):
            raise ValueError("Replay devices or driver identities differ")
    graph = a.get("allocation_graph")
    if not graph or graph != b.get("allocation_graph") or a.get("source_manifest_sha256") != b.get("source_manifest_sha256") or len(a.get("allocations", [])) != len(graph) or len(b.get("allocations", [])) != len(graph):
        raise ValueError("Replay results do not share a complete frozen input graph")
    differences = []
    for index, (x, y) in enumerate(zip(a["allocations"], b["allocations"])):
        if x.get("path") != f"allocation-{index}.bin" or y.get("path") != f"allocation-{index}.bin":
            raise ValueError("Output allocation order or identity differs")
        px, py = _snapshot(left, x, graph[index]["size"]), _snapshot(right, y, graph[index]["size"])
        if x["size"] != y["size"]:
            raise ValueError("Canonical output extents differ")
        if x["sha256"] != y["sha256"]:
            offset = 0
            with px.open("rb") as fx, py.open("rb") as fy:
                while block := fx.read(1024 * 1024):
                    other = fy.read(len(block))
                    if block != other:
                        differences.append({"allocation_index": index, "first_difference": offset + next(i for i, pair in enumerate(zip(block, other)) if pair[0] != pair[1])}); break
                    offset += len(block)
    return {"owned_frozen_outputs_match": not differences, "differences": differences, "compared_complete_allocations": len(a["allocations"]),
            "policies": [a.get("policy"), b.get("policy")], "historical_live_equivalence_established": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("capture", type=Path); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--policy", type=int, choices=(8, 16, 32), default=32); p.add_argument("--device", type=int, default=0)
    p = sub.add_parser("normalize"); p.add_argument("capture", type=Path); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--spirv", type=Path, required=True); p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--vendor-id", type=lambda value: int(value, 0), required=True); p.add_argument("--device-id", type=lambda value: int(value, 0), required=True)
    p = sub.add_parser("build"); p.add_argument("--output", type=Path, required=True); p.add_argument("--cc", default="cc")
    p = sub.add_parser("fixture"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("execute"); p.add_argument("prepared", type=Path); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--helper", type=Path, required=True); p.add_argument("--helper-sha256", required=True); p.add_argument("--driver", type=Path, required=True)
    p.add_argument("--authorize-plan", required=True); p.add_argument("--authorize-alternative"); p.add_argument("--timeout", type=int, default=90)
    p = sub.add_parser("compare"); p.add_argument("left", type=Path); p.add_argument("right", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare": result = prepare(args.capture, args.output, policy=args.policy, device=args.device)
        elif args.command == "normalize": result = normalize(args.capture, args.output, args.spirv, args.receipt, vendor_id=args.vendor_id, device_id=args.device_id)
        elif args.command == "build": result = build(args.output, args.cc)
        elif args.command == "fixture": result = make_fixture(args.output)
        elif args.command == "compare": result = compare(args.left, args.right)
        else: result = execute(args.prepared, args.output, args.helper, args.helper_sha256, args.driver, args.authorize_plan, alternative_authorization=args.authorize_alternative, timeout=args.timeout)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, struct.error, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
