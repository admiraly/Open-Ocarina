/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Compile-only mesh graphics-pipeline inspection: no command pools, queues or dispatch. */
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
    if (argc < 5 || argc > 6) { fprintf(stderr,"Usage: graphics-probe layout.txt device-index stage0.spv stage1.spv [stage2.spv]\n"); return 2; }
    char *end = NULL;
    unsigned long requested_device = strtoul(argv[2], &end, 10);
    if (!*argv[2] || *end || requested_device > 255) return 2;
    unsigned push_size,count,stage_count,push_mode,color,depth,samples,width,height,depth_test,depth_write,compare,cull,front,a2c,write_mask;
    FILE *file=fopen(argv[1],"r");
    if (!file || fscanf(file,"%u %u %u %u %u %u %u %u %u %u %u %u %u %u %u %u",
        &push_size,&count,&stage_count,&push_mode,&color,&depth,&samples,&width,&height,&depth_test,&depth_write,&compare,&cull,&front,&a2c,&write_mask)!=16 ||
        push_size>128 || push_size%4 || count>256 || stage_count!=(unsigned)argc-3 || push_mode>1 || !color ||
        (samples!=1 && samples!=2 && samples!=4 && samples!=8) || !width || !height || width>16384 || height>16384 ||
        depth_test>1 || depth_write>1 || compare>7 || cull>3 || front>1 || a2c>1 || write_mask>15 || (!depth&&(depth_test||depth_write))) return 2;
    VkDescriptorSetLayoutBinding bindings[256]={{0}};
    uint32_t total_descriptors=0,stage_mask=0;
    for (unsigned i=0;i<count;++i) {
        unsigned set,binding,type,n,flags;
        if (fscanf(file,"%u %u %u %u %u",&set,&binding,&type,&n,&flags)!=5 || set || binding>1023 || type>7 || !n || n>65536 || !flags || (flags&~208u)) return 2;
        for (unsigned j=0;j<i;++j) if(bindings[j].binding==binding) return 2;
        bindings[i]=(VkDescriptorSetLayoutBinding){.binding=binding,.descriptorType=(VkDescriptorType)type,.descriptorCount=n,.stageFlags=flags};
        total_descriptors+=n;
    }
    VkShaderStageFlagBits stage_types[3]; uint32_t required_widths[3];
    for (unsigned i=0;i<stage_count;++i) {
        unsigned stage,required;
        if(fscanf(file,"%u %u",&stage,&required)!=2 || (required!=0 && required!=8 && required!=16 && required!=32)) return 2;
        unsigned wanted= i==stage_count-1 ? 16 : i==0&&stage_count==3 ? 64 : 128;
        if(stage!=wanted)return 2;
        stage_types[i]=(VkShaderStageFlagBits)stage;required_widths[i]=required;stage_mask|=stage;
    }
    char tail;if(fscanf(file," %c",&tail)!=EOF)return 2;fclose(file);
    uint32_t *codes[3]={0};size_t code_sizes[3]={0};
    for(unsigned i=0;i<stage_count;++i) {
        file=fopen(argv[i+3],"rb");if(!file || fseek(file,0,SEEK_END))return 2;
        long n=ftell(file);if(n<20||n>16*1024*1024||n%4)return 2;rewind(file);
        codes[i]=malloc((size_t)n);code_sizes[i]=(size_t)n;
        if(!codes[i]||fread(codes[i],1,(size_t)n,file)!=(size_t)n)return 2;
        fclose(file);
        if(codes[i][0]!=0x07230203)return 2;
    }
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina graphics compile-only probe", .apiVersion = VK_API_VERSION_1_3};
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
    VkPhysicalDeviceMeshShaderPropertiesEXT mesh_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_PROPERTIES_EXT};
    VkPhysicalDevicePushDescriptorPropertiesKHR push_properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR, .pNext = &mesh_properties};
    VkPhysicalDeviceFloatControlsProperties float_controls = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES, .pNext = &push_properties};
    VkPhysicalDeviceSubgroupSizeControlProperties subgroup = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES, .pNext = &float_controls};
    VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &subgroup};
    VkPhysicalDeviceProperties2 properties = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(physical, &properties);
    if (properties.properties.apiVersion < VK_API_VERSION_1_3) return 1;
    for (unsigned i=0;i<stage_count;++i) if(required_widths[i] &&
        (subgroup.minSubgroupSize>required_widths[i] || subgroup.maxSubgroupSize<required_widths[i] ||
         !(subgroup.requiredSubgroupSizeStages&stage_types[i]))) {
        fprintf(stderr,"Requested per-stage subgroup policy unsupported\n");return 1;
    }
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    if (!extensions && extension_count) return 1;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int executable_extension = 0, robustness_extension = 0, push_extension = 0, mesh_extension = 0;
    for (unsigned i = 0; i < extension_count; ++i)
        if (!strcmp(extensions[i].extensionName, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME)) executable_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_EXT_ROBUSTNESS_2_EXTENSION_NAME)) robustness_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME)) push_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_EXT_MESH_SHADER_EXTENSION_NAME)) mesh_extension = 1;
    free(extensions);
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceRobustness2FeaturesEXT supported_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &executable : NULL};
    VkPhysicalDeviceMeshShaderFeaturesEXT supported_mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT, .pNext = &supported_robustness};
    VkPhysicalDeviceVulkan13Features supported13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &supported_mesh};
    VkPhysicalDeviceVulkan12Features supported12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &supported13};
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &supported12};
    vkGetPhysicalDeviceFeatures2(physical, &supported);
    if (!mesh_extension || !supported_mesh.meshShader || (stage_count==3&&!supported_mesh.taskShader) ||
        (push_mode&&(!push_extension || total_descriptors>push_properties.maxPushDescriptors)) ||
        !supported.features.robustBufferAccess || !robustness_extension || !supported_robustness.robustBufferAccess2 ||
        !supported.features.shaderInt16 || !supported.features.shaderInt64 ||
        !supported.features.fragmentStoresAndAtomics ||
        !supported.features.shaderStorageBufferArrayDynamicIndexing || !supported.features.shaderImageGatherExtended ||
        !supported12.uniformBufferStandardLayout || !supported12.bufferDeviceAddress || !supported12.shaderFloat16 || !supported12.shaderInt8 ||
        !float_controls.shaderSignedZeroInfNanPreserveFloat32 || !float_controls.shaderSignedZeroInfNanPreserveFloat16 ||
        !supported13.dynamicRendering || !supported13.subgroupSizeControl) {
        fprintf(stderr,"Required graphics diagnostic feature unavailable\n");return 1;
    }
    VkFormatProperties color_properties,depth_properties;
    vkGetPhysicalDeviceFormatProperties(physical,(VkFormat)color,&color_properties);
    if(!(color_properties.optimalTilingFeatures&VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT))return 1;
    if(depth) { vkGetPhysicalDeviceFormatProperties(physical,(VkFormat)depth,&depth_properties);
        if(!(depth_properties.optimalTilingFeatures&VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT))return 1; }
    if(!(properties.properties.limits.framebufferColorSampleCounts&samples) ||
       (depth&&!(properties.properties.limits.framebufferDepthSampleCounts&samples)))return 1;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR,
        .pipelineExecutableInfo = executable.pipelineExecutableInfo};
    VkPhysicalDeviceRobustness2FeaturesEXT enabled_robustness = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &enabled_executable : NULL, .robustBufferAccess2 = VK_TRUE,
        .nullDescriptor = supported_robustness.nullDescriptor};
    VkPhysicalDeviceMeshShaderFeaturesEXT enabled_mesh = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MESH_SHADER_FEATURES_EXT,
        .pNext = &enabled_robustness, .meshShader = VK_TRUE, .taskShader = stage_count==3};
    VkPhysicalDeviceVulkan13Features enabled13 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &enabled_mesh, .subgroupSizeControl = VK_TRUE, .dynamicRendering = VK_TRUE};
    VkPhysicalDeviceVulkan12Features enabled12 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &enabled13, .uniformBufferStandardLayout = VK_TRUE, .bufferDeviceAddress = VK_TRUE,
        .shaderFloat16 = VK_TRUE, .shaderInt8 = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &enabled12,
        .features = {.robustBufferAccess = VK_TRUE, .shaderInt16 = VK_TRUE, .shaderInt64 = VK_TRUE,
            .shaderStorageBufferArrayDynamicIndexing = VK_TRUE, .shaderImageGatherExtended = VK_TRUE, .fragmentStoresAndAtomics = VK_TRUE}};
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
    const char *extension_names[4] = {VK_EXT_ROBUSTNESS_2_EXTENSION_NAME, VK_EXT_MESH_SHADER_EXTENSION_NAME};
    uint32_t enabled_extension_count=2;
    if(push_mode)extension_names[enabled_extension_count++]=VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME;
    if(executable_extension)extension_names[enabled_extension_count++]=VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME;
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
        .enabledExtensionCount = enabled_extension_count,
        .ppEnabledExtensionNames = extension_names};
    VkDevice device;
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));

    VkDescriptorSetLayoutCreateInfo set_info = {.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .flags=push_mode?VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR:0,.bindingCount=count,.pBindings=bindings};
    VkDescriptorSetLayout set_layout;CHECK(vkCreateDescriptorSetLayout(device,&set_info,NULL,&set_layout));
    VkPushConstantRange push={.stageFlags=stage_mask,.size=push_size};
    VkPipelineLayoutCreateInfo layout_info={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount=1,.pSetLayouts=&set_layout,.pushConstantRangeCount=push_size?1:0,.pPushConstantRanges=push_size?&push:NULL};
    VkPipelineLayout layout;CHECK(vkCreatePipelineLayout(device,&layout_info,NULL,&layout));
    VkShaderModule modules[3];VkPipelineShaderStageCreateInfo stages[3];
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo required[3];
    for(unsigned i=0;i<stage_count;++i) {
        VkShaderModuleCreateInfo info={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=code_sizes[i],.pCode=codes[i]};
        CHECK(vkCreateShaderModule(device,&info,NULL,&modules[i]));
        required[i]=(VkPipelineShaderStageRequiredSubgroupSizeCreateInfo){.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO,.requiredSubgroupSize=required_widths[i]};
        stages[i]=(VkPipelineShaderStageCreateInfo){.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .pNext=required_widths[i]?&required[i]:NULL,.stage=stage_types[i],.module=modules[i],.pName="main"};
    }
    VkViewport viewport={0,0,(float)width,(float)height,0,1};VkRect2D scissor={{0,0},{width,height}};
    VkPipelineViewportStateCreateInfo vp={.sType=VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,.viewportCount=1,.pViewports=&viewport,.scissorCount=1,.pScissors=&scissor};
    VkPipelineRasterizationStateCreateInfo raster={.sType=VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,.polygonMode=VK_POLYGON_MODE_FILL,.cullMode=cull,.frontFace=(VkFrontFace)front,.lineWidth=1};
    VkPipelineMultisampleStateCreateInfo ms={.sType=VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,.rasterizationSamples=(VkSampleCountFlagBits)samples,.alphaToCoverageEnable=a2c};
    VkPipelineDepthStencilStateCreateInfo ds={.sType=VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO,.depthTestEnable=depth_test,.depthWriteEnable=depth_write,.depthCompareOp=(VkCompareOp)compare};
    VkPipelineColorBlendAttachmentState attachment={.colorWriteMask=write_mask};
    VkPipelineColorBlendStateCreateInfo blend={.sType=VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,.attachmentCount=1,.pAttachments=&attachment};
    VkFormat color_format=(VkFormat)color;
    VkPipelineRenderingCreateInfo rendering={.sType=VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,.colorAttachmentCount=1,.pColorAttachmentFormats=&color_format,.depthAttachmentFormat=(VkFormat)depth};
    VkGraphicsPipelineCreateInfo pipeline_info={.sType=VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,.pNext=&rendering,
        .flags=executable.pipelineExecutableInfo?VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR:0,
        .stageCount=stage_count,.pStages=stages,.pViewportState=&vp,.pRasterizationState=&raster,
        .pMultisampleState=&ms,.pDepthStencilState=&ds,.pColorBlendState=&blend,.layout=layout};
    VkPipeline pipeline;double start=seconds();
    CHECK(vkCreateGraphicsPipelines(device,VK_NULL_HANDLE,1,&pipeline_info,NULL,&pipeline));
    double elapsed=seconds()-start;
    printf("{\"schema\":1,\"compile_only\":true,\"dispatch_executed\":false,\"render_executed\":false,\"device\":");
    json_string(properties.properties.deviceName);printf(",\"driver\":");json_string(driver.driverName);
    printf(",\"driver_info\":");json_string(driver.driverInfo);
    printf(",\"vendor_id\":%u,\"device_id\":%u,\"api_version\":%u",properties.properties.vendorID,properties.properties.deviceID,properties.properties.apiVersion);
    printf(",\"pipeline_optimization_disabled\":false,\"capture_statistics_enabled\":%s,\"descriptor_mode\":\"%s\",\"push_constant_range_bytes\":%u",executable.pipelineExecutableInfo?"true":"false",push_mode?"push":"sets",push_size);
    printf(",\"null_descriptor_enabled\":%s,\"max_push_descriptors\":%u",enabled_robustness.nullDescriptor?"true":"false",push_properties.maxPushDescriptors);
    printf(",\"mesh_limits\":{\"maxTaskWorkGroupInvocations\":%u,\"maxMeshWorkGroupInvocations\":%u,\"maxMeshOutputVertices\":%u,\"maxMeshOutputPrimitives\":%u,\"maxTaskPayloadSize\":%u,\"maxMeshSharedMemorySize\":%u,\"maxMeshPayloadAndSharedMemorySize\":%u}",mesh_properties.maxTaskWorkGroupInvocations,mesh_properties.maxMeshWorkGroupInvocations,mesh_properties.maxMeshOutputVertices,mesh_properties.maxMeshOutputPrimitives,mesh_properties.maxTaskPayloadSize,mesh_properties.maxMeshSharedMemorySize,mesh_properties.maxMeshPayloadAndSharedMemorySize);
    printf(",\"required_subgroup_stage_mask\":%u,\"stages\":[",subgroup.requiredSubgroupSizeStages);
    for(unsigned i=0;i<stage_count;++i) {if(i)putchar(',');printf("{\"stage_flags\":%u,\"required_subgroup_size\":%u,\"require_full_subgroups\":false}",stage_types[i],required_widths[i]);}
    printf("],\"pipeline_create_seconds\":%.9f,\"executables\":[",elapsed);
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
            printf("{\"index\":%u,\"stage_flags\":%u,\"name\":",i,props[i].stages); json_string(props[i].name);
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
    vkDestroyPipeline(device,pipeline,NULL);
    for(unsigned i=0;i<stage_count;++i){vkDestroyShaderModule(device,modules[i],NULL);free(codes[i]);}
    vkDestroyPipelineLayout(device,layout,NULL);vkDestroyDescriptorSetLayout(device,set_layout,NULL);
    vkDestroyDevice(device,NULL);vkDestroyInstance(instance,NULL);return 0;
}
