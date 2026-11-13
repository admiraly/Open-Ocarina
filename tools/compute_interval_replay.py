#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit experimental interval replay profile. Preparation is CPU-only.

The existing physical tools, native helper, plans and evidence are never edited.
Derived native code is private and carries a complete counted-anchor diff.
"""
from __future__ import annotations
import argparse
import copy
import difflib
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
MAGIC = b"OOILRP1\0"
ORIGINAL = "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b"
CANDIDATE = "d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0"
RECORD_SHA = "49da4bdc0c4542b4fef7b5dcbb631d41fb030d80803cde3bb37185722daf9770"
GUARDED_SHA = "0ae9e410ac93d40b4ec73073a303fdcb5fa0921dbc01c81ff1e38a349f82c393"
DRIVER_SHA = "e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f"
ICD_SHA = "14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3"
MANIFEST_SHA = "4c4b5fcaf73318b8edc08406a58e39a2702544f86183e7d894455e7d363206b9"
RECORD_BYTES, GUARD_BYTES = 592, 4096
RECORD_OWNER_BYTES = RECORD_BYTES + 2 * GUARD_BYTES
RECORD_INDEX, RECORD_ID = 59, "interval-record-v1"
DEPENDENCIES = {
    "tools/physical_replay.py": "07172fa9b268fc65ffc5a83b1ddd51b11151c23354388dafca411280d4f87b7f",
    "tools/vulkan_physical_replay.c": "0bacac3eda5c93a50b2ff2efdd3a595a0b260bfee371cd7b3e4da85ed70b538d",
    "tools/compute_interval_lowering.py": "3176a7e0130b5d722a88fdb2d5ab9512fff4afecf78e10828eb6747b38ca00ca",
    "tools/physical_span_contract.py": "f60dabf565b21b5b883d2784541682c27b88df8520bc16fffe5117d115d6ac33",
    "tools/compile_probe.py": "3f545e63c23ff2f3e8b24c77941fb6e2519a2371e913be3df76b87f1667d72af",
    "tools/replay_contract.py": "9c4c4eec3fb027daa311a0017741b2a8d683b7a1941706f1b03a5ca23acd6c3d",
    "patches/hookshot/check_binding_provenance.py": "53b54a68256feba6d60a4919c1f530d0d94fcc8bec004ec559e20099feca7d4a",
    "patches/hookshot/check_dispatch_parameters.py": "ddf942ec838e6578fa3464e3d30288fc9572e4eab30b3d06fae1b4cbd1e96207",
    "patches/hookshot/check_sampled_image_capture.py": "130b2c0be1d2b7392ec4448750b1ed82e03c9da7de8fbc5a2a91b94f63f7d42f",
    "patches/hookshot/check_occurrence_capture.py": "0723a5cb5009d43f39213e420264c801154cc7c24ce6719b797b604ac1ed8f06",
    "patches/hookshot/check_page_table_patch.py": "ea13eb27b0df1d7cb30410316d7a05f44ba9a4e652260ddc7541062234de3a52"
}


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def modules():
    for name, expected in DEPENDENCIES.items():
        need(digest(ROOT / name) == expected, "Pinned dependency changed: " + name)
    return (importlib.import_module("tools.physical_replay"),
            importlib.import_module("tools.compute_interval_lowering"))


def private_new(path):
    path = Path(path).resolve()
    need(not path.exists(), "Choose a new private output directory")
    need(not path.is_relative_to(ROOT) or path.relative_to(ROOT).parts[0] in ("local", "work", "artifacts"),
         "Generated artifacts must remain private")
    return path


class Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, size):
        need(type(size) is int and size >= 0 and self.at + size <= len(self.data), "Truncated native control")
        value = self.data[self.at:self.at + size]; self.at += size
        return value

    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def u64(self): return struct.unpack("<Q", self.take(8))[0]
    def string(self):
        size = self.u32(); need(0 < size <= 4095, "Invalid control path length")
        data = self.take(size); need(b"\0" not in data, "NUL control path")
        return data.decode()


def read_control(data, *, experimental=False):
    r = Reader(data)
    need(r.take(8) == (MAGIC if experimental else b"OOPHYR1\0"), "Control magic differs")
    need(r.u32() == (1 if experimental else 2), "Control version differs")
    frame = {"width": r.u32(), "device": r.u32(), "vendor": r.u32(), "device_id": r.u32(),
             "manifest_sha256": r.take(32).hex()}
    if experimental:
        frame.update(module_sha256=r.take(32).hex(), record_sha256=r.take(32).hex())
    frame.update(shader=r.string(), push=r.string(), push_size=r.u32(), root_offset=r.u32(),
                 groups=[r.u32() for _ in range(3)])
    count = r.u32(); need(0 < count <= 128, "Invalid allocation count")
    frame["allocations"] = [{"id": r.string(), "size": r.u64(), "initial": r.string(), "expected": r.string()}
                            for _ in range(count)]
    frame.update(table=r.string(), entries=r.u32())
    count = r.u32(); need(count <= frame["entries"] <= 4194304, "Invalid mapping count")
    frame["mappings"] = [(r.u32(), r.u32(), r.u64()) for _ in range(count)]
    count = r.u32(); need(count <= 64, "Too many images")
    images = []
    for _ in range(count):
        row = {"words": [r.u32() for _ in range(7)]}
        n = r.u32(); need(n <= 4096, "Too many image subresources")
        row["subresources"] = [(r.u32(), r.u32(), r.u64(), r.string()) for _ in range(n)]
        images.append(row)
    frame["images"] = images
    count = r.u32(); need(count <= 64, "Too many image views")
    frame["image_views"] = [[r.u32() for _ in range(11)] for _ in range(count)]
    count = r.u32(); need(count <= 64, "Too many samplers")
    frame["samplers"] = [[r.u32() for _ in range(16)] for _ in range(count)]
    count = r.u32(); need(0 < count <= 256, "Too many descriptors")
    frame["descriptors"] = [[r.u32() for _ in range(7)] + [r.u64(), r.u64()] for _ in range(count)]
    need(r.at == len(data), "Trailing native control bytes")
    return frame


def write_control(frame, *, experimental=True):
    data = bytearray(MAGIC if experimental else b"OOPHYR1\0")
    def u32(*values): data.extend(struct.pack("<" + "I" * len(values), *values))
    def u64(*values): data.extend(struct.pack("<" + "Q" * len(values), *values))
    def string(value):
        encoded = str(value).encode(); need(0 < len(encoded) <= 4095 and b"\0" not in encoded, "Invalid native path")
        u32(len(encoded)); data.extend(encoded)
    u32(1 if experimental else 2, frame["width"], frame["device"], frame["vendor"], frame["device_id"])
    data.extend(bytes.fromhex(frame["manifest_sha256"]))
    if experimental:
        data.extend(bytes.fromhex(frame["module_sha256"])); data.extend(bytes.fromhex(frame["record_sha256"]))
    string(frame["shader"]); string(frame["push"]); u32(frame["push_size"], frame["root_offset"], *frame["groups"])
    u32(len(frame["allocations"]))
    for row in frame["allocations"]:
        string(row["id"]); u64(row["size"]); string(row["initial"]); string(row["expected"])
    string(frame["table"]); u32(frame["entries"], len(frame["mappings"]))
    for index, allocation, offset in frame["mappings"]: u32(index, allocation); u64(offset)
    u32(len(frame["images"]))
    for row in frame["images"]:
        u32(*row["words"], len(row["subresources"]))
        for mip, layer, size, path in row["subresources"]:
            u32(mip, layer); u64(size); string(path)
    u32(len(frame["image_views"]))
    for row in frame["image_views"]: u32(*row)
    u32(len(frame["samplers"]))
    for row in frame["samplers"]: u32(*row)
    u32(len(frame["descriptors"]))
    for row in frame["descriptors"]: u32(*row[:7]); u64(*row[7:])
    return bytes(data)


def check_record(data):
    need(len(data) == RECORD_BYTES, "Record extent must be exactly592 bytes")
    need(struct.unpack_from("<4I", data) == (6, 6, 0, 6), "Frozen width-specific record header differs")
    rows = []
    for i in range(12):
        row = struct.unpack_from("<QQQ6I", data, 16 + 48 * i)
        low, high, base, binding, element, width, route, reserved0, reserved1 = row
        need(0 <= base <= low < high and width == (4 if i < 6 else 16), "Invalid record extent/typed width")
        expected_route = binding - 2 if 2 <= binding <= 6 and element == 0 else 5 + element if binding == 7 and element < 3 else -1
        need(route == expected_route and 0 <= route < 8 and reserved0 == reserved1 == 0, "Invalid constant typed route/reserved record")
        if i not in (0, 6): need(rows[-1][1] <= low, "Record intervals overlap or are unsorted")
        rows.append(row)
    return rows


def guarded_record(data):
    check_record(data)
    return b"\x6b" * GUARD_BYTES + data + b"\xb6" * GUARD_BYTES


RECORD_C = r'''
#define INTERVAL_RECORD_INDEX 59
#define INTERVAL_RECORD_BYTES 592
#define INTERVAL_GUARD_BYTES 4096
#define INTERVAL_OWNER_BYTES (INTERVAL_RECORD_BYTES + 2 * INTERVAL_GUARD_BYTES)
static int interval_device_caps(const VkPhysicalDeviceLimits *limits,
                                uint32_t max_push_descriptors, VkBool32 null_descriptor) {
    REQUIRE(max_push_descriptors>=15 && limits->maxStorageBufferRange>=INTERVAL_RECORD_BYTES &&
            (!limits->minStorageBufferOffsetAlignment ||
             INTERVAL_GUARD_BYTES % limits->minStorageBufferOffsetAlignment==0));
    REQUIRE(limits->maxPerStageDescriptorStorageBuffers>=9 && limits->maxDescriptorSetStorageBuffers>=9 &&
            limits->maxPerStageResources>=15);
    REQUIRE(limits->maxPerStageDescriptorUniformBuffers>=2 && limits->maxDescriptorSetUniformBuffers>=2 &&
            limits->maxPerStageDescriptorSampledImages>=4 && limits->maxDescriptorSetSampledImages>=4 &&
            limits->maxPerStageDescriptorSamplers>=4 && limits->maxDescriptorSetSamplers>=4);
    REQUIRE(null_descriptor==VK_TRUE);
    return 0;
}
static uint32_t interval_u32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static uint64_t interval_u64(const unsigned char *p) {
    return interval_u32(p) | (uint64_t)interval_u32(p+4)<<32;
}
static int interval_cpu_contract(uint32_t allocation_count, Allocation *allocations,
                                 uint32_t mapping_count, const Mapping *mappings,
                                 uint32_t descriptor_count, const Binding *descriptors) {
    REQUIRE(allocation_count == 60 && descriptor_count == 15);
    Allocation *record = &allocations[INTERVAL_RECORD_INDEX];
    REQUIRE(!strcmp(record->id, "interval-record-v1") && record->gpu.size == INTERVAL_OWNER_BYTES);
    REQUIRE(!strcmp(record->initial, record->expected));
    for (uint32_t i=0;i<INTERVAL_RECORD_INDEX;i++)
        REQUIRE(strcmp(allocations[i].id, "interval-record-v1") && !strcmp(allocations[i].expected, "-"));
    for (uint32_t i=0;i<mapping_count;i++) REQUIRE(mappings[i].allocation < INTERVAL_RECORD_INDEX);
    unsigned found=0;
    for (uint32_t i=0;i<descriptor_count;i++) {
        const Binding *d=&descriptors[i];
        REQUIRE(d->binding<=12 && d->element<(d->binding==7?3u:1u));
        uint32_t required_type=d->binding<2?6u:(d->binding<8||d->binding==12)?7u:1u;
        REQUIRE(d->type==required_type && (d->null!=0)==(d->binding==7&&d->element>0));
        if (d->binding == 12) {
            found++;
            REQUIRE(d->element==0 && d->type==7 && !d->null && d->allocation==INTERVAL_RECORD_INDEX &&
                    d->offset==INTERVAL_GUARD_BYTES && d->size==INTERVAL_RECORD_BYTES && !d->view && !d->sampler);
        } else if (d->type!=1 && !d->null) REQUIRE(d->allocation<INTERVAL_RECORD_INDEX);
    }
    REQUIRE(found==1);
    unsigned char *owned=load(record->initial,INTERVAL_OWNER_BYTES);
    REQUIRE(owned);
    int valid=1;
    for (unsigned i=0;i<INTERVAL_GUARD_BYTES;i++)
        valid &= owned[i]==0x6b && owned[INTERVAL_GUARD_BYTES+INTERVAL_RECORD_BYTES+i]==0xb6;
    const unsigned char *p=owned+INTERVAL_GUARD_BYTES;
    valid &= interval_u32(p)==6 && interval_u32(p+4)==6 && interval_u32(p+8)==0 && interval_u32(p+12)==6;
    uint64_t prior_high=0;
    for (unsigned i=0;i<12;i++) {
        const unsigned char *row=p+16+48*i;
        uint64_t low=interval_u64(row), high=interval_u64(row+8), base=interval_u64(row+16);
        uint32_t binding=interval_u32(row+24), element=interval_u32(row+28), width=interval_u32(row+32), route=interval_u32(row+36);
        int expected=(binding>=2&&binding<=6&&element==0)?(int)binding-2:
                     binding==7&&element<3?5+(int)element:-1;
        valid &= base<=low && low<high && width==(i<6?4u:16u) && expected>=0 && route==(uint32_t)expected &&
                 interval_u32(row+40)==0 && interval_u32(row+44)==0;
        if (i!=0 && i!=6) valid &= prior_high<=low;
        prior_high=high;
    }
    free(owned);
    REQUIRE(valid);
    return 0;
}
'''


STATS_C = r'''
static void interval_json_string(const char *text) {
    putchar('"');
    for(const unsigned char *p=(const unsigned char*)text;*p;p++) {
        if(*p=='"'||*p=='\\') putchar('\\');
        if(*p<32) printf("\\u%04x",*p); else putchar(*p);
    }
    putchar('"');
}
static void interval_stats_json(uint32_t count, const VkPipelineExecutableStatisticKHR *stats) {
    printf("\"executable_statistics\":[");
    for(uint32_t i=0;i<count;i++) {
        if(i) putchar(',');
        printf("{\"name\":"); interval_json_string(stats[i].name);
        printf(",\"description\":"); interval_json_string(stats[i].description);
        printf(",\"format\":%u,\"value\":",stats[i].format);
        switch(stats[i].format) {
        case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_BOOL32_KHR: printf("%s",stats[i].value.b32?"true":"false"); break;
        case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_INT64_KHR: printf("%lld",(long long)stats[i].value.i64); break;
        case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR: printf("%llu",(unsigned long long)stats[i].value.u64); break;
        case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_FLOAT64_KHR:
            if(isfinite(stats[i].value.f64)) printf("%.17g",stats[i].value.f64); else printf("null"); break;
        default: printf("null"); break;
        }
        putchar('}');
    }
    putchar(']');
}
'''

STATS_QUERY_C = r'''
    PFN_vkGetPipelineExecutableStatisticsKHR get_statistics =
        (PFN_vkGetPipelineExecutableStatisticsKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutableStatisticsKHR");
    REQUIRE(get_statistics);
    VkPipelineExecutableInfoKHR statistics_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INFO_KHR, .pipeline = pipeline, .executableIndex = 0};
    uint32_t statistics_count = 0;
    CHECK(get_statistics(device, &statistics_info, &statistics_count, NULL));
    REQUIRE(statistics_count <= 128);
    VkPipelineExecutableStatisticKHR statistics[128];
    memset(statistics, 0, sizeof(statistics));
    for(uint32_t i=0;i<statistics_count;i++) statistics[i].sType=VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_STATISTIC_KHR;
    CHECK(get_statistics(device, &statistics_info, &statistics_count, statistics));
    if (compile_only) {
        printf("{\"interval_profile\":1,\"compile_only\":true,\"dispatch_executed\":false,\"actual_subgroup_size\":%u,\"experiment_manifest_sha256\":\"%s\",\"module_sha256\":\"%s\",\"record_payload_sha256\":\"%s\",\"device_vendor\":%u,\"device_id\":%u,", executable_properties.subgroupSize, manifest_hex, module_hex, record_hex, properties.properties.vendorID, properties.properties.deviceID);
        interval_stats_json(statistics_count,statistics);
        printf("}\n");
        vkDestroyPipeline(device,pipeline,NULL);
        vkDestroyShaderModule(device,module,NULL);
        vkDestroyPipelineLayout(device,layout,NULL);
        vkDestroyDescriptorSetLayout(device,set_layout,NULL);
        vkDestroyDevice(device,NULL);
        vkDestroyInstance(instance,NULL);
        return 0;
    }
'''


def derive_native(source):
    need(hashlib.sha256(source.encode()).hexdigest() == DEPENDENCIES["tools/vulkan_physical_replay.c"],
         "Original native source fingerprint differs")
    substitutions = []
    def replace(before, after, label):
        nonlocal source
        need(source.count(before) == 1, "Counted native anchor differs: " + label)
        source = source.replace(before, after, 1); substitutions.append(label)
    original = source
    replace('"OOPHYR1\\0"', '"OOILRP1\\0"', "independent-control-magic")
    replace('REQUIRE(u32() == 2);', 'REQUIRE(u32() == 1);', "independent-control-version")
    replace('REQUIRE((required_width == 8 || required_width == 16 || required_width == 32) &&',
            'REQUIRE(required_width == 32 &&', "fixed-width32-only")
    replace('    const int pinned = 1;', r'''    unsigned char module_digest[32], record_digest[32];
    char module_hex[65], record_hex[65];
    REQUIRE(fread(module_digest,1,32,control)==32 && fread(record_digest,1,32,control)==32);
    for(unsigned i=0;i<32;i++) { snprintf(module_hex+2*i,3,"%02x",module_digest[i]); snprintf(record_hex+2*i,3,"%02x",record_digest[i]); }
    REQUIRE(!strcmp(module_hex,"''' + ORIGINAL + r'''") || !strcmp(module_hex,"''' + CANDIDATE + r'''"));
    REQUIRE(!strcmp(record_hex,"''' + RECORD_SHA + r'''"));
    const int pinned = 1;''', "module-record-identity-frame")
    replace('int main(int argc, char **argv) {', (RECORD_C + STATS_C) + '\nint main(int argc, char **argv) {', "readonly-record-cpu-contract")
    replace('!strcmp(argv[3], "--execute-approved-plan")));',
            '!strcmp(argv[3], "--execute-approved-plan") || !strcmp(argv[3], "--compile-approved-plan")));', "compile-only-mode-argument")
    replace('    int execute = !strcmp(argv[3], "--execute-approved-plan");',
            '    int compile_only = !strcmp(argv[3], "--compile-approved-plan");\n    int execute = !strcmp(argv[3], "--execute-approved-plan");', "compile-only-mode-flag")
    replace('    if (!execute) {', '''    REQUIRE(push_size == 88 && root_offset == 48 && groups[0] == 2 && groups[1] == 1 && groups[2] == 1);
    int interval_status = interval_cpu_contract(allocation_count, allocations, mapping_count, mappings, descriptor_count, descriptors);
    if (interval_status) return interval_status;
    if (!execute && !compile_only) {''', "pre-vulkan-contract")
    replace('    vkGetPhysicalDeviceFeatures2(physical, &supported);', '''    vkGetPhysicalDeviceFeatures2(physical, &supported);
    int interval_caps_status = interval_device_caps(&properties.properties.limits,
        push_properties.maxPushDescriptors, supported_robustness.nullDescriptor);
    if (interval_caps_status) return interval_caps_status;''', "union-record-device-caps-and-null")
    replace('        int result = make_buffer(physical, device, size, buffer_usage, 1, &a->gpu);',
            '''        int has_address = i != INTERVAL_RECORD_INDEX;
        VkBufferUsageFlags usage = has_address ? buffer_usage : buffer_usage & ~VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
        int result = make_buffer(physical, device, size, usage, has_address, &a->gpu);''', "record-has-no-bda")
    replace('UINT64_C(10000000000)', 'UINT64_C(2000000000)', "two-second-fence")
    replace('source_manifest_', 'experiment_manifest_', "separate-experiment-report-identity")
    replace('    int compared = 0, matches = 1;', '''    printf("{\\"interval_profile\\":1,\\"module_sha256\\":\\"%s\\",\\"record_payload_sha256\\":\\"%s\\",", module_hex, record_hex);
    int compared = 0, matches = 1;''', "module-record-output-identity")
    replace('printf("{\\"schema\\":1,', 'printf("\\"schema\\":1,', "one-json-object")
    replace('Open-Ocarina owned physical replay', 'Open-Ocarina interval experimental replay', "application-label")
    replace('    REQUIRE(executable_properties.subgroupSize == required_width);',
            '    REQUIRE(executable_properties.subgroupSize == required_width);' + STATS_QUERY_C, "structured-executable-statistics")
    replace('    printf("}\\n");\n    vkDestroyFence(device, fence, NULL);',
            '    printf(","); interval_stats_json(statistics_count,statistics);\n    printf("}\\n");\n    vkDestroyFence(device, fence, NULL);', "dispatch-statistics-output")
    block_start = source.index('    VkDescriptorSetLayoutCreateInfo set_info = {')
    block_end = source.index('    VkDescriptorBufferInfo buffer_infos[MAX_BIND];', block_start)
    pipeline_block = source[block_start:block_end]
    need(pipeline_block.count('vkCreateComputePipelines(') == 1 and pipeline_block.count('if (compile_only)') == 1,
         "Pipeline movement anchors differ")
    source = source[:block_start] + source[block_end:]
    replace('    VkBufferUsageFlags buffer_usage =', pipeline_block + '    VkBufferUsageFlags buffer_usage =',
            "pipeline-before-allocation-for-compile-only")

    need(source.count("vkCmdDispatch(") == 1 and "vkDeviceWaitIdle" not in source, "Dispatch/idle scope changed")
    need(source.index("interval_cpu_contract(allocation_count") < source.index("vkCreateInstance("), "CPU contract must precede Vulkan")
    need(source.index("interval_device_caps(&properties.properties.limits") < source.index("vkCreateDevice("), "Added caps must precede logical device/pipeline")
    diff = "".join(difflib.unified_diff(original.splitlines(True), source.splitlines(True),
        fromfile="tools/vulkan_physical_replay.c", tofile="private/vulkan_compute_interval_replay.c"))
    return source, diff, substitutions


def build_native(output):
    source, diff, anchors = derive_native((ROOT / "tools/vulkan_physical_replay.c").read_text())
    output = Path(output)
    c_path, helper = output / "vulkan_compute_interval_replay.c", output / "native"
    formatted = subprocess.run(["clang-format", "-style={BasedOnStyle: LLVM, IndentWidth: 4, ColumnLimit: 100, AllowShortFunctionsOnASingleLine: None, AllowShortIfStatementsOnASingleLine: Never}"],
                               input=source, text=True, capture_output=True, check=True, timeout=15).stdout
    source = formatted
    original_source = (ROOT / "tools/vulkan_physical_replay.c").read_text()
    diff = "".join(difflib.unified_diff(original_source.splitlines(True), source.splitlines(True),
                                     fromfile="tools/vulkan_physical_replay.c", tofile="private/vulkan_compute_interval_replay.c"))
    c_path.write_text(source); (output / "native.diff").write_text(diff)
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "vulkan"], text=True, timeout=15))
    command = ["cc", "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", str(c_path), *flags, "-o", str(helper)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    for path in [c_path, output / "native.diff", helper]: path.chmod(0o444 if path.suffix in (".c", ".diff") else 0o555)
    return {"source_sha256": digest(c_path), "binary_sha256": digest(helper), "diff_sha256": digest(output / "native.diff"),
            "source_path": c_path.name, "helper_path": helper.name, "flags": command[1:7], "counted_anchors": anchors,
            "pipeline_created": False, "dispatch_executed": False}


def prove_selector_owner(state):
    manifest = state["manifest"]
    bindings = {(row["binding"], row["element"]): row for row in manifest["bindings"]}
    ubo = bindings[(0, 0)]
    need(ubo["type"] == 6 and ubo["range"] == 2224 and ubo["view"] in state["views"], "Exact visible C0 descriptor missing")
    owner = state["views"][ubo["view"]]["allocation"]
    need(all(row["allocation"] != owner for row in state["mappings"]), "C0 aliases a physical page owner")
    for binding in bindings.values():
        if binding["type"] == 7 and binding["view"] is not None:
            need(state["views"][binding["view"]]["allocation"] != owner, "C0 aliases an admitted storage descriptor")
    return {"allocation": owner, "view": ubo["view"], "offset": ubo["offset"], "range": ubo["range"],
            "physical_page_aliases": 0, "storage_descriptor_aliases": 0}


def assert_graph_extension(original, frame, module_sha256, guarded_path):
    """The only control changes are identity, module, one owner and one descriptor."""
    need(original["width"] == frame["width"] == 32 and original["groups"] == [2, 1, 1], "Subgroup/grid confound")
    need(len(original["allocations"]) == RECORD_INDEX and len(original["descriptors"]) == 14,
         "This profile requires the exact original59-owner/14-descriptor graph")
    need(all(row["expected"] == "-" and row["id"] != RECORD_ID for row in original["allocations"]),
         "Original expectations/owner conflict")
    need(module_sha256 in (ORIGINAL, CANDIDATE) and frame.get("module_sha256") == module_sha256 and
         frame.get("record_sha256") == RECORD_SHA, "Module/record profile differs")
    record = {"id": RECORD_ID, "size": RECORD_OWNER_BYTES, "initial": str(guarded_path), "expected": str(guarded_path)}
    need(frame["allocations"] == original["allocations"] + [record], "Original allocation graph changed")
    descriptor = [12, 0, 7, 0, RECORD_INDEX, 0, 0, GUARD_BYTES, RECORD_BYTES]
    need(frame["descriptors"] == original["descriptors"] + [descriptor], "Original descriptors or new readonly range changed")
    for key in ("device", "vendor", "device_id", "push", "push_size", "root_offset", "groups", "table", "entries",
                "mappings", "images", "image_views", "samplers"):
        need(frame[key] == original[key], "Original graph field changed: " + key)
    need(all(index < RECORD_INDEX for _, index, _ in frame["mappings"]), "Record owner became guest-mapped")
    need(frame["push_size"] == 88 and frame["root_offset"] == 48 and frame["vendor"] == 0x8086 and frame["device_id"] == 0x56a0,
         "Push/device ABI differs")


def prepare(capture, bundle, output, driver):
    old, lower = modules()
    capture, bundle, driver = Path(capture).resolve(), Path(bundle).resolve(), Path(driver).resolve()
    need(digest(capture / "manifest.json") == MANIFEST_SHA, "Owned input bank/graph identity differs")
    need(digest(driver) == ICD_SHA, "Reviewed E5 ICD differs")
    driver_identity = importlib.import_module("tools.compile_probe").driver_identity(driver)
    need(driver_identity["library_sha256"] == DRIVER_SHA, "Reviewed E5 library differs")
    for name, expected in (("original.spv", ORIGINAL), ("candidate.spv", CANDIDATE), ("records.bin", RECORD_SHA)):
        path = bundle / name
        need(path.is_file() and not path.is_symlink() and digest(path) == expected, "Frozen lowering artifact differs: " + name)
    state = old.validate(capture)
    need(state["manifest"]["kind"] == "target" and state["manifest"]["mode"] == "frozen-diagnostic" and
         state["manifest"]["expected_source"] == "none", "Unsupported original evidence scope")
    selector_owner = prove_selector_owner(state)
    output = private_new(output); output.mkdir(parents=True)
    proof = lower.build(bundle / "original.spv", capture / "manifest.json", output / "proof")
    need(proof["candidate_sha256"] == CANDIDATE and proof["record_sha256"] == RECORD_SHA and proof["record_bytes"] == RECORD_BYTES,
         "Recomputed lowering/record identity differs")
    need((output / "proof/candidate.spv").read_bytes() == (bundle / "candidate.spv").read_bytes() and
         (output / "proof/records.bin").read_bytes() == (bundle / "records.bin").read_bytes(), "Frozen bundle is not the exact rebuilt transformation")
    record = (output / "proof/records.bin").read_bytes(); check_record(record)
    actual_disassembly = subprocess.run(["spirv-dis", "--raw-id", "--no-indent", str(output / "proof/candidate.spv")],
                                        check=True, capture_output=True, text=True, timeout=30).stdout
    _, helpers = lower.discover_target((output / "proof/original-dis.stdout").read_text())
    partitions = {int(width): [lower.Row(**row) for row in rows] for width, rows in proof["intervals"].items()}
    actual_shape = lower.audit_lookup_shape(actual_disassembly, helpers, partitions)
    actual_preservation = lower.verify_preservation((bundle / "original.spv").read_bytes(), (bundle / "candidate.spv").read_bytes(), helpers)
    (output / "proof/actual-candidate.spvasm").write_text(actual_disassembly)
    need(proof["frozen_inputs"]["manifest_sha256"] == MANIFEST_SHA and
         proof["frozen_inputs"]["ubo0_visible_sha256"] == "778a893b3523a792183d46f8d8e0e0d517e3957b808cdea2be97294b3b008bcf",
         "Selected C0 visible input bank changed")
    old.prepare(capture, output / "base", policy=32)
    original_data = (output / "base/plan.bin").read_bytes()
    original = read_control(original_data)
    need(write_control(original, experimental=False) == original_data, "Original control roundtrip differs")
    (output / "inputs").mkdir()
    def put(data):
        sha = hashlib.sha256(data).hexdigest(); path = output / "inputs" / (sha + ".bin")
        if not path.exists(): path.write_bytes(data); path.chmod(0o444)
        return path
    guarded = put(guarded_record(record)); need(guarded.stem == GUARDED_SHA, "Guarded record identity differs"); candidate = put((bundle / "candidate.spv").read_bytes())
    native = build_native(output)
    union_layout = copy.deepcopy(state["layout"])
    union_layout["bindings"].append({"set": 0, "binding": 12, "type": 7, "count": 1})
    reflection = importlib.import_module("tools.compile_probe").reflect(candidate.read_bytes())
    need(reflection == union_layout, "Candidate must have exactly the union layout and original push/local shape")
    inventory = []
    for directory in (output / "base/inputs", output / "inputs"):
        for path in sorted(directory.iterdir()):
            need(not path.is_symlink() and path.name == digest(path) + ".bin", "Frozen input inventory differs")
            inventory.append({"path": path.relative_to(output).as_posix(), "size": path.stat().st_size, "sha256": path.stem})
    proof_report = {"original_source_sha256": MANIFEST_SHA, "full_original_access_certificate": state["target_access_certificate"],
        "selector_owner": selector_owner, "rebuilt_lowering_report_sha256": digest(output / "proof/report.json"),
        "actual_candidate_lookup_audit": actual_shape, "actual_binary_preservation": actual_preservation,
        "record_payload_sha256": RECORD_SHA, "record_payload_bytes": RECORD_BYTES, "record_rows_per_width": {"4": 6, "16": 6},
        "record_max_index": 11, "record_header_and_rows_fit_visible_range": True,
        "physical_miss_span_proof_reused_only_via_exact_suffix_and_main_preservation": True}
    (output / "admission-proof.json").write_text(json.dumps(proof_report, indent=2) + "\n")
    profile = {"schema": "open-ocarina.compute-interval-experimental-profile/v1", "mode": "frozen-diagnostic",
        "original_manifest_sha256": MANIFEST_SHA, "original_shader_sha256": ORIGINAL, "candidate_shader_sha256": CANDIDATE,
        "record": {"id": RECORD_ID, "allocation_index": RECORD_INDEX, "allocation_bytes": RECORD_OWNER_BYTES,
                   "descriptor_binding": 12, "descriptor_offset": GUARD_BYTES, "descriptor_range": RECORD_BYTES,
                   "payload_sha256": RECORD_SHA, "guarded_sha256": guarded.stem, "guest_mapped": False, "bda_enabled": False},
        "original_allocation_graph": [{"id": row["id"], "size": row["size"]} for row in original["allocations"]],
        "union_layout": union_layout, "push_bytes": 88, "root_push_offset": 48, "local_size": [64, 1, 1], "group_count": [2, 1, 1],
        "required_width": 32, "require_full_subgroups": True, "driver": driver_identity,
        "native": native, "base_plan_sha256": digest(output / "base/plan.bin"), "dependency_sha256": DEPENDENCIES, "adapter_source_sha256": digest(Path(__file__)),
        "proof_artifact_sha256": {path.relative_to(output).as_posix(): digest(path) for path in sorted((output / "proof").iterdir()) if path.is_file()},
        "input_inventory": inventory, "admission_proof_sha256": digest(output / "admission-proof.json"),
        "compiler_environment": {"HS_SPILL_BATCH": "1", "shader_spilling_rate": "1", "HS_CS32_MAX_INSTS": None,
                                 "HS_FORCE_SIMD8": None, "HS_SUBGROUP8": None},
        "runtime_environment": {"HS_PUSH_SHADOW": None}, "dynamic_device_caps_gate": "Before logical device/pipeline:15 push resources;9 perstage/set SSBO;2 UBO;4 sampled images/samplers;592B range;4096B alignment;nullDescriptor true",
        "execution_admitted": False, "pipeline_created": False, "dispatch_executed": False,
        "limits": ["Owned fixed graph only; no live capture admission or historical atomicity proof.",
                   "Compile statistics are compiler metadata, not GPU time, speedup or FPS.",
                   "Native expected bytes check only the extra record; original59 allocation correctness requires full baseline/reference comparisons.",
                   "Any Vulkan phase requires separate root review and exact-plan authorization."]}
    (output / "profile.json").write_text(json.dumps(profile, indent=2) + "\n"); profile_sha = digest(output / "profile.json")
    plans = {}
    for label, sha, shader in (("baseline32", ORIGINAL, Path(original["shader"])), ("candidate32", CANDIDATE, candidate)):
        frame = copy.deepcopy(original); frame.update(manifest_sha256=profile_sha, module_sha256=sha, record_sha256=RECORD_SHA, shader=str(shader))
        frame["allocations"].append({"id": RECORD_ID, "size": RECORD_OWNER_BYTES, "initial": str(guarded), "expected": str(guarded)})
        frame["descriptors"].append([12, 0, 7, 0, RECORD_INDEX, 0, 0, GUARD_BYTES, RECORD_BYTES])
        assert_graph_extension(original, frame, sha, guarded)
        data = write_control(frame); need(read_control(data, experimental=True) == frame, "Experimental control roundtrip differs")
        path = output / (label + ".bin"); path.write_bytes(data); path.chmod(0o444)
        check = subprocess.run([str(output / "native"), str(path), str(output), "--check-only"],
                               check=True, capture_output=True, text=True, timeout=15)
        metadata = json.loads(check.stdout); need(metadata.get("dispatch_executed") is False, "CPU check scope differs")
        plans[label] = {"path": path.name, "sha256": digest(path), "module_sha256": sha, "native_cpu_check": metadata}
    summary = {"schema": "open-ocarina.compute-interval-prepared/v1", "profile_sha256": profile_sha, "native": native,
        "plans": plans, "sequence": ["baseline32", "baseline32", "candidate32"], "original_allocations": 59,
        "original_bytes": sum(row["size"] for row in original["allocations"]), "extra_readonly_owner_bytes": RECORD_OWNER_BYTES,
        "added_descriptor_count": 1, "total_push_descriptors": 15, "pipeline_created": False, "dispatch_executed": False,
        "review_required_before_any_vulkan": True}
    (output / "prepared.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def validate_prepared(prepared, label, authorization):
    modules()
    prepared = Path(prepared).resolve()
    old = importlib.import_module("tools.physical_replay")
    profile = old._json(prepared / "profile.json")
    summary = old._json(prepared / "prepared.json")
    profile_sha = digest(prepared / "profile.json")
    need(summary["profile_sha256"] == profile_sha and profile["adapter_source_sha256"] == digest(Path(__file__)),
         "Experimental host/profile changed after freeze")
    need(profile["dependency_sha256"] == DEPENDENCIES and profile["driver"]["library_sha256"] == DRIVER_SHA,
         "Experimental dependency/driver scope differs")
    need(label in ("baseline32", "candidate32") and label in summary["plans"], "Unknown fixed32 case")
    plan = summary["plans"][label]; control = prepared / plan["path"]
    need(control.parent == prepared and control.is_file() and not control.is_symlink() and
         digest(control) == plan["sha256"] == authorization, "Exact experimental plan authorization required")
    native = profile["native"]; helper = prepared / native["helper_path"]
    need(helper.parent == prepared and digest(helper) == native["binary_sha256"] and
         digest(prepared / native["source_path"]) == native["source_sha256"] and
         digest(prepared / "native.diff") == native["diff_sha256"], "Derived native source/helper/diff changed")
    inventory = {}
    for row in profile["input_inventory"]:
        path = (prepared / row["path"]).resolve()
        need(path.is_relative_to(prepared) and not (prepared / row["path"]).is_symlink() and path.is_file(), "Experimental artifact escapes profile")
        need(path.stat().st_size == row["size"] and digest(path) == row["sha256"] and path.stem == row["sha256"],
             "Frozen experimental input changed")
        need(str(path) not in inventory, "Duplicate experimental artifact")
        inventory[str(path)] = row
    need(digest(prepared / "base/plan.bin") == profile["base_plan_sha256"], "Frozen original control changed")
    for name, expected in profile["proof_artifact_sha256"].items():
        path = (prepared / name).resolve()
        need(path.is_relative_to(prepared) and not (prepared / name).is_symlink() and digest(path) == expected,
             "Frozen transformation evidence changed")
    need(profile["record"]["guarded_sha256"] == GUARDED_SHA, "Guarded record expectation changed")
    original = read_control((prepared / "base/plan.bin").read_bytes())
    frame = read_control(control.read_bytes(), experimental=True)
    module = ORIGINAL if label == "baseline32" else CANDIDATE
    need(frame["manifest_sha256"] == profile_sha and digest(frame["shader"]) == module,
         "Module/common profile mismatch")
    guarded = prepared / "inputs" / (profile["record"]["guarded_sha256"] + ".bin")
    assert_graph_extension(original, frame, module, guarded)
    need(guarded.read_bytes() == guarded_record((prepared / "proof/records.bin").read_bytes()) and
         digest(prepared / "proof/records.bin") == RECORD_SHA, "Record/guard ownership changed")
    referenced = {frame["shader"], frame["push"], frame["table"]}
    for row in frame["allocations"]:
        referenced.add(row["initial"])
        if row["expected"] != "-": referenced.add(row["expected"])
    for image in frame["images"]:
        referenced.update(row[3] for row in image["subresources"])
    need(referenced <= set(inventory), "Control names an unpinned GPU input")
    need(digest(prepared / "admission-proof.json") == profile["admission_proof_sha256"], "Admission proof changed")
    return profile, frame, helper, control


def run(prepared, label, output, driver, authorization, *, compile_only=False, timeout=90):
    """Future exact-plan operation. A separate GPU/device lease is mandatory."""
    profile, frame, helper, control = validate_prepared(prepared, label, authorization)
    prepared = Path(prepared).resolve(); driver = Path(driver).resolve()
    identify = importlib.import_module("tools.compile_probe").driver_identity
    need(digest(driver) == ICD_SHA and identify(driver)["library_sha256"] == DRIVER_SHA, "Reviewed E5 driver differs")
    need(1 <= timeout <= 120, "Process timeout must be1..120 seconds")
    output = private_new(output); output.mkdir(parents=True)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(("HS_", "VK_", "MESA_", "INTEL_")) or key == "shader_spilling_rate": environment.pop(key)
    environment.update(VK_DRIVER_FILES=str(driver), MESA_SHADER_CACHE_DIR=str(output / "shader-cache"),
                       HS_SPILL_BATCH="1", shader_spilling_rate="1")
    mode = "--compile-approved-plan" if compile_only else "--execute-approved-plan"
    with (output / "native.json").open("w") as out, (output / "native.stderr").open("w") as err:
        process = subprocess.Popen([str(helper), str(control), str(output), mode], stdout=out, stderr=err,
                                   env=environment, start_new_session=True)
        try: status = process.wait(timeout=timeout)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
            raise ValueError("Bounded experimental process interrupted; only its own process group stopped")
    need(status == 0, "Experimental native phase failed; preserve private diagnostics and stop sequence")
    native = importlib.import_module("tools.physical_replay")._json(output / "native.json")
    need(native.get("interval_profile") == 1 and native.get("module_sha256") == frame["module_sha256"] and
         native.get("experiment_manifest_sha256") == frame["manifest_sha256"] and native.get("record_payload_sha256") == RECORD_SHA and
         native.get("actual_subgroup_size") == 32 and native.get("device_vendor") == 0x8086 and native.get("device_id") == 0x56a0,
         "Native experimental identity/actual width differs")
    need(identify(driver) == profile["driver"], "Driver changed during experimental phase")
    statistics = native.get("executable_statistics")
    need(isinstance(statistics, list) and len(statistics) <= 128, "Structured executable statistics missing")
    report = {"schema": "open-ocarina.compute-interval-result/v1", "label": label, "compile_only": compile_only,
        "profile_sha256": frame["manifest_sha256"], "original_manifest_sha256": MANIFEST_SHA,
        "plan_sha256": authorization, "module_sha256": frame["module_sha256"], "native_binary_sha256": digest(helper),
        "driver": profile["driver"], "native": native, "pipeline_optimization_disabled": False,
        "allocation_graph": profile["original_allocation_graph"], "record_allocation_bytes": RECORD_OWNER_BYTES,
        "record_guarded_sha256": GUARDED_SHA,
        "executable_statistics": statistics, "dispatch_executed": not compile_only,
        "limitations": ["Fixed owned graph only; no historical live equivalence, image-quality or FPS claim.",
                        "Executable compiler statistics are not GPU timing or a speed ratio."]}
    if compile_only:
        need(native.get("compile_only") is True and native.get("dispatch_executed") is False, "Compile-only phase dispatched")
    else:
        need(native.get("dispatch_count") == 1 and native.get("expected_comparison_performed") is True and
             native.get("comparison_passed") is True and len(native.get("allocations", [])) == 60,
             "Complete output/readonly record check failed")
        artifacts = []
        for index, row in enumerate(native["allocations"]):
            path = output / f"allocation-{index}.bin"
            expected_size = frame["allocations"][index]["size"]
            need(row["index"] == index and row["size"] == expected_size and path.stat().st_size == expected_size,
                 "Original/record output extent differs")
            artifacts.append({"path": path.name, "size": expected_size, "sha256": digest(path)})
        need(artifacts[-1]["sha256"] == profile["record"]["guarded_sha256"], "Record payload or guards changed")
        report.update(allocations=artifacts, complete_original_allocations=59, readonly_record_and_guards_unchanged=True)
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def compare(left, right):
    modules(); old = importlib.import_module("tools.physical_replay")
    left, right = Path(left).resolve(), Path(right).resolve()
    a, b = old._json(left / "result.json"), old._json(right / "result.json")
    for row in (a, b):
        need(row.get("schema") == "open-ocarina.compute-interval-result/v1" and row.get("dispatch_executed") is True and
             row.get("compile_only") is False and row.get("complete_original_allocations") == 59 and
             row.get("readonly_record_and_guards_unchanged") is True and len(row.get("allocations", [])) == 60,
             "Comparison needs complete executed experimental results")
    for key in ("profile_sha256", "native_binary_sha256", "driver", "allocation_graph", "record_allocation_bytes"):
        need(a[key] == b[key], "Experimental comparison identity differs: " + key)
    need(len(a["allocation_graph"]) == 59 and len({row["id"] for row in a["allocation_graph"]}) == 59 and
         sum(row["size"] for row in a["allocation_graph"]) == 1163919360 and
         all(row.get("record_guarded_sha256") == GUARDED_SHA and row["allocations"][-1]["sha256"] == GUARDED_SHA for row in (a, b)) and
         a["driver"]["library_sha256"] == DRIVER_SHA,
         "Incomplete/unreviewed experimental output graph")
    expected_sizes = [row["size"] for row in a["allocation_graph"]] + [RECORD_OWNER_BYTES]
    differences = []
    for index, (x, y, size) in enumerate(zip(a["allocations"], b["allocations"], expected_sizes)):
        for directory, artifact in ((left, x), (right, y)):
            need(artifact["path"] == f"allocation-{index}.bin", "Output index path differs")
            path = directory / artifact["path"]
            need(path.is_file() and not path.is_symlink() and path.stat().st_size == size == artifact["size"] and
                 digest(path) == artifact["sha256"], "Complete experimental output integrity differs")
        if x["sha256"] != y["sha256"]: differences.append(index)
    return {"complete_original_allocations": 59, "original_bytes": sum(expected_sizes[:-1]),
        "readonly_record_and_guards_compared_bytes": RECORD_OWNER_BYTES, "all_bytes_match": not differences,
        "different_allocation_indices": differences, "modules": [a["module_sha256"], b["module_sha256"]],
        "widths": [32, 32], "historical_live_equivalence_established": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("capture", type=Path); p.add_argument("bundle", type=Path)
    p.add_argument("--output", type=Path, required=True); p.add_argument("--driver", type=Path, required=True)
    for name in ("compile", "execute"):
        p = sub.add_parser(name); p.add_argument("prepared", type=Path)
        p.add_argument("--label", choices=("baseline32", "candidate32"), required=True)
        p.add_argument("--output", type=Path, required=True); p.add_argument("--driver", type=Path, required=True)
        p.add_argument("--authorize-plan", required=True); p.add_argument("--timeout", type=int, default=90)
    p = sub.add_parser("compare"); p.add_argument("left", type=Path); p.add_argument("right", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare": result = prepare(args.capture, args.bundle, args.output, args.driver)
        elif args.command == "compare": result = compare(args.left, args.right)
        else: result = run(args.prepared, args.label, args.output, args.driver, args.authorize_plan,
                           compile_only=args.command == "compile", timeout=args.timeout)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, struct.error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
