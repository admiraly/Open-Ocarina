#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the private Mesa cache-policy patch without creating Vulkan or GPU work."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / "hs-compiler-cache-policy.patch"
HEADER = "src/util/hs_compiler_options.h"
BASE_SHA256 = {
    "src/vulkan/runtime/vk_pipeline.c": "0e8c533c2361187f41c6e4a8f49c2e92f388e9379260f433b7a89cf1ceffc6de",
    "src/intel/vulkan/anv_physical_device.c": "1d2f507f2125deffae6b2155dcf357031e6e010bde9d407d5e313bf43a6b44ee",
    "src/intel/vulkan/anv_shader_compile.c": "93f2ec12ba1c056e618a4158e9700f0ae21f03f132d069ac69e9014b9a999714",
}
READER_SHA256 = {
    "src/intel/compiler/brw/brw_simd_selection.cpp": "f3eac98b61fcff559154a6b53ae95b766b74d13d42dfc69962a5a4724dad74bf",
    "src/intel/compiler/brw/brw_compile_cs.cpp": "f953bb35e59cd1282eea9ac1bf5fbcd5fed3f231aa1783289c3ca40a8d95a980",
    "src/intel/compiler/brw/brw_reg_allocate.cpp": "a2944a39b0600b145fc7fd2c6a83723d22610cae21d91340ea0a812105cec200",
    "src/intel/compiler/brw/brw_reg.h": "c2ee30045f38dc404e62befc42a2f2acbe09d423302ee5cd2fef3744f020d4bc",
    "src/intel/compiler/brw/brw_compiler.c": "053a656a2e8a16477bbb1e349686beb12e353c35f8ca701f235bfe277cf77b11",
    "src/intel/compiler/gen/gen_validate.cpp": "0769e34ef2e7949d77316ae8600f49b38cd6baacbd65f925ca79f166cc7b9ea4",
    "src/intel/vulkan/genX_init_state.c": "85fc0e551039cccf460faca2bc0c8fc206456771280537683948520c9d832d8a",
    "src/intel/vulkan/anv_cmd_buffer.c": "e493fb34ef6533d7382a73e8e6ae6d07158603eb400bebd935b8cca17db623ba",
    "src/intel/vulkan/anv_shader.c": "4090aefceca22ba9542ae13447569738256349609948c48e00e2578b4f1f7492",
    "src/vulkan/runtime/vk_pipeline_cache.c": "0033888aab4c4eb8d26342e17e042de49b9cd2858e71fb6963abbabc9aa66c96",
    "src/vulkan/runtime/vk_shader.c": "5671c681b76e8cd430f23e172c64627e1a3a208b49ae0f370cf362059dac5e49",
    "src/util/disk_cache.c": "b2fd0c05a18ae9272ee9fbb53b78ea6d71fefc1742d2d8a2340e4c4c5844191b"
}
KNOBS = ("HS_FORCE_SIMD8", "HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_SPILL_PCT",
         "HS_LARGE_GRF", "HS_SUBGROUP8", "shader_spilling_rate")
EXCLUDED = ("HS_RA_DEBUG", "HS_PUSH_SHADOW", "HS_LARGE_GRF_BIT")
SCOPES = {"device": 0, "compute": 32, "task": 64, "mesh": 128}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def header_from_patch(patch=PATCH):
    lines = Path(patch).read_text().splitlines(keepends=True)
    marker = f"diff --git a/{HEADER} b/{HEADER}\n"
    require(marker in lines, "Production policy header missing from patch")
    start = lines.index(marker) + 1
    output = []
    for line in lines[start:]:
        if line.startswith("diff --git "):
            break
        if line.startswith("+") and not line.startswith("+++"):
            output.append(line[1:])
    require(output, "Production policy header is empty")
    return "".join(output)


def function_body(source, name):
    match = re.search(r"\n" + re.escape(name) + r"\([^;]*?\n\{", source)
    require(match is not None, "Missing production function: " + name)
    start = match.end() - 1
    depth = 0
    for at in range(start, len(source)):
        depth += (source[at] == "{") - (source[at] == "}")
        if not depth:
            return source[start:at + 1]
    raise ValueError("Unterminated production function: " + name)


