/* SPDX-License-Identifier: GPL-3.0-or-later */
/* One explicitly authorized owned physical-memory compute dispatch. */
#define _POSIX_C_SOURCE 200809L
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define CHECK(call)                                                                                \
    do {                                                                                           \
        VkResult status = (call);                                                                  \
        if (status != VK_SUCCESS) {                                                                \
            fprintf(stderr, "%s failed: %d\n", #call, status);                                     \
            return 3;                                                                              \
        }                                                                                          \
    } while (0)
#define REQUIRE(test)                                                                              \
    do {                                                                                           \
        if (!(test)) {                                                                             \
            fprintf(stderr, "Invalid or unsupported replay at line %d\n", __LINE__);               \
            return 2;                                                                              \
        }                                                                                          \
    } while (0)
#define MAX_ALLOC 128
#define MAX_IMAGE 64
#define MAX_BIND 256
#define MAX_BYTES (UINT64_C(4) * 1024 * 1024 * 1024)

static FILE *control;
static int bad;
static uint32_t u32(void) {
    unsigned char b[4] = {0};
    if (fread(b, 1, 4, control) != 4)
        bad = 1;
    return (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}
static uint64_t u64(void) {
    uint64_t a = u32(), b = u32();
    return a | (b << 32);
}
static char *string(void) {
    uint32_t n = u32();
    if (!n || n > 4095) {
        bad = 1;
        return NULL;
    }
    char *s = calloc(n + 1, 1);
    if (!s || fread(s, 1, n, control) != n || memchr(s, 0, n)) {
        free(s);
        bad = 1;
        return NULL;
    }
    return s;
}
static void *load(const char *name, uint64_t size) {
    FILE *f = fopen(name, "rb");
    if (!f || size > SIZE_MAX) {
        if (f)
            fclose(f);
        return NULL;
    }
    void *p = malloc(size ? size : 1);
    if (!p || fread(p, 1, (size_t)size, f) != size || fgetc(f) != EOF) {
        free(p);
        p = NULL;
    }
    fclose(f);
    return p;
}
static float float_word(uint32_t word) {
    float value;
    memcpy(&value, &word, 4);
    return value;
}

typedef struct Buffer {
    uint64_t size;
    VkBuffer buffer;
    VkDeviceMemory memory;
    void *mapped;
    VkDeviceAddress address;
} Buffer;
typedef struct Allocation {
    char *id, *initial, *expected;
    Buffer gpu;
} Allocation;
typedef struct Mapping {
    uint32_t index, allocation;
    uint64_t offset;
} Mapping;
typedef struct Subresource {
    uint32_t mip, layer;
    uint64_t size, offset;
    char *path;
} Subresource;
typedef struct Image {
    uint32_t format, type, extent[3], mips, layers, count;
    Subresource *sub;
    VkImage image;
    VkDeviceMemory memory;
    Buffer upload;
} Image;
typedef struct View {
    uint32_t image, type, format, components[4], base_mip, mips, base_layer, layers;
    VkImageView view;
} View;
typedef struct Sampler {
    uint32_t words[16];
    VkSampler sampler;
} Sampler;
typedef struct Binding {
    uint32_t binding, element, type, null, allocation, view, sampler;
    uint64_t offset, size;
} Binding;

static int make_buffer(VkPhysicalDevice physical, VkDevice device, uint64_t size,
                       VkBufferUsageFlags usage, int address, Buffer *out) {
    out->size = size;
    VkBufferCreateInfo info = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
                               .size = size,
                               .usage = usage,
                               .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(device, &info, NULL, &out->buffer));
    VkMemoryRequirements needs;
    vkGetBufferMemoryRequirements(device, out->buffer, &needs);
    VkPhysicalDeviceMemoryProperties properties;
    vkGetPhysicalDeviceMemoryProperties(physical, &properties);
    uint32_t type = UINT32_MAX;
    VkMemoryPropertyFlags required =
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    for (unsigned pass = 0; pass < 2 && type == UINT32_MAX; pass++)
        for (uint32_t i = 0; i < properties.memoryTypeCount; i++)
            if ((needs.memoryTypeBits & (1u << i)) &&
                (properties.memoryTypes[i].propertyFlags & required) == required &&
                (pass ||
                 (properties.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_HOST_CACHED_BIT))) {
                type = i;
                break;
            }
    REQUIRE(type != UINT32_MAX);
    VkMemoryAllocateFlagsInfo flags = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO,
                                       .flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT};
    VkMemoryAllocateInfo allocation = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                                       .pNext = address ? &flags : NULL,
                                       .allocationSize = needs.size,
                                       .memoryTypeIndex = type};
    CHECK(vkAllocateMemory(device, &allocation, NULL, &out->memory));
    CHECK(vkBindBufferMemory(device, out->buffer, out->memory, 0));
    CHECK(vkMapMemory(device, out->memory, 0, size, 0, &out->mapped));
    if (address) {
        VkBufferDeviceAddressInfo query = {.sType = VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO,
                                           .buffer = out->buffer};
        out->address = vkGetBufferDeviceAddress(device, &query);
        REQUIRE(out->address != 0 && out->address % 16 == 0 && out->address <= UINT64_MAX - size);
    }
    return 0;
}
static void destroy_buffer(VkDevice device, Buffer *b) {
    if (b->mapped)
        vkUnmapMemory(device, b->memory);
    if (b->buffer)
        vkDestroyBuffer(device, b->buffer, NULL);
    if (b->memory)
        vkFreeMemory(device, b->memory, NULL);
}
static uint32_t pixel_bytes(uint32_t format) {
    switch (format) {
    case 9:
        return 1;
    case 16:
    case 70:
    case 76:
        return 2;
    case 37:
    case 43:
    case 83:
    case 98:
    case 100:
    case 122:
        return 4;
    case 97:
    case 103:
        return 8;
    case 109:
        return 16;
    default:
        return 0;
    }
}

