#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate evidence-backed native-frame benchmark compatibility, without inferring speedups."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
MODULES = {"main", "sdk", "subsdk0", "rtld"}
CHANGE_KEYS = {"renderer_revision", "driver_sha256", "shader_policy_sha256"}
LIMIT = 8 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def digest(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def artifact(root, reference):
    """Read one hash-pinned local metadata artifact; no symlinks or escaping paths."""
    require(isinstance(reference, dict) and set(reference) == {"path", "sha256"},
            "Artifact needs exactly path and sha256")
    name = reference["path"]
    require(isinstance(name, str) and name and "\\" not in name, "Invalid artifact path")
    relative = Path(name)
    require(not relative.is_absolute() and not set(relative.parts) & {"..", ".git"},
            "Artifact path must remain within the capture directory")
    root = root.resolve()
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), "Artifact paths must not contain symlinks")
    require(current.resolve().is_relative_to(root) and current.is_file(), "Missing artifact file")
    require(current.stat().st_size <= LIMIT, "Metadata artifact exceeds size limit")
    data = current.read_bytes()
    require(digest(reference["sha256"]) and hashlib.sha256(data).hexdigest() == reference["sha256"],
            "Artifact SHA-256 mismatch")
    return json.loads(data)


def validate_run(manifest, capture_root, trusted_producers=()):
    """Require externally audited producer identity; schema checks do not audit the producer."""
    require(isinstance(manifest, dict) and type(manifest.get("schema")) is int and manifest["schema"] == 1,
            "Unsupported benchmark schema")
    require(manifest.get("purpose") == "timing", "Attribution captures cannot supply normal frame timings")
    require(manifest.get("instrumentation") == [], "Timing capture must exclude attribution instrumentation")
    identity = manifest.get("identity", {})
    require(isinstance(identity, dict), "Missing identity")
    modules = identity.get("game_modules_sha256", {})
    require(isinstance(modules, dict) and set(modules) == MODULES and all(map(digest, modules.values())),
            "Pin all four input module hashes")
    require(isinstance(identity.get("renderer_revision"), str) and REVISION.fullmatch(identity["renderer_revision"]),
            "Pin a complete renderer revision")
    for key in ("driver_sha256", "shader_policy_sha256", "save_sha256", "settings_sha256", "input_sha256"):
        require(digest(identity.get(key)), "Missing pinned identity: " + key)
    require(isinstance(identity.get("device_uuid"), str) and re.fullmatch(r"[0-9a-f]{32}", identity["device_uuid"]),
            "Pin the Vulkan device UUID")
    identity_sha256 = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    producer = manifest.get("producer", {})
    require(isinstance(producer, dict) and producer.get("contract") == "open-ocarina-benchmark-v1",
            "A benchmark evidence producer is required; upstream input scripts alone do not produce camera evidence")
    require(digest(producer.get("sha256")) and producer["sha256"] in trusted_producers,
            "Producer is not externally reviewed/trusted; no camera-capture producer is bundled yet")
    require(manifest.get("native_frames_only") is True and manifest.get("frame_generation") is False,
            "Native frame timing must exclude frame generation")
    require(manifest.get("cache_warm") is True and integer(manifest.get("warmup_native_frames"), 1),
            "Record completed native-frame warmup")
    samples = manifest.get("samples")
    require(isinstance(samples, list) and len(samples) >= 3, "At least three independent repetitions are required")
    normalized = []
    run_ids = set()
    for sample in samples:
        require(isinstance(sample, dict), "Invalid repetition")
        run_id = sample.get("run_id")
        require(isinstance(run_id, str) and run_id and run_id not in run_ids, "Repetition run IDs must be unique")
        run_ids.add(run_id)
        frames = sample.get("native_frames")
        require(integer(frames, 1) and frames <= 100000, "Invalid native frame count")
        require(integer(sample.get("elapsed_ns"), 1), "Elapsed time must be a positive integer, not FPS text")
        evidence = artifact(capture_root, sample.get("evidence"))
        require(isinstance(evidence, dict) and type(evidence.get("schema")) is int and evidence["schema"] == 1,
                "Invalid repetition evidence")
        require(evidence.get("producer_sha256") == producer["sha256"] and evidence.get("run_id") == run_id,
                "Evidence producer/run identity disagrees")
        require(evidence.get("identity_sha256") == identity_sha256,
                "Pinned identities are not backed by repetition evidence")
        require(integer(evidence.get("native_frames"), 1) and integer(evidence.get("elapsed_ns"), 1)
                and evidence["native_frames"] == frames and evidence["elapsed_ns"] == sample["elapsed_ns"],
                "Timing is not backed by the same evidence")
        require(integer(evidence.get("warmup_native_frames"), 1)
                and evidence["warmup_native_frames"] == manifest["warmup_native_frames"]
                and evidence.get("cache_warm") is True and evidence.get("instrumentation") == [],
                "Warmup/cache/instrumentation claims are not backed by repetition evidence")
        require(evidence.get("live_input_isolated") is True and evidence.get("competing_gpu_processes") == [],
                "Live input or competing GPU use invalidates the sample")
        require(evidence.get("generated_frames") == 0 and type(evidence.get("generated_frames")) is int,
                "Generated frames cannot enter native timing")
        rows = evidence.get("frames")
        require(isinstance(rows, list) and len(rows) == frames, "Missing per-native-frame camera/workload evidence")
        signatures = []
        for index, row in enumerate(rows):
            require(isinstance(row, dict) and row.get("sample_frame") == index and type(row.get("sample_frame")) is int,
                    "Frame evidence must be consecutive and relative to the selected window")
            for key in ("camera_sha256", "scene_state_sha256", "logical_workload_sha256"):
                require(digest(row.get(key)), "Missing observed frame identity: " + key)
            for key in ("pending_shader_compiles", "skipped_draws", "skipped_dispatches"):
                require(type(row.get(key)) is int and row[key] == 0, "Incomplete work: " + key)
            require(row.get("correctness_checked") is True, "Rendering correctness has not been checked")
            signatures.append(tuple(row[key] for key in ("camera_sha256", "scene_state_sha256", "logical_workload_sha256")))
        normalized.append({"run_id": run_id, "native_frames": frames, "elapsed_ns": sample["elapsed_ns"],
                           "frame_signatures": signatures})
    require(all(sample["frame_signatures"] == normalized[0]["frame_signatures"] for sample in normalized[1:]),
            "Baseline repetitions disagree on camera, scene state or logical workload")
    return {"identity": identity, "producer": producer, "samples": normalized,
            "warmup_native_frames": manifest["warmup_native_frames"]}


