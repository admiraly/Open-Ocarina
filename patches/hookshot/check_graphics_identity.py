#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check native mesh identity metadata without Vulkan execution or a game input."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / "graphics-identity.patch"
PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
BASE = ("compute-identity.patch", "page-table-snapshot.patch", "allocation-capture.patch")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def header_from_patch(text=None):
    text = PATCH.read_text() if text is None else text
    active = False
    lines = []
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            active = line.rstrip().endswith(" b/gpu/vk/graphics_identity.h")
        elif active and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    result = "".join(lines)
    require("namespace mkwvk::oo_graphics_identity" in result, "Graphics identity header missing from patch")
    return result


def source_audit(before, after, cache):
    pattern = r"\bvkCmdDrawMeshTasks(?:IndirectCount|Indirect)?EXT\([^;]+;"
    original = re.findall(pattern, before)
    patched = re.findall(pattern, after)
    require(len(original) == 5 and original == patched, "Native mesh API calls or arguments changed")
    sites = list(re.finditer(pattern, after))
    require(all("oo_record_mesh(" in after[max(0, site.start()-450):site.start()] for site in sites),
            "A native direct/indirect/count/converted command lacks its identity observer")
    require(after.count("oo_record_mesh(") == 5, "Unexpected identity observer command-site count")
    first = after.index("void oo_graphics_identity_record(")
    last = after.index("\nvoid draw(", first)
    helper = after[first:last]
    require(not any(token in helper for token in ("vkCmd", "host->gpu_read", "memory_read_as_submitted", "gpu_buffer_bind(")),
            "Identity helper records GPU work or reads resource contents")
    for text in (before, after):
        require("void pin_subgroup(" in text and "inline uint64_t mix(" in text, "Original subgroup helper not found")
    def pin(text):
        return text[text.index("void pin_subgroup("):text.index("inline uint64_t mix(")]
    require(pin(before) == pin(after), "Default subgroup helper changed")
    require("&event.logical_recording_value" in helper and "&event.recording_sequence" not in helper,
            "Logical submission value mislabeled as actual recording sequence")
    require(cache.count("oo_graphics_identity::note_stage(") == 2,
            "Expected fresh translation/save and full graphics-cache digest paths")
    require("p.mesh && !p.fallback" in cache and "p->mesh && !p->fallback" in cache,
            "Graphics hashing must exclude compute and mesh fallback programs")
    require("oo_identity::selected(p.key, p.variant) || oo_graphics_identity::selected(p.key, p.variant)" in after,
            "Selected identity filters must not use an opaque module-ID shortcut")
    return {"native_command_sites": len(sites), "api_calls_and_arguments_unchanged": True,
            "subgroup_policy_unchanged": True, "identity_helper_has_no_resource_reads_or_gpu_calls": True}


def check_source(checkout, extra_patches=()):
    checkout = Path(checkout).resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    require(head == PIN, "Source checkout is not at the documented pinned revision")
    with tempfile.TemporaryDirectory(prefix="oo-graphics-index-") as temporary:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
        subprocess.run(["git", "read-tree", PIN], cwd=checkout, env=env, check=True)
        hashes = []
        for patch in [*(HERE / name for name in BASE), *(Path(name).resolve() for name in extra_patches)]:
            hashes.append({"patch": patch.name, "sha256": hashlib.sha256(patch.read_bytes()).hexdigest()})
            subprocess.run(["git", "apply", "--cached", "--whitespace=nowarn", str(patch.resolve())],
                           cwd=checkout, env=env, check=True)
        before = subprocess.check_output(["git", "show", ":gpu/vk/vk_draw.cpp"], cwd=checkout, env=env, text=True)
        subprocess.run(["git", "apply", "--cached", "--check", str(PATCH.resolve())], cwd=checkout, env=env, check=True)
        subprocess.run(["git", "apply", "--cached", str(PATCH.resolve())], cwd=checkout, env=env, check=True)
        after = subprocess.check_output(["git", "show", ":gpu/vk/vk_draw.cpp"], cwd=checkout, env=env, text=True)
        cache = subprocess.check_output(["git", "show", ":gpu/vk/vk_progcache.cpp"], cwd=checkout, env=env, text=True)
    return {"pinned_overlay_apply_check": True, "base_patches": hashes, **source_audit(before, after, cache)}


