/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Compile-only compute-pipeline inspection: no command pools, queues or dispatch. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

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

static double seconds(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return value.tv_sec + value.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    if (argc != 5 || (strcmp(argv[3], "pinned32") && strcmp(argv[3], "pinned16") && strcmp(argv[3], "unpinned"))) {
        fprintf(stderr, "Usage: compile-probe shader.spv layout.txt pinned32|pinned16|unpinned device-index\n");
        return 2;
    }
    char *end = NULL;
    unsigned long requested_device = strtoul(argv[4], &end, 10);
    if (!*argv[4] || *end || requested_device > 255) return 2;
    const int pinned = strcmp(argv[3], "unpinned") != 0;
    const uint32_t required_width = !strcmp(argv[3], "pinned16") ? 16 : 32;
    FILE *file = fopen(argv[1], "rb");
    if (!file) { perror("shader"); return 2; }
    if (fseek(file, 0, SEEK_END)) return 2;
    long byte_count = ftell(file);
    if (byte_count < 20 || byte_count > 16 * 1024 * 1024 || byte_count % 4) return 2;
    rewind(file);
    uint32_t *code = malloc((size_t)byte_count);
    if (!code || fread(code, 1, (size_t)byte_count, file) != (size_t)byte_count) return 2;
    fclose(file);
    if (code[0] != 0x07230203) return 2;

    VkDescriptorSetLayoutBinding bindings[8][256] = {{{0}}};
    uint32_t binding_counts[8] = {0};
    unsigned push_size, count, set_count = 0;
    file = fopen(argv[2], "r");
    if (!file || fscanf(file, "%u %u", &push_size, &count) != 2 || count > 256 || push_size > 128) return 2;
    for (unsigned i = 0; i < count; ++i) {
        unsigned set, binding, type, descriptors;
        if (fscanf(file, "%u %u %u %u", &set, &binding, &type, &descriptors) != 4 ||
            set >= 8 || binding > 1023 || type > 7 || !descriptors || descriptors > 65536) return 2;
        if (binding_counts[set] >= 256) return 2;
        for (unsigned j = 0; j < binding_counts[set]; ++j)
            if (bindings[set][j].binding == binding) return 2;
        bindings[set][binding_counts[set]++] = (VkDescriptorSetLayoutBinding){
            .binding = binding, .descriptorType = (VkDescriptorType)type,
            .descriptorCount = descriptors, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
        if (set_count <= set) set_count = set + 1;
    }
    char tail;
    if (fscanf(file, " %c", &tail) != EOF || set_count != 1) return 2;
    fclose(file);

    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina compile-only probe", .apiVersion = VK_API_VERSION_1_3};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance instance;
    CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t device_count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &device_count, NULL));
    if (!device_count || device_count > 256 || requested_device >= device_count) {
        fprintf(stderr, "Requested device is unavailable\n"); return 1;
    }
    VkPhysicalDevice physical_devices[256];
    CHECK(vkEnumeratePhysicalDevices(instance, &device_count, physical_devices));
    VkPhysicalDevice physical = physical_devices[requested_device];
    VkPhysicalDevicePushDescriptorPropertiesKHR push_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR};
    VkPhysicalDeviceFloatControlsProperties float_controls = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES, .pNext = &push_properties};
    VkPhysicalDeviceSubgroupSizeControlProperties subgroup = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES, .pNext = &float_controls};
    VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &subgroup};
    VkPhysicalDeviceProperties2 properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(physical, &properties);
    if (properties.properties.vendorID != 0x8086 || properties.properties.deviceID != 0x56a0 || properties.properties.apiVersion < VK_API_VERSION_1_3 ||
        (pinned && (subgroup.minSubgroupSize > required_width || subgroup.maxSubgroupSize < required_width ||
                    !(subgroup.requiredSubgroupSizeStages & VK_SHADER_STAGE_COMPUTE_BIT)))) {
        fprintf(stderr, "Vulkan 1.3 and requested compute subgroup policy are required\n"); return 1;
    }
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    if (!extensions && extension_count) return 1;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int executable_extension = 0, robustness_extension = 0, push_extension = 0;
    for (unsigned i = 0; i < extension_count; ++i)
        if (!strcmp(extensions[i].extensionName, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME)) executable_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_EXT_ROBUSTNESS_2_EXTENSION_NAME)) robustness_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME)) push_extension = 1;
    free(extensions);
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceRobustness2FeaturesEXT supported_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &executable : NULL};
    VkPhysicalDeviceVulkan13Features supported13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = robustness_extension ? &supported_robustness : (executable_extension ? (void *)&executable : NULL)};
    VkPhysicalDeviceVulkan12Features supported12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &supported13};
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported12};
    vkGetPhysicalDeviceFeatures2(physical, &supported);
    unsigned total_descriptors = 0;
    for (unsigned i = 0; i < binding_counts[0]; ++i) total_descriptors += bindings[0][i].descriptorCount;
    if (!push_extension || total_descriptors > push_properties.maxPushDescriptors ||
        !supported.features.robustBufferAccess || !robustness_extension || !supported_robustness.robustBufferAccess2 ||
        !supported.features.shaderInt16 || !supported.features.shaderInt64 ||
        !supported.features.shaderStorageBufferArrayDynamicIndexing || !supported.features.shaderImageGatherExtended ||
        !supported12.shaderFloat16 || !supported12.uniformBufferStandardLayout || !supported12.bufferDeviceAddress ||
        !float_controls.shaderSignedZeroInfNanPreserveFloat16 || !float_controls.shaderSignedZeroInfNanPreserveFloat32 ||
        !supported13.subgroupSizeControl || !supported13.computeFullSubgroups) {
        fprintf(stderr, "A required target shader feature is unavailable\n"); return 1;
    }
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR,
        .pipelineExecutableInfo = executable.pipelineExecutableInfo};
    VkPhysicalDeviceRobustness2FeaturesEXT enabled_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &enabled_executable : NULL, .robustBufferAccess2 = VK_TRUE,
        .nullDescriptor = supported_robustness.nullDescriptor};
    VkPhysicalDeviceVulkan13Features enabled13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &enabled_robustness, .subgroupSizeControl = VK_TRUE, .computeFullSubgroups = VK_TRUE};
    VkPhysicalDeviceVulkan12Features enabled12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &enabled13, .shaderFloat16 = VK_TRUE, .uniformBufferStandardLayout = VK_TRUE, .bufferDeviceAddress = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled12,
        .features = {.robustBufferAccess = VK_TRUE, .shaderInt16 = VK_TRUE, .shaderInt64 = VK_TRUE,
            .shaderStorageBufferArrayDynamicIndexing = VK_TRUE, .shaderImageGatherExtended = VK_TRUE}};
    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    VkQueueFamilyProperties *queues = calloc(queue_count, sizeof(*queues));
    if (!queues) return 1;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
    uint32_t queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < queue_count; ++i)
        if (queues[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { queue_family = i; break; }
    free(queues);
    if (queue_family == UINT32_MAX) return 1;
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family, .queueCount = 1, .pQueuePriorities = &priority};
    const char *extension_names[3] = {VK_EXT_ROBUSTNESS_2_EXTENSION_NAME, VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME,
        VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = executable_extension ? 3 : 2,
        .ppEnabledExtensionNames = extension_names};
    VkDevice device;
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkDescriptorSetLayout layouts[8];
    for (unsigned set = 0; set < set_count; ++set) {
        VkDescriptorSetLayoutCreateInfo info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR,
            .bindingCount = binding_counts[set], .pBindings = bindings[set]};
        CHECK(vkCreateDescriptorSetLayout(device, &info, NULL, &layouts[set]));
    }
    VkPushConstantRange push = {.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, .offset = 0, .size = push_size};
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = set_count, .pSetLayouts = layouts,
        .pushConstantRangeCount = push_size ? 1 : 0, .pPushConstantRanges = push_size ? &push : NULL};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModuleCreateInfo module_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = (size_t)byte_count, .pCode = code};
    VkShaderModule module;
    CHECK(vkCreateShaderModule(device, &module_info, NULL, &module));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo required = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO, .requiredSubgroupSize = required_width};
    VkComputePipelineCreateInfo pipeline_info = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .flags = executable.pipelineExecutableInfo ? VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR : 0,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .pNext = pinned ? &required : NULL,
            .flags = pinned ? VK_PIPELINE_SHADER_STAGE_CREATE_REQUIRE_FULL_SUBGROUPS_BIT : 0,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = module, .pName = "main"}, .layout = layout};
    VkPipeline pipeline;
    double start = seconds();
    CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    double elapsed = seconds() - start;
    printf("{\"schema\":1,\"compile_only\":true,\"dispatch_executed\":false,\"device\":");
    json_string(properties.properties.deviceName);
    printf(",\"driver\":"); json_string(driver.driverName);
    printf(",\"driver_info\":"); json_string(driver.driverInfo);
    printf(",\"vendor_id\":%u,\"device_id\":%u,\"api_version\":%u,\"policy\":", properties.properties.vendorID,
        properties.properties.deviceID, properties.properties.apiVersion);
    json_string(argv[3]);
    printf(",\"features\":{\"robustBufferAccess\":true,\"robustBufferAccess2\":true,"
        "\"uniformBufferStandardLayout\":true,\"bufferDeviceAddress\":true,"
        "\"shaderFloat16\":true,\"shaderSignedZeroInfNanPreserveFloat16\":true,\"shaderSignedZeroInfNanPreserveFloat32\":true,\"shaderInt16\":true,\"shaderInt64\":true,\"shaderStorageBufferArrayDynamicIndexing\":true,"
        "\"shaderImageGatherExtended\":true,\"subgroupSizeControl\":true,\"computeFullSubgroups\":true},"
        "\"pipeline_optimization_disabled\":false");
    printf(",\"null_descriptor_enabled\":%s,\"descriptor_mode\":\"push\",\"push_constant_range_bytes\":%u",
        enabled_robustness.nullDescriptor ? "true" : "false", push_size);
    printf(",\"pipeline_create_seconds\":%.9f,\"executables\":[", elapsed);
    if (executable.pipelineExecutableInfo) {
        PFN_vkGetPipelineExecutablePropertiesKHR get_properties = (PFN_vkGetPipelineExecutablePropertiesKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutablePropertiesKHR");
        PFN_vkGetPipelineExecutableStatisticsKHR get_statistics = (PFN_vkGetPipelineExecutableStatisticsKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutableStatisticsKHR");
        if (!get_properties || !get_statistics) return 1;
        VkPipelineInfoKHR info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR, .pipeline = pipeline};
        uint32_t count = 0;
        CHECK(get_properties(device, &info, &count, NULL));
        if (count > 128) return 1;
        VkPipelineExecutablePropertiesKHR *props = calloc(count, sizeof(*props));
        if (!props && count) return 1;
        for (unsigned i = 0; i < count; ++i) props[i].sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR;
        CHECK(get_properties(device, &info, &count, props));
        for (unsigned i = 0; i < count; ++i) {
            if (i) putchar(',');
            printf("{\"name\":"); json_string(props[i].name);
            printf(",\"subgroup_size\":%u,\"statistics\":[", props[i].subgroupSize);
            VkPipelineExecutableInfoKHR stat_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INFO_KHR, .pipeline = pipeline, .executableIndex = i};
            uint32_t stat_count = 0;
            CHECK(get_statistics(device, &stat_info, &stat_count, NULL));
            if (stat_count > 512) return 1;
            VkPipelineExecutableStatisticKHR *stats = calloc(stat_count, sizeof(*stats));
            if (!stats && stat_count) return 1;
            for (unsigned j = 0; j < stat_count; ++j) stats[j].sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_STATISTIC_KHR;
            CHECK(get_statistics(device, &stat_info, &stat_count, stats));
            for (unsigned j = 0; j < stat_count; ++j) {
                if (j) putchar(',');
                printf("{\"name\":"); json_string(stats[j].name); printf(",\"value\":");
                switch (stats[j].format) {
                    case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_BOOL32_KHR: printf("%s", stats[j].value.b32 ? "true" : "false"); break;
                    case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_INT64_KHR: printf("%lld", (long long)stats[j].value.i64); break;
                    case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR: printf("%llu", (unsigned long long)stats[j].value.u64); break;
                    case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_FLOAT64_KHR: printf("%.17g", stats[j].value.f64); break;
                    default: printf("null"); break;
                }
                putchar('}');
            }
            free(stats); printf("]}");
        }
        free(props);
    }
    printf("]}\n");
    vkDestroyPipeline(device, pipeline, NULL);
    vkDestroyShaderModule(device, module, NULL);
    vkDestroyPipelineLayout(device, layout, NULL);
    for (unsigned set = 0; set < set_count; ++set) vkDestroyDescriptorSetLayout(device, layouts[set], NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    free(code);
    return 0;
}
