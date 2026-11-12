/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Bounded synthetic mesh lane-mapping execution; no game shaders or assets. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OUTPUT_BYTES (32u * 7u * 4u)
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

static int make_buffer(VkPhysicalDevice physical, VkDevice device, VkDeviceSize size, Buffer *out) {
    VkBufferCreateInfo info = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = size,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
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

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "Usage: mesh-execution-probe synthetic.spv output.bin device-index\n"); return 2;
    }
    char *end;
    unsigned long device_index = strtoul(argv[3], &end, 10);
    if (!*argv[3] || *end || device_index > 255) return 2;
    size_t code_size = 0;
    uint32_t *code = read_file(argv[1], 20, 65536, &code_size);
    if (!code || code[0] != 0x07230203 || code[1] != 0x00010600) return 2;
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina bounded synthetic mesh mapping", .apiVersion = VK_API_VERSION_1_3};
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
    VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &subgroup};
    VkPhysicalDeviceProperties2 properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(physical, &properties);
    if (properties.properties.apiVersion < VK_API_VERSION_1_3 || subgroup.minSubgroupSize > 32 ||
        subgroup.maxSubgroupSize < 32 || !(subgroup.requiredSubgroupSizeStages & VK_SHADER_STAGE_MESH_BIT_EXT) ||
        !(operations.supportedStages & VK_SHADER_STAGE_MESH_BIT_EXT) ||
        !(operations.supportedOperations & VK_SUBGROUP_FEATURE_BASIC_BIT) ||
        mesh_properties.maxMeshWorkGroupInvocations < 32 || mesh_properties.maxMeshWorkGroupSize[0] < 32 ||
        mesh_properties.maxMeshOutputVertices < 3 || mesh_properties.maxMeshOutputPrimitives < 1) return 1;
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    if (extension_count > 4096) return 1;
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    if (!extensions && extension_count) return 1;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int has_executable = 0, has_mesh = 0;
    for (uint32_t i = 0; i < extension_count; ++i) {
        if (!strcmp(extensions[i].extensionName, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME)) has_executable = 1;
        if (!strcmp(extensions[i].extensionName, VK_EXT_MESH_SHADER_EXTENSION_NAME)) has_mesh = 1;
    }
    free(extensions);
    if (!has_executable || !has_mesh) return 1;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceMeshShaderFeaturesEXT mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT, .pNext = &executable};
    VkPhysicalDeviceVulkan13Features features13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, .pNext = &mesh};
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &features13};
    vkGetPhysicalDeviceFeatures2(physical, &supported);
    if (!features13.subgroupSizeControl || !features13.dynamicRendering || !mesh.meshShader ||
        !executable.pipelineExecutableInfo || !supported.features.robustBufferAccess ||
        !supported.features.vertexPipelineStoresAndAtomics) return 1;
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
    VkPhysicalDeviceMeshShaderFeaturesEXT enabled_mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT,
        .pNext = &enabled_executable, .meshShader = VK_TRUE};
    VkPhysicalDeviceVulkan13Features enabled13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &enabled_mesh, .subgroupSizeControl = VK_TRUE, .dynamicRendering = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &enabled13, .features = {.robustBufferAccess = VK_TRUE, .vertexPipelineStoresAndAtomics = VK_TRUE}};
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    const char *extension_names[2] = {VK_EXT_MESH_SHADER_EXTENSION_NAME, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = 2, .ppEnabledExtensionNames = extension_names};
    VkDevice device;
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));
    Buffer buffer = {0};
    if (make_buffer(physical, device, OUTPUT_BYTES, &buffer)) return 1;
    memset(buffer.mapped, 0xcd, OUTPUT_BYTES);
    VkDescriptorSetLayoutBinding binding = {.binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_MESH_BIT_EXT};
    VkDescriptorSetLayoutCreateInfo set_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 1, .pBindings = &binding};
    VkDescriptorSetLayout set_layout;
    CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &set_layout));
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &set_layout};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModuleCreateInfo module_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = code_size, .pCode = code};
    VkShaderModule module;
    CHECK(vkCreateShaderModule(device, &module_info, NULL, &module));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo required = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO, .requiredSubgroupSize = 32};
    /* SPIR-V1.6 and X=32 divisible by required width32 supply full-subgroup
     * behavior here; the optional REQUIRE_FULL_SUBGROUPS flag stays unset. */
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
    if (prop.subgroupSize != 32 || prop.stages != VK_SHADER_STAGE_MESH_BIT_EXT) {
        fprintf(stderr, "Executable stage/width differs from mesh32\n"); return 1;
    }
    VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1};
    VkDescriptorPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size};
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(device, &pool_info, NULL, &pool));
    VkDescriptorSetAllocateInfo set_alloc = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = pool, .descriptorSetCount = 1, .pSetLayouts = &set_layout};
    VkDescriptorSet set;
    CHECK(vkAllocateDescriptorSets(device, &set_alloc, &set));
    VkDescriptorBufferInfo buffer_info = {.buffer = buffer.buffer, .range = OUTPUT_BYTES};
    VkWriteDescriptorSet write = {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = set, .dstBinding = 0,
        .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &buffer_info};
    vkUpdateDescriptorSets(device, 1, &write, 0, NULL);
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
    vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_GRAPHICS, layout, 0, 1, &set, 0, NULL);
    draw(command, 1, 1, 1);
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
    FILE *output = fopen(argv[2], "wb");
    if (!output) return 1;
    int write_failed = fwrite(buffer.mapped, 1, OUTPUT_BYTES, output) != OUTPUT_BYTES;
    if (fclose(output) || write_failed) return 1;
    printf("{\"schema\":1,\"synthetic_only\":true,\"mesh_draw_executed\":true,\"device\":");
    json_string(properties.properties.deviceName);
    printf(",\"driver\":"); json_string(driver.driverName);
    printf(",\"driver_info\":"); json_string(driver.driverInfo);
    printf(",\"vendor_id\":%u,\"device_id\":%u,\"requested_width\":32,\"actual_width\":%u,"
        "\"width_source\":\"pipeline_executable_properties\",\"executable_stage\":%u,"
        "\"workgroup\":[32,1,1],\"draw_mesh_tasks\":[1,1,1],\"emitted_vertices\":0,\"emitted_primitives\":0,"
        "\"fence_timeout_ns\":2000000000,\"features\":{\"meshShader\":true,\"dynamicRendering\":true,"
        "\"robustBufferAccess\":true,\"vertexPipelineStoresAndAtomics\":true,\"subgroupSizeControl\":true,"
        "\"pipelineExecutableInfo\":true},\"rasterizer_discard\":true,\"attachments\":0,"
        "\"pipeline_optimization_disabled\":false}\n",
        properties.properties.vendorID, properties.properties.deviceID, prop.subgroupSize, prop.stages);
    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, command_pool, NULL);
    vkDestroyDescriptorPool(device, pool, NULL);
    vkDestroyPipeline(device, pipeline, NULL);
    vkDestroyShaderModule(device, module, NULL);
    vkDestroyPipelineLayout(device, layout, NULL);
    vkDestroyDescriptorSetLayout(device, set_layout, NULL);
    vkUnmapMemory(device, buffer.memory);
    vkDestroyBuffer(device, buffer.buffer, NULL);
    vkFreeMemory(device, buffer.memory, NULL);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    free(code);
    return 0;
}