static int create_sampled_inputs(VkPhysicalDevice physical, VkDevice device, uint32_t image_count,
                                 Image *images, uint32_t view_count, View *views,
                                 uint32_t sampler_count, Sampler *samplers,
                                 uint32_t descriptor_count, const Binding *descriptors,
                                 const VkPhysicalDeviceProperties *device_properties,
                                 VkBool32 sampler_anisotropy) {
    for (uint32_t i = 0; i < image_count; i++) {
        Image *im = &images[i];
        VkFormatProperties format_properties;
        vkGetPhysicalDeviceFormatProperties(physical, (VkFormat)im->format, &format_properties);
        REQUIRE((format_properties.optimalTilingFeatures & VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT) &&
                (format_properties.optimalTilingFeatures & VK_FORMAT_FEATURE_TRANSFER_DST_BIT));
        VkImageCreateFlags image_flags = 0;
        for (uint32_t j = 0; j < view_count; j++)
            if (views[j].image == i && (views[j].type == 3 || views[j].type == 6))
                image_flags |= VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT;
        VkImageCreateInfo info = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                                  .flags = image_flags,
                                  .imageType = (VkImageType)im->type,
                                  .format = (VkFormat)im->format,
                                  .extent = {im->extent[0], im->extent[1], im->extent[2]},
                                  .mipLevels = im->mips,
                                  .arrayLayers = im->layers,
                                  .samples = VK_SAMPLE_COUNT_1_BIT,
                                  .tiling = VK_IMAGE_TILING_OPTIMAL,
                                  .usage =
                                      VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
                                  .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
                                  .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
        CHECK(vkCreateImage(device, &info, NULL, &im->image));
        VkMemoryRequirements needs;
        vkGetImageMemoryRequirements(device, im->image, &needs);
        VkPhysicalDeviceMemoryProperties memory;
        vkGetPhysicalDeviceMemoryProperties(physical, &memory);
        uint32_t memory_type = UINT32_MAX;
        for (unsigned pass = 0; pass < 2 && memory_type == UINT32_MAX; pass++)
            for (uint32_t j = 0; j < memory.memoryTypeCount; j++)
                if ((needs.memoryTypeBits & (1u << j)) &&
                    (pass ||
                     (memory.memoryTypes[j].propertyFlags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT))) {
                    memory_type = j;
                    break;
                }
        REQUIRE(memory_type != UINT32_MAX);
        VkMemoryAllocateInfo allocation = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                                           .allocationSize = needs.size,
                                           .memoryTypeIndex = memory_type};
        CHECK(vkAllocateMemory(device, &allocation, NULL, &im->memory));
        CHECK(vkBindImageMemory(device, im->image, im->memory, 0));
        uint64_t upload_size = im->upload.size;
        if (make_buffer(physical, device, upload_size, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, 0,
                        &im->upload))
            return 3;
        memset(im->upload.mapped, 0, (size_t)upload_size);
        for (uint32_t j = 0; j < im->count; j++) {
            Subresource *sub = &im->sub[j];
            void *data = load(sub->path, sub->size);
            REQUIRE(data);
            memcpy((unsigned char *)im->upload.mapped + sub->offset, data, (size_t)sub->size);
            free(data);
        }
    }
    for (uint32_t i = 0; i < view_count; i++) {
        View *v = &views[i];
        VkImageViewCreateInfo info = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
                                      .image = images[v->image].image,
                                      .viewType = (VkImageViewType)v->type,
                                      .format = (VkFormat)v->format,
                                      .components = {(VkComponentSwizzle)v->components[0],
                                                     (VkComponentSwizzle)v->components[1],
                                                     (VkComponentSwizzle)v->components[2],
                                                     (VkComponentSwizzle)v->components[3]},
                                      .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, v->base_mip,
                                                           v->mips, v->base_layer, v->layers}};
        CHECK(vkCreateImageView(device, &info, NULL, &v->view));
    }
    for (uint32_t i = 0; i < sampler_count; i++) {
        uint32_t *w = samplers[i].words;
        REQUIRE(w[0] == 0 && w[1] <= 1 && w[2] <= 1 && w[3] <= 1 && w[4] <= 3 && w[5] <= 3 &&
                w[6] <= 3 && w[8] <= 1 && w[10] == 0 && w[11] <= 7 && w[14] <= 5 && w[15] == 0);
        REQUIRE(isfinite(float_word(w[7])) && isfinite(float_word(w[9])) &&
                isfinite(float_word(w[12])) && isfinite(float_word(w[13])));
        REQUIRE(fabsf(float_word(w[7])) <= device_properties->limits.maxSamplerLodBias &&
                float_word(w[12]) <= float_word(w[13]));
        REQUIRE(!w[8] || (sampler_anisotropy && float_word(w[9]) >= 1 &&
                          float_word(w[9]) <= device_properties->limits.maxSamplerAnisotropy));
        for (uint32_t j = 0; j < descriptor_count; j++)
            if (descriptors[j].type == 1 && descriptors[j].sampler == i && (w[1] || w[2] || w[3])) {
                VkFormatProperties f;
                vkGetPhysicalDeviceFormatProperties(
                    physical, (VkFormat)views[descriptors[j].view].format, &f);
                REQUIRE(f.optimalTilingFeatures &
                        VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT);
            }
        VkSamplerCreateInfo info = {.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
                                    .magFilter = (VkFilter)w[1],
                                    .minFilter = (VkFilter)w[2],
                                    .mipmapMode = (VkSamplerMipmapMode)w[3],
                                    .addressModeU = (VkSamplerAddressMode)w[4],
                                    .addressModeV = (VkSamplerAddressMode)w[5],
                                    .addressModeW = (VkSamplerAddressMode)w[6],
                                    .mipLodBias = float_word(w[7]),
                                    .anisotropyEnable = w[8],
                                    .maxAnisotropy = float_word(w[9]),
                                    .compareEnable = w[10],
                                    .compareOp = (VkCompareOp)w[11],
                                    .minLod = float_word(w[12]),
                                    .maxLod = float_word(w[13]),
                                    .borderColor = (VkBorderColor)w[14],
                                    .unnormalizedCoordinates = w[15]};
        CHECK(vkCreateSampler(device, &info, NULL, &samplers[i].sampler));
    }
    return 0;
}

