#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare a frozen E5 descriptor-array compile-only host; never open Vulkan.

The prepared native binary has a future single-pipeline mode. This wrapper only
builds it and calls --validate, which returns before vkCreateInstance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SHA = "0e4fc9d15438f078bf206e40d8838052a91a9e16af696f44cbb77a7b8a94838e"
PLAN_SHA = "b162f6a20ed01da6835474f873b08a37cc4c74fd2183d92a262c52d081349f2f"
BASE_HOST_SHA = "69f5900a6993a0c9ab468fd85292f89f9d7405f7c0647c2383517d0daa8f81cc"
DRIVER_SHA = "e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f"
ICD_SHA = "14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3"
MODULES = {"pointer": "5c2b9e427817f278e6a2ff8e348624c7ed85ae82298af2c53c4f064dae0ac248",
           "value": "a7c784c6f41f63666345a02e4a285d0e5e6e88a9d5f60435b70a2971f9bb1691"}
CONTROLLED_OPTIONS = ("HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate",
                      "INTEL_DEBUG", "HS_SPILL_PCT", "HS_RA_DEBUG", "HS_FORCE_SIMD8", "HS_LARGE_GRF",
                      "HS_SUBGROUP8", "HS_LARGE_GRF_BIT", "RX_VK_WARP64", "RX_VK_NOOPT")
NATIVE_SAFE = {"HS_SPILL_BATCH": "1", "HS_PUSH_SHADOW": "1", "shader_spilling_rate": "1"}
FLAGS = ["-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror"]
LAYOUT = "48 5\n0 0 7 2\n0 1 7 2\n0 2 7 1\n0 3 7 1\n0 4 7 1\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()


def native_safe_environment(ambient):
    env = dict(ambient)
    for key in CONTROLLED_OPTIONS:
        env.pop(key, None)
    env.update(NATIVE_SAFE)
    return env, {"name": "native-safe", "controlled_options": {key: env.get(key) for key in CONTROLLED_OPTIONS},
                 "full_environment_snapshot": False}


def driver_identity(icd):
    data = Path(icd).read_bytes()
    if digest(data) != ICD_SHA:
        raise ValueError("ICD differs from frozen E5 profile")
    declaration = json.loads(data)
    library = Path(declaration["ICD"]["library_path"])
    if not library.is_absolute():
        library = Path(icd).resolve().parent / library
    if not library.is_file() or digest(library.read_bytes()) != DRIVER_SHA:
        raise ValueError("Driver library differs from frozen E5 profile")
    return {"profile": "cache-safe-e5", "icd_sha256": ICD_SHA, "library_sha256": DRIVER_SHA,
            "library_name": library.name}


