/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Independently authored bounded KHR cooperative-matrix fixtures. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GUARD 256u
#define CHECK(call) do { VkResult status = (call); if (status != VK_SUCCESS) { \
    fprintf(stderr, "%s failed: %d\n", #call, status); return 1; } } while (0)
#define REQUIRE(test) do { if (!(test)) { fprintf(stderr, "Rejected: %s\n", #test); return 1; } } while (0)

typedef struct Fixture {
    uint32_t kind, width, id, groups, counts[4];
    void *storage;
    uint16_t *a, *b;
    uint32_t *c, *expected;
} Fixture;
typedef struct Buffer {
    VkBuffer buffer;
    VkDeviceMemory memory;
    uint8_t *mapped;
    size_t bytes;
} Buffer;

static void *read_file(const char *name, size_t minimum, size_t maximum, size_t *size) {
    FILE *file = fopen(name, "rb");
    if (!file) return NULL;
    if (fseek(file, 0, SEEK_END)) { fclose(file); return NULL; }
    long length = ftell(file);
    if (length < 0 || (size_t)length < minimum || (size_t)length > maximum) { fclose(file); return NULL; }
    rewind(file);
    void *data = malloc((size_t)length);
    if (!data || fread(data, 1, (size_t)length, file) != (size_t)length) {
        free(data); fclose(file); return NULL;
    }
    fclose(file); *size = (size_t)length; return data;
}

static uint32_t float_word(float value) {
    uint32_t word; memcpy(&word, &value, 4); return word;
}
/* Only small nonnegative integer fixture values, all exactly representable. */
static uint16_t small_half(unsigned value) {
    if (!value) return 0;
    uint32_t word = float_word((float)value);
    return (uint16_t)((((word >> 23) & 255u) - 127u + 15u) << 10 | ((word >> 13) & 1023u));
}

static unsigned safe_a(const Fixture *f, unsigned group, unsigned row, unsigned k) {
    return f->id == 1 ? row == k : (row + k + group) % 3 + 1;
}
static unsigned safe_b(const Fixture *f, unsigned group, unsigned k, unsigned col) {
    return f->id == 1 ? k * 8 + col + 1 + group * 4 : (k + 2 * col + group) % 5 + 1;
}
static unsigned safe_c(const Fixture *f, unsigned group, unsigned row, unsigned col) {
    return f->id == 1 ? row * 4 + col + group * 100 : (row + col + group) % 7 + group * 100;
}
static uint16_t rounding_a(unsigned row, unsigned k) {
    if (row % 4 == 0) return k == 0 ? 0x3c00 : k == 1 ? 0x1000 : k == 8 ? 0xbc00 : 0;
    if (row % 4 == 1) return 0x8000;
    if (row % 4 == 2) return k == 0 ? 0x7bff : k == 1 ? 0x3c00 : k == 2 ? 0xfbff : 0;
    return 0;
}

/* Validate every input/reference word before even vkCreateInstance. Expected
 * safe sums are independent integer sums; diagnostic rounding references are
 * four explicitly known software-checkpoint results, not a DPAS promise. */
static int load_fixture(const char *path, Fixture *f) {
    size_t size;
    uint8_t *data = read_file(path, 40, 32768, &size);
    REQUIRE(data && !memcmp(data, "OOMAT001", 8));
    const uint16_t endian = 1;
    REQUIRE(*(const uint8_t *)&endian == 1 && sizeof(float) == 4);
    uint32_t header[8]; memcpy(header, data + 8, sizeof(header));
    f->kind = header[0]; f->width = header[1]; f->id = header[2]; f->groups = header[3];
    memcpy(f->counts, header + 4, 16);
    REQUIRE((f->kind == 1 || f->kind == 2) && (f->width == 8 || f->width == 16 || f->width == 32));
    REQUIRE(f->id >= 1 && f->id <= 3 && f->groups == 32 / f->width);
    const unsigned na = f->kind == 1 ? 128 : 512, nb = f->kind == 1 ? 128 : 256;
    const unsigned nd = f->kind == 1 ? 64 : 128, rows = nd / 8;
    REQUIRE(f->counts[0] == na * f->groups && f->counts[1] == nb * f->groups &&
            f->counts[2] == nd * f->groups && f->counts[3] == nd * f->groups);
    REQUIRE(size == 40 + (size_t)(na + nb) * f->groups * 2 + (size_t)nd * f->groups * 8);
    f->storage = data; f->a = (uint16_t *)(data + 40); f->b = f->a + f->counts[0];
    f->c = (uint32_t *)(f->b + f->counts[1]); f->expected = f->c + f->counts[2];
    for (unsigned g = 0; g < f->groups; ++g) {
        for (unsigned i = 0; i < na; ++i) {
            unsigned row, k; int padded;
            if (f->kind == 1) { row = i / 16; k = i % 16; padded = 0; }
            else { unsigned tile = i / 128; row = tile / 2 * 8 + i % 128 / 16;
                   k = tile % 2 * 8 + i % 16; padded = i % 16 >= 8; }
            uint16_t expected = padded ? 0 : f->id == 3 ? rounding_a(row, k) : small_half(safe_a(f, g, row, k));
            REQUIRE(f->a[g * na + i] == expected);
        }
        for (unsigned i = 0; i < nb; ++i) {
            unsigned k = f->kind == 1 ? i / 8 : i / 128 * 8 + i % 128 / 8;
            unsigned col = i % 8;
            int padded = f->kind == 2 && i % 128 / 8 >= 8;
            uint16_t expected = padded ? 0 : f->id == 3 ? 0x3c00 : small_half(safe_b(f, g, k, col));
            REQUIRE(f->b[g * nb + i] == expected);
        }
        for (unsigned row = 0; row < rows; ++row) for (unsigned col = 0; col < 8; ++col) {
            uint32_t cin, expected;
            if (f->id == 3) {
                cin = row % 4 == 1 ? 0x80000000 : row % 4 == 3 ? 0x33800000 : 0;
                expected = row % 4 == 1 ? 0x80000000 : row % 4 == 2 ? 0x3f800000 : row % 4 == 3 ? 0x33800000 : 0;
            } else {
                unsigned sum = safe_c(f, g, row, col);
                cin = float_word((float)sum);
                for (unsigned k = 0; k < 16; ++k) sum += safe_a(f, g, row, k) * safe_b(f, g, k, col);
                REQUIRE(sum <= 2048); /* exact at both half checkpoints */
                expected = float_word((float)sum);
            }
            REQUIRE(f->c[g * nd + row * 8 + col] == cin && f->expected[g * nd + row * 8 + col] == expected);
        }
    }
    return 0;
}

static int make_buffer(VkPhysicalDevice physical, VkDevice device, size_t bytes, Buffer *out) {
    out->bytes = bytes;
    VkBufferCreateInfo info = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = bytes + GUARD * 2,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(device, &info, NULL, &out->buffer));
    VkMemoryRequirements requirements;
    vkGetBufferMemoryRequirements(device, out->buffer, &requirements);
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(physical, &props);
    unsigned type = UINT32_MAX;
    VkMemoryPropertyFlags needed = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    for (unsigned i = 0; i < props.memoryTypeCount; ++i)
        if ((requirements.memoryTypeBits & (1u << i)) && (props.memoryTypes[i].propertyFlags & needed) == needed) { type = i; break; }
    REQUIRE(type != UINT32_MAX);
    VkMemoryAllocateInfo allocation = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = requirements.size, .memoryTypeIndex = type};
    CHECK(vkAllocateMemory(device, &allocation, NULL, &out->memory));
    CHECK(vkBindBufferMemory(device, out->buffer, out->memory, 0));
    CHECK(vkMapMemory(device, out->memory, 0, info.size, 0, (void **)&out->mapped));
    memset(out->mapped, 0xa5, (size_t)info.size);
    return 0;
}

