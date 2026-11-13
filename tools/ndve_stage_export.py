#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only conditional export of one digest-pinned NDVE software-matrix stage.

Uses the frozen open recompiler tree in a private snapshot. No Vulkan loader,
device, pipeline, game-host build, runtime capture or arbitrary stage admission.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
TREE = "58ca709617448e06af382c3443dd88dac3ffcb8e"
PROGRAM = "e409733c45530d75"
NATIVE_SHA = "1d660217708e2a99339df4ac6814d2843c5ad83f9c20dd7b429cb529aee41afa"
BAKE_SHA = "e3710be6a55a0fd901291bff48505aa85a148db1cf1e4c1a2613ea649e9e3e49"
PREFIXES = ("gpu/vk/eden", "gpu/vk/sirit", "gpu/vk/compat", "gpu/compat", "gpu/third_party/suyu", "nx2/bake_ds")
FLAGS = ("-std=c++2b", "-O2", "-DNDEBUG", "-march=x86-64-v3", "-Wno-everything", "-DFMT_HEADER_ONLY", "-DVK_NO_PROTOTYPES",
         "-include", "algorithm", "-include", "cstdio", "-include", "cstdlib", "-include", "cstring",
         "-include", "functional", "-include", "string", "-include", "vector")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fnv(data, value=0xcbf29ce484222325):
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & 0xffffffffffffffff
    return value


def validate_native(control, area):
    if len(control) < 216 or len(area) < 22528 or control[:4] != b"\x34\x12\x76\x98":
        raise ValueError("Invalid exact NDVE native stage framing")
    length, end = struct.unpack_from("<I", control, 56)[0], struct.unpack_from("<I", control, 68)[0]
    stage = struct.unpack_from("<H", control, 86)[0]
    local = list(struct.unpack_from("<3I", control, 88))
    shared = struct.unpack_from("<I", control, 100)[0]
    if stage != 5 or length != 22400 or end != 22528 or local != [32, 1, 1] or shared != 3360:
        raise ValueError("Native stage differs from the exact reviewed control contract")
    if sha(control[:216] + area[:end]) != NATIVE_SHA or f"{fnv(area[:end], fnv(control[:216])):016x}" != PROGRAM:
        raise ValueError("Native control/code digest or runtime key differs")
    hmma = sum((words[0] & 4095) == 0x23c and ((words[0] >> 12) & 15) != 15
               for words in struct.iter_unpack("<4I", area[:length]))
    if hmma != 72:
        raise ValueError("Unexpected encoded active-predicate HMMA count")
    return {"program": PROGRAM, "native_control_code_sha256": NATIVE_SHA, "nvn_stage": stage,
            "workgroup": local, "shared_bytes": shared, "declared_native_instruction_bytes": length,
            "native_end_code_bytes": end, "encoded_HMMA16816F16_nonfalse_predicate_sites": hmma}


def singleton_seed(source):
    """Validate the complete seed framing, retain only the exact known stage."""
    source = Path(source)
    size = source.stat().st_size
    if not 16 <= size <= 2 * 1024**3:
        raise ValueError("Seed exceeds bounded input size")
    found = []
    with source.open("rb") as stream:
        header = stream.read(16)
        magic, programs, stages = struct.unpack("<8sII", header)
        if magic != b"HKSEED01" or stages > size // 20 or programs > size // 4:
            raise ValueError("Unsupported shader seed framing")
        for index in range(stages):
            raw = stream.read(20)
            if len(raw) != 20:
                raise ValueError("Truncated seed stage header")
            key, stage, control_size, code_size = struct.unpack("<QIII", raw)
            if not 216 <= control_size <= 1024**2 or code_size > 16 * 1024**2 or stream.tell() + control_size + code_size > size:
                raise ValueError("Invalid seed stage extent")
            control = stream.read(control_size)
            if stage == 5 and code_size >= 22528:
                area = stream.read(code_size)
                if sha(control[:216] + area[:22528]) == NATIVE_SHA:
                    identity = validate_native(control, area)
                    found.append((key, control[:216], area[:22528], identity | {"seed_stage_index": index}))
            else:
                stream.seek(code_size, 1)
        for _ in range(programs):
            raw = stream.read(4)
            if len(raw) != 4:
                raise ValueError("Truncated seed program table")
            count, = struct.unpack("<I", raw)
            if count > 8:
                raise ValueError("Invalid seed program stage count")
            ids = stream.read(count * 4)
            if len(ids) != count * 4 or any(index >= stages for index in struct.unpack(f"<{count}I", ids)):
                raise ValueError("Invalid seed program stage reference")
        if stream.tell() != size or len(found) != 1:
            raise ValueError("Expected one exact NDVE stage and complete seed framing")
    key, control, area, identity = found[0]
    packed = struct.pack("<8sIIQIII", b"HKSEED01", 1, 1, key, 5, 216, len(area)) + control + area + struct.pack("<II", 1, 0)
    return packed, identity


