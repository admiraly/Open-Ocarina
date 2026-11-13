/* SPDX-License-Identifier: GPL-3.0-or-later */
/* D8 interval mesh-output replay. --validate and an absent admit token return before vkCreateInstance. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_INPUTS 12u
#define MAX_DESCRIPTORS 12u
#define RECORD_BYTES 400u
#define RECORD_GUARD 64u
#define RECORD_TOTAL (RECORD_GUARD + RECORD_BYTES + RECORD_GUARD)
#define MAX_TOTAL_BYTES UINT64_C(1073741824)
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr, "Invalid bounded mesh plan: %s\n", #x); return 2; } } while (0)
#define CHECK(call) do { VkResult status = (call); if (status != VK_SUCCESS) { \
    fprintf(stderr, "%s failed: %d\n", #call, status); return 1; } } while (0)

static void json_string(const char *text) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') { putchar('\\'); putchar(*p); }
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static void *read_file(const char *name, size_t minimum, size_t maximum, size_t *size) {
    FILE *file = fopen(name, "rb");
    if (!file || fseek(file, 0, SEEK_END)) return NULL;
    long length = ftell(file);
    if (length < 0 || (size_t)length < minimum || (size_t)length > maximum || length % 4) {
        fclose(file); return NULL;
    }
    rewind(file);
    void *data = malloc((size_t)length);
    if (!data || fread(data, 1, (size_t)length, file) != (size_t)length) {
        free(data); fclose(file); return NULL;
    }
    fclose(file); *size = (size_t)length; return data;
}

typedef struct Buffer {
    VkBuffer buffer;
    VkDeviceMemory memory;
    void *mapped;
} Buffer;

static int make_buffer(VkPhysicalDevice physical, VkDevice device, VkDeviceSize size, VkBufferUsageFlags usage, Buffer *out) {
    VkBufferCreateInfo info = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = size,
        .usage = usage, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(device, &info, NULL, &out->buffer));
    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(device, out->buffer, &requirements);
    VkPhysicalDeviceMemoryProperties properties;
    vkGetPhysicalDeviceMemoryProperties(physical, &properties);
    uint32_t memory_type = UINT32_MAX;
    const VkMemoryPropertyFlags flags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    for (uint32_t i = 0; i < properties.memoryTypeCount; ++i)
        if ((requirements.memoryTypeBits & (1u << i)) &&
            (properties.memoryTypes[i].propertyFlags & flags) == flags) { memory_type = i; break; }
    if (memory_type == UINT32_MAX) { fprintf(stderr, "No coherent host-visible memory\n"); return 1; }
    VkMemoryAllocateInfo allocation = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size, .memoryTypeIndex = memory_type};
    CHECK(vkAllocateMemory(device, &allocation, NULL, &out->memory));
    CHECK(vkBindBufferMemory(device, out->buffer, out->memory, 0));
    CHECK(vkMapMemory(device, out->memory, 0, size, 0, &out->mapped));
    return 0;
}


typedef struct Input { uint32_t size; unsigned char *data; Buffer gpu; } Input;
typedef struct Descriptor { uint32_t binding, element, type, input, range, offset; } Descriptor;
typedef struct Plan {
    uint32_t input_count, descriptor_count, push_size, capture_size, capture_offset, capture_range;
    uint32_t groups[3], origin[3], grid[3];
    uint32_t record_bytes, record_words, marker_word, suffix_word, max_vertices, max_primitives, ubo_count, vertex_output_components;
    const char *output_profile;
    Input inputs[MAX_INPUTS];
    Descriptor descriptors[MAX_INPUTS];
    unsigned char push[128];
    uint32_t *capture;
} Plan;

static uint32_t read32(FILE *file, int *bad) {
    unsigned char bytes[4];
    if (fread(bytes, 1, 4, file) != 4) { *bad = 1; return 0; }
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

static int load_plan(const char *path, Plan *plan) {
    FILE *file = fopen(path, "rb");
    REQUIRE(file);
    unsigned char magic[8];
    REQUIRE(fread(magic, 1, 8, file) == 8);
    if (!memcmp(magic, "OOD8IV1", 8)) {
        plan->record_bytes = 44928; plan->record_words = 11232;
        plan->marker_word = 5712; plan->suffix_word = 11216;
        plan->max_vertices = 128; plan->max_primitives = 128;
        plan->ubo_count = 5; plan->vertex_output_components = 40;
        plan->output_profile = "d8-interval-mesh-owned/v1";
    } else REQUIRE(0);
    int bad = 0;
    REQUIRE(read32(file, &bad) == 1);
    plan->input_count = read32(file, &bad);
    plan->descriptor_count = read32(file, &bad);
    plan->push_size = read32(file, &bad);
    plan->capture_size = read32(file, &bad);
    plan->capture_offset = read32(file, &bad);
    plan->capture_range = read32(file, &bad);
    for (unsigned i = 0; i < 3; ++i) plan->groups[i] = read32(file, &bad);
    for (unsigned i = 0; i < 3; ++i) plan->origin[i] = read32(file, &bad);
    for (unsigned i = 0; i < 3; ++i) plan->grid[i] = read32(file, &bad);
    REQUIRE(!bad && plan->input_count <= MAX_INPUTS && plan->descriptor_count == MAX_DESCRIPTORS &&
            plan->push_size <= 128 && !(plan->push_size % 4));
    uint64_t groups = 1;
    for (unsigned i = 0; i < 3; ++i) {
        REQUIRE(plan->groups[i] && plan->groups[i] <= 32 && plan->grid[i] &&
                plan->origin[i] < plan->grid[i] && plan->groups[i] <= plan->grid[i] - plan->origin[i]);
        groups *= plan->groups[i];
    }
    REQUIRE(groups <= 32 && plan->capture_range == groups * plan->record_bytes &&
            plan->capture_offset == 64 && plan->capture_size == plan->capture_range + 128);
    uint64_t total = plan->capture_size;
    for (uint32_t i = 0; i < plan->input_count; ++i) {
        Input *input = &plan->inputs[i];
        input->size = read32(file, &bad);
        REQUIRE(!bad && input->size && input->size <= 512u * 1024u * 1024u);
        total += input->size;
        REQUIRE(total <= MAX_TOTAL_BYTES);
        input->data = malloc(input->size);
        REQUIRE(input->data && fread(input->data, 1, input->size, file) == input->size);
    }
    uint32_t used = 0;
    for (uint32_t i = 0; i < plan->descriptor_count; ++i) {
        Descriptor *descriptor = &plan->descriptors[i];
        REQUIRE(read32(file, &bad) == 0);
        descriptor->binding = read32(file, &bad);
        descriptor->element = read32(file, &bad);
        descriptor->type = read32(file, &bad);
        descriptor->input = read32(file, &bad);
        descriptor->range = read32(file, &bad);
        descriptor->offset = read32(file, &bad);
        REQUIRE(!bad && descriptor->element < 3 &&
                (descriptor->type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER || descriptor->type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER));
        REQUIRE((descriptor->binding < 9 || descriptor->binding == 30) &&
                descriptor->type == (descriptor->binding < plan->ubo_count ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER) &&
                (descriptor->binding == 8 || descriptor->element == 0));
        if (descriptor->input == UINT32_MAX) {
            REQUIRE(descriptor->binding == 8 && (descriptor->element == 1 || descriptor->element == 2) &&
                    descriptor->type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER && descriptor->range == 0 && descriptor->offset == 0);
        } else if (descriptor->binding == 30) {
            REQUIRE(descriptor->input < plan->input_count && descriptor->offset == RECORD_GUARD && descriptor->range == RECORD_BYTES &&
                    plan->inputs[descriptor->input].size == RECORD_TOTAL);
            used |= 1u << descriptor->input;
        } else {
            REQUIRE(descriptor->input < plan->input_count && descriptor->offset == 0 &&
                    descriptor->range == plan->inputs[descriptor->input].size);
            used |= 1u << descriptor->input;
        }
        for (uint32_t j = 0; j < i; ++j) {
            Descriptor *other = &plan->descriptors[j];
            REQUIRE(other->binding != descriptor->binding ||
                    (other->element != descriptor->element && other->type == descriptor->type));
        }
        for (uint32_t element = 0; element < descriptor->element; ++element) {
            int found = 0;
            for (uint32_t j = 0; j < plan->descriptor_count; ++j) {
                if (j >= i) break;
                found |= plan->descriptors[j].binding == descriptor->binding && plan->descriptors[j].element == element;
            }
            REQUIRE(found);
        }
    }
    uint32_t record_slots = 0;
    for (uint32_t i = 0; i < plan->descriptor_count; ++i)
        record_slots += plan->descriptors[i].binding == 30;
    REQUIRE(record_slots == 1 && used == (1u << plan->input_count) - 1u);
    REQUIRE(fread(plan->push, 1, plan->push_size, file) == plan->push_size);
    plan->capture = malloc(plan->capture_size);
    REQUIRE(plan->capture && fread(plan->capture, 1, plan->capture_size, file) == plan->capture_size && fgetc(file) == EOF);
    fclose(file);
    for (uint32_t i = 0; i < plan->capture_size / 4; ++i) {
        uint32_t expected = 0xcdcdcdcdu;
        if (i < 16 || i >= plan->capture_size / 4 - 16) expected = 0xb0a7d123u;
        else {
            uint32_t position = (i - 16) % plan->record_words;
            if (position < 16 || position >= plan->suffix_word) expected = 0xa11ce55au;
            else if (position >= plan->marker_word) expected = 0;
        }
        REQUIRE(plan->capture[i] == expected);
    }
    for (uint32_t i = 0; i < plan->descriptor_count; ++i) {
        if (plan->descriptors[i].binding != 30) continue;
        const unsigned char *record = plan->inputs[plan->descriptors[i].input].data;
        for (uint32_t word = 0; word < RECORD_GUARD / 4; ++word) {
            uint32_t value = (uint32_t)record[word * 4] | ((uint32_t)record[word * 4 + 1] << 8) |
                ((uint32_t)record[word * 4 + 2] << 16) | ((uint32_t)record[word * 4 + 3] << 24);
            uint32_t tail = (uint32_t)record[RECORD_GUARD + RECORD_BYTES + word * 4] |
                ((uint32_t)record[RECORD_GUARD + RECORD_BYTES + word * 4 + 1] << 8) |
                ((uint32_t)record[RECORD_GUARD + RECORD_BYTES + word * 4 + 2] << 16) |
                ((uint32_t)record[RECORD_GUARD + RECORD_BYTES + word * 4 + 3] << 24);
            REQUIRE(value == 0xd8a1c0deu && tail == 0xd8a1c0deu);
        }
    }
    return 0;
}

static int validate_shader_profile(const uint32_t *code, size_t byte_count, const Plan *plan) {
    size_t words = byte_count / 4, cursor = 5;
    uint32_t entry = 0, entries = 0, local_sizes = 0, vertex_modes = 0, primitive_modes = 0;
    while (cursor < words) {
        uint32_t count = code[cursor] >> 16, opcode = code[cursor] & 0xffff;
        REQUIRE(count && count <= words - cursor);
        if (opcode == 15) { /* OpEntryPoint */
            REQUIRE(count >= 5 && code[cursor + 1] == 5365 && code[cursor + 3] == 0x6e69616d && code[cursor + 4] == 0);
            entry = code[cursor + 2]; ++entries;
        } else if (opcode == 16) { /* OpExecutionMode */
            REQUIRE(count >= 3 && entry && code[cursor + 1] == entry);
            uint32_t mode = code[cursor + 2];
            if (mode == 17) {
                REQUIRE(count == 6 && code[cursor + 3] == 32 && code[cursor + 4] == 1 && code[cursor + 5] == 1);
                ++local_sizes;
            } else if (mode == 26) {
                REQUIRE(count == 4 && code[cursor + 3] == plan->max_vertices); ++vertex_modes;
            } else if (mode == 5270) {
                REQUIRE(count == 4 && code[cursor + 3] == plan->max_primitives); ++primitive_modes;
            }
        } else if (opcode == 331) REQUIRE(0); /* OpExecutionModeId is outside both literal profiles. */
        cursor += count;
    }
    REQUIRE(entries == 1 && local_sizes == 1 && vertex_modes == 1 && primitive_modes == 1);
    return 0;
}