def audit_candidate(directory):
    """Check actual post-patch call sites, including the builtin-NIR early path."""
    directory = Path(directory)
    runtime = (directory / "src/vulkan/runtime/vk_pipeline.c").read_text()
    stage = function_body(runtime, "vk_pipeline_hash_shader_stage_blake3")
    require(stage.count("hs_compiler_options_blake3_update") == 1,
            "Stage keys need exactly one common policy encoder")
    prefix = stage[:stage.index("hs_compiler_options_blake3_update")]
    require("if (" not in prefix and "get_builtin_nir" not in prefix,
            "Policy hashing must precede every stage/builtin conditional")
    require("hs_compiler_options_blake3_update(&ctx, info->stage)" in stage,
            "Stage scope must be the actual Vulkan stage")
    require('getenv("HS_' not in stage and "hs_mode" not in stage and "hs_max" not in stage,
            "Legacy unframed compute-only encoding must be removed")
    require("_mesa_blake3_update(&ctx, blob.data, blob.size)" in stage and
            "_mesa_blake3_compute(blob.data" not in stage,
            "Builtin NIR must retain the prefixed policy context")

    physical = (directory / "src/intel/vulkan/anv_physical_device.c").read_text()
    shader = (directory / "src/intel/vulkan/anv_shader_compile.c").read_text()
    sites = ((physical, "anv_physical_device_init_uuids"),
             (physical, "anv_physical_device_init_disk_cache"),
             (shader, "anv_shader_init_uuid"), (shader, "anv_shader_hash_state"))
    for text, name in sites:
        body = function_body(text, name)
        require(body.count("hs_compiler_options_blake3_update") == 1 and
                body.count("hs_compiler_effective_blake3_update") == 1,
                "Raw/effective policy must share one encoder at " + name)
        require("brw_get_compiler_config_value" in body and "compiler->spilling_rate" in body,
                "Effective compiler configuration/rate missing at " + name)
        require(body.index("hs_compiler_effective_blake3_update") < body.index("_mesa_blake3_final"),
                "Policy must precede finalization at " + name)
    disk = function_body(physical, "anv_physical_device_init_disk_cache")
    require("_mesa_blake3_format(timestamp, namespace_hash)" in disk and
            "_mesa_blake3_format(timestamp, device->driver_build_sha1)" not in disk,
            "Disk namespace must use the compiler-policy digest")
    require((directory / HEADER).read_text() == header_from_patch(),
            "Applied production encoder differs from the reviewed patch")
    return {"all_stage_keys": True, "builtin_nir": True, "pipeline_cache_uuid": True,
            "disk_namespace": True, "shader_binary_uuid": True, "anv_state_key": True,
            "effective_spilling_rate_and_compiler_config": True}


def check_source(directory):
    """Apply to copies of three pinned files; leave the actual checkout untouched."""
    directory = Path(directory).resolve()
    before = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in BASE_SHA256}
    require(before == BASE_SHA256, "Private Mesa source baseline SHA-256 differs")
    readers = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in READER_SHA256}
    require(readers == READER_SHA256, "Compiler/cache policy reader SHA-256 differs")
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary)
        for name in BASE_SHA256:
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / name, target)
        subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=candidate,
                       check=True, capture_output=True, text=True, timeout=15)
        subprocess.run(["git", "apply", str(PATCH)], cwd=candidate,
                       check=True, capture_output=True, text=True, timeout=15)
        result = audit_candidate(candidate)
        result["candidate_sha256"] = {name: hashlib.sha256((candidate / name).read_bytes()).hexdigest()
                                      for name in (*BASE_SHA256, HEADER)}
    require(before == {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in BASE_SHA256},
            "Source unexpectedly changed during read-only audit")
    require(readers == {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in READER_SHA256},
            "Policy readers unexpectedly changed during read-only audit")
    return {"base_sha256": before, "policy_reader_sha256": readers, "private_copy_apply_check": True, "source_modified": False, **result}


# This shim records the exact byte stream passed by the production encoder to
# BLAKE3. It does not implement/retest BLAKE3 and does not create Vulkan objects.
TRACE_HASHER = r'''#ifndef TRACE_MESA_BLAKE3_H
#define TRACE_MESA_BLAKE3_H
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
struct mesa_blake3 { unsigned char *data; size_t size, capacity; };
static inline void _mesa_blake3_update(struct mesa_blake3 *ctx, const void *data, size_t size) {
    if (size > SIZE_MAX - ctx->size) abort();
    size_t need = ctx->size + size;
    if (need > ctx->capacity) {
        ctx->capacity = need;
        unsigned char *next = realloc(ctx->data, need);
        if (!next) abort();
        ctx->data = next;
    }
    if (size) memcpy(ctx->data + ctx->size, data, size);
    ctx->size = need;
}
#endif
'''
HARNESS = r'''#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <limits.h>
#include "util/hs_compiler_options.h"
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    char *end;
    errno = 0;
    unsigned long scope = strtoul(argv[1], &end, 0);
    if (errno || *end || scope > UINT32_MAX) return 2;
    unsigned long long config = strtoull(argv[2], &end, 0);
    if (errno || *end) return 2;
    long rate = strtol(argv[3], &end, 0);
    if (errno || *end || rate < INT32_MIN || rate > INT32_MAX) return 2;
    struct mesa_blake3 ctx = {0};
    hs_compiler_options_blake3_update(&ctx, (uint32_t)scope);
    hs_compiler_effective_blake3_update(&ctx, (uint64_t)config, (int32_t)rate);
    if (fwrite(ctx.data, 1, ctx.size, stdout) != ctx.size) return 3;
    free(ctx.data);
    return 0;
}
'''