RESTRICTED_MAIN = r'''
int main(int argc, char **argv) {
    if (argc != 3 || std::filesystem::file_size(argv[1]) > 65536) return 2;
    const auto seed = read_seed(argv[1]);
    if (!seed.error.empty() || seed.stages.size() != 1 || seed.programs.size() != 1 ||
        seed.programs[0] != std::vector<uint32_t>{0} || seed.stages[0].stage != 5 ||
        seed.stages[0].key != 0xe409733c45530d75ULL) return 2;
    Options options;
    options.seed = argv[1]; options.out = argv[2]; options.bank = false;
    options.limit = 1; options.variants = 0; options.variant_cap = 1;
    options.ssbo_align = 16; options.depth = 0;
    options.target = *target_preset("tensor-software");
    export_target_env(options.target);
    return run_translate(options);
}
'''


def restricted_driver(original):
    if sha(original.encode()) != BAKE_SHA:
        raise ValueError("Frozen offline driver source changed")
    marker = "// ---- device query (--target query):"
    if original.count(marker) != 1:
        raise ValueError("Offline query cut context changed")
    result = original[:original.index(marker)].replace("#include <dlfcn.h>\n", "") + RESTRICTED_MAIN
    anchor = "                auto tr = translate(env, false);\n"
    if result.count(anchor) != 1:
        raise ValueError("Offline IR audit insertion context changed")
    result = result.replace(anchor, anchor + r'''                size_t matrix_sites = 0;
                for (const auto *block : tr->program.blocks)
                    for (const auto &inst : block->Instructions())
                        matrix_sites += inst.GetOpcode() == Shader::IR::Opcode::MatrixMulAddF16;
                std::fprintf(stderr, "offline_reference_IR MatrixMulAddF16=%zu\n", matrix_sites);
                if (matrix_sites != 72) throw Shader::RuntimeError("Unexpected reference matrix count");
''')
    query_hooks = {
        "    u32 ReadCbufValue(u32 index, u32 offset) override {\n": r'''        uint32_t recorded = 0;
        const bool resolved = index == 1 && static_cast<size_t>(offset) + 4 <= nvn.c1.size();
        if (resolved) std::memcpy(&recorded, nvn.c1.data() + offset, 4);
        const bool slot_token = forced && index == 0 && stage == Shader::Stage::Compute &&
            offset >= forced->lo && offset < forced->hi && !(offset & 7);
        if (slot_token) recorded = 0x8000 | offset;
        std::fprintf(stderr, "offline_query cbuf bank=%u offset=%u answer=%u resolved=%u synthetic_slot_token=%u\n",
            index, offset, recorded, resolved, slot_token);
''',
        "    Shader::TextureType ReadTextureType(u32 handle) override {\n": r'''        if (forced && !forced->want.empty()) throw Shader::RuntimeError("Unexpected nondefault forced answers in one-stage exporter");
        std::fprintf(stderr, "offline_query texture_type handle=%u answer=%u resolved=0\n", handle,
            static_cast<unsigned>(Shader::TextureType::Color2D));
''',
        "    bool IsTexturePixelFormatInteger(u32 handle) override {\n": r'''        if (forced && !forced->want.empty()) throw Shader::RuntimeError("Unexpected nondefault forced answers in one-stage exporter");
        std::fprintf(stderr, "offline_query texture_integer handle=%u answer=0 resolved=0\n", handle);
''',
        "    bool IsCbufStorageBufferPointer(u32 index, u32 offset) override {\n": r'''        std::fprintf(stderr, "offline_query cbuf_storage_pointer bank=%u offset=%u answer=0 resolved=0\n", index, offset);
''',
        "    u32 TextureBoundBuffer() const override {\n": r'''        std::fprintf(stderr, "offline_query texture_bound_buffer answer=%u resolved=%u\n",
            nvn.texture_bound != ~0u ? nvn.texture_bound : 0u, nvn.texture_bound != ~0u);
'''
    }
    for signature, logging in query_hooks.items():
        if result.count(signature) != 1:
            raise ValueError("Offline environment query hook context changed")
        result = result.replace(signature, signature + logging)
    pixel = "Shader::TexturePixelFormat ReadTexturePixelFormat(u32) override { return Shader::TexturePixelFormat{}; }"
    if result.count(pixel) != 1:
        raise ValueError("Offline texture-format query hook changed")
    result = result.replace(pixel, r'''Shader::TexturePixelFormat ReadTexturePixelFormat(u32 handle) override {
        std::fprintf(stderr, "offline_query texture_pixel_format handle=%u answer=0 resolved=0\n", handle);
        return Shader::TexturePixelFormat{};
    }''')
    if any(token in result for token in ("dlopen(", "dlsym(", "vkCreateInstance(", "vkCreateDevice(", "vkQueueSubmit(")):
        raise ValueError("Restricted offline driver retains a runtime device path")
    return result