def check_helper(compiler=None):
    compiler = compiler or shutil.which("clang++") or shutil.which("g++")
    require(bool(compiler), "A C++20 compiler is required for the game-free helper check")
    spec = importlib.util.spec_from_file_location("oo_compute_identity_checker", HERE / "check_identity_patch.py")
    compute = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compute)
    with tempfile.TemporaryDirectory(prefix="oo-graphics-helper-") as temporary:
        work = Path(temporary)
        (work / "compute_identity.h").write_text(compute.header_from_patch())
        (work / "graphics_identity.h").write_text(header_from_patch())
        source = work / "test.cpp"
        source.write_text(r'''#include "graphics_identity.h"
+int main(int argc,char**argv) {
+    using namespace mkwvk::oo_graphics_identity;
+    Event e;e.key=0xd8e59341cf4f629cull;e.variant=0x1234;e.cache_stamp=7;e.pipeline_key=8;
+    e.stage_count=3;e.width=2048;e.height=1152;e.samples=4;e.color_count=2;e.color_formats[0]=97;e.color_formats[1]=122;e.depth_format=126;
+    e.group_count[0]=5;e.group_count[1]=1;e.group_count[2]=2;e.vendor=0x8086;e.device=0x56a0;e.driver=1;
+    for(unsigned i=0;i<3;++i) {
+        e.stages[i].nvn_stage=i==0?6:i==1?7:1;e.stages[i].vk_stage=i==0?64:i==1?128:16;
+        e.stages[i].required_size=i==2?0:32;
+        note_stage(true,e.key,e.variant,e.stages[i].identity,{0x07230203,0x00010600,i,1,0});
+    }
+    if(argc>1&&std::strcmp(argv[1],"invalid")==0) {
+        e.stage_count=9;if(emit(e))return 2;e.stage_count=3;
+        e.command=Command::Indirect;e.argument_buffer=1;e.argument_offset=~uint64_t(0)-3;e.stride=12;e.max_draw_count=2;
+        if(emit(e))return 3;
+        e.argument_offset=0;e.command=Command(99);if(emit(e))return 4;
+        return 0;
+    }
+    emit(e);
+    e.command=Command::Indirect;e.argument_buffer=100;e.argument_offset=20;e.max_draw_count=3;e.stride=16;e.rewritten=true;emit(e);
+    e.command=Command::IndirectCount;e.converted=true;e.rewritten=false;e.count_buffer=200;e.count_offset=16;e.stride=12;e.recording_known=true;e.recording_part=10;e.logical_recording_value=11;emit(e);
+    e.stages[1].identity={};emit(e);
+    emit(e); // selected-record cap must reject this fifth event
+    return 0;
+}
+'''.replace("\n+", "\n"))
        executable = work / "check"
        subprocess.run([compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(source), "-o", str(executable)], check=True, timeout=60)
        output = work / "events.jsonl"
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("RX_VK_OO_GRAPHICS_"):
                env.pop(key)
        env["RX_VK_OO_GRAPHICS_IDENTITY_FILE"] = str(output)
        env["RX_VK_OO_GRAPHICS_LIMIT"] = "4"
        subprocess.run([str(executable)], env=env, check=True, timeout=10)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        require(len(rows) == 4, "Selected event limit failed")
        require(all(row["producer"] == {"stage": "identity-only", "replay_complete": False} for row in rows), "Replay completeness fabricated")
        require(all(not any(row["coverage"].values()) for row in rows), "Coverage overstated")
        require(rows[0]["identity_complete"] and not rows[3]["identity_complete"], "Missing stage digest must invalidate identity completeness")
        require(rows[3]["pipeline"]["stages"][1]["spirv"]["sha256"] is None, "Missing stage digest fabricated")
        require([stage["vk_stage"] for stage in rows[0]["pipeline"]["stages"]] == [64,128,16], "Stage roles lost")
        for i, stage in enumerate(rows[0]["pipeline"]["stages"]):
            expected = hashlib.sha256(struct.pack("<5I", 0x07230203, 0x00010600, i, 1, 0)).hexdigest()
            require(stage["index"] == i and stage["spirv"]["sha256"] == expected, "Per-stage identity/hash mismatch")
            require(stage["driver_selected_simd_width"] is None, "Requested pin must not masquerade as actual SIMD width")
        require(rows[0]["draw"]["group_count"] == [5,1,2], "Direct mesh grid metadata mismatch")
        require(rows[1]["draw"]["arguments"]["byte_extent"] == 44, "Indirect range must exclude final stride padding")
        require(rows[1]["draw"]["arguments"]["api_draw_count"] == 3, "Known API record count lost")
        require(rows[2]["draw"]["arguments"]["api_draw_count"] is None and rows[2]["draw"]["count_argument"]["value"] is None,
                "GPU indirect count must remain unknown")
        require(rows[2]["draw"]["recording_part"] == 10 and rows[0]["draw"]["recording_part"] is None, "Unknown recording identity fabricated")
        require(rows[2]["draw"]["logical_recording_value"] == 11 and rows[0]["draw"]["logical_recording_value"] is None,
                "Logical submission value missing or fabricated")
        require(all(row["draw"]["recording_sequence"] is None and row["draw"]["gpu_completion_value"] is None for row in rows),
                "Logical submission value is not an actual recording-timeline sequence or GPU completion")
        require(all(row["draw"]["gpu_executed_draw_count"] is None for row in rows), "Recording is not proof of GPU execution")
        require(rows[0]["attachments"]["color_formats"] == [97,122] and rows[0]["attachments"]["sample_count_flag"] == 4,
                "Retained attachment state lost")
        output.unlink()
        subprocess.run([str(executable), "invalid"], env=env, check=True, timeout=10)
        require(not output.exists(), "Invalid bounds/command created misleading event")
        for field, value in (("RX_VK_OO_GRAPHICS_PROGRAMS", "d8e59341"), ("RX_VK_OO_GRAPHICS_PROGRAMS", ""),
                             ("RX_VK_OO_GRAPHICS_VARIANT", "0000000000004321"), ("RX_VK_OO_GRAPHICS_LIMIT", "257")):
            wrong = env.copy();wrong[field] = value
            subprocess.run([str(executable)], env=wrong, check=True, timeout=10)
            require(not output.exists(), "Disabled/invalid/nonmatching filter emitted an event")
        disabled = env.copy();disabled.pop("RX_VK_OO_GRAPHICS_IDENTITY_FILE")
        subprocess.run([str(executable)], env=disabled, check=True, timeout=10)
        require(not output.exists(), "Default mode wrote output")
    return {"optimized_helper_compile": True, "stage_identity_and_missing_digest_checks": True,
            "direct_indirect_and_count_metadata_checked": True, "logical_recording_distinguished_from_completion": True, "invalid_bounds_refused": True,
            "disabled_and_exact_filters_checked": True, "scope": "synthetic metadata checks; no GPU execution or gameplay claim"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--extra-patch", type=Path, action="append", default=[])
    parser.add_argument("--compiler")
    args = parser.parse_args()
    try:
        report = check_helper(args.compiler)
        if args.checkout:
            report.update(check_source(args.checkout, args.extra_patch))
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