def reflect_profile(data, variant):
    """Exact synthetic layout/stage audit, not general SPIR-V reflection."""
    if variant not in MODULES or not 20 <= len(data) <= 65536 or len(data) % 4:
        raise ValueError("Unsupported bounded module")
    words = struct.unpack(f"<{len(data)//4}I", data)
    if words[0] != 0x07230203 or not 0 < words[3] <= 2048:
        raise ValueError("Invalid module header")
    types, constants, decorated, offsets, variables = {}, {}, {}, {}, []
    entries, modes, capabilities, memory_models, length_queries, pointer_functions = [], [], [], [], 0, []
    cursor = 5
    while cursor < len(words):
        count, opcode = words[cursor] >> 16, words[cursor] & 65535
        if count == 0 or cursor + count > len(words):
            raise ValueError("Malformed SPIR-V instruction")
        args = words[cursor+1:cursor+count]
        if opcode == 15:
            raw = struct.pack(f"<{len(args)-2}I", *args[2:])
            entries.append((args[0], args[1], raw.split(b"\0", 1)[0]))
        elif opcode == 16:
            modes.append(args)
        elif opcode == 17:
            capabilities.extend(args)
        elif opcode == 14:
            memory_models.append(args)
        elif 19 <= opcode <= 33:
            types[args[0]] = opcode, args[1:]
        elif opcode == 43 and len(args) == 3:
            constants[args[1]] = args[2]
        elif opcode == 71:
            decorated.setdefault(args[0], {})[args[1]] = args[2:]
        elif opcode == 72 and args[2] == 35:
            offsets[args[0], args[1]] = args[3]
        elif opcode == 59:
            variables.append(args[:3])
        elif opcode == 68:
            length_queries += 1
        elif opcode == 54:
            pointer_functions.append(args[0])
        cursor += count
    expected_caps = {1, 11, 5301, 5308, 5347} | ({4441} if variant == "pointer" else set())
    if set(capabilities) != expected_caps or len(capabilities) != len(expected_caps):
        raise ValueError("Unexpected module capabilities")
    if len(entries) != 1 or entries[0][0] != 5 or entries[0][2] != b"main" or modes != [(entries[0][1], 17, 64, 1, 1)]:
        raise ValueError("Expected literal compute main64x1x1 only")
    if memory_models != [(5348, 1)] or length_queries != 6:
        raise ValueError("Unexpected memory model or runtime typed length queries")

    def integer_width(identity):
        opcode, args = types[identity]
        if opcode == 21 and args[1] == 0:
            return args[0] // 8
        if opcode == 23 and types[args[0]] == (21, (32, 0)):
            return args[1] * 4
        raise ValueError("Unexpected typed element")

    aliases, push_count, input_count = [], 0, 0
    for pointer, identity, storage in variables:
        if storage == 7:
            continue
        if storage == 1:
            input_count += 1
            continue
        if storage not in (9, 12):
            raise ValueError("Unexpected shared/private/global storage")
        if types[pointer][0] != 32 or types[pointer][1][0] != storage:
            raise ValueError("Unexpected variable pointer")
        pointee = types[pointer][1][1]
        if storage == 9:
            push_count += 1
            members = types[pointee]
            if members[0] != 30 or [integer_width(member) for member in members[1]] != [16, 16, 8, 4, 4]:
                raise ValueError("Unexpected push-constant members")
            if [offsets.get((pointee, index)) for index in range(5)] != [0, 16, 32, 40, 44]:
                raise ValueError("Expected exact48-byte push layout")
            continue
        decoration = decorated.get(identity, {})
        binding = decoration.get(33)
        if decoration.get(34) != (0,) or binding is None or len(binding) != 1:
            raise ValueError("Expected explicit set0 bindings")
        binding = binding[0]
        array_count = 1
        if types[pointee][0] == 28:
            pointee, count_id = types[pointee][1]
            array_count = constants.get(count_id)
        if types[pointee][0] != 30 or 2 not in decorated.get(pointee, {}):
            raise ValueError("Expected StorageBuffer block")
        fields = types[pointee][1]
        if len(fields) != 1 or offsets.get((pointee, 0)) != 0 or types[fields[0]][0] != 29:
            raise ValueError("Expected single-member runtime array block")
        element_type = types[fields[0]][1][0]
        stride = decorated.get(fields[0], {}).get(6)
        if binding in (0, 1):
            width = integer_width(element_type)
            if width not in (4, 8, 16) or stride != (width,) or array_count != 2:
                raise ValueError("Unexpected typed descriptor-array shape")
        elif binding == 2:
            fields2 = types[element_type]
            if fields2[0] != 30 or [integer_width(member) for member in fields2[1]] != [8, 8, 8, 4, 4]:
                raise ValueError("Unexpected interval record fields")
            if [offsets.get((element_type, index)) for index in range(5)] != [0, 8, 16, 24, 28] or stride != (32,):
                raise ValueError("Unexpected interval record layout")
            width = 32
        elif binding in (3, 4):
            width = integer_width(element_type)
            if width != {3: 8, 4: 16}[binding] or stride != (width,):
                raise ValueError("Unexpected query/result runtime array")
        else:
            raise ValueError("Unexpected descriptor binding")
        if binding >= 2 and array_count != 1:
            raise ValueError("Metadata descriptors cannot be arrays")
        aliases.append({"set": 0, "binding": binding, "count": array_count, "element_bytes": width})
    expected_aliases = sorted([(binding, 2, width) for binding in (0, 1) for width in (4, 8, 16)] + [(2, 1, 32), (3, 1, 8), (4, 1, 16)])
    if sorted((row["binding"], row["count"], row["element_bytes"]) for row in aliases) != expected_aliases or push_count != 1 or input_count != 1:
        raise ValueError("Descriptor aliases, push count or input profile differ")
    returned_pointers = [identity for identity in pointer_functions if types[identity][0] == 32 and types[identity][1][0] == 12]
    if len(returned_pointers) != (3 if variant == "pointer" else 0):
        raise ValueError("Unexpected storage-pointer helper count")
    return {"stage": "compute", "entry_point": "main", "local_size": [64, 1, 1], "shared_memory_bytes": 0,
            "push_constant_bytes": 48, "descriptor_mode": "push", "descriptor_alias_declarations": aliases,
            "bindings": [{"set": 0, "binding": index, "type": 7, "count": 2 if index < 2 else 1} for index in range(5)],
            "capabilities": sorted(capabilities), "typed_array_length_queries": length_queries,
            "storage_pointer_return_functions": len(returned_pointers)}


