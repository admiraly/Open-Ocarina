/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Compile-only D8 common-33 set layout. No queue retrieval, commands, resources, submit, or draw. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(call) do { VkResult status = (call); if (status != VK_SUCCESS) { \
    fprintf(stderr, "%s failed: %d\n", #call, status); return 1; } } while (0)

enum {
    COMMON_BINDINGS = 31,
    COMMON_ELEMENTS = 33,
    PUSH_BYTES = 88,
    MESH_STAGE = 128,
    FRAGMENT_STAGE = 16,
    RECORD_BINDING = 30
};

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
    if (argc != 5) {
        fprintf(stderr, "Usage: grok-d8-interval-compile layout.txt device-index mesh.spv fragment.spv\n");
        return 2;
    }
    char *end = NULL;
    unsigned long requested_device = strtoul(argv[2], &end, 10);
    if (!*argv[2] || *end || requested_device > 255) return 2;
    unsigned push_size, count, stage_count, push_mode, color, depth, samples, width, height;
    unsigned depth_test, depth_write, compare, cull, front, a2c, write_mask;
    FILE *file = fopen(argv[1], "r");
    if (!file || fscanf(file, "%u %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u",
        &push_size, &count, &stage_count, &push_mode, &color, &depth, &samples, &width, &height,
        &depth_test, &depth_write, &compare, &cull, &front, &a2c, &write_mask) != 16 ||
        push_size != PUSH_BYTES || count != COMMON_BINDINGS || stage_count != 2 || push_mode != 0 || !color ||
        (samples != 1 && samples != 2 && samples != 4 && samples != 8) || !width || !height ||
        width > 16384 || height > 16384 || depth_test > 1 || depth_write > 1 || compare > 7 ||
        cull > 3 || front > 1 || a2c > 1 || write_mask > 15 || (!depth && (depth_test || depth_write))) return 2;
    VkDescriptorSetLayoutBinding bindings[COMMON_BINDINGS];
    memset(bindings, 0, sizeof bindings);
    uint32_t total_descriptors = 0, record_seen = 0;
    uint32_t uniform_elements = 0, storage_elements = 0, combined_elements = 0;
    uint32_t mesh_uniform = 0, mesh_storage = 0, fragment_uniform = 0, fragment_storage = 0, fragment_combined = 0;
    for (unsigned i = 0; i < count; ++i) {
        unsigned set, binding, type, n, flags;
        if (fscanf(file, "%u %u %u %u %u", &set, &binding, &type, &n, &flags) != 5 || set || binding > 1023 ||
            !n || n > 65536 || !flags || (flags & ~208u)) return 2;
        for (unsigned j = 0; j < i; ++j) if (bindings[j].binding == binding) return 2;
        if (binding == RECORD_BINDING) {
            if (type != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER || n != 1 || flags != MESH_STAGE) return 2;
            record_seen = 1;
        }
        bindings[i] = (VkDescriptorSetLayoutBinding){.binding = binding, .descriptorType = (VkDescriptorType)type,
            .descriptorCount = n, .stageFlags = flags};
        total_descriptors += n;
        if (type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER) uniform_elements += n;
        else if (type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER) storage_elements += n;
        else if (type == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER) combined_elements += n;
        else return 2;
        if (flags & MESH_STAGE) {
            if (type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER) mesh_uniform += n;
            else if (type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER) mesh_storage += n;
            else return 2;
        }
        if (flags & FRAGMENT_STAGE) {
            if (type == VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER) fragment_uniform += n;
            else if (type == VK_DESCRIPTOR_TYPE_STORAGE_BUFFER) fragment_storage += n;
            else if (type == VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER) fragment_combined += n;
            else return 2;
        }
    }
    unsigned mesh_flags = 0, mesh_width = 0, fragment_flags = 0, fragment_width = 1;
    if (fscanf(file, "%u %u", &mesh_flags, &mesh_width) != 2 || mesh_flags != MESH_STAGE || mesh_width != 32) return 2;
    if (fscanf(file, "%u %u", &fragment_flags, &fragment_width) != 2 || fragment_flags != FRAGMENT_STAGE || fragment_width != 0) return 2;
    char tail;
    if (fscanf(file, " %c", &tail) != EOF) return 2;
    fclose(file);
    /* MaxPushDescriptors limits the sum of descriptor counts. 31 bindings can still be 33 elements. */
    if (!record_seen || total_descriptors != COMMON_ELEMENTS || push_mode != 0) return 2;
    if (uniform_elements != 14 || storage_elements != 9 || combined_elements != 10) return 2;
    if (mesh_uniform != 5 || mesh_storage != 7 || fragment_uniform != 9 || fragment_storage != 2 || fragment_combined != 10) return 2;

    uint32_t *codes[2] = {0};
    size_t code_sizes[2] = {0};
    for (unsigned i = 0; i < 2; ++i) {
        file = fopen(argv[i + 3], "rb");
        if (!file || fseek(file, 0, SEEK_END)) return 2;
        long n = ftell(file);
        if (n < 20 || n > 16 * 1024 * 1024 || n % 4) return 2;
        rewind(file);
        codes[i] = malloc((size_t)n);
        code_sizes[i] = (size_t)n;
        if (!codes[i] || fread(codes[i], 1, (size_t)n, file) != (size_t)n) return 2;
        fclose(file);
        if (codes[i][0] != 0x07230203) return 2;
    }

    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina D8 common-33 compile-only probe", .apiVersion = VK_API_VERSION_1_3};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
    VkInstance instance;
    CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t device_count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &device_count, NULL));
    if (!device_count || device_count > 256 || requested_device >= device_count) return 1;
    VkPhysicalDevice physical_devices[256];
    CHECK(vkEnumeratePhysicalDevices(instance, &device_count, physical_devices));
    VkPhysicalDevice physical = physical_devices[requested_device];
    VkPhysicalDeviceMeshShaderPropertiesEXT mesh_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_PROPERTIES_EXT};
    VkPhysicalDevicePushDescriptorPropertiesKHR push_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR, .pNext = &mesh_properties};
    VkPhysicalDeviceFloatControlsProperties float_controls = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES, .pNext = &push_properties};
    VkPhysicalDeviceSubgroupSizeControlProperties subgroup = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES, .pNext = &float_controls};
    VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &subgroup};
    VkPhysicalDeviceProperties2 properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(physical, &properties);
    const VkPhysicalDeviceLimits *limits = &properties.properties.limits;
    if (properties.properties.apiVersion < VK_API_VERSION_1_3) return 1;
    if (subgroup.minSubgroupSize > 32 || subgroup.maxSubgroupSize < 32 || !(subgroup.requiredSubgroupSizeStages & MESH_STAGE)) return 1;
    if (limits->maxPerStageDescriptorUniformBuffers < 9 || limits->maxPerStageDescriptorStorageBuffers < 7 ||
        limits->maxPerStageDescriptorSamplers < 10 || limits->maxPerStageDescriptorSampledImages < 10 ||
        limits->maxDescriptorSetUniformBuffers < 14 || limits->maxDescriptorSetStorageBuffers < 9 ||
        limits->maxDescriptorSetSamplers < 10 || limits->maxDescriptorSetSampledImages < 10 ||
        limits->maxPerStageResources < 21 || limits->maxBoundDescriptorSets < 1 || limits->maxPushConstantsSize < PUSH_BYTES) return 1;
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    if (!extensions && extension_count) return 1;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int executable_extension = 0, robustness_extension = 0, mesh_extension = 0;
    for (unsigned i = 0; i < extension_count; ++i) {
        if (!strcmp(extensions[i].extensionName, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME)) executable_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_EXT_ROBUSTNESS_2_EXTENSION_NAME)) robustness_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_EXT_MESH_SHADER_EXTENSION_NAME)) mesh_extension = 1;
    }
    free(extensions);
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceRobustness2FeaturesEXT supported_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &executable : NULL};
    VkPhysicalDeviceMeshShaderFeaturesEXT supported_mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT, .pNext = &supported_robustness};
    VkPhysicalDeviceVulkan13Features supported13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &supported_mesh};
    VkPhysicalDeviceVulkan12Features supported12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &supported13};
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported12};
    vkGetPhysicalDeviceFeatures2(physical, &supported);
    if (!mesh_extension || !supported_mesh.meshShader || !supported.features.robustBufferAccess || !robustness_extension ||
        !supported_robustness.robustBufferAccess2 || !supported_robustness.nullDescriptor ||
        !supported.features.shaderInt16 || !supported.features.shaderInt64 || !supported.features.fragmentStoresAndAtomics ||
        !supported.features.shaderStorageBufferArrayDynamicIndexing || !supported.features.shaderImageGatherExtended ||
        !supported12.uniformBufferStandardLayout || !supported12.bufferDeviceAddress || !supported12.shaderFloat16 || !supported12.shaderInt8 ||
        !float_controls.shaderSignedZeroInfNanPreserveFloat32 || !float_controls.shaderSignedZeroInfNanPreserveFloat16 ||
        !supported13.dynamicRendering || !supported13.subgroupSizeControl) {
        fprintf(stderr, "Required graphics diagnostic feature unavailable\n");
        return 1;
    }
    VkFormatProperties color_properties, depth_properties;
    vkGetPhysicalDeviceFormatProperties(physical, (VkFormat)color, &color_properties);
    if (!(color_properties.optimalTilingFeatures & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT)) return 1;
    if (depth) {
        vkGetPhysicalDeviceFormatProperties(physical, (VkFormat)depth, &depth_properties);
        if (!(depth_properties.optimalTilingFeatures & VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT)) return 1;
    }
    if (!(limits->framebufferColorSampleCounts & samples) || (depth && !(limits->framebufferDepthSampleCounts & samples))) return 1;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR,
        .pipelineExecutableInfo = executable.pipelineExecutableInfo};
    VkPhysicalDeviceRobustness2FeaturesEXT enabled_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &enabled_executable : NULL, .robustBufferAccess2 = VK_TRUE, .nullDescriptor = VK_TRUE};
    VkPhysicalDeviceMeshShaderFeaturesEXT enabled_mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT,
        .pNext = &enabled_robustness, .meshShader = VK_TRUE, .taskShader = VK_FALSE};
    VkPhysicalDeviceVulkan13Features enabled13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &enabled_mesh, .subgroupSizeControl = VK_TRUE, .dynamicRendering = VK_TRUE};
    VkPhysicalDeviceVulkan12Features enabled12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &enabled13, .uniformBufferStandardLayout = VK_TRUE, .bufferDeviceAddress = VK_TRUE,
        .shaderFloat16 = VK_TRUE, .shaderInt8 = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled12,
        .features = {.robustBufferAccess = VK_TRUE, .shaderInt16 = VK_TRUE, .shaderInt64 = VK_TRUE,
            .shaderStorageBufferArrayDynamicIndexing = VK_TRUE, .shaderImageGatherExtended = VK_TRUE, .fragmentStoresAndAtomics = VK_TRUE}};
    /* Declare one graphics queue family on the device. Do not retrieve a queue. */
    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    VkQueueFamilyProperties *queues = calloc(queue_count, sizeof(*queues));
    if (!queues) return 1;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
    uint32_t queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < queue_count; ++i)
        if (queues[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { queue_family = i; break; }
    free(queues);
    if (queue_family == UINT32_MAX) return 1;
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family, .queueCount = 1, .pQueuePriorities = &priority};
    const char *extension_names[3] = {VK_EXT_ROBUSTNESS_2_EXTENSION_NAME, VK_EXT_MESH_SHADER_EXTENSION_NAME};
    uint32_t enabled_extension_count = 2;
    if (executable_extension) extension_names[enabled_extension_count++] = VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME;
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = enabled_extension_count, .ppEnabledExtensionNames = extension_names};
    VkDevice device;
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkDescriptorSetLayoutCreateInfo set_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = count, .pBindings = bindings};
    VkDescriptorSetLayout set_layout;
    CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &set_layout));
    VkPushConstantRange push = {.stageFlags = MESH_STAGE | FRAGMENT_STAGE, .size = push_size};
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &set_layout, .pushConstantRangeCount = 1, .pPushConstantRanges = &push};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModule modules[2];
    VkPipelineShaderStageCreateInfo stages[2];
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo required = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO, .requiredSubgroupSize = 32};
    VkShaderStageFlagBits stage_types[2] = {VK_SHADER_STAGE_MESH_BIT_EXT, VK_SHADER_STAGE_FRAGMENT_BIT};
    for (unsigned i = 0; i < 2; ++i) {
        VkShaderModuleCreateInfo info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = code_sizes[i], .pCode = codes[i]};
        CHECK(vkCreateShaderModule(device, &info, NULL, &modules[i]));
        stages[i] = (VkPipelineShaderStageCreateInfo){.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .pNext = i == 0 ? &required : NULL, .stage = stage_types[i], .module = modules[i], .pName = "main"};
    }
    VkViewport viewport = {0, 0, (float)width, (float)height, 0, 1};
    VkRect2D scissor = {{0, 0}, {width, height}};
    VkPipelineViewportStateCreateInfo vp = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
        .viewportCount = 1, .pViewports = &viewport, .scissorCount = 1, .pScissors = &scissor};
    VkPipelineRasterizationStateCreateInfo raster = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
        .polygonMode = VK_POLYGON_MODE_FILL, .cullMode = cull, .frontFace = (VkFrontFace)front, .lineWidth = 1};
    VkPipelineMultisampleStateCreateInfo ms = {.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
        .rasterizationSamples = (VkSampleCountFlagBits)samples, .alphaToCoverageEnable = a2c};
    VkPipelineDepthStencilStateCreateInfo ds = {.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO,
        .depthTestEnable = depth_test, .depthWriteEnable = depth_write, .depthCompareOp = (VkCompareOp)compare};
    VkPipelineColorBlendAttachmentState attachment = {.colorWriteMask = write_mask};
    VkPipelineColorBlendStateCreateInfo blend = {.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
        .attachmentCount = 1, .pAttachments = &attachment};
    VkFormat color_format = (VkFormat)color;
    VkPipelineRenderingCreateInfo rendering = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,
        .colorAttachmentCount = 1, .pColorAttachmentFormats = &color_format, .depthAttachmentFormat = (VkFormat)depth};
    VkGraphicsPipelineCreateInfo pipeline_info = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, .pNext = &rendering,
        .flags = executable.pipelineExecutableInfo ? VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR : 0,
        .stageCount = 2, .pStages = stages, .pViewportState = &vp, .pRasterizationState = &raster,
        .pMultisampleState = &ms, .pDepthStencilState = &ds, .pColorBlendState = &blend, .layout = layout};
    VkPipeline pipeline;
    double start = seconds();
    CHECK(vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    double elapsed = seconds() - start;
    printf("{\"schema\":1,\"compile_only\":true,\"dispatch_executed\":false,\"render_executed\":false,\"queue_retrieved\":false");
    printf(",\"resources_allocated\":false,\"descriptor_mode\":\"sets\",\"descriptor_binding_count\":%u,\"descriptor_element_count\":%u", count, total_descriptors);
    printf(",\"push_constant_range_bytes\":%u,\"push_layout_rejected\":true,\"device\":", push_size);
    json_string(properties.properties.deviceName);
    printf(",\"driver\":"); json_string(driver.driverName);
    printf(",\"driver_info\":"); json_string(driver.driverInfo);
    printf(",\"vendor_id\":%u,\"device_id\":%u,\"api_version\":%u", properties.properties.vendorID, properties.properties.deviceID, properties.properties.apiVersion);
    printf(",\"pipeline_optimization_disabled\":false,\"capture_statistics_enabled\":%s", executable.pipelineExecutableInfo ? "true" : "false");
    printf(",\"null_descriptor_enabled\":true,\"robust_buffer_access_enabled\":true,\"robust_buffer_access2_enabled\":true");
    printf(",\"max_push_descriptors\":%u,\"full_subgroups\":false", push_properties.maxPushDescriptors);
    printf(",\"limit_thresholds\":{\"maxPerStageDescriptorUniformBuffers\":9,\"maxPerStageDescriptorStorageBuffers\":7");
    printf(",\"maxPerStageDescriptorSamplers\":10,\"maxPerStageDescriptorSampledImages\":10,\"maxDescriptorSetUniformBuffers\":14");
    printf(",\"maxDescriptorSetStorageBuffers\":9,\"maxDescriptorSetSamplers\":10,\"maxDescriptorSetSampledImages\":10");
    printf(",\"maxPerStageResources\":21,\"maxBoundDescriptorSets\":1,\"maxPushConstantsSize\":88}");
    printf(",\"stages\":[{\"stage_flags\":128,\"required_subgroup_size\":32,\"require_full_subgroups\":false},{\"stage_flags\":16,\"required_subgroup_size\":0,\"require_full_subgroups\":false}]");
    printf(",\"pipeline_create_seconds\":%.9f,\"executables\":[", elapsed);
    if (executable.pipelineExecutableInfo) {
        PFN_vkGetPipelineExecutablePropertiesKHR get_properties = (PFN_vkGetPipelineExecutablePropertiesKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutablePropertiesKHR");
        PFN_vkGetPipelineExecutableStatisticsKHR get_statistics = (PFN_vkGetPipelineExecutableStatisticsKHR)vkGetDeviceProcAddr(device, "vkGetPipelineExecutableStatisticsKHR");
        if (!get_properties || !get_statistics) return 1;
        VkPipelineInfoKHR info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR, .pipeline = pipeline};
        uint32_t executable_count = 0;
        CHECK(get_properties(device, &info, &executable_count, NULL));
        if (executable_count > 128) return 1;
        VkPipelineExecutablePropertiesKHR *props = calloc(executable_count, sizeof(*props));
        if (!props && executable_count) return 1;
        for (unsigned i = 0; i < executable_count; ++i) props[i].sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR;
        CHECK(get_properties(device, &info, &executable_count, props));
        for (unsigned i = 0; i < executable_count; ++i) {
            if (i) putchar(',');
            printf("{\"index\":%u,\"stage_flags\":%u,\"name\":", i, props[i].stages);
            json_string(props[i].name);
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
            free(stats);
            printf("]}");
        }
        free(props);
    }
    printf("]}\n");
    vkDestroyPipeline(device, pipeline, NULL);
    for (unsigned i = 0; i < 2; ++i) { vkDestroyShaderModule(device, modules[i], NULL); free(codes[i]); }
    vkDestroyPipelineLayout(device, layout, NULL);
    vkDestroyDescriptorSetLayout(device, set_layout, NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    return 0;
}