static void json_string(const char *s) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p == '\\' || *p == '"') { putchar('\\'); putchar(*p); }
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "Usage: matrix-helper cpu fixture | properties/compile/dispatch fixture module output\n"); return 2; }
    const int cpu = !strcmp(argv[1], "cpu"), properties_only = !strcmp(argv[1], "properties");
    const int dispatch = !strcmp(argv[1], "dispatch"), compile = !strcmp(argv[1], "compile");
    REQUIRE((cpu && argc == 3) || ((properties_only || compile || dispatch) && argc == 5));
    Fixture fixture = {0};
    if (load_fixture(argv[2], &fixture)) return 2;
    if (cpu) { puts("{\"cpu_validated\":true,\"vulkan_instance_created\":false}"); free(fixture.storage); return 0; }
    size_t code_size = 0;
    uint32_t *code = read_file(argv[3], 20, 65536, &code_size);
    REQUIRE(code && code_size % 4 == 0 && code[0] == 0x07230203);
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina synthetic cooperative matrix", .apiVersion = VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance instance;
    CHECK(vkCreateInstance(&ici, NULL, &instance));
    uint32_t n = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &n, NULL));
    REQUIRE(n > 0 && n <= 256);
    VkPhysicalDevice devices[256], physical = VK_NULL_HANDLE;
    CHECK(vkEnumeratePhysicalDevices(instance, &n, devices));
    for (uint32_t i = 0; i < n; ++i) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(devices[i], &p);
        if (p.vendorID == 0x8086 && p.deviceID == 0x56a0) { REQUIRE(physical == VK_NULL_HANDLE); physical = devices[i]; }
    }
    REQUIRE(physical != VK_NULL_HANDLE);
    VkPhysicalDeviceSubgroupProperties sg = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_PROPERTIES};
    VkPhysicalDeviceSubgroupSizeControlProperties widths = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES, .pNext = &sg};
    VkPhysicalDeviceCooperativeMatrixPropertiesKHR matrix = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COOPERATIVE_MATRIX_PROPERTIES_KHR, .pNext = &widths};
    VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &matrix};
    VkPhysicalDeviceProperties2 props = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(physical, &props);
    REQUIRE(props.properties.apiVersion >= VK_API_VERSION_1_3 && driver.driverID == VK_DRIVER_ID_INTEL_OPEN_SOURCE_MESA);
    REQUIRE(fixture.width >= widths.minSubgroupSize && fixture.width <= widths.maxSubgroupSize &&
            (widths.requiredSubgroupSizeStages & VK_SHADER_STAGE_COMPUTE_BIT) && (sg.supportedStages & VK_SHADER_STAGE_COMPUTE_BIT) &&
            (sg.supportedOperations & VK_SUBGROUP_FEATURE_BASIC_BIT) && (matrix.cooperativeMatrixSupportedStages & VK_SHADER_STAGE_COMPUTE_BIT));
    REQUIRE(props.properties.limits.minStorageBufferOffsetAlignment && GUARD % props.properties.limits.minStorageBufferOffsetAlignment == 0);
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    REQUIRE(extension_count > 0 && extension_count <= 4096);
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    REQUIRE(extensions);
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int has_matrix = 0, has_executable = 0;
    for (uint32_t i = 0; i < extension_count; ++i) {
        has_matrix |= !strcmp(extensions[i].extensionName, VK_KHR_COOPERATIVE_MATRIX_EXTENSION_NAME);
        has_executable |= !strcmp(extensions[i].extensionName, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME);
    }
    free(extensions); REQUIRE(has_matrix && has_executable);
    PFN_vkGetPhysicalDeviceCooperativeMatrixPropertiesKHR get_shapes = (PFN_vkGetPhysicalDeviceCooperativeMatrixPropertiesKHR)
        vkGetInstanceProcAddr(instance, "vkGetPhysicalDeviceCooperativeMatrixPropertiesKHR");
    REQUIRE(get_shapes);
    uint32_t shape_count = 0;
    CHECK(get_shapes(physical, &shape_count, NULL)); REQUIRE(shape_count > 0 && shape_count <= 4096);
    VkCooperativeMatrixPropertiesKHR *shapes = calloc(shape_count, sizeof(*shapes)); REQUIRE(shapes);
    for (uint32_t i = 0; i < shape_count; ++i) shapes[i].sType = VK_STRUCTURE_TYPE_COOPERATIVE_MATRIX_PROPERTIES_KHR;
    CHECK(get_shapes(physical, &shape_count, shapes));
    int shape_supported = 0;
    for (uint32_t i = 0; i < shape_count; ++i) {
        VkCooperativeMatrixPropertiesKHR *s = &shapes[i];
        if (s->MSize == 8 && s->NSize == 8 && s->KSize == 16 && s->AType == VK_COMPONENT_TYPE_FLOAT16_KHR &&
            s->BType == VK_COMPONENT_TYPE_FLOAT16_KHR && s->CType == VK_COMPONENT_TYPE_FLOAT32_KHR &&
            s->ResultType == VK_COMPONENT_TYPE_FLOAT32_KHR && s->scope == VK_SCOPE_SUBGROUP_KHR && !s->saturatingAccumulation) shape_supported = 1;
    }
    free(shapes); REQUIRE(shape_supported);
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceCooperativeMatrixFeaturesKHR cooperative = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COOPERATIVE_MATRIX_FEATURES_KHR, .pNext = &executable};
    VkPhysicalDeviceVulkan13Features f13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &cooperative};
    VkPhysicalDeviceVulkan12Features f12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &f13};
    VkPhysicalDeviceVulkan11Features f11 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &f12};
    VkPhysicalDeviceFeatures2 features = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f11};
    vkGetPhysicalDeviceFeatures2(physical, &features);
    REQUIRE(features.features.robustBufferAccess && f11.storageBuffer16BitAccess && f12.shaderFloat16 && f12.vulkanMemoryModel &&
            f13.subgroupSizeControl && f13.computeFullSubgroups && cooperative.cooperativeMatrix && executable.pipelineExecutableInfo);
    if (properties_only) {
        printf("{\"phase\":\"properties\",\"requested_width\":%u,\"advertised_shape\":true,\"subgroup_min\":%u,\"subgroup_max\":%u,\"subgroup_default\":%u,\"pipeline_compiled\":false,\"gpu_executed\":false,\"device\":",
               fixture.width, widths.minSubgroupSize, widths.maxSubgroupSize, sg.subgroupSize);
        json_string(props.properties.deviceName); puts("}");
        free(code); free(fixture.storage); vkDestroyInstance(instance, NULL); return 0;
    }
    uint32_t queue_count = 0, family = UINT32_MAX;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL); REQUIRE(queue_count > 0 && queue_count <= 256);
    VkQueueFamilyProperties queues[256]; vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
    for (uint32_t i = 0; i < queue_count; ++i) if (queues[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { family = i; break; }
    REQUIRE(family != UINT32_MAX);
    /* Enable only fixture requirements, not every advertised feature. */
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR eexec = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR, .pipelineExecutableInfo = VK_TRUE};
    VkPhysicalDeviceCooperativeMatrixFeaturesKHR ecoop = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_COOPERATIVE_MATRIX_FEATURES_KHR, .pNext = &eexec, .cooperativeMatrix = VK_TRUE};
    VkPhysicalDeviceVulkan13Features e13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &ecoop, .subgroupSizeControl = VK_TRUE, .computeFullSubgroups = VK_TRUE};
    VkPhysicalDeviceVulkan12Features e12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &e13, .shaderFloat16 = VK_TRUE, .vulkanMemoryModel = VK_TRUE};
    VkPhysicalDeviceVulkan11Features e11 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &e12, .storageBuffer16BitAccess = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &e11, .features = {.robustBufferAccess = VK_TRUE}};
    const char *device_extensions[] = {VK_KHR_COOPERATIVE_MATRIX_EXTENSION_NAME, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME};
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info, .enabledExtensionCount = 2, .ppEnabledExtensionNames = device_extensions};
    VkDevice device; CHECK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkDescriptorSetLayoutBinding bindings[4];
    for (uint32_t i = 0; i < 4; ++i) bindings[i] = (VkDescriptorSetLayoutBinding){.binding = i, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo set_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 4, .pBindings = bindings};
    VkDescriptorSetLayout set_layout; CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &set_layout));
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1, .pSetLayouts = &set_layout};
    VkPipelineLayout layout; CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModuleCreateInfo module_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = code_size, .pCode = code};
    VkShaderModule module; CHECK(vkCreateShaderModule(device, &module_info, NULL, &module));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo required = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO, .requiredSubgroupSize = fixture.width};
    VkComputePipelineCreateInfo pipeline_info = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, .flags = VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .pNext = &required, .flags = VK_PIPELINE_SHADER_STAGE_CREATE_REQUIRE_FULL_SUBGROUPS_BIT,
                  .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = module, .pName = "main"}, .layout = layout};
    VkPipeline pipeline; CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    PFN_vkGetPipelineExecutablePropertiesKHR get_executable = (PFN_vkGetPipelineExecutablePropertiesKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutablePropertiesKHR");
    REQUIRE(get_executable);
    VkPipelineInfoKHR pi = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR, .pipeline = pipeline};
    uint32_t executable_count = 0; CHECK(get_executable(device, &pi, &executable_count, NULL)); REQUIRE(executable_count == 1);
    VkPipelineExecutablePropertiesKHR compiled = {.sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR};
    CHECK(get_executable(device, &pi, &executable_count, &compiled));
    if (compiled.subgroupSize != fixture.width || compiled.stages != VK_SHADER_STAGE_COMPUTE_BIT)
        fprintf(stderr, "Compiled executable mismatch: requested width %u, actual width %u, stage mask %u; no dispatch\n",
                fixture.width, compiled.subgroupSize, compiled.stages);
    REQUIRE(compiled.subgroupSize == fixture.width && compiled.stages == VK_SHADER_STAGE_COMPUTE_BIT);
    unsigned mismatches = 0; int intact = 1;
    Buffer buffers[4] = {{0}};
    VkCommandPool command_pool = VK_NULL_HANDLE;
    VkDescriptorPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    if (dispatch) {
        const void *inputs[] = {fixture.a, fixture.b, fixture.c};
        for (unsigned i = 0; i < 4; ++i) {
            size_t bytes = fixture.counts[i] * (i < 2 ? 2u : 4u);
            if (make_buffer(physical, device, bytes, &buffers[i])) return 1;
            if (i < 3) memcpy(buffers[i].mapped + GUARD, inputs[i], bytes);
            else memset(buffers[i].mapped + GUARD, 0xcd, bytes);
        }
        VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 4};
        VkDescriptorPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size};
        CHECK(vkCreateDescriptorPool(device, &pool_info, NULL, &pool));
        VkDescriptorSetAllocateInfo set_alloc = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = pool, .descriptorSetCount = 1, .pSetLayouts = &set_layout};
        VkDescriptorSet set; CHECK(vkAllocateDescriptorSets(device, &set_alloc, &set));
        VkDescriptorBufferInfo views[4]; VkWriteDescriptorSet writes[4];
        for (unsigned i = 0; i < 4; ++i) {
            views[i] = (VkDescriptorBufferInfo){.buffer = buffers[i].buffer, .offset = GUARD, .range = buffers[i].bytes};
            writes[i] = (VkWriteDescriptorSet){.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = set, .dstBinding = i, .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &views[i]};
        }
        vkUpdateDescriptorSets(device, 4, writes, 0, NULL);
        VkCommandPoolCreateInfo command_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family};
        CHECK(vkCreateCommandPool(device, &command_info, NULL, &command_pool));
        VkCommandBufferAllocateInfo alloc = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = command_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
        VkCommandBuffer command; CHECK(vkAllocateCommandBuffers(device, &alloc, &command));
        VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
        CHECK(vkBeginCommandBuffer(command, &begin));
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, NULL);
        vkCmdDispatch(command, 1, 1, 1);
        VkMemoryBarrier barrier = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT};
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &barrier, 0, NULL, 0, NULL);
        CHECK(vkEndCommandBuffer(command));
        VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        CHECK(vkCreateFence(device, &fence_info, NULL, &fence));
        VkQueue queue; vkGetDeviceQueue(device, family, 0, &queue);
        VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &command};
        CHECK(vkQueueSubmit(queue, 1, &submit, fence));
        VkResult waited = vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_C(2000000000));
        if (waited != VK_SUCCESS) { fprintf(stderr, "Fence bound failed: %d; exiting without idle waits\n", waited); fflush(stderr); _Exit(3); }
        for (unsigned i = 0; i < 4; ++i) {
            for (unsigned j = 0; j < GUARD; ++j)
                if (buffers[i].mapped[j] != 0xa5 || buffers[i].mapped[GUARD + buffers[i].bytes + j] != 0xa5) intact = 0;
            if (i < 3 && memcmp(buffers[i].mapped + GUARD, inputs[i], buffers[i].bytes)) intact = 0;
        }
        for (unsigned i = 0; i < fixture.counts[3]; ++i) {
            uint32_t actual; memcpy(&actual, buffers[3].mapped + GUARD + i * 4, 4);
            mismatches += actual != fixture.expected[i];
        }
        FILE *file = fopen(argv[4], "wb"); REQUIRE(file);
        int failed = fwrite(buffers[3].mapped + GUARD, 1, buffers[3].bytes, file) != buffers[3].bytes;
        if (fclose(file)) failed = 1;
        REQUIRE(!failed);
    }
    printf("{\"phase\":\"%s\",\"requested_width\":%u,\"actual_width\":%u,\"width_source\":\"pipeline_executable_properties\",\"pipeline_compiled\":true,\"gpu_executed\":%s,\"dispatches\":%u,\"output_checked\":%s,\"guards_and_inputs_unchanged\":%s,\"full_output_word_count\":%u,\"mismatches_with_software_reference\":%u,\"safe_exact_fixture\":%s,\"fence_timeout_ns\":2000000000,\"device\":",
           argv[1], fixture.width, compiled.subgroupSize, dispatch ? "true" : "false", dispatch ? 1 : 0,
           dispatch ? "true" : "false", dispatch ? (intact ? "true" : "false") : "null", fixture.counts[3], mismatches, fixture.id != 3 ? "true" : "false");
    json_string(props.properties.deviceName); printf(",\"driver\":"); json_string(driver.driverInfo); puts("}");
    if (fence) vkDestroyFence(device, fence, NULL);
    if (command_pool) vkDestroyCommandPool(device, command_pool, NULL);
    if (pool) vkDestroyDescriptorPool(device, pool, NULL);
    for (unsigned i = 0; i < 4; ++i) if (buffers[i].buffer) {
        vkUnmapMemory(device, buffers[i].memory); vkDestroyBuffer(device, buffers[i].buffer, NULL); vkFreeMemory(device, buffers[i].memory, NULL);
    }
    vkDestroyPipeline(device, pipeline, NULL); vkDestroyShaderModule(device, module, NULL);
    vkDestroyPipelineLayout(device, layout, NULL); vkDestroyDescriptorSetLayout(device, set_layout, NULL);
    vkDestroyDevice(device, NULL); vkDestroyInstance(instance, NULL);
    free(code); free(fixture.storage);
    return !intact || (dispatch && fixture.id != 3 && mismatches) ? 1 : 0;
}
