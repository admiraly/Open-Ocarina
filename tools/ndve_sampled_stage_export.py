#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only what-if export of one NDVE stage from recorded compiler answers.

The production renderer still excludes this network before SPIR-V emission.
This tool does not change that policy, link the renderer, or call Vulkan.
It assigns the 21 host fields observed at use and the 77-field profile
sampled later, and it answers only queries present in the pinned journal.
A sampled texture type is returned only as a labeled conditional input.
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
import subprocess
import sys
import tarfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ndve_stage_export import (
    BAKE_SHA, FLAGS, NATIVE_SHA, PREFIXES, PROGRAM, TREE, clean_environment, file_sha, run, singleton_seed,
)

ROOT = Path(__file__).resolve().parents[1]
JOURNAL_SHA = "8e5c274e1fa8a16c0b6175dca377b91cbc5972634ef8aca146f4e833737addf9"
RESULT_SHA = "f64f83dda29d9874fce05dc75abd33f45875b9d8f97ad56b47cc645250d9255e"
CHECKED_SHA = "8b7fe347e7fe6dc01d5135e1818fe8516b9be9ab205cf3f1d80dff31cdf66648"
SINGLETON_SEED_SHA = "95400de44200c3239709520b6c239556c641c86ff82f0bb301c9fc33fd144744"
FROZEN_EXPORT_PINS = {
    "tools/ndve_stage_export.py": "489fb296c20e00f27dcb3838e094751db2579ba0ff631c110677267f15606409",
    "tools/ndve_stage_contract.py": "d48cef16c34c5266eaf88fb1989eddc676a3514385f874d0e86bc3f9b391c48b",
    "tests/test_ndve_stage_export.py": "25774419b121b1749952c37ead3baf267b646fee3306d2daf821689b2b1f8168",
    "tests/test_ndve_stage_contract.py": "3aaf3a364c952f02f71a613607f2a209e1679c79e3f583762a3450ffa900b121",
    "research/ndve-e409-software-export.json": "864c33e224d71246d108592d36f1f089cb95dca22544d37b6bea15a137885a61",
    "docs/ndve-stage-software-export.md": "f7dce17539c92de49882b0a23e2d67be2b858d3197947878e0fa47adf1cd0701",
}
HOST_OBJECT = "host_info_used"
PROFILE_OBJECT = "sampled_pinned_profile_not_use_proof"
HANDLE = 281020472
CONDITIONAL_TEXTURE_TYPE = 2


def frozen_export_pins():
    mismatches = {}
    for relative, expected in FROZEN_EXPORT_PINS.items():
        actual = file_sha(ROOT / relative)
        if actual != expected:
            mismatches[relative] = actual
    if mismatches:
        raise ValueError(f"Frozen public export pins changed: {sorted(mismatches)}")
    return dict(FROZEN_EXPORT_PINS)


def struct_fields(text, struct_name):
    match = re.search(rf"struct {struct_name} \{{(.*?)\n\}};", text, re.S)
    if not match:
        raise ValueError(f"Missing struct {struct_name}")
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    body = re.sub(r"//.*?$", "", body, flags=re.M)
    fields = []
    for raw in body.splitlines():
        line = raw.strip().rstrip(";")
        found = re.match(r"(u32|u64|bool)\s+([A-Za-z_][A-Za-z0-9_]*)\b", line)
        if found and "(" not in line:
            fields.append({"type": found.group(1), "name": found.group(2)})
    names = [field["name"] for field in fields]
    if len(names) != len(set(names)) or not fields:
        raise ValueError(f"Unusable {struct_name} field list")
    return fields


def enum_value(text, enum_name, enumerator):
    match = re.search(rf"enum class {enum_name}[^{{]*\{{(.*?)\n\}};", text, re.S)
    if not match:
        raise ValueError(f"Missing enum {enum_name}")
    body = re.sub(r"//.*?$", "", match.group(1), flags=re.M)
    value = 0
    for raw in body.split(","):
        name = raw.strip().split("=")[0].strip()
        if not name:
            continue
        if name == enumerator:
            return value
        value += 1
    raise ValueError(f"{enum_name}::{enumerator} is absent")