def compare(baseline, candidate, baseline_root, candidate_root, allowed_change, trusted_producers=()):
    require(allowed_change in CHANGE_KEYS, "Choose one supported experimental change")
    left = validate_run(baseline, baseline_root, trusted_producers)
    right = validate_run(candidate, candidate_root, trusted_producers)
    require(set(left["identity"]) == set(right["identity"]), "Identity fields differ")
    changed = {key for key in left["identity"] if left["identity"][key] != right["identity"][key]}
    require(changed <= {allowed_change}, "Uncontrolled identity changes: " + ", ".join(sorted(changed - {allowed_change})))
    require(left["producer"] == right["producer"], "Evidence producer differs")
    require(left["warmup_native_frames"] == right["warmup_native_frames"], "Warmup differs")
    require(left["samples"][0]["frame_signatures"] == right["samples"][0]["frame_signatures"],
            "Same save is insufficient: observed camera, scene state or logical workload differs")
    require(not ({sample["run_id"] for sample in left["samples"]} &
                 {sample["run_id"] for sample in right["samples"]}), "Do not reuse the same repetition in both conditions")
    return {"schema": 1, "contract_compatible": True, "changed_identity_fields": sorted(changed),
            "baseline_repetitions": len(left["samples"]), "candidate_repetitions": len(right["samples"]),
            "speedup_inferred": False,
            "limitations": ["Producer trust is supplied by the operator and is not established by hashes or schema alone.",
                            "This tool validates evidence compatibility, not unseen rendering coverage or a statistical speedup.",
                            "No audited camera/workload evidence producer is bundled with this validator."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--change", choices=sorted(CHANGE_KEYS), required=True)
    parser.add_argument("--trust-producer-sha256", action="append", default=[],
                        help="Explicit digest of an externally audited evidence producer; none trusted by default")
    args = parser.parse_args()
    try:
        for path in (args.baseline, args.candidate):
            require(path.is_file() and not path.is_symlink() and path.stat().st_size <= LIMIT, "Invalid manifest file")
        result = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()),
                         args.baseline.parent, args.candidate.parent, args.change, args.trust_producer_sha256)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