NATIVE_VALIDATOR = r'''
/* Bounded native validation returns before the Vulkan instance boundary. Full
 * alias/type reflection and SHA pinning are also required by the wrapper. */
static int array_header_profile(const uint32_t *code, size_t words) {
    unsigned entries = 0, local = 0, lengths = 0, storage = 0, push = 0, shared = 0;
    unsigned counts[5] = {0}, bindings[2048], sets[2048];
    if (words < 5 || code[0] != 0x07230203 || code[3] >= 2048) return 0;
    for (unsigned i=0; i<2048; ++i) bindings[i]=sets[i]=UINT32_MAX;
    for (size_t at=5; at<words;) {
        unsigned n=code[at]>>16, op=code[at]&65535;
        if (!n || at+n>words) return 0;
        const uint32_t *p=code+at;
        if (op==15) {
            if (n<5 || p[1]!=5 || memcmp(p+3,"main",5)) return 0;
            ++entries;
        } else if (op==16) {
            if (n!=6 || p[2]!=17 || p[3]!=64 || p[4]!=1 || p[5]!=1) return 0;
            ++local;
        } else if (op==68) ++lengths;
        else if (op==71 && n==4 && (p[2]==33 || p[2]==34)) {
            if (p[1]>=2048) return 0;
            if (p[2]==33) bindings[p[1]]=p[3]; else sets[p[1]]=p[3];
        } else if (op==59 && n>=4) {
            if (p[3]==4) ++shared;
            if (p[3]==9) ++push;
            if (p[3]==12) {
                if (p[2]>=2048 || bindings[p[2]]>4 || sets[p[2]]!=0) return 0;
                ++counts[bindings[p[2]]]; ++storage;
            }
        }
        at+=n;
    }
    return entries==1 && local==1 && lengths==6 && storage==9 && push==1 && !shared &&
           counts[0]==3 && counts[1]==3 && counts[2]==1 && counts[3]==1 && counts[4]==1;
}

static int native_safe_policy(void) {
    static const char *keys[] = {"HS_CS32_MAX_INSTS", "HS_SPILL_BATCH", "HS_PUSH_SHADOW", "shader_spilling_rate",
        "INTEL_DEBUG", "HS_SPILL_PCT", "HS_RA_DEBUG", "HS_FORCE_SIMD8", "HS_LARGE_GRF", "HS_SUBGROUP8",
        "HS_LARGE_GRF_BIT", "RX_VK_WARP64", "RX_VK_NOOPT"};
    for (unsigned i=0; i<sizeof(keys)/sizeof(keys[0]); ++i) {
        const char *value=getenv(keys[i]);
        if (i>=1 && i<=3) { if (!value || strcmp(value,"1")) return 0; }
        else if (value) return 0;
    }
    return getenv("VK_DRIVER_FILES") != NULL;
}
'''