def environment_queries(stderr):
    queries = {}
    for line in stderr.splitlines():
        if not line.startswith("offline_query "):
            continue
        pieces = line.split()
        fields = {key: int(value) for key, value in (item.split("=", 1) for item in pieces[2:])}
        if fields.get("resolved") not in (0, 1) or "answer" not in fields:
            raise ValueError("Malformed offline environment query audit")
        key = (pieces[1], tuple(sorted(fields.items())))
        queries[key] = queries.get(key, 0) + 1
    if not queries:
        raise ValueError("No compiler environment query audit was recorded")
    return [{"kind": kind, **dict(fields), "calls": count} for (kind, fields), count in sorted(queries.items())]


def clean_environment():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"}


def run(command, env, log, timeout=180):
    result = subprocess.run(command, env=env, capture_output=True, timeout=timeout)
    log.with_suffix(".stdout").write_bytes(result.stdout)
    log.with_suffix(".stderr").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(f"CPU command failed ({result.returncode}); private log: {log.name}")
    return result.stdout


def export(source_repo, seed, output, jobs=4, compiler="clang++"):
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ValueError("Offline build allows one through four CPU jobs")
    output = Path(output).resolve()
    if output.exists() or (output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",))):
        raise ValueError("Choose a fresh private output directory")
    seed_bytes, identity = singleton_seed(seed)
    output.mkdir(parents=True)
    (output / "input.seed").write_bytes(seed_bytes)
    snapshot = output / "source"
    snapshot.mkdir()
    archive = subprocess.run(["git", "-C", str(Path(source_repo).resolve()), "archive", TREE, *PREFIXES], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(snapshot, filter="data")
    sources = {str(path.relative_to(snapshot)): file_sha(path) for path in sorted(snapshot.rglob("*")) if path.is_file()}
    driver = output / "ndve_offline_driver.cpp"
    driver.write_text(restricted_driver((snapshot / "nx2/bake_ds/bake_ds.cpp").read_text()))
    (output / "source-manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    # Preserve the clang++ invocation name: resolving its symlink to clang
    # changes the link driver's automatic C++ runtime selection.
    cc = Path(shutil.which(compiler) or compiler).absolute()
    env = clean_environment()
    includes = [snapshot / part for part in ("gpu/vk/compat", "gpu/compat", "gpu/vk/eden", "gpu/third_party/suyu", "gpu/vk/sirit/include", "gpu/vk/sirit/src")]
    include_flags = ["-I" + str(path) for path in includes] + ["-I/usr/include/spirv/unified1"]
    cpp = sorted((snapshot / "gpu/vk/eden/shader_recompiler").rglob("*.cpp")) + sorted((snapshot / "gpu/vk/sirit/src").rglob("*.cpp")) + [driver]
    objects = output / "objects"
    objects.mkdir()
    def compile_one(index, source):
        obj = objects / f"{index:04}.o"
        run([str(cc), *FLAGS, *include_flags, "-MD", "-MF", str(obj.with_suffix(".d")), "-c", str(source), "-o", str(obj)], env, objects / f"compile-{index:04}")
        return obj
    finished = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(compile_one, index, source) for index, source in enumerate(cpp)]
        for future in as_completed(futures):
            finished.append(future.result())
            if len(finished) % 25 == 0:
                print(f"CPU recompiler sources built: {len(finished)}/{len(cpp)}", flush=True)
    helper = output / "ndve-offline-export"
    run([str(cc), *FLAGS, *map(str, sorted(finished)), "-pthread", "-o", str(helper)], env, output / "link")
    imports = run(["nm", "-u", str(helper)], env, output / "undefined-imports").decode()
    if re.search(r"\b(vk[A-Z]\w*|dlopen|dlsym)\b", imports):
        raise ValueError("Offline helper unexpectedly imports Vulkan/runtime-loader functions")
    run([str(helper), str(output / "input.seed"), str(output / "translated")], env, output / "translate", 300)
    manifest = json.loads((output / "translated/bake_manifest.json").read_text())
    if manifest.get("translated") != 1 or manifest.get("failed") != 0 or manifest.get("skipped") != 0 or manifest.get("mma_software") is not True:
        raise ValueError("Offline reference was not completely translated in software mode")
    module = output / "translated/spv" / (PROGRAM + ".5.spv")
    if not module.is_file():
        raise ValueError("Expected exact target module was not emitted")
    run(["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(module)], env, output / "validate", 30)
    disassembly = run(["spirv-dis", "--raw-id", str(module)], env, output / "disassemble", 30)
    (output / "reference.spvasm").write_bytes(disassembly)
    if b"CooperativeMatrix" in disassembly or b"offline_reference_IR MatrixMulAddF16=72" not in (output / "translate.stderr").read_bytes():
        raise ValueError("Reference did not retain all software-matrix IR work")
    queries = environment_queries((output / "translate.stderr").read_text())
    (output / "environment-queries.json").write_text(json.dumps(queries, indent=2) + "\n")
    dependencies = set()
    for dep in objects.glob("*.d"):
        for word in dep.read_text().replace("\\\n", " ").split()[1:]:
            path = Path(word.replace("\\ ", " "))
            if path.is_file():
                dependencies.add(path)
    dependency_hashes = {str(path): file_sha(path) for path in sorted(dependencies)}
    (output / "compile-dependencies.json").write_text(json.dumps(dependency_hashes, indent=2) + "\n")
    report = {"schema": "open-ocarina.ndve-stage-software-export/v1", "CPU_only": True, "Vulkan_calls": False,
              "identity": identity, "source_tree": TREE, "source_manifest_sha256": file_sha(output / "source-manifest.json"),
              "driver_source_sha256": file_sha(driver), "helper_sha256": file_sha(helper), "wrapper_sha256": file_sha(Path(__file__)),
              "compiler_sha256": file_sha(cc), "compiler_flags": FLAGS, "CPU_jobs": jobs,
              "compile_dependencies_sha256": file_sha(output / "compile-dependencies.json"), "source_seed_sha256": file_sha(seed),
              "singleton_seed_sha256": file_sha(output / "input.seed"), "module_sha256": file_sha(module), "module_words": module.stat().st_size // 4,
              "config": {"renderer_hardware_native_mma": False, "Ampere_native_matrix_multiply": True,
                         "SPIRV_mma_software": True, "EDEN_NX2_TENSOR": None, "required_future_subgroup": 32,
                         "SSBO_alignment_lower_bound": 16, "offline_answers": "Default compute texture/pointer answers with synthetic slot tokens for query tracking; no live answer provenance"},
              "encoded_HMMA_sites": 72, "translated_MatrixMulAddF16_sites": 72, "all_translation_failures": 0,
              "SPIRV_validation_passed": True, "no_cooperative_matrix_in_module": True,
              "conditional_on_offline_query_configuration": True,
              "environment_queries_sha256": file_sha(output / "environment-queries.json"),
              "unresolved_compiler_queries": [query for query in queries if query["resolved"] == 0],
              "claims": {"original_console_arithmetic_independently_certified": False, "live_variant_selected": False,
                         "complete_replay_inputs": False, "GPU_execution": False, "rendering_equivalence": False, "FPS_gain": False}}
    (output / "export-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--compiler", default="clang++")
    args = parser.parse_args()
    print(json.dumps(export(args.source_repo, args.seed, args.output, args.jobs, args.compiler), indent=2))


if __name__ == "__main__":
    main()