int main(int argc, char **argv) {
    const uint32_t little_endian = 1;
    REQUIRE(*(const unsigned char *)&little_endian == 1);
    REQUIRE(argc == 6 || (argc == 7 && !strcmp(argv[6], "--validate")));
    char *end;
    unsigned long required_width = strtoul(argv[4], &end, 10);
    REQUIRE(*argv[4] && !*end && (required_width == 8 || required_width == 16 || required_width == 32));
    unsigned long device_index = strtoul(argv[5], &end, 10);
    REQUIRE(*argv[5] && !*end && device_index <= 255);
    Plan plan = {0};
    int loaded = load_plan(argv[2], &plan);
    if (loaded) return loaded;
    size_t code_size = 0;
    uint32_t *code = read_file(argv[1], 20, 2 * 1024 * 1024, &code_size);
    REQUIRE(code && code[0] == 0x07230203 && code[1] == 0x00010600);
    int shader_profile = validate_shader_profile(code, code_size, &plan);
    if (shader_profile) return shader_profile;
    if (argc == 7) {
        uint32_t set0_bindings = 0, elements = 0;
        int seen[31] = {0};
        for (uint32_t i = 0; i < plan.descriptor_count; ++i) {
            uint32_t binding = plan.descriptors[i].binding;
            if (!seen[binding == 30 ? 30 : binding]) {
                seen[binding == 30 ? 30 : binding] = 1;
                if (binding <= 30) ++set0_bindings;
            }
        }
        /* Distinct bindings are 0-8 and 30. Elements are one per row: 12, plus capture and control. */
        elements = plan.descriptor_count + 2;
        printf("{\"output_profile\":"); json_string(plan.output_profile);
        printf(",\"native_plan_validated\":true,\"gpu_executed\":false,\"queue_retrieved\":false,"
               "\"device_created\":false,\"input_buffers\":%u,\"descriptor_elements\":%u,"
               "\"set0_bindings\":%u,\"owned_elements\":%u,\"timing_admitted\":false}\n",
               plan.input_count, plan.descriptor_count, set0_bindings, elements);
        return 0;
    }
    const char *admit = getenv("GROK_D8_INTERVAL_REPLAY_ADMIT");
    if (!admit || strcmp(admit, "root-complete-output-grant")) {
        fprintf(stderr, "D8 interval replay execution is not admitted\n");
        return 4;
    }
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina bounded logical mesh output replay", .apiVersion = VK_API_VERSION_1_3};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance instance;
    CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t physical_count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &physical_count, NULL));
    if (!physical_count || physical_count > 256 || device_index >= physical_count) return 1;
    VkPhysicalDevice physicals[256];
    CHECK(vkEnumeratePhysicalDevices(instance, &physical_count, physicals));
    VkPhysicalDevice physical = physicals[device_index];
    VkPhysicalDeviceMeshShaderPropertiesEXT mesh_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_PROPERTIES_EXT};
    VkPhysicalDeviceSubgroupProperties operations = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES, .pNext = &mesh_properties};
    VkPhysicalDeviceSubgroupSizeControlProperties subgroup = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES, .pNext = &operations};
    VkPhysicalDeviceFloatControlsProperties float_controls = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES, .pNext = &subgroup};
    VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &float_controls};
    VkPhysicalDeviceProperties2 properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(physical, &properties);
    if (properties.properties.apiVersion < VK_API_VERSION_1_3 || subgroup.minSubgroupSize > required_width ||
        subgroup.maxSubgroupSize < required_width || !(subgroup.requiredSubgroupSizeStages & VK_SHADER_STAGE_MESH_BIT_EXT) ||
        !(operations.supportedStages & VK_SHADER_STAGE_MESH_BIT_EXT) ||
        !(operations.supportedOperations & VK_SUBGROUP_FEATURE_BASIC_BIT) ||
        mesh_properties.maxMeshWorkGroupInvocations < 32 || mesh_properties.maxMeshWorkGroupSize[0] < 32 ||
        mesh_properties.maxMeshOutputVertices < plan.max_vertices || mesh_properties.maxMeshOutputPrimitives < plan.max_primitives ||
        mesh_properties.maxMeshOutputComponents < plan.vertex_output_components ||
        !float_controls.shaderSignedZeroInfNanPreserveFloat32) return 1;
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    if (extension_count > 4096) return 1;
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    if (!extensions && extension_count) return 1;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int has_executable = 0, has_mesh = 0, has_robustness = 0;
    for (uint32_t i = 0; i < extension_count; ++i) {
        if (!strcmp(extensions[i].extensionName, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME)) has_executable = 1;
        if (!strcmp(extensions[i].extensionName, VK_EXT_MESH_SHADER_EXTENSION_NAME)) has_mesh = 1;
        if (!strcmp(extensions[i].extensionName, VK_EXT_ROBUSTNESS_2_EXTENSION_NAME)) has_robustness = 1;
    }
    free(extensions);
    if (!has_executable || !has_mesh || !has_robustness) return 1;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceRobustness2FeaturesEXT robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT, .pNext = &executable};
    VkPhysicalDeviceMeshShaderFeaturesEXT mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT, .pNext = &robustness};
    VkPhysicalDeviceVulkan13Features features13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &mesh};
    VkPhysicalDeviceVulkan12Features features12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &features13};
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &features12};
    vkGetPhysicalDeviceFeatures2(physical, &supported);
    if (!features13.subgroupSizeControl || !features13.dynamicRendering || !mesh.meshShader ||
        !executable.pipelineExecutableInfo || !supported.features.robustBufferAccess ||
        !supported.features.vertexPipelineStoresAndAtomics || !robustness.robustBufferAccess2 ||
        !supported.features.shaderInt16 || !supported.features.shaderInt64 ||
        !supported.features.shaderStorageBufferArrayDynamicIndexing || !supported.features.shaderImageGatherExtended ||
        !features12.uniformBufferStandardLayout || !features12.shaderFloat16 || !features12.shaderInt8) return 1;
    for (uint32_t i = 0; i < plan.descriptor_count; ++i)
        if (plan.descriptors[i].input == UINT32_MAX && !robustness.nullDescriptor) return 1;
    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    if (!queue_count || queue_count > 256) return 1;
    VkQueueFamilyProperties queues[256];
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
    uint32_t family = UINT32_MAX;
    for (uint32_t i = 0; i < queue_count; ++i)
        if (queues[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { family = i; break; }
    if (family == UINT32_MAX) return 1;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR, .pipelineExecutableInfo = VK_TRUE};
    VkPhysicalDeviceRobustness2FeaturesEXT enabled_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = &enabled_executable, .robustBufferAccess2 = VK_TRUE, .nullDescriptor = robustness.nullDescriptor};
    VkPhysicalDeviceMeshShaderFeaturesEXT enabled_mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT,
        .pNext = &enabled_robustness, .meshShader = VK_TRUE};
    VkPhysicalDeviceVulkan13Features enabled13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &enabled_mesh, .subgroupSizeControl = VK_TRUE, .dynamicRendering = VK_TRUE};
    VkPhysicalDeviceVulkan12Features enabled12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &enabled13, .uniformBufferStandardLayout = VK_TRUE, .shaderFloat16 = VK_TRUE, .shaderInt8 = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &enabled12, .features = {.robustBufferAccess = VK_TRUE, .vertexPipelineStoresAndAtomics = VK_TRUE,
            .shaderInt16 = VK_TRUE, .shaderInt64 = VK_TRUE, .shaderStorageBufferArrayDynamicIndexing = VK_TRUE, .shaderImageGatherExtended = VK_TRUE}};
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    const char *extension_names[3] = {VK_EXT_MESH_SHADER_EXTENSION_NAME, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME, VK_EXT_ROBUSTNESS_2_EXTENSION_NAME};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = 3, .ppEnabledExtensionNames = extension_names};
    VkDevice device;
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));

    REQUIRE(properties.properties.vendorID == 0x8086 && strstr(properties.properties.deviceName, "A770"));
    REQUIRE(plan.capture_offset % properties.properties.limits.minStorageBufferOffsetAlignment == 0 &&
            plan.capture_range <= properties.properties.limits.maxStorageBufferRange &&
            plan.push_size <= properties.properties.limits.maxPushConstantsSize);
    for (unsigned i = 0; i < 3; ++i) REQUIRE(plan.groups[i] <= mesh_properties.maxMeshWorkGroupCount[i]);
    REQUIRE((uint64_t)plan.groups[0] * plan.groups[1] * plan.groups[2] <= mesh_properties.maxMeshWorkGroupTotalCount);
    for (uint32_t i = 0; i < plan.descriptor_count; ++i) {
        Descriptor *descriptor = &plan.descriptors[i];
        if (descriptor->input == UINT32_MAX) continue;
        REQUIRE(descriptor->range <= (descriptor->type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER ?
                properties.properties.limits.maxUniformBufferRange : properties.properties.limits.maxStorageBufferRange));
    }
    for (uint32_t i = 0; i < plan.input_count; ++i) {
        Input *input = &plan.inputs[i];
        if (make_buffer(physical, device, input->size, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT, &input->gpu)) return 1;
        memcpy(input->gpu.mapped, input->data, input->size);
    }
    Buffer capture = {0}, control = {0};
    if (make_buffer(physical, device, plan.capture_size, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, &capture) ||
        make_buffer(physical, device, 32, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, &control)) return 1;
    memcpy(capture.mapped, plan.capture, plan.capture_size);
    uint32_t origin_control[8] = {plan.origin[0], plan.origin[1], plan.origin[2], 0, plan.groups[0], plan.groups[1], plan.groups[2], 0};
    memcpy(control.mapped, origin_control, sizeof(origin_control));
    VkDescriptorSetLayoutBinding bindings[2][12] = {{{0}}};
    uint32_t binding_counts[2] = {0};
    for (uint32_t i = 0; i < plan.descriptor_count; ++i) {
        Descriptor *descriptor = &plan.descriptors[i];
        uint32_t index = 0;
        while (index < binding_counts[0] && bindings[0][index].binding != descriptor->binding) ++index;
        if (index == binding_counts[0]) {
            bindings[0][index] = (VkDescriptorSetLayoutBinding){.binding = descriptor->binding,
                .descriptorType = (VkDescriptorType)descriptor->type, .stageFlags = VK_SHADER_STAGE_MESH_BIT_EXT};
            ++binding_counts[0];
        }
        bindings[0][index].descriptorCount = descriptor->element + 1;
    }
    binding_counts[1] = 2;
    for (unsigned i = 0; i < 2; ++i) bindings[1][i] = (VkDescriptorSetLayoutBinding){.binding = i,
        .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_MESH_BIT_EXT};
    VkDescriptorSetLayout set_layouts[2];
    for (unsigned i = 0; i < 2; ++i) {
        VkDescriptorSetLayoutCreateInfo set_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .bindingCount = binding_counts[i], .pBindings = bindings[i]};
        CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &set_layouts[i]));
    }
    VkPushConstantRange push = {.stageFlags = VK_SHADER_STAGE_MESH_BIT_EXT, .size = plan.push_size};
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 2, .pSetLayouts = set_layouts, .pushConstantRangeCount = plan.push_size ? 1 : 0,
        .pPushConstantRanges = plan.push_size ? &push : NULL};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModuleCreateInfo module_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = code_size, .pCode = code};
    VkShaderModule module;
    CHECK(vkCreateShaderModule(device, &module_info, NULL, &module));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo required = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO, .requiredSubgroupSize = (uint32_t)required_width};
    /* SPIR-V1.6, local X32 and each supported required width divide evenly. */
    VkPipelineShaderStageCreateInfo stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
        .pNext = &required, .stage = VK_SHADER_STAGE_MESH_BIT_EXT, .module = module, .pName = "main"};
    VkViewport viewport = {0, 0, 1, 1, 0, 1};
    VkRect2D scissor = {{0, 0}, {1, 1}};
    VkPipelineViewportStateCreateInfo vp = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1, .pViewports = &viewport, .scissorCount = 1, .pScissors = &scissor};
    VkPipelineRasterizationStateCreateInfo raster = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .rasterizerDiscardEnable = VK_TRUE, .polygonMode = VK_POLYGON_MODE_FILL, .lineWidth = 1};
    VkPipelineMultisampleStateCreateInfo multisample = {.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
    VkPipelineColorBlendStateCreateInfo blend = {.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO};
    VkPipelineRenderingCreateInfo rendering = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO};
    VkGraphicsPipelineCreateInfo pipeline_info = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
        .pNext = &rendering, .stageCount = 1, .pStages = &stage, .pViewportState = &vp,
        .pRasterizationState = &raster, .pMultisampleState = &multisample, .pColorBlendState = &blend, .layout = layout};
    VkPipeline pipeline;
    CHECK(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    PFN_vkGetPipelineExecutablePropertiesKHR get = (PFN_vkGetPipelineExecutablePropertiesKHR)
        vkGetDeviceProcAddr(device, "vkGetPipelineExecutablePropertiesKHR");
    PFN_vkCmdDrawMeshTasksEXT draw = (PFN_vkCmdDrawMeshTasksEXT)vkGetDeviceProcAddr(device, "vkCmdDrawMeshTasksEXT");
    if (!get || !draw) return 1;
    VkPipelineInfoKHR info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR, .pipeline = pipeline};
    uint32_t count = 0;
    CHECK(get(device, &info, &count, NULL));
    if (count != 1) { fprintf(stderr, "Expected one mesh executable\n"); return 1; }
    VkPipelineExecutablePropertiesKHR prop = {.sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR};
    CHECK(get(device, &info, &count, &prop));
    if (prop.subgroupSize != required_width || prop.stages != VK_SHADER_STAGE_MESH_BIT_EXT) {
        fprintf(stderr, "Executable stage/width differs from requested mesh width\n"); return 1;
    }

    VkDescriptorPoolSize pool_sizes[2] = {{.type = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, .descriptorCount = MAX_INPUTS},
        {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = MAX_INPUTS + 2}};
    VkDescriptorPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 2, .poolSizeCount = 2, .pPoolSizes = pool_sizes};
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(device, &pool_info, NULL, &pool));
    VkDescriptorSetAllocateInfo set_alloc = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = pool, .descriptorSetCount = 2, .pSetLayouts = set_layouts};
    VkDescriptorSet sets[2];
    CHECK(vkAllocateDescriptorSets(device, &set_alloc, sets));
    VkDescriptorBufferInfo infos[MAX_INPUTS + 2];
    VkWriteDescriptorSet writes[MAX_INPUTS + 2];
    uint32_t write_count = plan.descriptor_count + 2;
    for (uint32_t i = 0; i < plan.descriptor_count; ++i) {
        Descriptor *descriptor = &plan.descriptors[i];
        if (descriptor->input == UINT32_MAX)
            infos[i] = (VkDescriptorBufferInfo){.buffer = VK_NULL_HANDLE, .offset = 0, .range = VK_WHOLE_SIZE};
        else
            infos[i] = (VkDescriptorBufferInfo){.buffer = plan.inputs[descriptor->input].gpu.buffer, .offset = descriptor->offset, .range = descriptor->range};
        writes[i] = (VkWriteDescriptorSet){.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = sets[0],
            .dstBinding = descriptor->binding, .dstArrayElement = descriptor->element, .descriptorCount = 1,
            .descriptorType = (VkDescriptorType)descriptor->type, .pBufferInfo = &infos[i]};
    }
    infos[plan.descriptor_count] = (VkDescriptorBufferInfo){.buffer = capture.buffer, .offset = plan.capture_offset, .range = plan.capture_range};
    infos[plan.descriptor_count + 1] = (VkDescriptorBufferInfo){.buffer = control.buffer, .range = 32};
    for (unsigned i = 0; i < 2; ++i) writes[plan.descriptor_count + i] = (VkWriteDescriptorSet){
        .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = sets[1], .dstBinding = i,
        .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &infos[plan.descriptor_count + i]};
    vkUpdateDescriptorSets(device, write_count, writes, 0, NULL);
    VkCommandPoolCreateInfo command_pool_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family};
    VkCommandPool command_pool;
    CHECK(vkCreateCommandPool(device, &command_pool_info, NULL, &command_pool));
    VkCommandBufferAllocateInfo command_alloc = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = command_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer command;
    CHECK(vkAllocateCommandBuffers(device, &command_alloc, &command));
    VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CHECK(vkBeginCommandBuffer(command, &begin));
    VkRenderingInfo render = {.sType = VK_STRUCTURE_TYPE_RENDERING_INFO,
        .renderArea = {{0, 0}, {1, 1}}, .layerCount = 1};
    vkCmdBeginRendering(command, &render);
    vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline);
    vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 2, sets, 0, NULL);
    if (plan.push_size) vkCmdPushConstants(command, layout, VK_SHADER_STAGE_MESH_BIT_EXT, 0, plan.push_size, plan.push);
    draw(command, plan.groups[0], plan.groups[1], plan.groups[2]);
    vkCmdEndRendering(command);
    VkMemoryBarrier host_read = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_MESH_SHADER_BIT_EXT, VK_PIPELINE_STAGE_HOST_BIT,
        0, 1, &host_read, 0, NULL, 0, NULL);
    CHECK(vkEndCommandBuffer(command));
    VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    CHECK(vkCreateFence(device, &fence_info, NULL, &fence));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);
    VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &command};
    CHECK(vkQueueSubmit(queue, 1, &submit, fence));
    VkResult waited = vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_C(2000000000));
    if (waited != VK_SUCCESS) {
        fprintf(stderr, "Bounded fence wait failed: %d; exiting without idle/destruction waits\n", waited);
        fflush(stderr); _Exit(3);
    }

    FILE *output = fopen(argv[3], "wb");
    REQUIRE(output);
    int write_failed = fwrite(capture.mapped, 1, plan.capture_size, output) != plan.capture_size;
    REQUIRE(!fclose(output) && !write_failed);
    int inputs_unchanged = 1;
    for (uint32_t i = 0; i < plan.input_count; ++i)
        inputs_unchanged &= !memcmp(plan.inputs[i].gpu.mapped, plan.inputs[i].data, plan.inputs[i].size);
    REQUIRE(inputs_unchanged && !memcmp(control.mapped, origin_control, sizeof(origin_control)));
    printf("{\"schema\":2,\"output_profile\":"); json_string(plan.output_profile);
    printf(",\"gpu_executed\":true,\"mesh_draw_calls\":1,\"device\":");
    json_string(properties.properties.deviceName);
    printf(",\"driver\":"); json_string(driver.driverName);
    printf(",\"driver_info\":"); json_string(driver.driverInfo);
    printf(",\"vendor_id\":%u,\"device_id\":%u,\"requested_width\":%lu,\"actual_width\":%u,"
        "\"width_source\":\"pipeline_executable_properties\",\"executable_stage\":%u,"
        "\"workgroup\":[32,1,1],\"draw_mesh_tasks\":[%u,%u,%u],\"origin\":[%u,%u,%u],"
        "\"fence_timeout_ns\":2000000000,\"input_bytes_unchanged\":true,\"control_bytes_unchanged\":true,"
        "\"robustBufferAccess2\":true,\"nullDescriptor\":%s,\"capture_bytes\":%u,"
        "\"rasterizer_discard\":true,\"attachments\":0,\"pipeline_optimization_disabled\":false}\n",
        properties.properties.vendorID, properties.properties.deviceID, required_width, prop.subgroupSize, prop.stages,
        plan.groups[0], plan.groups[1], plan.groups[2], plan.origin[0], plan.origin[1], plan.origin[2],
        robustness.nullDescriptor ? "true" : "false", plan.capture_size);
    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, command_pool, NULL);
    vkDestroyDescriptorPool(device, pool, NULL);
    vkDestroyPipeline(device, pipeline, NULL);
    vkDestroyShaderModule(device, module, NULL);
    vkDestroyPipelineLayout(device, layout, NULL);
    for (unsigned i = 0; i < 2; ++i) vkDestroyDescriptorSetLayout(device, set_layouts[i], NULL);
    for (uint32_t i = 0; i < plan.input_count + 2; ++i) {
        Buffer *buffer = i < plan.input_count ? &plan.inputs[i].gpu : i == plan.input_count ? &capture : &control;
        vkUnmapMemory(device, buffer->memory);
        vkDestroyBuffer(device, buffer->buffer, NULL);
        vkFreeMemory(device, buffer->memory, NULL);
    }
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    for (uint32_t i = 0; i < plan.input_count; ++i) free(plan.inputs[i].data);
    free(plan.capture); free(code);
    return 0;
}