INTERNAL_REPRESENTATIONS = r'''
            PFN_vkGetPipelineExecutableInternalRepresentationsKHR get_ir =
                (PFN_vkGetPipelineExecutableInternalRepresentationsKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutableInternalRepresentationsKHR");
            if (!get_ir) return 1;
            uint32_t ir_count = 0;
            CHECK(get_ir(device, &stat_info, &ir_count, NULL));
            if (ir_count > 32) return 1;
            VkPipelineExecutableInternalRepresentationKHR *irs = calloc(ir_count, sizeof(*irs));
            if (!irs && ir_count) return 1;
            for (unsigned r = 0; r < ir_count; ++r) irs[r].sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INTERNAL_REPRESENTATION_KHR;
            CHECK(get_ir(device, &stat_info, &ir_count, irs));
            size_t total_ir_bytes = 0;
            for (unsigned r = 0; r < ir_count; ++r) {
                if (irs[r].dataSize > 16*1024*1024 || total_ir_bytes + irs[r].dataSize > 32*1024*1024) return 1;
                total_ir_bytes += irs[r].dataSize;
                irs[r].pData = calloc(irs[r].dataSize + 1, 1);
                if (!irs[r].pData) return 1;
            }
            CHECK(get_ir(device, &stat_info, &ir_count, irs));
            printf(",\"internal_representations\":[");
            for (unsigned r = 0; r < ir_count; ++r) {
                if (r) putchar(',');
                printf("{\"name\":"); json_string(irs[r].name);
                printf(",\"is_text\":%s,\"bytes\":%zu", irs[r].isText ? "true" : "false", irs[r].dataSize);
                if (irs[r].isText) { printf(",\"text\":"); json_string((const char *)irs[r].pData); }
                printf("}"); free(irs[r].pData);
            }
            free(irs); printf("]");
'''