def _rows(path):
    path = Path(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != JOURNAL_SHA or len(data) != 78034:
        raise ValueError("Metadata journal pin differs from the reviewed observation")
    rows = [json.loads(line) for line in data.decode().splitlines()]
    if len(rows) != 328:
        raise ValueError("Metadata journal row count differs")
    return rows


def _object_fields(rows, object_name):
    values = {}
    for row in rows:
        if row.get("event") != "field" or row.get("object") != object_name:
            continue
        field = row.get("field")
        if field in values:
            raise ValueError(f"Duplicate journal field {object_name}.{field}")
        if type(row.get("value")) is not int:
            raise ValueError(f"Non-integer journal field {object_name}.{field}")
        values[field] = row["value"]
    return values


def configuration_from_rows(rows, host_fields, profile_fields, texture_type_color2d):
    if texture_type_color2d != CONDITIONAL_TEXTURE_TYPE:
        raise ValueError("TextureType::Color2D is not the reviewed sampled value 2")
    begins = [row for row in rows if row.get("event") == "context_begin"]
    ends = [row for row in rows if row.get("event") == "context_end" and row.get("complete") is True]
    if len(begins) != 4 or len(ends) != 4:
        raise ValueError("Journal does not contain four complete contexts")
    queries = [row for row in rows if row.get("event") == "compiler_query"]
    if not queries or [row.get("ordinal") for row in queries] != list(range(1, len(queries) + 1)):
        raise ValueError("Journal compiler queries are not one contiguous sequence")
    for row in queries:
        if row.get("phase") != "nonforced_translation" or row.get("key") != PROGRAM or row.get("forced") is not False:
            raise ValueError("Compiler query is outside the non-forced bound translation")
        if row.get("questions_replay") is not False or row.get("questions_recording") is not True:
            raise ValueError("Compiler query was replayed or not recorded")
    host = _object_fields(rows, HOST_OBJECT)
    sampled_host = _object_fields(rows, "sampled_host_info_not_use_proof")
    pinned = _object_fields(rows, PROFILE_OBJECT)
    base = _object_fields(rows, "sampled_base_profile_not_use_proof")
    if [field["name"] for field in host_fields] != list(host) or host != sampled_host:
        raise ValueError("Host fields disagree with the source declaration or the later sample")
    if [field["name"] for field in profile_fields] != list(pinned):
        raise ValueError("Pinned profile fields disagree with the source Profile declaration")
    if set(base) != set(pinned):
        raise ValueError("Base and pinned profile field sets differ")
    for field in profile_fields:
        if field["type"] == "bool" and pinned[field["name"]] not in (0, 1):
            raise ValueError(f"Boolean profile field {field['name']} is not 0 or 1")
    for field in host_fields:
        if field["type"] == "bool" and host[field["name"]] not in (0, 1):
            raise ValueError(f"Boolean host field {field['name']} is not 0 or 1")
    sample = _object_fields(rows, "bound_state_sample")
    registration = _object_fields(rows, "texture_registration")
    if sample.get("c0_byte88") != HANDLE or sample.get("texture_integer_response") != 0:
        raise ValueError("Bound sample does not repeat the observed handle and integer answer")
    if sample.get("texture_type_response") != CONDITIONAL_TEXTURE_TYPE or registration.get("known") != 1:
        raise ValueError("Sampled registration cannot supply the labeled texture type")
    if registration.get("has_view") != 0 or registration.get("generation_stable_during_sample") != 1:
        raise ValueError("Sampled registration is not the reviewed stable view-less record")
    native = _object_fields(rows, "native")
    if native.get("local_x") != 32 or native.get("shared_bytes") != 3360 or native.get("lowered_ok") != 1:
        raise ValueError("Journal native local size or shared memory differs")
    settings = _object_fields(rows, "native_settings")
    if settings.get("hardware_native_mma") != 1 or settings.get("Ampere_native_matrix_multiply") != 1 or settings.get("SPIRV_mma_software") != 1:
        raise ValueError("Journal matrix policy flags differ")
    digest = next(row for row in rows if row.get("event") == "native_digest_verified")
    if digest.get("sha256") != NATIVE_SHA or digest.get("key") != PROGRAM:
        raise ValueError("Journal native digest pin differs")
    normalized = []
    for row in queries:
        normalized.append({"ordinal": row["ordinal"], "kind": row["kind"], "first": row["first"], "second": row["second"],
                           "answer": row["answer"], "provenance": "observed_consumed"})
    return {"host": host, "pinned_profile": pinned, "base_profile_equals_pinned_sample": base == pinned,
            "queries": normalized, "conditional_texture_type": {
                "kind": "texture_type", "handle": HANDLE, "answer": CONDITIONAL_TEXTURE_TYPE,
                "provenance": "conditional_sampled", "source_enum": "Shader::TextureType::Color2D",
                "actual_compiler_query_observed": False}}


def load_configuration(journal, host_header, profile_header, shader_info_header):
    host_fields = struct_fields(Path(host_header).read_text(), "HostTranslateInfo")
    profile_fields = struct_fields(Path(profile_header).read_text(), "Profile")
    if len(host_fields) != 21 or len(profile_fields) != 77:
        raise ValueError("Source host/profile field counts are not the reviewed 21 and 77")
    color2d = enum_value(Path(shader_info_header).read_text(), "TextureType", "Color2D")
    config = configuration_from_rows(_rows(journal), host_fields, profile_fields, color2d)
    if len(config["queries"]) != 47:
        raise ValueError("Journal compiler queries are not the reviewed 47-step sequence")
    return host_fields, profile_fields, config


def _literal(field, value):
    if field["type"] == "bool":
        return "true" if value else "false"
    if field["type"] == "u64":
        return f"{value}ull"
    return f"{value}u"


def assignment_source(host_fields, profile_fields, config):
    host_lines = [f"    h.{field['name']} = {_literal(field, config['host'][field['name']])};" for field in host_fields]
    profile_lines = [f"    p.{field['name']} = {_literal(field, config['pinned_profile'][field['name']])};" for field in profile_fields]
    query_rows = ",\n".join(
        f'        {{"{item["kind"]}", {item["first"]}u, {item["second"]}u, {item["answer"]}u}}' for item in config["queries"])
    return f'''
struct WhatifExpect {{ const char *kind; uint32_t first, second, answer; }};
static const WhatifExpect kWhatifExpect[] = {{
{query_rows}
}};
static size_t g_whatif_at = 0;
static uint32_t g_whatif_conditional = 0, g_whatif_native_c1 = 0;
static void whatif_log(const char *provenance, const char *kind, uint32_t first, uint32_t second, uint32_t answer) {{
    std::fprintf(stderr, "whatif_query provenance=%s kind=%s first=%u second=%u answer=%u\\n",
                 provenance, kind, first, second, answer);
}}
static void whatif_deny(const char *kind, uint32_t first, uint32_t second) {{
    std::fprintf(stderr, "whatif_deny kind=%s first=%u second=%u\\n", kind, first, second);
    throw Shader::RuntimeError("unobserved compiler query");
}}
static uint32_t whatif_answer(const char *kind, uint32_t first, uint32_t second) {{
    if (g_whatif_at < std::size(kWhatifExpect)) {{
        const WhatifExpect &row = kWhatifExpect[g_whatif_at];
        if (std::strcmp(row.kind, kind) == 0 && row.first == first && row.second == second) {{
            ++g_whatif_at;
            whatif_log("observed_consumed", kind, first, second, row.answer);
            return row.answer;
        }}
    }}
    for (size_t seen = 0; seen < g_whatif_at; ++seen) {{
        const WhatifExpect &row = kWhatifExpect[seen];
        if (std::strcmp(row.kind, kind) == 0 && row.first == first && row.second == second) {{
            whatif_log("observed_repeat", kind, first, second, row.answer);
            return row.answer;
        }}
    }}
    if (std::strcmp(kind, "texture_type") == 0 && first == {HANDLE}u && second == 0u) {{
        ++g_whatif_conditional;
        whatif_log("conditional_sampled", kind, first, second, {CONDITIONAL_TEXTURE_TYPE}u);
        return {CONDITIONAL_TEXTURE_TYPE}u;
    }}
    whatif_deny(kind, first, second);
}}
static void assign_observed_host(Shader::HostTranslateInfo &h) {{
    h = Shader::HostTranslateInfo{{}};
{chr(10).join(host_lines)}
}}
static void assign_observed_profile(Shader::Profile &p) {{
    p = Shader::Profile{{}};
{chr(10).join(profile_lines)}
}}
static void whatif_finish() {{
    if (g_whatif_at != std::size(kWhatifExpect))
        throw Shader::RuntimeError("observed compiler query sequence was not consumed");
    std::fprintf(stderr, "whatif_complete observed_consumed=%zu conditional_sampled=%u native_c1=%u\\n",
                 g_whatif_at, g_whatif_conditional, g_whatif_native_c1);
}}
'''


def _replace_function(text, signature, replacement):
    start = text.find(signature)
    if start < 0 or text.find(signature, start + 1) >= 0:
        raise ValueError(f"Driver function context changed: {signature}")
    brace = text.find("{", start)
    depth = 0
    for index in range(brace, len(text)):
        depth += text[index] == "{"
        depth -= text[index] == "}"
        if depth == 0:
            return text[:start] + replacement + text[index + 1:]
    raise ValueError(f"Unclosed driver function: {signature}")


def sampled_driver(original, assignment):
    if hashlib.sha256(original.encode()).hexdigest() != BAKE_SHA:
        raise ValueError("Frozen offline driver source changed")
    marker = "// ---- device query (--target query):"
    if original.count(marker) != 1 or original.count("#include <dlfcn.h>\n") != 1:
        raise ValueError("Offline driver cut context changed")
    text = original[:original.index(marker)].replace("#include <dlfcn.h>\n", "")
    text = _replace_function(text, "Shader::Profile make_profile()", """Shader::Profile make_profile() {
    Shader::Profile profile;
    assign_observed_profile(profile);
    return profile;
}""")
    text = _replace_function(text, "Shader::HostTranslateInfo host_info()", """Shader::HostTranslateInfo host_info() {
    Shader::HostTranslateInfo info;
    assign_observed_host(info);
    return info;
}""")
    replacements = {
        "u32 ReadCbufValue(u32 index, u32 offset) override": """u32 ReadCbufValue(u32 index, u32 offset) override {
        if (index == 1) {
            if ((offset & 3u) || static_cast<size_t>(offset) + 4 > nvn.c1.size()) whatif_deny("cbuf", index, offset);
            uint32_t value = 0;
            std::memcpy(&value, nvn.c1.data() + offset, 4);
            ++g_whatif_native_c1;
            whatif_log("native_c1_constant", "cbuf", index, offset, value);
            return value;
        }
        return whatif_answer("cbuf", index, offset);
    }""",
        "Shader::TextureType ReadTextureType(u32 handle) override": """Shader::TextureType ReadTextureType(u32 handle) override {
        texture_questions++;
        return static_cast<Shader::TextureType>(whatif_answer("texture_type", handle, 0));
    }""",
        "Shader::TexturePixelFormat ReadTexturePixelFormat(u32) override": """Shader::TexturePixelFormat ReadTexturePixelFormat(u32 handle) override {
        whatif_deny("texture_pixel_format", handle, 0);
        return {};
    }""",
        "bool IsTexturePixelFormatInteger(u32 handle) override": """bool IsTexturePixelFormatInteger(u32 handle) override {
        texture_questions++;
        return whatif_answer("texture_integer", handle, 0) != 0;
    }""",
        "u32 ReadViewportTransformState() override": """u32 ReadViewportTransformState() override {
        return whatif_answer("viewport_transform", 0, 0);
    }""",
        "u32 TextureBoundBuffer() const override": """u32 TextureBoundBuffer() const override {
        return whatif_answer("texture_bound_buffer", 0, 0);
    }""",
        "u32 LocalMemorySize() const override": """u32 LocalMemorySize() const override {
        return whatif_answer("local_memory_size", 0, 0);
    }""",
        "u32 SharedMemorySize() const override": """u32 SharedMemorySize() const override {
        const uint32_t answer = whatif_answer("shared_memory_size", 0, 0);
        if (answer != nvn.shared_memory) throw Shader::RuntimeError("shared memory answer disagrees with native control");
        return answer;
    }""",
        "bool HasHLEMacroState() const override": """bool HasHLEMacroState() const override {
        return whatif_answer("hle_macro_state", 0, 0) != 0;
    }""",
        "bool HasNativeInstructions() const override": """bool HasNativeInstructions() const override {
        return whatif_answer("has_native_instructions", 0, 0) != 0;
    }""",
        "bool IsNativeProgram() const override": """bool IsNativeProgram() const override {
        return whatif_answer("is_native_program", 0, 0) != 0;
    }""",
        "bool IsCbufStorageBufferPointer(u32 index, u32 offset) override": """bool IsCbufStorageBufferPointer(u32 index, u32 offset) override {
        return whatif_answer("cbuf_storage_pointer", index, offset) != 0;
    }""",
    }
    for signature, body in replacements.items():
        text = _replace_function(text, signature, body)
    anchor = "                auto tr = translate(env, false);\n"
    if text.count(anchor) != 1:
        raise ValueError("IR audit insertion context changed")
    answer_call = "            record_answers(pr.answers, infos, envs.data());\n"
    if text.count(answer_call) != 1:
        raise ValueError("Answer recording call context changed")
    text = text.replace(answer_call, """            try {
                record_answers(pr.answers, infos, envs.data());
            } catch (const Shader::RuntimeError&) {
                pr.answers.clear();
                std::fprintf(stderr, "whatif_answer_record_incomplete=1\\n");
            }
""")
    texture_answer = """uint8_t texture_answer(uint32_t handle) {   // (live: 618; an unregistered or zero handle is Color2D, not integer)
    return handle ? static_cast<uint8_t>(Shader::TextureType::Color2D) : 0xFF;
}"""
    if text.count(texture_answer) != 1:
        raise ValueError("Offline texture answer helper changed")
    text = text.replace(texture_answer, f"""uint8_t texture_answer(uint32_t handle) {{
    if (handle == {HANDLE}u) {{
        whatif_log("conditional_sampled", "texture_answer_record", handle, 0, {CONDITIONAL_TEXTURE_TYPE}u);
        ++g_whatif_conditional;
        return {CONDITIONAL_TEXTURE_TYPE};
    }}
    if (handle == 0) {{
        whatif_log("unregistered_handle_marker", "texture_answer_record", 0, 0, 0xffu);
        return 0xff;
    }}
    whatif_deny("texture_answer_record", handle, 0);
    return 0;
}}""")
    if any(token in text for token in ("dlopen(", "dlsym(", "vkCreateInstance(", "vkCreateDevice(", "return Shader::TextureType::Color2D", "0x8000 | offset")):
        raise ValueError("Sampled driver retained a device path or a synthetic answer")
    text = text.replace(anchor, anchor + r'''                size_t matrix_sites = 0;
                for (const auto *block : tr->program.blocks)
                    for (const auto &inst : block->Instructions())
                        matrix_sites += inst.GetOpcode() == Shader::IR::Opcode::MatrixMulAddF16;
                std::fprintf(stderr, "whatif_IR MatrixMulAddF16=%zu\n", matrix_sites);
                if (matrix_sites != 72) throw Shader::RuntimeError("Unexpected matrix count");
''')
    text += r'''
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
    const int status = run_translate(options);
    if (status == 0) whatif_finish();
    return status;
}
'''
    # The assignment helpers use std::size and must appear before the methods.
    insert = text.find("class OfflineEnv")
    if insert < 0:
        raise ValueError("Offline environment class moved")
    text = text[:insert] + assignment + "\n" + text[insert:]
    return text


def query_audit(stderr):
    rows = []
    denied = []
    summary = None
    matrix = None
    for line in stderr.splitlines():
        if line.startswith("whatif_query "):
            fields = dict(item.split("=", 1) for item in line.split()[1:])
            fields["first"] = int(fields["first"])
            fields["second"] = int(fields["second"])
            fields["answer"] = int(fields["answer"])
            rows.append(fields)
        elif line.startswith("whatif_deny "):
            fields = dict(item.split("=", 1) for item in line.split()[1:])
            denied.append({key: int(value) if key != "kind" else value for key, value in fields.items()})
        elif line.startswith("whatif_complete "):
            summary = dict(item.split("=", 1) for item in line.split()[1:])
        elif line.startswith("whatif_IR "):
            matrix = int(line.split("=", 1)[1])
    return rows, denied, summary, matrix


def _private_output(path):
    output = Path(path).resolve()
    if output.exists():
        raise ValueError("Choose a fresh private output directory")
    if output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[:1] not in (("local",), ("work",), ("artifacts",)):
        raise ValueError("Private output must stay under local, work, or artifacts")
    return output


def export(source_repo, seed, journal, result, checked, output, jobs=4, compiler="clang++"):
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ValueError("This build allows one through four CPU jobs")
    frozen_export_pins()
    for path, digest in ((result, RESULT_SHA), (checked, CHECKED_SHA)):
        if file_sha(path) != digest:
            raise ValueError(f"Companion pin differs: {Path(path).name}")
    output = _private_output(output)
    output.mkdir(parents=True)
    snapshot = output / "source"
    snapshot.mkdir()
    archive = subprocess.run(["git", "-C", str(Path(source_repo).resolve()), "archive", TREE, *PREFIXES],
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(snapshot, filter="data")
    host_fields, profile_fields, config = load_configuration(
        journal, snapshot / "gpu/vk/eden/shader_recompiler/host_translate_info.h",
        snapshot / "gpu/vk/eden/shader_recompiler/profile.h",
        snapshot / "gpu/vk/eden/shader_recompiler/shader_info.h")
    seed_bytes, identity = singleton_seed(seed)
    if file_sha(seed) != SINGLETON_SEED_SHA:
        raise ValueError("Seed is not the reviewed exact-stage singleton")
    (output / "input.seed").write_bytes(seed_bytes)
    (output / "sampled-configuration.json").write_text(json.dumps({
        "host_fields": host_fields, "profile_fields": profile_fields, "configuration": config,
        "profile_provenance": PROFILE_OBJECT, "host_provenance": HOST_OBJECT,
        "descriptor_limit_zero_policy": "TranslateProgram applies HostTranslateInfo::ApplyDescriptorLimitPolicy after this assignment; zero limits become 1024 inside that call",
        "production_network_exclusion": "unchanged; this helper does not link gpu/vk/vk_shader.cpp and does not read RX_VK_NETWORK_KEEP",
    }, indent=2) + "\n")
    driver = output / "ndve_sampled_driver.cpp"
    driver.write_text(sampled_driver((snapshot / "nx2/bake_ds/bake_ds.cpp").read_text(),
                                      assignment_source(host_fields, profile_fields, config)))
    cc = Path(shutil.which(compiler) or compiler).absolute()
    env = clean_environment()
    includes = [snapshot / part for part in ("gpu/vk/compat", "gpu/compat", "gpu/vk/eden", "gpu/third_party/suyu", "gpu/vk/sirit/include", "gpu/vk/sirit/src")]
    include_flags = ["-I" + str(path) for path in includes] + ["-I/usr/include/spirv/unified1"]
    cpp = sorted((snapshot / "gpu/vk/eden/shader_recompiler").rglob("*.cpp")) + sorted((snapshot / "gpu/vk/sirit/src").rglob("*.cpp")) + [driver]
    objects = output / "objects"
    objects.mkdir()

    def compile_one(index, source):
        obj = objects / f"{index:04}.o"
        run([str(cc), *FLAGS, *include_flags, "-MD", "-MF", str(obj.with_suffix(".d")), "-c", str(source), "-o", str(obj)],
            env, objects / f"compile-{index:04}")
        return obj

    finished = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(compile_one, index, source) for index, source in enumerate(cpp)]
        for future in as_completed(futures):
            finished.append(future.result())
            if len(finished) % 25 == 0:
                print(f"CPU recompiler sources built: {len(finished)}/{len(cpp)}", flush=True)
    helper = output / "ndve-sampled-export"
    run([str(cc), *FLAGS, *map(str, sorted(finished)), "-pthread", "-o", str(helper)], env, output / "link")
    imports = run(["nm", "-u", str(helper)], env, output / "undefined-imports").decode()
    if re.search(r"\b(vk[A-Z]\w*|dlopen|dlsym)\b", imports):
        raise ValueError("Helper imports Vulkan or a dynamic loader")
    translation = subprocess.run([str(helper), str(output / "input.seed"), str(output / "translated")],
                                 env=env, capture_output=True, timeout=300)
    (output / "translate.stdout").write_bytes(translation.stdout)
    (output / "translate.stderr").write_bytes(translation.stderr)
    queries, denied, summary, matrix = query_audit(translation.stderr.decode(errors="replace"))
    (output / "query-audit.json").write_text(json.dumps({"queries": queries, "denied": denied, "summary": summary, "matrix_sites": matrix}, indent=2) + "\n")
    if translation.returncode:
        raise RuntimeError(f"What-if translation stopped ({translation.returncode}); denied={denied}")
    if summary != {"observed_consumed": "47", "conditional_sampled": str(sum(row["provenance"] == "conditional_sampled" for row in queries)),
                   "native_c1": str(sum(row["provenance"] == "native_c1_constant" for row in queries))} or matrix != 72:
        raise ValueError("What-if completion audit does not match the query log")
    manifest = json.loads((output / "translated/bake_manifest.json").read_text())
    if manifest.get("translated") != 1 or manifest.get("failed") != 0 or manifest.get("mma_software") is not True:
        raise ValueError("Software translation did not complete")
    module = output / "translated/spv" / (PROGRAM + ".5.spv")
    run(["spirv-val", "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout", str(module)], env, output / "validate", 30)
    disassembly = run(["spirv-dis", "--raw-id", str(module)], env, output / "disassemble", 30)
    (output / "reference.spvasm").write_bytes(disassembly)
    if b"CooperativeMatrix" in disassembly:
        raise ValueError("Module contains a cooperative-matrix instruction")
    report = {"schema": "open-ocarina.ndve-sampled-stage-export/v1", "CPU_only": True, "Vulkan_calls": False,
              "identity": identity, "source_tree": TREE, "journal_sha256": JOURNAL_SHA,
              "driver_source_sha256": file_sha(driver), "helper_sha256": file_sha(helper),
              "wrapper_sha256": file_sha(Path(__file__)), "compiler_sha256": file_sha(cc), "CPU_jobs": jobs,
              "singleton_seed_sha256": file_sha(output / "input.seed"), "module_sha256": file_sha(module),
              "module_words": module.stat().st_size // 4, "query_audit_sha256": file_sha(output / "query-audit.json"),
              "configuration_sha256": file_sha(output / "sampled-configuration.json"),
              "host_assignment": "21 observed HostTranslateInfo fields, by source declaration order",
              "profile_assignment": "77 sampled_pinned_profile_not_use_proof fields, by source declaration order",
              "base_profile_sample_equals_pinned_sample": config["base_profile_equals_pinned_sample"],
              "actual_profile_consumption_observed": False,
              "conditional_texture_type_calls": sum(row["provenance"] == "conditional_sampled" for row in queries),
              "native_c1_constant_reads": sum(row["provenance"] == "native_c1_constant" for row in queries),
              "observed_queries_consumed": 47, "encoded_HMMA_sites": 72, "translated_MatrixMulAddF16_sites": 72,
              "SPIRV_validation_passed": True, "production_network_exclusion_changed": False,
              "offline_module_comparison_performed_by_this_tool": False,
              "claims": {"hardware_matrix_execution": False, "native_network_restored": False,
                         "complete_replay_inputs": False, "GPU_execution": False, "native_quality": False}}
    (output / "export-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--checked", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--compiler", default="clang++")
    args = parser.parse_args()
    print(json.dumps(export(args.source_repo, args.seed, args.journal, args.result, args.checked,
                            args.output, args.jobs, args.compiler), indent=2))


if __name__ == "__main__":
    main()