int main(int argc, char **argv) {
    REQUIRE(argc == 4 &&
            (!strcmp(argv[3], "--check-only") || !strcmp(argv[3], "--execute-approved-plan")));
    int execute = !strcmp(argv[3], "--execute-approved-plan");
    control = fopen(argv[1], "rb");
    REQUIRE(control);
    char magic[8];
    REQUIRE(fread(magic, 1, 8, control) == 8 && !memcmp(magic, "OOPHYR1\0", 8));
    REQUIRE(u32() == 2);
    uint32_t required_width = u32(), requested_device = u32(), expected_vendor = u32(),
             expected_device = u32();
    unsigned char manifest_digest[32];
    char manifest_hex[65];
    REQUIRE(fread(manifest_digest, 1, 32, control) == 32);
    for (unsigned i = 0; i < 32; i++) {
        snprintf(manifest_hex + 2 * i, 3, "%02x", manifest_digest[i]);
    }
    REQUIRE((required_width == 8 || required_width == 16 || required_width == 32) &&
            requested_device <= 255);
    const int pinned = 1;
    char *shader_path = string(), *push_path = string();
    uint32_t push_size = u32(), root_offset = u32(), groups[3];
    for (unsigned i = 0; i < 3; i++)
        groups[i] = u32();
    REQUIRE(!bad && push_size <= 128 && push_size % 4 == 0 && root_offset <= push_size &&
            push_size - root_offset >= 8 && root_offset % 8 == 0);
    uint64_t group_count = 1;
    for (unsigned i = 0; i < 3; i++) {
        REQUIRE(groups[i] && groups[i] <= 65536 / group_count);
        group_count *= groups[i];
    }
    uint32_t allocation_count = u32();
    REQUIRE(allocation_count && allocation_count <= MAX_ALLOC);
    Allocation *allocations = calloc(allocation_count, sizeof(*allocations));
    REQUIRE(allocations);
    uint64_t total = 0;
    for (uint32_t i = 0; i < allocation_count; i++) {
        Allocation *a = &allocations[i];
        a->id = string();
        a->gpu.size = u64();
        a->initial = string();
        a->expected = string();
        REQUIRE(!bad && a->gpu.size && a->gpu.size <= MAX_BYTES - total);
        total += a->gpu.size;
    }
    char *table_path = string();
    uint32_t entries = u32(), mapping_count = u32();
    REQUIRE(!bad && entries && entries <= 4 * 1024 * 1024 && mapping_count <= entries);
    Mapping *mappings = calloc(mapping_count ? mapping_count : 1, sizeof(*mappings));
    unsigned char *seen = calloc(entries, 1);
    REQUIRE(mappings && seen);
    for (uint32_t i = 0; i < mapping_count; i++) {
        Mapping *m = &mappings[i];
        m->index = u32();
        m->allocation = u32();
        m->offset = u64();
        REQUIRE(!bad && m->index < entries && !seen[m->index] && m->allocation < allocation_count &&
                m->offset <= allocations[m->allocation].gpu.size &&
                4096 <= allocations[m->allocation].gpu.size - m->offset);
        seen[m->index] = 1;
    }
    uint32_t image_count = u32();
    REQUIRE(image_count <= MAX_IMAGE);
    Image *images = calloc(image_count ? image_count : 1, sizeof(*images));
    REQUIRE(images);
    uint64_t image_bytes = 0;
    for (uint32_t i = 0; i < image_count; i++) {
        Image *im = &images[i];
        im->format = u32();
        im->type = u32();
        for (unsigned j = 0; j < 3; j++)
            im->extent[j] = u32();
        im->mips = u32();
        im->layers = u32();
        im->count = u32();
        REQUIRE(pixel_bytes(im->format) && im->type <= 1 && im->extent[0] &&
                im->extent[0] <= 16384 && im->extent[1] && im->extent[1] <= 16384 &&
                im->extent[2] == 1 && (im->type || im->extent[1] == 1) && im->mips &&
                im->mips <= 15 && im->layers && im->layers <= 256 && im->count &&
                im->count <= 4096);
        im->sub = calloc(im->count, sizeof(*im->sub));
        REQUIRE(im->sub);
        uint64_t offset = 0;
        for (uint32_t j = 0; j < im->count; j++) {
            Subresource *s = &im->sub[j];
            s->mip = u32();
            s->layer = u32();
            s->size = u64();
            s->path = string();
            REQUIRE(!bad && s->mip < im->mips && s->layer < im->layers);
            uint32_t w = im->extent[0] >> s->mip, h = im->extent[1] >> s->mip;
            if (!w)
                w = 1;
            if (!h)
                h = 1;
            REQUIRE(s->size == (uint64_t)w * h * pixel_bytes(im->format));
            offset = (offset + 3) & ~UINT64_C(3);
            s->offset = offset;
            REQUIRE(s->size <= MAX_BYTES - offset);
            offset += s->size;
        }
        im->upload.size = offset;
        REQUIRE(offset <= MAX_BYTES - image_bytes);
        image_bytes += offset;
    }
    uint32_t view_count = u32();
    REQUIRE(view_count <= 64);
    View views[64] = {0};
    for (uint32_t i = 0; i < view_count; i++) {
        View *v = &views[i];
        v->image = u32();
        v->type = u32();
        v->format = u32();
        for (unsigned j = 0; j < 4; j++)
            v->components[j] = u32();
        v->base_mip = u32();
        v->mips = u32();
        v->base_layer = u32();
        v->layers = u32();
        REQUIRE(!bad && v->image < image_count && v->format == images[v->image].format && v->mips &&
                v->base_mip + v->mips <= images[v->image].mips && v->layers &&
                v->base_layer + v->layers <= images[v->image].layers);
    }
    uint32_t sampler_count = u32();
    REQUIRE(sampler_count <= 64);
    Sampler samplers[64] = {0};
    for (uint32_t i = 0; i < sampler_count; i++)
        for (unsigned j = 0; j < 16; j++)
            samplers[i].words[j] = u32();
    uint32_t descriptor_count = u32();
    REQUIRE(descriptor_count && descriptor_count <= MAX_BIND);
    Binding descriptors[MAX_BIND] = {0};
    VkDescriptorSetLayoutBinding bindings[8][256] = {{{0}}};
    uint32_t binding_counts[8] = {0};
    for (uint32_t i = 0; i < descriptor_count; i++) {
        Binding *d = &descriptors[i];
        d->binding = u32();
        d->element = u32();
        d->type = u32();
        d->null = u32();
        d->allocation = u32();
        d->view = u32();
        d->sampler = u32();
        d->offset = u64();
        d->size = u64();
        REQUIRE(!bad && d->binding < 256 && d->element < 256 &&
                (d->type == 1 || d->type == 6 || d->type == 7) && d->null <= 1);
        if (d->type == 1)
            REQUIRE(!d->null && d->view < view_count && d->sampler < sampler_count);
        else if (!d->null)
            REQUIRE(d->allocation < allocation_count &&
                    d->offset <= allocations[d->allocation].gpu.size && d->size &&
                    d->size <= allocations[d->allocation].gpu.size - d->offset);
        for (uint32_t j = 0; j < i; j++)
            REQUIRE(descriptors[j].binding != d->binding || descriptors[j].element != d->element);
        uint32_t k = 0;
        for (; k < binding_counts[0]; k++)
            if (bindings[0][k].binding == d->binding)
                break;
        if (k == binding_counts[0]) {
            bindings[0][k] =
                (VkDescriptorSetLayoutBinding){.binding = d->binding,
                                               .descriptorType = d->type,
                                               .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
            binding_counts[0]++;
        }
        REQUIRE(bindings[0][k].descriptorType == d->type);
        if (bindings[0][k].descriptorCount <= d->element)
            bindings[0][k].descriptorCount = d->element + 1;
    }
    REQUIRE(!bad && fgetc(control) == EOF);
    fclose(control);
    for (uint32_t k = 0; k < binding_counts[0]; k++)
        for (uint32_t e = 0; e < bindings[0][k].descriptorCount; e++) {
            int found = 0;
            for (uint32_t j = 0; j < descriptor_count; j++)
                found |=
                    descriptors[j].binding == bindings[0][k].binding && descriptors[j].element == e;
            REQUIRE(found);
        }
    FILE *shader_file = fopen(shader_path, "rb");
    REQUIRE(shader_file && fseek(shader_file, 0, SEEK_END) == 0);
    long code_size = ftell(shader_file);
    fclose(shader_file);
    REQUIRE(code_size >= 20 && code_size <= 16 * 1024 * 1024 && code_size % 4 == 0);
    uint32_t *code = load(shader_path, (uint64_t)code_size);
    unsigned char *push = load(push_path, push_size);
    uint64_t *table_data = load(table_path, (uint64_t)entries * 8);
    REQUIRE(code && code[0] == 0x07230203 && push && table_data);
    for (uint32_t i = 0; i < entries; i++) {
        REQUIRE((table_data[i] != 0) == (seen[i] != 0));
    }
    free(seen);
    if (!execute) {
        printf("{\"native_plan_validated\":true,\"dispatch_executed\":false,\"allocations\":%u,"
               "\"images\":%u}\n",
               allocation_count, image_count);
        return 0;
    }
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                             .pApplicationName = "Open-Ocarina owned physical replay",
                             .apiVersion = VK_API_VERSION_1_3};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                                          .pApplicationInfo = &app};
    VkInstance instance;
    CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t device_count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &device_count, NULL));
    if (!device_count || device_count > 256 || requested_device >= device_count) {
        fprintf(stderr, "Requested device is unavailable\n");
        return 1;
    }
    VkPhysicalDevice physical_devices[256];
    CHECK(vkEnumeratePhysicalDevices(instance, &device_count, physical_devices));
    VkPhysicalDevice physical = physical_devices[requested_device];
    VkPhysicalDevicePushDescriptorPropertiesKHR push_properties = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR};
    VkPhysicalDeviceFloatControlsProperties float_controls = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES,
        .pNext = &push_properties};
    VkPhysicalDeviceSubgroupSizeControlProperties subgroup = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES,
        .pNext = &float_controls};
    VkPhysicalDeviceDriverProperties driver = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &subgroup};
    VkPhysicalDeviceIDProperties device_identity = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES, .pNext = &driver};
    VkPhysicalDeviceProperties2 properties = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &device_identity};
    vkGetPhysicalDeviceProperties2(physical, &properties);
    if (properties.properties.apiVersion < VK_API_VERSION_1_3 ||
        (pinned &&
         (subgroup.minSubgroupSize > required_width || subgroup.maxSubgroupSize < required_width ||
          !(subgroup.requiredSubgroupSizeStages & VK_SHADER_STAGE_COMPUTE_BIT)))) {
        fprintf(stderr, "Vulkan 1.3 and requested compute subgroup policy are required\n");
        return 1;
    }
    uint32_t extension_count = 0;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, NULL));
    VkExtensionProperties *extensions = calloc(extension_count, sizeof(*extensions));
    if (!extensions && extension_count)
        return 1;
    CHECK(vkEnumerateDeviceExtensionProperties(physical, NULL, &extension_count, extensions));
    int executable_extension = 0, robustness_extension = 0, push_extension = 0;
    for (unsigned i = 0; i < extension_count; ++i)
        if (!strcmp(extensions[i].extensionName,
                    VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME))
            executable_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_EXT_ROBUSTNESS_2_EXTENSION_NAME))
            robustness_extension = 1;
        else if (!strcmp(extensions[i].extensionName, VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME))
            push_extension = 1;
    free(extensions);
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceRobustness2FeaturesEXT supported_robustness = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &executable : NULL};
    VkPhysicalDeviceVulkan13Features supported13 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = robustness_extension ? &supported_robustness
                                      : (executable_extension ? (void *)&executable : NULL)};
    VkPhysicalDeviceVulkan12Features supported12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &supported13};
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
                                           .pNext = &supported12};
    vkGetPhysicalDeviceFeatures2(physical, &supported);
    unsigned total_descriptors = 0;
    for (unsigned i = 0; i < binding_counts[0]; ++i)
        total_descriptors += bindings[0][i].descriptorCount;
    if (!push_extension || total_descriptors > push_properties.maxPushDescriptors ||
        !supported.features.robustBufferAccess || !robustness_extension ||
        !supported_robustness.robustBufferAccess2 || !supported.features.shaderInt16 ||
        !supported.features.shaderInt64 ||
        !supported.features.shaderStorageBufferArrayDynamicIndexing ||
        !supported.features.shaderImageGatherExtended || !supported12.uniformBufferStandardLayout ||
        !supported12.bufferDeviceAddress || !float_controls.shaderSignedZeroInfNanPreserveFloat32 ||
        !supported13.subgroupSizeControl || !supported13.computeFullSubgroups) {
        fprintf(stderr, "A required target shader feature is unavailable\n");
        return 1;
    }
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR enabled_executable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR,
        .pipelineExecutableInfo = executable.pipelineExecutableInfo};
    VkPhysicalDeviceRobustness2FeaturesEXT enabled_robustness = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,
        .pNext = executable_extension ? &enabled_executable : NULL,
        .robustBufferAccess2 = VK_TRUE,
        .nullDescriptor = supported_robustness.nullDescriptor};
    VkPhysicalDeviceVulkan13Features enabled13 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
        .pNext = &enabled_robustness,
        .subgroupSizeControl = VK_TRUE,
        .computeFullSubgroups = VK_TRUE};
    VkPhysicalDeviceVulkan12Features enabled12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &enabled13,
        .uniformBufferStandardLayout = VK_TRUE,
        .bufferDeviceAddress = VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &enabled12,
        .features = {.robustBufferAccess = VK_TRUE,
                     .shaderInt16 = VK_TRUE,
                     .shaderInt64 = VK_TRUE,
                     .shaderStorageBufferArrayDynamicIndexing = VK_TRUE,
                     .shaderImageGatherExtended = VK_TRUE,
                     .samplerAnisotropy = supported.features.samplerAnisotropy,
                     .imageCubeArray = supported.features.imageCubeArray}};
    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    VkQueueFamilyProperties *queues = calloc(queue_count, sizeof(*queues));
    if (!queues)
        return 1;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);
    uint32_t queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < queue_count; ++i)
        if (queues[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {
            queue_family = i;
            break;
        }
    free(queues);
    if (queue_family == UINT32_MAX)
        return 1;
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                          .queueFamilyIndex = queue_family,
                                          .queueCount = 1,
                                          .pQueuePriorities = &priority};
    const char *extension_names[3] = {VK_EXT_ROBUSTNESS_2_EXTENSION_NAME,
                                      VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME,
                                      VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                                      .pNext = &enabled,
                                      .queueCreateInfoCount = 1,
                                      .pQueueCreateInfos = &queue_info,
                                      .enabledExtensionCount = executable_extension ? 3 : 2,
                                      .ppEnabledExtensionNames = extension_names};
    VkDevice device;
    CHECK(vkCreateDevice(physical, &device_info, NULL, &device));

    REQUIRE((!expected_vendor || expected_vendor == properties.properties.vendorID) &&
            (!expected_device || expected_device == properties.properties.deviceID));
    for (uint32_t i = 0; i < 3; i++)
        REQUIRE(groups[i] <= properties.properties.limits.maxComputeWorkGroupCount[i]);
    VkBufferUsageFlags buffer_usage =
        VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT |
        VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT |
        VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    for (uint32_t i = 0; i < allocation_count; i++) {
        Allocation *a = &allocations[i];
        uint64_t size = a->gpu.size;
        int result = make_buffer(physical, device, size, buffer_usage, 1, &a->gpu);
        if (result)
            return result;
        void *data = load(a->initial, size);
        REQUIRE(data);
        memcpy(a->gpu.mapped, data, (size_t)size);
        free(data);
    }
    Buffer table_gpu = {0};
    if (make_buffer(physical, device, (uint64_t)entries * 8, buffer_usage, 1, &table_gpu))
        return 3;
    for (uint32_t i = 0; i < mapping_count; i++) {
        Mapping *m = &mappings[i];
        REQUIRE(m->offset % 16 == 0 &&
                allocations[m->allocation].gpu.address <= UINT64_MAX - m->offset);
        table_data[m->index] = allocations[m->allocation].gpu.address + m->offset;
    }
    memcpy(table_gpu.mapped, table_data, (size_t)entries * 8);
    memcpy(push + root_offset, &table_gpu.address, 8);
    int sampled_status =
        create_sampled_inputs(physical, device, image_count, images, view_count, views,
                              sampler_count, samplers, descriptor_count, descriptors,
                              &properties.properties, supported.features.samplerAnisotropy);
    if (sampled_status)
        return sampled_status;
    VkDescriptorSetLayoutCreateInfo set_info = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR,
        .bindingCount = binding_counts[0],
        .pBindings = bindings[0]};
    VkDescriptorSetLayout set_layout;
    CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &set_layout));
    VkPushConstantRange push_range = {VK_SHADER_STAGE_COMPUTE_BIT, 0, push_size};
    VkPipelineLayoutCreateInfo layout_info = {.sType =
                                                  VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
                                              .setLayoutCount = 1,
                                              .pSetLayouts = &set_layout,
                                              .pushConstantRangeCount = 1,
                                              .pPushConstantRanges = &push_range};
    VkPipelineLayout layout;
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkShaderModuleCreateInfo module_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                            .codeSize = (size_t)code_size,
                                            .pCode = code};
    VkShaderModule module;
    CHECK(vkCreateShaderModule(device, &module_info, NULL, &module));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo width_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO,
        .requiredSubgroupSize = required_width};
    VkComputePipelineCreateInfo pipeline_info = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .flags =
            executable.pipelineExecutableInfo ? VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR : 0,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                  .pNext = &width_info,
                  .flags = VK_PIPELINE_SHADER_STAGE_CREATE_REQUIRE_FULL_SUBGROUPS_BIT,
                  .stage = VK_SHADER_STAGE_COMPUTE_BIT,
                  .module = module,
                  .pName = "main"},
        .layout = layout};
    VkPipeline pipeline;
    CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    REQUIRE(executable.pipelineExecutableInfo);
    PFN_vkGetPipelineExecutablePropertiesKHR get_executable =
        (PFN_vkGetPipelineExecutablePropertiesKHR)vkGetDeviceProcAddr(
            device, "vkGetPipelineExecutablePropertiesKHR");
    REQUIRE(get_executable);
    VkPipelineInfoKHR pipeline_query = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR,
                                        .pipeline = pipeline};
    uint32_t executable_count = 0;
    CHECK(get_executable(device, &pipeline_query, &executable_count, NULL));
    REQUIRE(executable_count == 1);
    VkPipelineExecutablePropertiesKHR executable_properties = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR};
    CHECK(get_executable(device, &pipeline_query, &executable_count, &executable_properties));
    REQUIRE(executable_properties.subgroupSize == required_width);
    VkDescriptorBufferInfo buffer_infos[MAX_BIND];
    VkDescriptorImageInfo image_infos[MAX_BIND];
    VkWriteDescriptorSet writes[MAX_BIND];
    for (uint32_t i = 0; i < descriptor_count; i++) {
        Binding *d = &descriptors[i];
        writes[i] = (VkWriteDescriptorSet){.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
                                           .dstBinding = d->binding,
                                           .dstArrayElement = d->element,
                                           .descriptorCount = 1,
                                           .descriptorType = (VkDescriptorType)d->type};
        if (d->type == 1) {
            image_infos[i] =
                (VkDescriptorImageInfo){.sampler = samplers[d->sampler].sampler,
                                        .imageView = views[d->view].view,
                                        .imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
            writes[i].pImageInfo = &image_infos[i];
        } else {
            if (d->null) {
                REQUIRE(enabled_robustness.nullDescriptor);
                buffer_infos[i] = (VkDescriptorBufferInfo){VK_NULL_HANDLE, 0, VK_WHOLE_SIZE};
            } else {
                uint64_t alignment =
                    d->type == 6 ? properties.properties.limits.minUniformBufferOffsetAlignment
                                 : properties.properties.limits.minStorageBufferOffsetAlignment;
                uint64_t maximum = d->type == 6
                                       ? properties.properties.limits.maxUniformBufferRange
                                       : properties.properties.limits.maxStorageBufferRange;
                REQUIRE(d->size <= maximum && (!alignment || d->offset % alignment == 0));
                buffer_infos[i] = (VkDescriptorBufferInfo){allocations[d->allocation].gpu.buffer,
                                                           d->offset, d->size};
            }
            writes[i].pBufferInfo = &buffer_infos[i];
        }
    }
    VkCommandPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
                                         .queueFamilyIndex = queue_family};
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    VkCommandBufferAllocateInfo command_info = {.sType =
                                                    VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
                                                .commandPool = pool,
                                                .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
                                                .commandBufferCount = 1};
    VkCommandBuffer cmd;
    CHECK(vkAllocateCommandBuffers(device, &command_info, &cmd));
    VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                      .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CHECK(vkBeginCommandBuffer(cmd, &begin));
    VkMemoryBarrier host_barrier = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                                    .srcAccessMask = VK_ACCESS_HOST_WRITE_BIT,
                                    .dstAccessMask = VK_ACCESS_SHADER_READ_BIT |
                                                     VK_ACCESS_SHADER_WRITE_BIT |
                                                     VK_ACCESS_TRANSFER_READ_BIT};
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_HOST_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT, 0,
                         1, &host_barrier, 0, NULL, 0, NULL);
    for (uint32_t i = 0; i < image_count; i++) {
        Image *im = &images[i];
        VkImageMemoryBarrier to_copy = {
            .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
            .srcAccessMask = 0,
            .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
            .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
            .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = im->image,
            .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, im->mips, 0, im->layers}};
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             0, 0, NULL, 0, NULL, 1, &to_copy);
        VkBufferImageCopy *copies = calloc(im->count, sizeof(*copies));
        REQUIRE(copies);
        for (uint32_t j = 0; j < im->count; j++) {
            Subresource *s = &im->sub[j];
            uint32_t w = im->extent[0] >> s->mip, h = im->extent[1] >> s->mip;
            if (!w)
                w = 1;
            if (!h)
                h = 1;
            copies[j] = (VkBufferImageCopy){
                .bufferOffset = s->offset,
                .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, s->mip, s->layer, 1},
                .imageExtent = {w, h, 1}};
        }
        vkCmdCopyBufferToImage(cmd, im->upload.buffer, im->image,
                               VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, im->count, copies);
        free(copies);
        VkImageMemoryBarrier to_read = to_copy;
        to_read.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
        to_read.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
        to_read.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
        to_read.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, NULL, 0, NULL, 1,
                             &to_read);
    }
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    PFN_vkCmdPushDescriptorSetKHR push_descriptors =
        (PFN_vkCmdPushDescriptorSetKHR)vkGetDeviceProcAddr(device, "vkCmdPushDescriptorSetKHR");
    REQUIRE(push_descriptors);
    push_descriptors(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, descriptor_count, writes);
    vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, push_size, push);
    vkCmdDispatch(cmd, groups[0], groups[1], groups[2]);
    VkMemoryBarrier readback = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                                .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
                                .dstAccessMask = VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0,
                         1, &readback, 0, NULL, 0, NULL);
    CHECK(vkEndCommandBuffer(cmd));
    VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    CHECK(vkCreateFence(device, &fence_info, NULL, &fence));
    VkQueue queue;
    vkGetDeviceQueue(device, queue_family, 0, &queue);
    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd};
    CHECK(vkQueueSubmit(queue, 1, &submit, fence));
    CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_C(10000000000)));
    char device_uuid[33], driver_uuid[33];
    for (unsigned i = 0; i < 16; i++) {
        snprintf(device_uuid + 2 * i, 3, "%02x", device_identity.deviceUUID[i]);
        snprintf(driver_uuid + 2 * i, 3, "%02x", device_identity.driverUUID[i]);
    }
    int compared = 0, matches = 1;
    printf("{\"schema\":1,\"dispatch_executed\":true,\"dispatch_count\":1,\"source_manifest_"
           "sha256\":\"%s\",\"actual_subgroup_size\":%u,\"device_uuid\":\"%s\",\"driver_uuid\":\"%"
           "s\",\"device_vendor\":%u,\"device_id\":%u,\"allocations\":[",
           manifest_hex, executable_properties.subgroupSize, device_uuid, driver_uuid,
           properties.properties.vendorID, properties.properties.deviceID);
    for (uint32_t i = 0; i < allocation_count; i++) {
        Allocation *a = &allocations[i];
        char path[8192];
        int length = snprintf(path, sizeof(path), "%s/allocation-%u.bin", argv[2], i);
        REQUIRE(length > 0 && (size_t)length < sizeof(path));
        FILE *output = fopen(path, "wb");
        REQUIRE(output && fwrite(a->gpu.mapped, 1, (size_t)a->gpu.size, output) == a->gpu.size &&
                fclose(output) == 0);
        uint64_t difference = UINT64_MAX;
        if (strcmp(a->expected, "-")) {
            compared++;
            FILE *expected = fopen(a->expected, "rb");
            REQUIRE(expected);
            unsigned char block[65536];
            uint64_t offset = 0;
            while (offset < a->gpu.size) {
                size_t count =
                    (size_t)((a->gpu.size - offset) > sizeof(block) ? sizeof(block)
                                                                    : (a->gpu.size - offset));
                REQUIRE(fread(block, 1, count, expected) == count);
                if (difference == UINT64_MAX)
                    for (size_t j = 0; j < count; j++)
                        if (block[j] != ((unsigned char *)a->gpu.mapped)[offset + j]) {
                            difference = offset + j;
                            matches = 0;
                            break;
                        }
                offset += count;
            }
            REQUIRE(fgetc(expected) == EOF);
            fclose(expected);
        }
        if (i) {
            putchar(',');
        }
        printf("{\"index\":%u,\"size\":%llu,\"first_difference\":", i,
               (unsigned long long)a->gpu.size);
        if (difference == UINT64_MAX)
            printf("null");
        else
            printf("%llu", (unsigned long long)difference);
        putchar('}');
    }
    printf("],\"expected_comparison_performed\":%s,\"comparison_passed\":",
           compared ? "true" : "false");
    if (compared == 0)
        printf("null");
    else
        printf("%s", matches ? "true" : "false");
    printf("}\n");
    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, pool, NULL);
    vkDestroyPipeline(device, pipeline, NULL);
    vkDestroyShaderModule(device, module, NULL);
    vkDestroyPipelineLayout(device, layout, NULL);
    vkDestroyDescriptorSetLayout(device, set_layout, NULL);
    for (uint32_t i = 0; i < sampler_count; i++) {
        vkDestroySampler(device, samplers[i].sampler, NULL);
    }
    for (uint32_t i = 0; i < view_count; i++) {
        vkDestroyImageView(device, views[i].view, NULL);
    }
    for (uint32_t i = 0; i < image_count; i++) {
        destroy_buffer(device, &images[i].upload);
        vkDestroyImage(device, images[i].image, NULL);
        vkFreeMemory(device, images[i].memory, NULL);
    }
    destroy_buffer(device, &table_gpu);
    for (uint32_t i = 0; i < allocation_count; i++)
        destroy_buffer(device, &allocations[i].gpu);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    return matches ? 0 : 1;
}