def adapted_host(original):
    if digest(original.encode()) != BASE_HOST_SHA:
        raise ValueError("Frozen base host changed")
    replacements = [
        ("#include <time.h>", "#include <time.h>\n#include <unistd.h>\n" + NATIVE_VALIDATOR),
        ("int main(int argc, char **argv) {", "int main(int argc, char **argv) {\n    const int validate_only = argc==6 && !strcmp(argv[1], \"--validate\");\n    if (validate_only) { ++argv; --argc; }"),
        ("    VkApplicationInfo app =", '''    if (push_size!=48 || count!=5 || binding_counts[0]!=5 || !array_header_profile(code,(size_t)byte_count/4)) return 2;
    for (unsigned i=0; i<5; ++i)
        if (bindings[0][i].binding!=i || bindings[0][i].descriptorType!=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
            bindings[0][i].descriptorCount!=(i<2 ? 2U : 1U)) return 2;
    if (!pinned || required_width!=32) return 2;
    if (validate_only) { puts("{\\"validated_before_vulkan\\":true,\\"vulkan_instance_created\\":false,\\"native_profile\\":\\"array-pointers/v1\\"}"); free(code); return 0; }
    if (!native_safe_policy()) { fprintf(stderr,"Native-safe compiler policy or explicit ICD is missing\\n"); return 2; }
    alarm(25); /* one bounded compilation, no idle wait on failure */
    VkApplicationInfo app ='''),
        ("VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported12};",
         "VkPhysicalDeviceVulkan11Features supported11 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &supported12};\n    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported11};"),
        ("VkPhysicalDevicePushDescriptorPropertiesKHR push_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR};",
         "VkPhysicalDeviceRobustness2PropertiesEXT robustness_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_PROPERTIES_EXT};\n    VkPhysicalDevicePushDescriptorPropertiesKHR push_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR, .pNext = &robustness_properties};"),
        ("if (!push_extension || total_descriptors", "if (!supported11.variablePointersStorageBuffer || !supported12.shaderStorageBufferArrayNonUniformIndexing ||\n        !executable_extension || !executable.pipelineExecutableInfo || !push_extension || total_descriptors"),
        ("    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable =", '''    if (properties.properties.vendorID!=0x8086 || properties.properties.deviceID!=0x56a0 ||
        properties.properties.limits.maxPushConstantsSize<48 || properties.properties.limits.maxPerStageDescriptorStorageBuffers<7 ||
        properties.properties.limits.maxDescriptorSetStorageBuffers<7 || properties.properties.limits.maxComputeWorkGroupInvocations<64 ||
        properties.properties.limits.maxComputeWorkGroupSize[0]<64) { fprintf(stderr,"Device/profile limits differ\\n"); return 1; }
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable ='''),
        (".pNext = &enabled13, .uniformBufferStandardLayout = VK_TRUE, .bufferDeviceAddress = VK_TRUE};",
         ".pNext = &enabled13, .uniformBufferStandardLayout = VK_TRUE, .bufferDeviceAddress = VK_TRUE, .shaderStorageBufferArrayNonUniformIndexing = VK_TRUE};"),
        ("VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled12,",
         "VkPhysicalDeviceVulkan11Features enabled11 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &enabled12, .variablePointersStorageBuffer = VK_TRUE};\n    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled11,"),
        (".flags = executable.pipelineExecutableInfo ? VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR : 0,",
         ".flags = VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR | VK_PIPELINE_CREATE_CAPTURE_INTERNAL_REPRESENTATIONS_BIT_KHR,"),
        ('    printf(",\\"features\\":{\\"robustBufferAccess\\":true,\\"robustBufferAccess2\\":true,"',
         '    printf(",\\"features\\":{\\"variablePointersStorageBuffer\\":true,\\"shaderStorageBufferArrayNonUniformIndexing\\":true,\\"robustBufferAccess\\":true,\\"robustBufferAccess2\\":true,"'),
        ('            free(stats); printf("]}");', '            free(stats); printf("]");\n' + INTERNAL_REPRESENTATIONS + '\n            printf("}");'),
        ('    printf(",\\"pipeline_create_seconds\\":%.9f,\\"executables\\":[", elapsed);',
         '    printf(",\\"robust_storage_bounds_alignment\\":%llu,\\"robust_uniform_bounds_alignment\\":%llu",\n        (unsigned long long)robustness_properties.robustStorageBufferAccessSizeAlignment, (unsigned long long)robustness_properties.robustUniformBufferAccessSizeAlignment);\n    printf(",\\"pipeline_create_seconds\\":%.9f,\\"executables\\":[", elapsed);'),
    ]
    for before, after in replacements:
        if original.count(before) != 1:
            raise ValueError("Base host source adaptation anchor changed")
        original = original.replace(before, after, 1)
    for forbidden in ("vkCmd", "vkQueueSubmit", "vkGetDeviceQueue", "vkDeviceWaitIdle", "vkQueueWaitIdle", "vkCreateCommandPool"):
        if forbidden in original:
            raise ValueError("Compile-only host has an execution operation")
    return original