def build_helper(directory, compiler=None):
    compiler = compiler or shutil.which("cc")
    require(compiler is not None, "C compiler required for exact production-function tests")
    directory = Path(directory)
    (directory / "util").mkdir(parents=True, exist_ok=True)
    (directory / HEADER.removeprefix("src/")).write_text(header_from_patch())
    (directory / "util/mesa-blake3.h").write_text(TRACE_HASHER)
    (directory / "helper.c").write_text(HARNESS)
    helper = directory / "helper"
    subprocess.run([compiler, "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                    "-I", str(directory), str(directory / "helper.c"), "-o", str(helper)],
                   check=True, capture_output=True, text=True, timeout=30)
    return helper


def encode(helper, values=None, *, scope=32, compiler_config=0, spilling_rate=0):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("HS_") and key != "shader_spilling_rate"}
    environment.update(values or {})
    return subprocess.run([str(helper), str(scope), str(compiler_config), str(spilling_rate)],
                          env=environment, check=True, capture_output=True, timeout=10).stdout


def decode(data):
    """Parse the documented framing independently of the native implementation."""
    domain = b"anv-hs-compiler-policy-v1\0"
    require(data.startswith(domain), "Policy domain missing")
    at = len(domain)

    def integer():
        nonlocal at
        require(at + 8 <= len(data), "Truncated integer")
        value = struct.unpack_from("<Q", data, at)[0]; at += 8
        return value

    def block(size):
        nonlocal at
        require(at + size <= len(data), "Truncated block")
        value = data[at:at + size]; at += size
        return value

    scope, count = integer(), integer()
    require(count == len(KNOBS), "Codegen knob inventory differs")
    rows = {}
    for expected in KNOBS:
        name = block(integer()).decode()
        require(name == expected, "Knob ordering/identity differs")
        presence = block(1)[0]
        require(presence in (0, 1), "Invalid presence")
        value = block(integer())
        require(presence or not value, "Absent value has bytes")
        rows[name] = {"present": bool(presence), "bytes": value}
    effective = b"anv-hs-effective-policy-v1\0"
    require(block(len(effective)) == effective, "Effective policy domain missing")
    compiler_config, spilling_rate = integer(), integer()
    require(at == len(data), "Trailing policy bytes")
    return {"scope": scope, "options": rows, "compiler_config": compiler_config,
            "spilling_rate_u32": spilling_rate}


def check_helper(compiler=None):
    comparisons = 0
    with tempfile.TemporaryDirectory() as temporary:
        helper = build_helper(temporary, compiler)
        for scope in SCOPES.values():
            baseline = encode(helper, scope=scope)
            decoded = decode(baseline)
            require(decoded["scope"] == scope and not any(row["present"] for row in decoded["options"].values()),
                    "Missing option framing differs")
            for knob in KNOBS:
                variants = [encode(helper, {knob: value}, scope=scope) for value in ("", "none", "1", "12")]
                require(len(set([baseline, *variants])) == 5, "Option presence/length collision: " + knob)
                comparisons += 4
            for knob in EXCLUDED:
                require(encode(helper, {knob: "1"}, scope=scope) == baseline, "Excluded knob unexpectedly hashed")
        require(encode(helper, {"HS_FORCE_SIMD8": "cs", "HS_CS32_MAX_INSTS": "1"}) !=
                encode(helper, {"HS_FORCE_SIMD8": "cs1"}), "Adjacent raw values collide")
        require(encode(helper, compiler_config=1) != encode(helper), "Effective debug/SIMD config omitted")
        require(encode(helper, spilling_rate=1) != encode(helper), "Effective drirc rate omitted")
    return {"exact_native_encoder_tested": True, "optimized_helper": True,
            "hash_input_trace_only": True, "blake3_algorithm_retested": False,
            "scopes": SCOPES, "codegen_knobs": list(KNOBS),
            "presence_value_comparisons": comparisons, "gpu_execution": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--cc")
    args = parser.parse_args()
    try:
        result = {"patch_sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest(),
                  "native": check_helper(args.cc)}
        if args.source:
            result["source"] = check_source(args.source)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
