#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate private logical-buffer capture contracts and compare complete output allocations.

This is not a capture producer, Vulkan replay executor, or proof that a producer
enumerated every dependency. Physical-pointer and external temporal state are
deliberately unsupported by the first contract.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import sys

SCHEMA = "open-ocarina.compute-capture/v1"
MAX_MANIFEST = 2 * 1024 * 1024
MAX_SPIRV = 64 * 1024 * 1024
MAX_SNAPSHOT = 1024 * 1024 * 1024
MAX_VIEWS = 4096
MAX_DESCRIPTORS = 65536
SHA256 = re.compile(r"[0-9a-f]{64}")
KEY = re.compile(r"[0-9a-f]{16}")
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Missing, unknown or invalid contract fields")
    return value


def _integer(value, name, minimum=0, maximum=(1 << 64) - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("Invalid identifier")
    return value


def _triple(values, name):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError(f"{name} must contain three dimensions")
    return [_integer(value, name, 1, (1 << 32) - 1) for value in values]


def _json(path, maximum=MAX_MANIFEST):
    if path.stat().st_size > maximum:
        raise ValueError("JSON evidence exceeds size limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def no_constant(value):
        raise ValueError(f"Non-finite JSON constant is forbidden: {value}")

    return json.loads(path.read_text(), object_pairs_hook=unique, parse_constant=no_constant)


def _path(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or
            any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("Snapshot path must be a normalized relative path")
    relative = PurePosixPath(name)
    if relative.is_absolute():
        raise ValueError("Absolute snapshot paths are forbidden")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlinked snapshot paths are forbidden")
    if not current.is_file():
        raise ValueError("Snapshot is missing or not a regular file")
    return current


def _snapshot(root, value, expected_size=None, maximum=MAX_SNAPSHOT):
    _object(value, ("path", "size", "sha256"))
    size = _integer(value["size"], "snapshot size", 0, maximum)
    if expected_size is not None and size != expected_size:
        raise ValueError("Snapshot must cover the complete declared allocation/range")
    if not isinstance(value["sha256"], str) or not SHA256.fullmatch(value["sha256"]):
        raise ValueError("Snapshot requires a complete lowercase SHA-256 digest")
    path = _path(root, value["path"])
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        if before.st_size != size:
            raise ValueError("Snapshot length differs from the declared complete length")
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Snapshot changed while being validated")
    if digest != value["sha256"]:
        raise ValueError("Snapshot SHA-256 mismatch")
    return path


def _spirv(path, entry_point, local_size):
    """Check framing and independently refuse physical pointers; not spirv-val."""
    data = path.read_bytes()
    if len(data) < 20 or len(data) % 4:
        raise ValueError("Invalid SPIR-V framing")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    if (words[0] != 0x07230203 or words[1] not in range(0x00010000, 0x00010700, 0x100)
            or words[4] != 0 or not 0 < words[3] <= (1 << 24)):
        raise ValueError("Invalid SPIR-V header")
    counts = Counter()
    entries = {}
    sizes = {}
    decorations = {}
    cursor = 5
    while cursor < len(words):
        count, opcode = words[cursor] >> 16, words[cursor] & 0xFFFF
        if count == 0 or cursor + count > len(words):
            raise ValueError("Truncated or zero-length SPIR-V instruction")
        operands = words[cursor + 1:cursor + count]
        counts[opcode] += 1
        if opcode == 120 or (opcode == 32 and len(operands) >= 3 and operands[1] == 5349):
            raise ValueError("Physical-storage pointer use/types are unsupported: capture reachable page tables and address targets first")
        if opcode == 14 and (len(operands) != 2 or operands[0] != 0):
            raise ValueError("Only the logical SPIR-V addressing model is supported by this contract")
        if opcode == 15:
            if len(operands) < 3:
                raise ValueError("Malformed SPIR-V entry point")
            encoded = struct.pack(f"<{len(operands) - 2}I", *operands[2:])
            if b"\0" not in encoded:
                raise ValueError("Unterminated SPIR-V entry point")
            name = encoded.split(b"\0", 1)[0].decode("utf-8")
            if operands[0] == 5:
                if name in entries:
                    raise ValueError("Duplicate compute entry point name")
                entries[name] = operands[1]
        if opcode == 16 and len(operands) >= 2 and operands[1] == 17:
            if len(operands) != 5 or operands[0] in sizes:
                raise ValueError("Malformed or duplicate SPIR-V local size")
            sizes[operands[0]] = list(operands[2:])
        if opcode == 331 and len(operands) >= 2 and operands[1] == 38:
            raise ValueError("Specialized LocalSizeId is unsupported by this first contract")
        if opcode == 71 and len(operands) >= 2 and operands[1] in (33, 34):
            if len(operands) != 3 or operands[1] in decorations.setdefault(operands[0], {}):
                raise ValueError("Malformed or duplicate descriptor decoration")
            decorations[operands[0]][operands[1]] = operands[2]
        cursor += count
    if entry_point not in entries:
        raise ValueError("Requested compute entry point is absent")
    if sizes.get(entries[entry_point]) != local_size:
        raise ValueError("SPIR-V local size differs from the declared pipeline")
    declared_bindings = []
    for decoration in decorations.values():
        if set(decoration) != {33, 34}:
            raise ValueError("Incomplete descriptor set/binding decoration")
        declared_bindings.append([decoration[34], decoration[33]])
    return {"instruction_count": sum(counts.values()), "physical_pointer_operations": 0,
            "declared_descriptor_bindings": sorted(set(tuple(pair) for pair in declared_bindings)),
            "scope": "framing, entry identity and conservative pointer rejection; not full SPIR-V validation"}


def _range(offset, size, limit):
    _integer(offset, "buffer offset")
    _integer(size, "buffer range", 1)
    if offset + size > limit:
        raise ValueError("Buffer view extends beyond its allocation/resource")


def _validate(root, document):
    _object(document, ("schema", "producer", "pipeline", "dispatch", "allocations", "resources", "bindings",
                       "aliases", "push_constants", "address_model", "temporal", "synchronization"))
    if document["schema"] != SCHEMA:
        raise ValueError("Unsupported capture schema")
    producer = _object(document["producer"], ("name", "revision", "stage", "coverage_evidence"))
    _identifier(producer["name"])
    if not isinstance(producer["revision"], str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", producer["revision"]):
        raise ValueError("Producer revision must be pinned")
    if producer["stage"] != "complete-buffer-capture":
        raise ValueError("Producer capture is incomplete; identity-only events cannot be replayed")
    coverage_path = _snapshot(root, producer["coverage_evidence"], maximum=MAX_MANIFEST)
    coverage = _object(_json(coverage_path), ("schema", "spirv_sha256", "method", "descriptor_slots", "resource_ids", "unresolved_accesses"))
    if coverage["schema"] != "open-ocarina.buffer-coverage/v1" or not isinstance(coverage["method"], str) or not coverage["method"].strip():
        raise ValueError("Unsupported or unexplained producer coverage evidence")
    if coverage["unresolved_accesses"] != []:
        raise ValueError("Unresolved resource reachability cannot be omitted")
    pipeline = _object(document["pipeline"], ("program", "variant", "spirv", "entry_point", "local_size", "specialization",
                                             "subgroup", "layout", "translator_stamp", "cache_stamp", "device", "float_controls"))
    for field in ("program", "variant", "translator_stamp", "cache_stamp"):
        if not isinstance(pipeline[field], str) or not KEY.fullmatch(pipeline[field]):
            raise ValueError(f"Invalid pipeline {field}")
    _identifier(pipeline["entry_point"])
    _triple(pipeline["local_size"], "local size")
    spirv_path = _snapshot(root, pipeline["spirv"], maximum=MAX_SPIRV)
    spirv_metadata = _spirv(spirv_path, pipeline["entry_point"], pipeline["local_size"])
    if coverage["spirv_sha256"] != pipeline["spirv"]["sha256"]:
        raise ValueError("Coverage evidence belongs to another shader")
    subgroup = _object(pipeline["subgroup"], ("required_size", "require_full_subgroups"))
    if subgroup["required_size"] is not None:
        size = _integer(subgroup["required_size"], "subgroup size", 1, 128)
        if size & (size - 1):
            raise ValueError("Subgroup size must be a power of two")
    if type(subgroup["require_full_subgroups"]) is not bool:
        raise ValueError("Full subgroup policy must be boolean")
    device = _object(pipeline["device"], ("vendor_id", "device_id", "driver_version", "api_version",
                                        "driver_uuid", "device_uuid", "enabled_features", "enabled_extensions"))
    for name in ("vendor_id", "device_id", "driver_version", "api_version"):
        _integer(device[name], "device identity", 0, (1 << 32) - 1)
    for name in ("driver_uuid", "device_uuid"):
        if not isinstance(device[name], str) or not re.fullmatch(r"[0-9a-f]{32}", device[name]):
            raise ValueError("Complete device and driver UUIDs are required")
    for name in ("enabled_features", "enabled_extensions"):
        values = device[name]
        if not isinstance(values, list) or any(not isinstance(value, str) or not ID.fullmatch(value) for value in values) or len(set(values)) != len(values):
            raise ValueError("Enabled device features/extensions must be explicit unique names")
    if not isinstance(pipeline["float_controls"], dict) or not pipeline["float_controls"]:
        raise ValueError("Floating-point controls must be explicit")
    if not isinstance(pipeline["specialization"], list):
        raise ValueError("Specialization must be an explicit list")
    specialization_ids = set()
    for item in pipeline["specialization"]:
        _object(item, ("id", "value_hex"))
        ident = _integer(item["id"], "specialization ID", 0, (1 << 32) - 1)
        if ident in specialization_ids or not isinstance(item["value_hex"], str) or not re.fullmatch(r"(?:[0-9a-f]{2}){1,8}", item["value_hex"]):
            raise ValueError("Duplicate or malformed specialization constant")
        specialization_ids.add(ident)
    dispatch = _object(document["dispatch"], ("kind", "group_count", "present", "submission", "ordinal", "event_id"))
    if dispatch["kind"] != "direct":
        raise ValueError("Indirect dispatch capture is unsupported until argument provenance is captured")
    _triple(dispatch["group_count"], "dispatch group count")
    for name in ("present", "submission", "ordinal"):
        _integer(dispatch[name], name)
    _identifier(dispatch["event_id"])
    address = _object(document["address_model"], ("kind", "evidence"))
    if address["kind"] != "logical-buffers":
        raise ValueError("Physical-address capture/replay is unsupported")
    if not isinstance(address["evidence"], str) or not address["evidence"].strip():
        raise ValueError("Address-model evidence is required; a boolean is insufficient")
    temporal = _object(document["temporal"], ("model", "external_dependencies", "evidence"))
    if temporal["model"] != "self-contained-dispatch" or temporal["external_dependencies"] != []:
        raise ValueError("External temporal dependencies are unsupported")
    if not isinstance(temporal["evidence"], str) or not temporal["evidence"].strip():
        raise ValueError("Temporal evidence is required; a boolean is insufficient")
    synchronization = _object(document["synchronization"], ("before_snapshot", "after_snapshot"))
    for phase, value in synchronization.items():
        _object(value, ("dispatch_event", "method", "evidence"))
        if value["dispatch_event"] != dispatch["event_id"] or value["method"] != "gpu-visible-command-position":
            raise ValueError("Snapshots must be synchronized at the selected GPU-visible command position")
        if not isinstance(value["evidence"], str) or not value["evidence"].strip():
            raise ValueError("Snapshot synchronization evidence is required")
    allocations = {}
    if not isinstance(document["allocations"], list) or not 1 <= len(document["allocations"]) <= MAX_VIEWS:
        raise ValueError("Complete memory allocation snapshots are required")
    for allocation in document["allocations"]:
        _object(allocation, ("id", "size", "initial"), ("expected",))
        ident = _identifier(allocation["id"])
        if ident in allocations:
            raise ValueError("Duplicate allocation identity")
        size = _integer(allocation["size"], "allocation size", 1, MAX_SNAPSHOT)
        _snapshot(root, allocation["initial"], size)
        if "expected" in allocation:
            _snapshot(root, allocation["expected"], size)
        allocations[ident] = allocation
    resources = {}
    written_allocations = set()
    used_allocations = set()
    if not isinstance(document["resources"], list) or not 1 <= len(document["resources"]) <= MAX_VIEWS:
        raise ValueError("Resource views are required")
    for resource in document["resources"]:
        _object(resource, ("id", "kind", "allocation", "offset", "size", "access", "role", "comparison"))
        ident = _identifier(resource["id"])
        if ident in resources or resource["allocation"] not in allocations:
            raise ValueError("Duplicate resource or missing backing allocation")
        if resource["kind"] != "buffer":
            raise ValueError("Image capture is unsupported by the buffer-only contract")
        if resource["access"] not in ("read", "write", "read_write") or resource["role"] not in ("data", "counter", "indirect-argument", "uniform"):
            raise ValueError("Unsupported resource access/role")
        if resource["comparison"] != "byte_exact":
            raise ValueError("Only byte-exact comparison is supported; floating tolerances require a separate justified contract")
        _range(resource["offset"], resource["size"], allocations[resource["allocation"]]["size"])
        used_allocations.add(resource["allocation"])
        if resource["access"] != "read":
            written_allocations.add(resource["allocation"])
            if "expected" not in allocations[resource["allocation"]]:
                raise ValueError("Every written allocation requires a complete expected snapshot")
        resources[ident] = resource
    if used_allocations != allocations.keys():
        raise ValueError("Unclassified allocations cannot establish complete resource coverage")
    if not written_allocations:
        raise ValueError("At least one complete writable allocation is required for an output oracle")
    actual_aliases = set()
    for index, left in enumerate(document["resources"]):
        for right in document["resources"][index + 1:]:
            if left["allocation"] == right["allocation"] and max(left["offset"], right["offset"]) < min(left["offset"] + left["size"], right["offset"] + right["size"]):
                actual_aliases.add(tuple(sorted((left["id"], right["id"]))))
    if not isinstance(document["aliases"], list):
        raise ValueError("Aliases must be explicit")
    declared_aliases = set()
    for pair in document["aliases"]:
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(value, str) and value in resources for value in pair) or pair[0] == pair[1]:
            raise ValueError("Invalid alias relationship")
        key = tuple(sorted(pair))
        if key in declared_aliases:
            raise ValueError("Duplicate alias relationship")
        declared_aliases.add(key)
    if declared_aliases != actual_aliases:
        raise ValueError("Declared aliases do not match overlapping resource views")
    expected_slots = set()
    if not isinstance(pipeline["layout"], list) or not pipeline["layout"]:
        raise ValueError("Pipeline descriptor layout is required")
    layout_keys = set()
    for binding in pipeline["layout"]:
        _object(binding, ("set", "binding", "descriptor_type", "count"))
        descriptor_set = _integer(binding["set"], "descriptor set", 0, 31)
        number = _integer(binding["binding"], "binding number", 0, (1 << 32) - 1)
        if (descriptor_set, number) in layout_keys or binding["descriptor_type"] not in ("storage_buffer", "uniform_buffer"):
            raise ValueError("Duplicate or unsupported descriptor binding")
        layout_keys.add((descriptor_set, number))
        count = _integer(binding["count"], "descriptor count", 1, 65536)
        if len(expected_slots) + count > MAX_DESCRIPTORS:
            raise ValueError("Descriptor layout exceeds the bounded contract limit")
        expected_slots.update((descriptor_set, number, index) for index in range(count))
    if not set(map(tuple, spirv_metadata["declared_descriptor_bindings"])) <= layout_keys:
        raise ValueError("SPIR-V declares a descriptor absent from the pipeline layout")
    slots = set()
    bound_resources = set()
    if not isinstance(document["bindings"], list):
        raise ValueError("Descriptor bindings must be explicit")
    for binding in document["bindings"]:
        _object(binding, ("set", "binding", "element", "resource", "offset", "size"))
        slot = tuple(_integer(binding[name], name, 0, (1 << 32) - 1) for name in ("set", "binding", "element"))
        if slot in slots or slot not in expected_slots or binding["resource"] not in resources:
            raise ValueError("Duplicate, unexpected or unresolved descriptor slot")
        _range(binding["offset"], binding["size"], resources[binding["resource"]]["size"])
        slots.add(slot)
        bound_resources.add(binding["resource"])
    if slots != expected_slots or bound_resources != resources.keys():
        raise ValueError("Missing descriptor array elements or unbound resources")
    if (not isinstance(coverage["descriptor_slots"], list) or
            sorted(coverage["descriptor_slots"]) != [list(slot) for slot in sorted(slots)] or
            not isinstance(coverage["resource_ids"], list) or sorted(coverage["resource_ids"]) != sorted(resources)):
        raise ValueError("Producer coverage evidence disagrees with the descriptor/resource contract")
    push = document["push_constants"]
    _snapshot(root, push, maximum=4096)
    return {"document": document, "root": root, "allocations": allocations,
            "written_allocations": written_allocations, "spirv_metadata": spirv_metadata}


def load_capture(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Capture root must be an ordinary directory")
    root = directory.resolve()
    manifest = _path(root, "manifest.json")
    try:
        return _validate(root, _json(manifest))
    except (TypeError, KeyError, IndexError) as exc:
        raise ValueError("Invalid nested contract field type") from exc


def validate_capture(directory):
    capture = load_capture(directory)
    document = capture["document"]
    return {"schema": SCHEMA, "manifest_consistent": True,
            "program": document["pipeline"]["program"], "variant": document["pipeline"]["variant"],
            "spirv_sha256": document["pipeline"]["spirv"]["sha256"],
            "allocations": len(capture["allocations"]), "written_allocations": len(capture["written_allocations"]),
            "resource_views": len(document["resources"]), "spirv_checks": capture["spirv_metadata"],
            "producer_completeness_independently_proven": False, "replay_implemented": False,
            "scope": "Declared buffer contract consistency and artifact integrity only; producer reachability, synchronization and temporal claims require independent review."}


def _input_contract(capture):
    document = capture["document"]
    pipeline = dict(document["pipeline"])
    for name in ("spirv", "subgroup", "translator_stamp", "cache_stamp"):
        pipeline.pop(name)
    pipeline["layout"] = sorted(pipeline["layout"], key=lambda item: (item["set"], item["binding"]))
    pipeline["specialization"] = sorted(pipeline["specialization"], key=lambda item: item["id"])
    pipeline["device"] = {**pipeline["device"],
                          "enabled_features": sorted(pipeline["device"]["enabled_features"]),
                          "enabled_extensions": sorted(pipeline["device"]["enabled_extensions"])}
    return {"pipeline": pipeline, "dispatch": {name: document["dispatch"][name] for name in ("kind", "group_count")},
            "allocations": [{"id": ident, "size": allocation["size"], "initial_sha256": allocation["initial"]["sha256"]}
                            for ident, allocation in sorted(capture["allocations"].items())],
            "resources": sorted(document["resources"], key=lambda item: item["id"]),
            "bindings": sorted(document["bindings"], key=lambda item: (item["set"], item["binding"], item["element"])),
            "aliases": sorted(sorted(pair) for pair in document["aliases"]),
            "push_constants": {name: document["push_constants"][name] for name in ("size", "sha256")},
            "address_model": document["address_model"]["kind"], "temporal_model": document["temporal"]["model"]}


def compare_outputs(reference_dir, candidate_dir):
    reference, candidate = load_capture(reference_dir), load_capture(candidate_dir)
    contract = _input_contract(reference)
    if contract != _input_contract(candidate):
        raise ValueError("Input/resource contracts differ; output comparison is not an equivalence experiment")
    differences = []
    compared = 0
    for ident in sorted(reference["written_allocations"]):
        left = reference["allocations"][ident]["expected"]
        right = candidate["allocations"][ident]["expected"]
        compared += left["size"]
        with _path(reference["root"], left["path"]).open("rb") as first, _path(candidate["root"], right["path"]).open("rb") as second:
            offset = 0
            first_difference = None
            left_hash, right_hash = hashlib.sha256(), hashlib.sha256()
            while offset < left["size"]:
                count = min(1024 * 1024, left["size"] - offset)
                before, after = first.read(count), second.read(count)
                if len(before) != count or len(after) != count:
                    raise ValueError("Snapshot changed during output comparison")
                left_hash.update(before)
                right_hash.update(after)
                if before != after and first_difference is None:
                    difference = next(index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1])
                    first_difference = offset + difference
                offset += count
            if left_hash.hexdigest() != left["sha256"] or right_hash.hexdigest() != right["sha256"] or first.read(1) or second.read(1):
                raise ValueError("Output snapshots changed after integrity checks")
            if first_difference is not None:
                differences.append({"allocation": ident, "first_difference": first_difference})
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"outputs_byte_equal": not differences, "compared_bytes": compared, "differences": differences,
            "input_contract_sha256": digest, "reference_spirv_sha256": reference["document"]["pipeline"]["spirv"]["sha256"],
            "candidate_spirv_sha256": candidate["document"]["pipeline"]["spirv"]["sha256"],
            "reference_subgroup": reference["document"]["pipeline"]["subgroup"],
            "candidate_subgroup": candidate["document"]["pipeline"]["subgroup"],
            "producer_completeness_independently_proven": False,
            "scope": "Complete declared writable allocations compared byte-exactly; not a proof of capture completeness, shader semantics, gameplay or performance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("capture", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("reference", type=Path)
    compare.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        result = validate_capture(args.capture) if args.command == "validate" else compare_outputs(args.reference, args.candidate)
        print(json.dumps(result, indent=2))
        return 1 if result.get("outputs_byte_equal") is False else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