def prepare(args):
    output = args.output.resolve()
    if output.exists() or not any(output.is_relative_to(ROOT / name) for name in ("local", "work", "artifacts")):
        raise ValueError("Use a fresh private output directory")
    identity = driver_identity(args.driver)
    generator = ROOT / "tools/descriptor_array_pointer_experiment.py"
    plan = ROOT / "research/descriptor-array-pointer-plan.json"
    if digest(generator.read_bytes()) != GENERATOR_SHA or digest(plan.read_bytes()) != PLAN_SHA:
        raise ValueError("Frozen generator or semantic plan changed")
    original = (ROOT / "tools/vulkan_compile_probe.c").read_text()
    host = adapted_host(original)
    _, policy = native_safe_environment(os.environ)
    inputs = {}
    for variant, path in (("pointer", args.pointer), ("value", args.value)):
        data = path.read_bytes()
        if digest(data) != MODULES[variant]:
            raise ValueError("Module differs from frozen " + variant)
        inputs[variant] = (data, reflect_profile(data, variant))
    output.mkdir(parents=True)
    host_path = output / "compile-array.c"
    host_path.write_text(host)
    layout = output / "layout.txt"
    layout.write_text(LAYOUT)
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"], check=True, capture_output=True, text=True, timeout=15).stdout)
    executable = output / "compile-array"
    subprocess.run([args.cc, *FLAGS, str(host_path), *flags, "-o", str(executable)], check=True, capture_output=True, text=True, timeout=30)
    modules = []
    for variant, (data, reflection) in inputs.items():
        path = output / (variant + ".spv")
        path.write_bytes(data)
        subprocess.run([args.validator, "--target-env", "vulkan1.2", str(path)], check=True, capture_output=True, text=True, timeout=15)
        native_validation = subprocess.run([str(executable), "--validate", str(path), str(layout), "pinned32", "0"], check=True, capture_output=True, text=True, timeout=5)
        validation = json.loads(native_validation.stdout)
        if validation != {"validated_before_vulkan": True, "vulkan_instance_created": False, "native_profile": "array-pointers/v1"}:
            raise ValueError("Native CPU validator returned an unexpected profile")
        modules.append({"variant": variant, "path": path.name, "sha256": MODULES[variant], "reflection": reflection,
                        "spirv_validation_passed": True, "native_cpu_validation": validation,
                        "future_command": ["./compile-array", path.name, "layout.txt", "pinned32", "0"]})
    prepared_plan = {"schema": 1, "profile": "array-pointers/v1", "prepare_only": True,
                     "gpu_execution_authorized": False, "pipeline_creation_authorized": False,
                     "driver_identity": identity, "driver_policy": policy,
                     "generator_sha256": GENERATOR_SHA, "semantic_plan_sha256": PLAN_SHA,
                     "wrapper_sha256": digest(Path(__file__).read_bytes()), "base_host_sha256": BASE_HOST_SHA,
                     "native_host_source_sha256": digest(host.encode()), "native_host_binary_sha256": digest(executable.read_bytes()),
                     "host_build_flags": FLAGS, "layout_sha256": digest(LAYOUT.encode()), "modules": modules,
                     "tool_versions": {"cc": subprocess.run([args.cc, "--version"], check=True, capture_output=True, text=True, timeout=5).stdout.splitlines()[0],
                                       "spirv_val": subprocess.run([args.validator, "--version"], check=True, capture_output=True, text=True, timeout=5).stdout.splitlines()[0]},
                     "process_timeout_seconds": 30, "native_alarm_seconds": 25,
                     "required_subgroup_size": 32, "full_subgroups": True, "shader_optimization_disabled": False,
                     "capture_statistics": True, "capture_internal_representations": True,
                     "vulkan_instance_created": False, "pipeline_created": False, "dispatch_executed": False,
                     "feature_support_known": False,
                     "limitations": ["Preparation and native CPU validation stop before vkCreateInstance.",
                                     "Exact static profile validation is not a native address-lowering certificate.",
                                     "Future compile needs root grant and an exclusive GPU lease.",
                                     "All enabled optional features must be supported; no uniform-indexing fallback."]}
    plan_bytes = canonical(prepared_plan)
    (output / "prepared-plan.json").write_bytes(plan_bytes)
    result = {"prepared_plan_sha256": digest(plan_bytes), "plan": prepared_plan}
    (output / "preparation.json").write_bytes(canonical(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--pointer", type=Path, required=True)
    parser.add_argument("--value", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--validator", default="spirv-val")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, IndexError) as error:
        print(str(error))
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
