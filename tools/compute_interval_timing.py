#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Separate, bounded paired timing protocol. prepare/check perform no Vulkan calls.

Execution requires external exclusive GPU authorization of the frozen new plan.
Profile 02, its helper, inputs, controls and evidence are never modified.
"""
from __future__ import annotations
import argparse
import copy
import difflib
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shlex
import signal
import struct
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
MAGIC = b"OOCITM1\0"
BASE_ADAPTER_SHA = "9a5e3835c251047f51ba23e9042ac9b821c9e6543adc9f979186d10afcdecb85"
BASE_C_SHA = "9b67fbdec5487cd5138395455b58c7148114ee902f7bfa643bebc30cb741e55f"
BASE_PROFILE_SHA = "384a4064fc0aa593e4f9984122fe3f93dfaf089dfed608ee8fb0bacbace658e7"
BASE_PLANS = {"baseline32": "bc1e6c3a2788190bd16d922c6e5a33b038d6774af02df3325408c0c9d43c26a4",
              "candidate32": "2f29fb6fc6104eccbd52137591119d2ecdbd6856ab3b4a4cd8d7732118e139c9"}
BASE_RESULT_SHA = "077895e5db733accf85bb5b97ff7996bdc2a15a112478696a82f94a53451aa47"
WARMUPS_PER_MODULE, ABBA_REPEATS, DISPATCHES = 2, 4, 20
WRITABLE_INDEX, WRITABLE_BYTES = 7, 4194304
WRITABLE_SHA = "323334824a3f64ff80ba0cb8f8602c8f6dd7be3384a32353bbd8e1eb0628c7c2"
FORMAT_STYLE = "{BasedOnStyle: LLVM, IndentWidth: 4, ColumnLimit: 100, AllowShortFunctionsOnASingleLine: None, AllowShortIfStatementsOnASingleLine: Never}"


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def base_module():
    need(digest(ROOT / "tools/compute_interval_replay.py") == BASE_ADAPTER_SHA, "Frozen 02 adapter changed")
    base = importlib.import_module("tools.compute_interval_replay")
    base.modules()
    return base


def protocol():
    return [(0, False), (0, False), (1, False), (1, False)] + [(m, True) for m in (0, 1, 1, 0) * 4]


def timestamp_delta(start, end, bits, period):
    need(type(bits) is int and 1 <= bits <= 64 and math.isfinite(period) and period > 0,
         "Invalid timestamp capability")
    need((2.0 ** bits) * period > 120e9, "Timestamp wrap horizon must exceed whole process bound")
    mask = (1 << bits) - 1
    need(type(start) is int and type(end) is int and 0 <= start <= (1 << 64)-1 and 0 <= end <= (1 << 64)-1,
         "Invalid raw timestamp")
    ticks = ((end & mask) - (start & mask)) & mask
    nanoseconds = ticks * period
    need(ticks > 0 and math.isfinite(nanoseconds) and nanoseconds < 2e9, "Invalid bounded dispatch interval")
    return ticks, nanoseconds


def encode_control(frame, candidate, oracles):
    base = base_module()
    need(frame["module_sha256"] == base.ORIGINAL and len(oracles) == 60, "Paired plan requires original baseline and 60 oracles")
    blob = base.write_control(frame)
    body = bytearray(MAGIC + struct.pack('<IQ', 1, len(blob) + 8) + blob[12:])
    def string(value):
        data = str(value).encode()
        need(0 < len(data) <= 4095 and b'\0' not in data, "Invalid timing path")
        body.extend(struct.pack('<I', len(data))); body.extend(data)
    body.extend(struct.pack('<5I', 2, 4, 20, 7, 60))
    string(candidate); body.extend(bytes.fromhex(base.CANDIDATE))
    for path in oracles: string(path)
    return bytes(body)


def decode_control(data):
    base = base_module(); reader = base.Reader(data)
    need(reader.take(8) == MAGIC and reader.u32() == 1, "Timing control magic/version differs")
    end = reader.u64()
    need(20 <= end <= len(data), "Timing base extent differs")
    frame = base.read_control(base.MAGIC + struct.pack('<I', 1) + data[20:end], experimental=True)
    reader.at = end
    need([reader.u32() for _ in range(5)] == [2, 4, 20, 7, 60], "Fixed timing schedule/reset/oracle count differs")
    candidate = reader.string()
    need(reader.take(32).hex() == base.CANDIDATE, "Candidate digest frame differs")
    oracles = [reader.string() for _ in range(60)]
    need(reader.at == len(data), "Trailing timing control bytes")
    return frame, candidate, oracles


def audit_writes(text):
    """Strict reachable store roots; no inference from past unchanged output alone."""
    _, lowering = base_module().modules()
    module = lowering.Assembly(text)
    entries = [a[1] for _, op, a, _ in module.rows if op == 'OpEntryPoint' and a[0] == 'GLCompute' and a[2] == 'main']
    need(len(entries) == 1, "Expected one compute entry")
    pending, reachable = list(entries), set()
    while pending:
        function = pending.pop()
        if function in reachable: continue
        need(function in module.functions, "Unresolved called function")
        reachable.add(function)
        pending += [a[1] for _, op, a, _ in module.functions[function] if op == 'OpFunctionCall']
    def root(pointer, seen=None):
        seen = set() if seen is None else seen
        need(pointer not in seen and pointer in module.definitions, "Unresolved store pointer")
        seen = seen | {pointer}
        _, op, args, _ = module.definitions[pointer]
        if op == 'OpVariable':
            storage, _ = module.args(args[0], 'OpTypePointer')
            need(storage == args[1], "Variable storage mismatch")
            return storage, pointer
        need(op in ('OpAccessChain', 'OpInBoundsAccessChain'), "Unsupported store pointer origin")
        return root(args[1], seen)
    stores, locals_ = 0, 0
    for function in reachable:
        for _, op, args, _ in module.functions[function]:
            if op == 'OpStore':
                storage, variable = root(args[0])
                if storage == 'Function': locals_ += 1
                else:
                    need(storage == 'StorageBuffer' and module.binding(variable) == 6,
                         "Reachable nonlocal write outside binding6")
                    stores += 1
            else:
                need(not any(word in op for word in ('Atomic', 'Store', 'Write')) and not op.startswith('OpCopyMemory'),
                     "Unsupported reachable memory-writing operation: " + op)
    need(stores > 0, "No identified writable descriptor")
    return {'reachable_functions':len(reachable), 'storage_write_binding':6, 'storage_store_sites':stores,
            'local_store_sites':locals_, 'physical_stores':0, 'image_writes':0, 'unresolved_write_origins':0}


TIMING_C = r'''
#define TIMING_DISPATCHES 20
#define TIMING_WRITABLE_INDEX 7
#define TIMING_WRITABLE_BYTES 4194304
static unsigned timing_module(unsigned trial) {
    static const unsigned abba[4] = {0, 1, 1, 0};
    return trial < 4 ? trial / 2 : abba[(trial - 4) % 4];
}
static int timing_capabilities(unsigned valid_bits, double period) {
    REQUIRE(valid_bits > 0 && valid_bits <= 64 && isfinite(period) && period > 0);
    REQUIRE(ldexpl(1.0L, (int)valid_bits) * period > 120000000000.0L);
    return 0;
}
static int timing_interval(uint64_t start, uint64_t end, unsigned valid_bits, double period,
                           uint64_t *ticks, double *nanoseconds) {
    int status = timing_capabilities(valid_bits, period);
    if (status) return status;
    uint64_t mask = valid_bits == 64 ? UINT64_MAX : (UINT64_C(1) << valid_bits) - 1;
    *ticks = ((end & mask) - (start & mask)) & mask;
    *nanoseconds = (double)*ticks * period;
    REQUIRE(*ticks && isfinite(*nanoseconds) && *nanoseconds < 2000000000.0);
    return 0;
}
static int timing_reset_owner(Allocation *allocation, const void *initial) {
    REQUIRE(allocation->gpu.size == TIMING_WRITABLE_BYTES && allocation->gpu.mapped && initial);
    memcpy(allocation->gpu.mapped, initial, TIMING_WRITABLE_BYTES);
    return 0;
}
static int timing_check_all(unsigned count, Allocation *allocations, void **oracles) {
    for (unsigned i = 0; i < count; i++) {
        REQUIRE(allocations[i].gpu.mapped && oracles[i]);
        if (memcmp(allocations[i].gpu.mapped, oracles[i], (size_t)allocations[i].gpu.size)) {
            fprintf(stderr, "Complete allocation comparison failed at owner %u\n", i);
            return 2;
        }
    }
    return 0;
}
static void timing_target_commands(VkCommandBuffer cmd, VkQueryPool queries, int timed,
                                    const uint32_t groups[3]) {
    if (timed) {
        vkCmdResetQueryPool(cmd, queries, 0, 2);
        vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, queries, 0);
    }
    vkCmdDispatch(cmd, groups[0], groups[1], groups[2]);
    if (timed)
        vkCmdWriteTimestamp(cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, queries, 1);
}
static int timing_cpu_inputs(unsigned count, Allocation *allocations, char **oracle_paths,
                             void **oracles, void **initial_writable) {
    REQUIRE(count == 60 && allocations[TIMING_WRITABLE_INDEX].gpu.size == TIMING_WRITABLE_BYTES);
    for (unsigned i = 0; i < count; i++) {
        int fd = open(oracle_paths[i], O_RDONLY);
        struct stat st;
        REQUIRE(fd >= 0 && !fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size >= 0 &&
                (uint64_t)st.st_size == allocations[i].gpu.size);
        oracles[i] = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
        close(fd);
        REQUIRE(oracles[i] != MAP_FAILED);
        if (i != TIMING_WRITABLE_INDEX) {
            void *initial = load(allocations[i].initial, allocations[i].gpu.size);
            REQUIRE(initial && !memcmp(initial, oracles[i], (size_t)allocations[i].gpu.size));
            free(initial);
        }
    }
    *initial_writable = load(allocations[TIMING_WRITABLE_INDEX].initial, TIMING_WRITABLE_BYTES);
    REQUIRE(*initial_writable);
    return 0;
}
typedef struct TimingPipeline {
    VkShaderModule module;
    VkPipeline pipeline;
    VkPipelineExecutablePropertiesKHR properties;
    uint32_t statistics_count;
    VkPipelineExecutableStatisticKHR statistics[128];
} TimingPipeline;
'''


def derive_native(source):
    need(hashlib.sha256(source.encode()).hexdigest() == BASE_C_SHA, "Frozen 02 native source changed")
    original = source; anchors = []
    def replace(before, after, label):
        nonlocal source
        need(source.count(before) == 1, "Counted timing anchor differs: " + label)
        source = source.replace(before, after); anchors.append(label)
    replace('#include <math.h>', '#include <math.h>\n#include <fcntl.h>\n#include <sys/mman.h>\n#include <sys/stat.h>\n#include <unistd.h>', 'readonly-oracle-mapping-headers')
    start = source.index('    VkShaderModuleCreateInfo module_info =')
    end = source.index('    if (compile_only) {', start)
    make = source[start:end].replace('executable.pipelineExecutableInfo', 'capture_statistics')
    make += '''    out->module = module; out->pipeline = pipeline; out->properties = executable_properties;
    out->statistics_count = statistics_count;
    memcpy(out->statistics, statistics, sizeof(statistics));
    return 0;
}\n'''
    make = '''static int timing_make_pipeline(VkDevice device, VkPipelineLayout layout,
                                 const uint32_t *code, long code_size, uint32_t required_width,
                                 VkBool32 capture_statistics, TimingPipeline *out) {\n''' + make
    replace('int main(int argc, char **argv) {', TIMING_C + '\n' + make + '\nint main(int argc, char **argv) {', 'production-timing-functions')
    start = source.index('    VkShaderModuleCreateInfo module_info =', source.index('int main('))
    end = source.index('    VkBufferUsageFlags buffer_usage =', start)
    replace(source[start:end], '''    TimingPipeline pair[2] = {0};
    int pipeline_status = timing_make_pipeline(device, layout, code, code_size, required_width,
                                              executable.pipelineExecutableInfo, &pair[0]);
    if (pipeline_status) return pipeline_status;
    pipeline_status = timing_make_pipeline(device, layout, candidate_code, candidate_size, required_width,
                                          executable.pipelineExecutableInfo, &pair[1]);
    if (pipeline_status) return pipeline_status;
\n''', 'two-pipelines-same-layout-before-allocation')
    start = source.index('    REQUIRE(argc == 4 &&', source.index('int main('))
    end = source.index('    control = fopen', start)
    replace(source[start:end], '''    REQUIRE(argc == 4 && (!strcmp(argv[3], "--check-only") || !strcmp(argv[3], "--execute-approved-timing")));
    int execute = !strcmp(argv[3], "--execute-approved-timing");
''', 'separate-execution-abi')
    replace('!memcmp(magic, "OOILRP1\\0", 8)', '!memcmp(magic, "OOCITM1\\0", 8)', 'timing-magic')
    replace('    REQUIRE(u32() == 1);', '    REQUIRE(u32() == 1);\n    uint64_t base_end = u64();\n    REQUIRE(base_end >= 20 && base_end <= 128 * 1024 * 1024);', 'bounded-base-frame')
    replace('    REQUIRE(!bad && fgetc(control) == EOF);', r'''    REQUIRE(!bad && ftell(control) >= 0 && (uint64_t)ftell(control) == base_end);
    REQUIRE(u32() == 2 && u32() == 4 && u32() == TIMING_DISPATCHES &&
            u32() == TIMING_WRITABLE_INDEX && u32() == 60);
    char *candidate_path = string();
    unsigned char candidate_digest[32]; char candidate_hex[65];
    REQUIRE(fread(candidate_digest,1,32,control)==32);
    for(unsigned i=0;i<32;i++) snprintf(candidate_hex+2*i,3,"%02x",candidate_digest[i]);
    REQUIRE(!strcmp(module_hex,"b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b") &&
            !strcmp(candidate_hex,"d5148342b4517882b25a48e63700cbfb8695454cc05d131da718509ac2563dc0"));
    char *oracle_paths[60]; void *oracles[60] = {0}; void *initial_writable = NULL;
    for(unsigned i=0;i<60;i++) oracle_paths[i]=string();
    REQUIRE(!bad && fgetc(control) == EOF);''', 'fixed-schedule-candidate-and-oracle-frame')
    replace('    if (!execute && !compile_only) {', r'''    FILE *candidate_file = fopen(candidate_path,"rb");
    REQUIRE(candidate_file && !fseek(candidate_file,0,SEEK_END));
    long candidate_size = ftell(candidate_file); fclose(candidate_file);
    REQUIRE(candidate_size>=20 && candidate_size<=16*1024*1024 && candidate_size%4==0);
    uint32_t *candidate_code = load(candidate_path,(uint64_t)candidate_size);
    REQUIRE(candidate_code && candidate_code[0]==0x07230203);
    REQUIRE(!strcmp(allocations[TIMING_WRITABLE_INDEX].id,"buffer-16") &&
            allocations[TIMING_WRITABLE_INDEX].gpu.size==TIMING_WRITABLE_BYTES);
    unsigned writable_bindings=0;
    for(unsigned i=0;i<descriptor_count;i++) if(descriptors[i].binding==6) {
        REQUIRE(descriptors[i].element==0 && !descriptors[i].null &&
                descriptors[i].allocation==TIMING_WRITABLE_INDEX); writable_bindings++;
    }
    REQUIRE(writable_bindings==1);
    int timing_cpu_status=timing_cpu_inputs(allocation_count,allocations,oracle_paths,oracles,&initial_writable);
    if(timing_cpu_status) return timing_cpu_status;
    if (!execute) {''', 'all-inputs-and-oracles-pre-vulkan')
    replace('    uint32_t queue_family = UINT32_MAX;', '    uint32_t queue_family = UINT32_MAX, timestamp_bits = 0;', 'queue-timestamp-state')
    replace('        if (queues[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {', '        if ((queues[i].queueFlags & VK_QUEUE_COMPUTE_BIT) && queues[i].timestampValidBits) {', 'timestamp-capable-compute-queue')
    replace('            queue_family = i;', '            queue_family = i; timestamp_bits = queues[i].timestampValidBits;', 'selected-queue-valid-bits')
    replace('    float priority = 1;', '''    REQUIRE(properties.properties.limits.timestampComputeAndGraphics);
    int timestamp_status = timing_capabilities(timestamp_bits, properties.properties.limits.timestampPeriod);
    if (timestamp_status) return timestamp_status;
    float priority = 1;''', 'timestamp-capability-before-device')
    # Retain exact initial upload barriers/copies from reviewed 02, in a separate fenced submission.
    upload_start = source.index('    for (uint32_t i = 0; i < image_count; i++) {', source.index('    CHECK(vkBeginCommandBuffer(cmd'))
    upload_end = source.index('    vkCmdBindPipeline(', upload_start)
    upload = source[upload_start:upload_end]
    start = source.index('    VkCommandPoolCreateInfo pool_info =', source.index('int main('))
    end = source.index('    vkDestroyPipelineLayout(device, layout, NULL);', start)
    loop = r'''
    VkCommandPoolCreateInfo pool_info = {.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags=VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,.queueFamilyIndex=queue_family};
    VkCommandPool pool; CHECK(vkCreateCommandPool(device,&pool_info,NULL,&pool));
    VkCommandBufferAllocateInfo command_info={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool=pool,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};
    VkCommandBuffer cmd; CHECK(vkAllocateCommandBuffers(device,&command_info,&cmd));
    VkCommandBufferBeginInfo begin={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    VkFenceCreateInfo fence_info={.sType=VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence; CHECK(vkCreateFence(device,&fence_info,NULL,&fence));
    VkQueue queue; vkGetDeviceQueue(device,queue_family,0,&queue);
    VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cmd};
    VkQueryPoolCreateInfo query_info={.sType=VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
        .queryType=VK_QUERY_TYPE_TIMESTAMP,.queryCount=2};
    VkQueryPool queries; CHECK(vkCreateQueryPool(device,&query_info,NULL,&queries));
    PFN_vkCmdPushDescriptorSetKHR push_descriptors=(PFN_vkCmdPushDescriptorSetKHR)
        vkGetDeviceProcAddr(device,"vkCmdPushDescriptorSetKHR"); REQUIRE(push_descriptors);
    CHECK(vkBeginCommandBuffer(cmd,&begin));
    VkMemoryBarrier host_barrier={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask=VK_ACCESS_HOST_WRITE_BIT,
        .dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT|VK_ACCESS_TRANSFER_READ_BIT};
    vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_HOST_BIT,
        VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT|VK_PIPELINE_STAGE_TRANSFER_BIT,0,1,&host_barrier,0,NULL,0,NULL);
''' + upload + r'''
    CHECK(vkEndCommandBuffer(cmd));
    CHECK(vkQueueSubmit(queue,1,&submit,fence));
    CHECK(vkWaitForFences(device,1,&fence,VK_TRUE,UINT64_C(2000000000)));
    char device_uuid[33], driver_uuid[33];
    for(unsigned i=0;i<16;i++) {
        snprintf(device_uuid+2*i,3,"%02x",device_identity.deviceUUID[i]);
        snprintf(driver_uuid+2*i,3,"%02x",device_identity.driverUUID[i]);
    }
    printf("{\"schema\":\"open-ocarina.compute-interval-paired-native/v1\",\"profile_sha256\":\"%s\","
           "\"actual_widths\":[%u,%u],\"device_vendor\":%u,\"device_id\":%u,"
           "\"timestamp_valid_bits\":%u,\"timestamp_period_ns\":%.17g,"
           "\"module_sha256\":[\"%s\",\"%s\"],\"device_uuid\":\"%s\",\"driver_uuid\":\"%s\","
           "\"record_sha256\":\"%s\",\"queue_family\":%u,\"trials\":[",
           manifest_hex,pair[0].properties.subgroupSize,pair[1].properties.subgroupSize,
           properties.properties.vendorID,properties.properties.deviceID,
           timestamp_bits,(double)properties.properties.limits.timestampPeriod,
           module_hex,candidate_hex,device_uuid,driver_uuid,record_hex,queue_family);
    fflush(stdout);
    host_barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    for(unsigned trial=0;trial<TIMING_DISPATCHES;trial++) {
        unsigned module_index=timing_module(trial); int timed=trial>=4;
        CHECK(vkResetFences(device,1,&fence));
        CHECK(vkResetCommandBuffer(cmd,0));
        int reset_status=timing_reset_owner(&allocations[TIMING_WRITABLE_INDEX],initial_writable);
        if(reset_status) return reset_status;
        CHECK(vkBeginCommandBuffer(cmd,&begin));
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
            0,1,&host_barrier,0,NULL,0,NULL);
        vkCmdBindPipeline(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,pair[module_index].pipeline);
        push_descriptors(cmd,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,descriptor_count,writes);
        vkCmdPushConstants(cmd,layout,VK_SHADER_STAGE_COMPUTE_BIT,0,push_size,push);
        timing_target_commands(cmd,queries,timed,groups);
        VkMemoryBarrier readback={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,
            .srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT};
        vkCmdPipelineBarrier(cmd,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,
            0,1,&readback,0,NULL,0,NULL);
        CHECK(vkEndCommandBuffer(cmd));
        CHECK(vkQueueSubmit(queue,1,&submit,fence));
        CHECK(vkWaitForFences(device,1,&fence,VK_TRUE,UINT64_C(2000000000)));
        uint64_t ticks=0, values[4]={0}; double nanoseconds=0;
        if(timed) {
            CHECK(vkGetQueryPoolResults(device,queries,0,2,sizeof(values),values,2*sizeof(uint64_t),
                VK_QUERY_RESULT_64_BIT|VK_QUERY_RESULT_WITH_AVAILABILITY_BIT));
            REQUIRE(values[1] && values[3]);
            int interval_status=timing_interval(values[0],values[2],timestamp_bits,
                properties.properties.limits.timestampPeriod,&ticks,&nanoseconds);
            if(interval_status) return interval_status;
        }
        int comparison_status=timing_check_all(allocation_count,allocations,oracles);
        if(comparison_status) return comparison_status;
        if(trial) putchar(',');
        printf("{\"index\":%u,\"module\":%u,\"timed\":%s,\"all_60_complete_buffers_match\":true,"
            "\"timestamp_start\":%llu,\"timestamp_end\":%llu,\"ticks\":%llu,\"nanoseconds\":%.17g}",
            trial,module_index,timed?"true":"false",(unsigned long long)values[0],
            (unsigned long long)values[2],(unsigned long long)ticks,nanoseconds);
        fflush(stdout);
    }
    printf("],\"dispatch_count\":20,\"full_comparisons\":20,\"complete_allocation_count\":60,\"pipelines\":[");
    for(unsigned i=0;i<2;i++) { if(i) putchar(','); putchar('{');
        interval_stats_json(pair[i].statistics_count,pair[i].statistics); putchar('}'); }
    printf("]}\n");
    for(unsigned i=0;i<allocation_count;i++) {
        char path[8192]; int n=snprintf(path,sizeof(path),"%s/allocation-%u.bin",argv[2],i);
        REQUIRE(n>0 && (size_t)n<sizeof(path)); FILE *f=fopen(path,"wb");
        REQUIRE(f && fwrite(allocations[i].gpu.mapped,1,(size_t)allocations[i].gpu.size,f)==allocations[i].gpu.size && !fclose(f));
        munmap(oracles[i],(size_t)allocations[i].gpu.size);
    }
    free(initial_writable);
    vkDestroyQueryPool(device,queries,NULL);
    vkDestroyFence(device,fence,NULL);
    vkDestroyCommandPool(device,pool,NULL);
    for(unsigned i=0;i<2;i++) {
        vkDestroyPipeline(device,pair[i].pipeline,NULL);
        vkDestroyShaderModule(device,pair[i].module,NULL);
    }
'''
    replace(source[start:end], loop, 'separate-initial-upload-fixed-pair-loop-and-oracles')
    replace('    return matches ? 0 : 1;', '    return 0;', 'success-after-all-fixed-checks')
    need(source.count('vkCmdDispatch(') == 1 and 'vkDeviceWaitIdle' not in source and 'DISABLE_OPTIMIZATION' not in source,
         'Timing dispatch/optimization boundary changed')
    need(source.index('timing_cpu_inputs(allocation_count') < source.index('vkCreateInstance('), 'CPU oracle admission must precede Vulkan')
    return source, anchors


def build_native(output):
    base = base_module()
    source, _, _ = base.derive_native((ROOT/'tools/vulkan_physical_replay.c').read_text())
    fmt = lambda text: subprocess.run(['clang-format','-style='+FORMAT_STYLE],input=text,text=True,capture_output=True,check=True,timeout=15).stdout
    frozen = fmt(source); need(hashlib.sha256(frozen.encode()).hexdigest() == BASE_C_SHA, 'Cannot reproduce frozen 02 source')
    source, anchors = derive_native(frozen); source = fmt(source)
    output = Path(output); c = output/'vulkan_compute_interval_timing.c'; binary = output/'native'
    c.write_text(source)
    diff = ''.join(difflib.unified_diff(frozen.splitlines(True), source.splitlines(True),
            fromfile='frozen02/vulkan_compute_interval_replay.c',tofile='private/vulkan_compute_interval_timing.c'))
    (output/'native.diff').write_text(diff)
    flags = shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','vulkan'],text=True,timeout=15))
    command=['cc','-std=c11','-O2','-DNDEBUG','-Wall','-Wextra','-Werror',str(c),*flags,'-lm','-o',str(binary)]
    subprocess.run(command,check=True,capture_output=True,text=True,timeout=60)
    return {'source_sha256':digest(c),'binary_sha256':digest(binary),'diff_sha256':digest(output/'native.diff'),
            'source':c.name,'helper':binary.name,'counted_anchors':anchors,'flags':command[1:7],
            'base02_source_sha256':BASE_C_SHA,'cpu_build_only':True}


def _json(path): return json.loads(Path(path).read_text())


def prove_reference(base_frame, reference, source):
    base = base_module()
    need(reference.get('schema') == 'open-ocarina.compute-interval-result/v1' and
         reference.get('profile_sha256') == BASE_PROFILE_SHA and reference.get('module_sha256') == base.ORIGINAL and
         reference.get('plan_sha256') == BASE_PLANS['baseline32'] and
         reference.get('native_binary_sha256') == '6bbf44eeb4f1521bc052d3a6473d5b2ba0656f986d091c9a19f97d39fb0c87dd' and
         reference.get('complete_original_allocations') == 59 and len(reference.get('allocations',[])) == 60,
         'Pinned baseline output graph incomplete')
    oracles = []
    for i, (owner, output) in enumerate(zip(base_frame['allocations'],reference['allocations'])):
        path = source/output['path']
        need(output['path'] == f'allocation-{i}.bin' and path.is_file() and not path.is_symlink() and
             path.stat().st_size == owner['size'] == output['size'] and digest(path) == output['sha256'],
             'Pinned baseline output changed')
        if i != WRITABLE_INDEX:
            need(digest(owner['initial']) == output['sha256'], 'An owner outside sole writable owner changed')
        else:
            need(owner['id'] == 'buffer-16' and owner['size'] == WRITABLE_BYTES and output['sha256'] == WRITABLE_SHA,
                 'Complete writable owner identity differs')
        oracles.append(path.resolve())
    return oracles


def prepare(prepared02, reference_dir, output):
    base = base_module(); prepared02, reference_dir = Path(prepared02).resolve(), Path(reference_dir).resolve()
    output = base.private_new(output)
    profile0, frame0, _, control0 = base.validate_prepared(prepared02,'baseline32',BASE_PLANS['baseline32'])
    _, candidate_frame, _, candidate_control = base.validate_prepared(prepared02,'candidate32',BASE_PLANS['candidate32'])
    need(digest(prepared02/'profile.json') == BASE_PROFILE_SHA and digest(reference_dir/'result.json') == BASE_RESULT_SHA,
         'Reviewed 02 profile/reference differs')
    base.compare(reference_dir,reference_dir)  # Rehash every complete reference owner.
    oracles = prove_reference(frame0,_json(reference_dir/'result.json'),reference_dir)
    output.mkdir(parents=True)
    # Preserve 02 input files, controls and binaries. Only generated copies live here.
    expected_paths=[]
    for i, (owner, path) in enumerate(zip(frame0['allocations'],oracles)):
        if i == WRITABLE_INDEX:
            copied=output/'expected-writable.bin'; copied.write_bytes(path.read_bytes()); expected_paths.append(str(copied))
        else: expected_paths.append(owner['initial'])
    write_proofs=[]
    for label, path in [('baseline32',frame0['shader']),('candidate32',candidate_frame['shader'])]:
        dis=subprocess.run(['spirv-dis','--raw-id',str(path)],capture_output=True,text=True,check=True,timeout=30).stdout
        write_proofs.append({'label':label,'module_sha256':digest(path),'write_proof':audit_writes(dis)})
    writable_binding=[d for d in frame0['descriptors'] if d[0]==6 and d[1]==0]
    need(len(writable_binding)==1 and writable_binding[0][4]==7 and writable_binding[0][3]==0,
         'Binding6 does not name complete owner7')
    native=build_native(output)
    source_files={str(prepared02/'profile.json'):digest(prepared02/'profile.json'),
                  str(control0):digest(control0),str(candidate_control):digest(candidate_control),
                  str(reference_dir/'result.json'):digest(reference_dir/'result.json')}
    # Every original input is already hash-checked by 02 admission; also pin it in this new profile.
    paths={frame0['shader'],candidate_frame['shader'],frame0['push'],frame0['table'],*expected_paths}
    paths.update(a['initial'] for a in frame0['allocations'])
    paths.update(s[3] for image in frame0['images'] for s in image['subresources'])
    sources={str(Path(p).resolve()):{'size':Path(p).stat().st_size,'sha256':digest(p)} for p in sorted(paths)}
    profile={'schema':'open-ocarina.compute-interval-timing-profile/v1','mode':'frozen-owned-paired-dispatch',
        'adapter_sha256':digest(Path(__file__)),'base_adapter_sha256':BASE_ADAPTER_SHA,'base_profile_sha256':BASE_PROFILE_SHA,
        'base_prepared':str(prepared02),'reference_result_sha256':BASE_RESULT_SHA,'source_files':source_files,
        'driver':profile0['driver'],'native':native,'inputs':sources,'schedule':protocol(),
        'warmups_per_module':2,'abba_repeats':4,'timed_samples_per_module':8,'total_dispatches':20,
        'writable_reset':{'owner_index':7,'bytes':4194304,'before_every_dispatch':True},
        'oracle':{'owners':60,'original_bytes':1163919360,'record_bytes':8784,'after_every_dispatch':True,
                  'expected_paths':expected_paths,'reference_source':str(reference_dir)},
        'write_proofs':write_proofs,'timestamp_contract':{'fence_ns':2000000000,'process_seconds':120,
            'minimum_wrap_horizon_ns':120000000000,'start_stage':'TOP_OF_PIPE','end_stage':'BOTTOM_OF_PIPE','preceding_host_barrier_destination':'ALL_COMMANDS',
            'only_target_dispatch_inside_queries':True},
        'policy':{'HS_SPILL_BATCH':'1','shader_spilling_rate':'1','HS_PUSH_SHADOW':None,'HS_CS32_MAX_INSTS':None,
                  'other_HS_VK_MESA_INTEL_overrides':'cleared'},
        'limitations':['Isolated frozen dispatch durations only; never normal-frame FPS or live-renderer speed.',
            'The host oracle comparison and complete writable reset are outside every GPU interval.',
            'Cold pipeline compilation/cache population is excluded by four warmup dispatches.',
            'GPU/device/timestamp paths remain unexecuted until separate exact authorization.']}
    (output/'profile.json').write_text(json.dumps(profile,indent=2)+'\n')
    frame=copy.deepcopy(frame0); frame['manifest_sha256']=digest(output/'profile.json')
    control=output/'paired.bin'; control.write_bytes(encode_control(frame,candidate_frame['shader'],expected_paths))
    decoded, candidate_path, _ = decode_control(control.read_bytes())
    need(decoded==frame and candidate_path==candidate_frame['shader'],'Timing frame roundtrip differs')
    check=subprocess.run([str(output/'native'),str(control),str(output),'--check-only'],capture_output=True,text=True,timeout=30)
    need(check.returncode==0,'Native CPU-only paired admission failed: '+check.stderr)
    receipt={'schema':'open-ocarina.compute-interval-timing-prepared/v1','profile_sha256':digest(output/'profile.json'),
        'plan_sha256':digest(control),'native':native,'native_cpu_check':json.loads(check.stdout),
        'adapter_sha256':digest(Path(__file__)),'vulkan_called':False,'dispatches':0}
    (output/'handoff.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def validate_prepared(prepared, authorization):
    base=base_module(); prepared=Path(prepared).resolve()
    need(digest(prepared/'paired.bin')==authorization,'Exact paired plan authorization differs')
    receipt=_json(prepared/'handoff.json'); profile=_json(prepared/'profile.json')
    need(receipt['plan_sha256']==authorization and receipt['profile_sha256']==digest(prepared/'profile.json') and
         receipt['adapter_sha256']==profile['adapter_sha256']==digest(Path(__file__)) and
         profile['schema']=='open-ocarina.compute-interval-timing-profile/v1' and profile['schedule']==[list(r) for r in protocol()],
         'Timing source/profile/protocol changed')
    native=profile['native']
    for key,name in [('source_sha256',native['source']),('binary_sha256',native['helper']),('diff_sha256','native.diff')]:
        need(Path(name).name==name and digest(prepared/name)==native[key],'Timing native source/helper/diff changed')
    for path,expected in profile['source_files'].items(): need(digest(path)==expected,'Timing source evidence changed')
    for path,row in profile['inputs'].items():
        need(Path(path).stat().st_size==row['size'] and digest(path)==row['sha256'],'Timing input/oracle changed')
    frame,candidate,oracles=decode_control((prepared/'paired.bin').read_bytes())
    need(frame['manifest_sha256']==receipt['profile_sha256'] and oracles==profile['oracle']['expected_paths'],
         'Timing frame/profile differs')
    _,original,_,_=base.validate_prepared(profile['base_prepared'],'baseline32',BASE_PLANS['baseline32'])
    _,alternative,_,_=base.validate_prepared(profile['base_prepared'],'candidate32',BASE_PLANS['candidate32'])
    expected=copy.deepcopy(original); expected['manifest_sha256']=frame['manifest_sha256']
    need(frame==expected and candidate==alternative['shader'],'Original graph or candidate changed')
    return profile,frame,prepared/native['helper'],prepared/'paired.bin'


def run(prepared, output, driver, authorization):
    base=base_module(); profile,frame,helper,control=validate_prepared(prepared,authorization)
    driver=Path(driver).resolve(); identify=importlib.import_module('tools.compile_probe').driver_identity
    need(digest(driver)==base.ICD_SHA and identify(driver)==profile['driver'],'Exact E5 driver differs')
    output=base.private_new(output); output.mkdir(parents=True)
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('HS_','VK_','MESA_','INTEL_')) or key=='shader_spilling_rate':env.pop(key)
    env.update(VK_DRIVER_FILES=str(driver),MESA_SHADER_CACHE_DIR=str(output/'shader-cache'),HS_SPILL_BATCH='1',shader_spilling_rate='1')
    with (output/'native.json').open('w') as out,(output/'native.stderr').open('w') as err:
        child=subprocess.Popen([str(helper),str(control),str(output),'--execute-approved-timing'],stdout=out,stderr=err,env=env,start_new_session=True)
        try: status=child.wait(timeout=120)
        except (KeyboardInterrupt,subprocess.TimeoutExpired):
            os.killpg(child.pid,signal.SIGTERM)
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
            raise ValueError('Bounded timing process interrupted; stopped only its own process group')
    need(status==0,'Paired native process failed; no retries; preserve diagnostics')
    native=_json(output/'native.json'); validate_native_result(native,profile,digest(Path(prepared)/'profile.json'))
    outputs=[]
    for i,owner in enumerate(frame['allocations']):
        path=output/f'allocation-{i}.bin'; oracle=profile['oracle']['expected_paths'][i]
        need(path.stat().st_size==owner['size'] and digest(path)==digest(oracle),'Complete final timing output differs')
        outputs.append({'index':i,'size':owner['size'],'sha256':digest(path)})
    validate_prepared(prepared,authorization);need(identify(driver)==profile['driver'],'Driver changed during timing')
    result={'schema':'open-ocarina.compute-interval-timing-result/v1','profile_sha256':digest(Path(prepared)/'profile.json'),
        'plan_sha256':authorization,'native':native,'outputs':outputs,
        'scope':'Isolated frozen GPU dispatch durations, not normal frame timing/FPS, live-renderer or general equivalence.'}
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');return result


def validate_native_result(native, profile, expected_profile_sha256):
    need(native.get('schema')=='open-ocarina.compute-interval-paired-native/v1' and
         native.get('actual_widths')==[32,32] and
         native.get('module_sha256')==[base_module().ORIGINAL,base_module().CANDIDATE] and
         native.get('record_sha256')==base_module().RECORD_SHA and native.get('device_vendor')==0x8086 and native.get('device_id')==0x56a0 and
         native.get('dispatch_count')==20 and native.get('full_comparisons')==20 and native.get('complete_allocation_count')==60,
         'Native timing identity/count/width/coverage differs')
    need(native.get('profile_sha256')==expected_profile_sha256,'Native profile differs')
    need(profile.get('total_dispatches')==20 and profile.get('timed_samples_per_module')==8,'Prepared timing count changed')
    rows=native.get('trials',[]);need(len(rows)==20,'Fixed timing trials incomplete')
    for i,(row,(module,timed)) in enumerate(zip(rows,protocol())):
        need(row['index']==i and row['module']==module and row['timed'] is timed and row['all_60_complete_buffers_match'] is True,
             'Fixed trial order/oracle differs')
        if timed:
            ticks,ns=timestamp_delta(row['timestamp_start'],row['timestamp_end'],native['timestamp_valid_bits'],native['timestamp_period_ns'])
            need(row['ticks']==ticks and math.isclose(row['nanoseconds'],ns,rel_tol=1e-12),'Native timestamp arithmetic differs')
        else: need(row['ticks']==0 and row['nanoseconds']==0 and row['timestamp_start']==row['timestamp_end']==0,'Warmup unexpectedly timed')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('prepared02',type=Path);p.add_argument('reference',type=Path);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('check');p.add_argument('prepared',type=Path);p.add_argument('--authorize-plan',required=True)
    p=sub.add_parser('execute');p.add_argument('prepared',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--driver',type=Path,required=True);p.add_argument('--authorize-plan',required=True)
    args=parser.parse_args()
    try:
        if args.command=='prepare': result=prepare(args.prepared02,args.reference,args.output)
        elif args.command=='check':
            profile,frame,_,_=validate_prepared(args.prepared,args.authorize_plan)
            result={'cpu_validated':True,'vulkan_called':False,'profile':frame['manifest_sha256'],'dispatches':profile['total_dispatches']}
        else:result=run(args.prepared,args.output,args.driver,args.authorize_plan)
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError,struct.error,subprocess.SubprocessError) as error:
        print(str(error),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
