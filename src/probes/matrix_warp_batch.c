/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Independently authored finite-half batch, one warp32 per independent workgroup. */
#define PROFILE "open-ocarina.matrix-warp32-finite-batch/v2"
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fixture_binding.h"
#define INPUT_STRIDE 288u
#define OUTPUT_STRIDE 96u
#define INNER 16u
#define INNER_GUARD UINT32_C(0xa11ce55a)
#define SENTINEL UINT32_C(0xcdcdcdcd)
#define MARKER_BASE UINT32_C(0x57420000)

#define GUARD 256u
#define CHECK(call) do { VkResult status = (call); if (status != VK_SUCCESS) { \
    fprintf(stderr, "%s failed: %d\n", #call, status); return 1; } } while (0)
#define REQUIRE(test) do { if (!(test)) { fprintf(stderr, "Rejected: %s\n", #test); return 1; } } while (0)

typedef struct Fixture {
    uint32_t width, groups, safe_groups, counts[3];
    void *storage;
    uint32_t *records, *input, *expected, *expected_markers;
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

static uint32_t ror(uint32_t value, unsigned n) { return (value >> n) | (value << (32-n)); }
/* Standard SHA-256, independently checked against hashlib for the full corpus.
 * This binds all diagnostic input/reference bits without duplicating their
 * floating arithmetic model in C. No subprocess or cryptographic library. */
static void sha256(const uint8_t *data, size_t size, uint8_t result[32]) {
    static const uint32_t k[64]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t state[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    const size_t blocks=(size+9+63)/64;
    for (size_t block=0;block<blocks;++block) {
        uint8_t bytes[64]={0};
        for (unsigned j=0;j<64;++j) {
            size_t index=block*64+j;
            if (index<size) bytes[j]=data[index];
            else if (index==size) bytes[j]=0x80;
        }
        if (block+1==blocks) for (unsigned j=0;j<8;++j) bytes[63-j]=(uint8_t)(((uint64_t)size*8)>>(j*8));
        uint32_t w[64];
        for (unsigned j=0;j<16;++j) w[j]=(uint32_t)bytes[j*4]<<24|(uint32_t)bytes[j*4+1]<<16|(uint32_t)bytes[j*4+2]<<8|bytes[j*4+3];
        for (unsigned j=16;j<64;++j) {
            uint32_t s0=ror(w[j-15],7)^ror(w[j-15],18)^(w[j-15]>>3);
            uint32_t s1=ror(w[j-2],17)^ror(w[j-2],19)^(w[j-2]>>10);
            w[j]=w[j-16]+s0+w[j-7]+s1;
        }
        uint32_t a=state[0],b=state[1],c=state[2],d=state[3],e=state[4],f=state[5],g=state[6],h=state[7];
        for (unsigned j=0;j<64;++j) {
            uint32_t s1=ror(e,6)^ror(e,11)^ror(e,25),choose=(e&f)^(~e&g);
            uint32_t t1=h+s1+choose+k[j]+w[j];
            uint32_t s0=ror(a,2)^ror(a,13)^ror(a,22),majority=(a&b)^(a&c)^(b&c);
            uint32_t t2=s0+majority;
            h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
        }
        state[0]+=a;state[1]+=b;state[2]+=c;state[3]+=d;state[4]+=e;state[5]+=f;state[6]+=g;state[7]+=h;
    }
    for (unsigned j=0;j<8;++j) for (unsigned i=0;i<4;++i) result[j*4+i]=(uint8_t)(state[j]>>(24-i*8));
}
static int sha256_selftest(void) {
    const uint8_t empty[32]={0xe3,0xb0,0xc4,0x42,0x98,0xfc,0x1c,0x14,0x9a,0xfb,0xf4,0xc8,0x99,0x6f,0xb9,0x24,0x27,0xae,0x41,0xe4,0x64,0x9b,0x93,0x4c,0xa4,0x95,0x99,0x1b,0x78,0x52,0xb8,0x55};
    const uint8_t abc[32]={0xba,0x78,0x16,0xbf,0x8f,0x01,0xcf,0xea,0x41,0x41,0x40,0xde,0x5d,0xae,0x22,0x23,0xb0,0x03,0x61,0xa3,0x96,0x17,0x7a,0x9c,0xb4,0x10,0xff,0x61,0xf2,0x00,0x15,0xad};
    uint8_t result[32];
    sha256((const uint8_t *)"",0,result); if (memcmp(result,empty,32)) return 1;
    sha256((const uint8_t *)"abc",3,result); return memcmp(result,abc,32)!=0;
}
static int small_uint(uint16_t half, uint32_t *out) {
    if (!half) { *out=0;return 0; }
    unsigned exponent=(half>>10)&31,significand=1024+(half&1023);
    if ((half&0x8000)||exponent==0||exponent==31) return 1;
    int power=(int)exponent-25;
    if (power<0) {
        if (-power>24 || (significand&((1u<<(-power))-1))) return 1;
        *out=significand>>(-power);
    } else *out=significand<<power;
    return *out>2048;
}
static uint16_t integer_half(uint32_t value) {
    if (!value) return 0;
    unsigned exponent=0;
    for (uint32_t v=value;v>1;v>>=1) ++exponent;
    uint32_t significand=exponent<=10 ? value<<(10-exponent) : value>>(exponent-10);
    return (uint16_t)(((exponent+15)<<10)|(significand-1024));
}
static int validate_safe_group(const uint32_t *input,const uint32_t *expected) {
    uint32_t a[16][16]={{0}},b[16][8]={{0}},c[16][8]={{0}};
    for (unsigned lane=0;lane<32;++lane) {
        unsigned g=lane/4,i=lane%4;
        for (unsigned word=0;word<8;++word) for (unsigned h=0;h<2;++h) {
            uint16_t half=(uint16_t)(input[lane*8+word]>>(h*16));
            uint32_t value; REQUIRE(!small_uint(half,&value));
            if (word<4) a[g+(word%2)*8][i*2+(word/2)*8+h]=value;
            else if (word<6) b[i*2+(word-4)*8+h][g]=value;
            else c[g+(word-6)*8][i*2+h]=value;
        }
    }
    for (unsigned lane=0;lane<32;++lane) for (unsigned word=0;word<2;++word) for (unsigned h=0;h<2;++h) {
        unsigned row=lane/4+word*8,col=(lane%4)*2+h;
        uint64_t sum=c[row][col];
        for (unsigned k=0;k<16;++k) sum+=(uint64_t)a[row][k]*b[k][col];
        REQUIRE(sum<=2048 && (uint16_t)(expected[lane*2+word]>>(h*16))==integer_half((uint32_t)sum));
    }
    return 0;
}
/* All diagnostic bits are bound to the compiled SHA-256. Safe references also
 * receive an independent positive-integer matrix check before any instance. */
static int load_fixture(const char *path, Fixture *f) {
    size_t size;
    uint8_t *data=read_file(path,48,131072,&size);
    REQUIRE(data);
    uint8_t hash[32];sha256(data,size,hash);REQUIRE(!memcmp(hash,approved_bundle_sha256,32));
    REQUIRE(!memcmp(data,"OOMATB04",8));
    const uint16_t endian=1;REQUIRE(*(const uint8_t *)&endian==1);
    uint32_t header[10];memcpy(header,data+8,sizeof(header));
    f->width=header[1];f->groups=header[2];f->counts[0]=header[6];f->counts[1]=header[7];f->counts[2]=header[8];
    REQUIRE(header[0]==2 && f->width==32 && f->groups>=1 && f->groups<=64 && f->groups==APPROVED_GROUP_COUNT);
    REQUIRE(header[3]==INPUT_STRIDE && header[4]==OUTPUT_STRIDE && header[5]==OUTPUT_STRIDE && header[9]==f->groups);
    REQUIRE(f->counts[0]==f->groups*INPUT_STRIDE && f->counts[1]==f->groups*OUTPUT_STRIDE && f->counts[2]==f->groups*OUTPUT_STRIDE);
    REQUIRE(size==48+(size_t)f->groups*8+(size_t)(f->counts[0]+f->counts[1]+f->counts[2])*4);
    f->storage=data;f->records=(uint32_t *)(data+48);f->input=f->records+f->groups*2;f->expected=f->input+f->counts[0];f->expected_markers=f->expected+f->counts[1];
    for (unsigned group=0;group<f->groups;++group) {
        REQUIRE(f->records[group*2]==group && f->records[group*2+1]<=1);
        const uint32_t *input=f->input+group*INPUT_STRIDE,*expected=f->expected+group*OUTPUT_STRIDE,*markers=f->expected_markers+group*OUTPUT_STRIDE;
        for (unsigned j=0;j<INNER;++j) REQUIRE(input[j]==INNER_GUARD && input[INPUT_STRIDE-INNER+j]==INNER_GUARD && expected[j]==INNER_GUARD && expected[OUTPUT_STRIDE-INNER+j]==INNER_GUARD && markers[j]==INNER_GUARD && markers[OUTPUT_STRIDE-INNER+j]==INNER_GUARD);
        for (unsigned j=0;j<64;++j) REQUIRE(markers[INNER+j]==(MARKER_BASE|(group*64+j+1)));
        for (unsigned j=0;j<256;++j) {
            uint32_t pair=input[INNER+j];
            REQUIRE((pair&0x7c00)!=0x7c00 && ((pair>>16)&0x7c00)!=0x7c00);
        }
        if (f->records[group*2+1]) { if(validate_safe_group(input+INNER,expected+INNER)) return 1; ++f->safe_groups; }
    }
    REQUIRE(f->safe_groups==16);
    return 0;
}

/* Validate every guard and every owner token BEFORE counting any numerical
 * difference. Diagnostics are allowed to differ numerically, never to omit a
 * payload store/completion marker. Runtime checks outer guards/input first. */
static int check_outputs(const Fixture *fixture,const uint32_t *out,const uint32_t *markers,unsigned *safe_bad,unsigned *diagnostic_bad) {
    *safe_bad=0;*diagnostic_bad=0;
    for (unsigned group=0;group<fixture->groups;++group) {
        const unsigned base=group*OUTPUT_STRIDE;
        for (unsigned j=0;j<INNER;++j) REQUIRE(out[base+j]==INNER_GUARD && out[base+OUTPUT_STRIDE-INNER+j]==INNER_GUARD && markers[base+j]==INNER_GUARD && markers[base+OUTPUT_STRIDE-INNER+j]==INNER_GUARD);
        for (unsigned word=0;word<64;++word) {
            uint32_t expected=MARKER_BASE|(group*64+word+1);
            if (markers[base+INNER+word]!=expected) {
                fprintf(stderr,"Missing/incorrect output marker at group %u word %u; no numerical classification\n",group,word);
                return 1;
            }
        }
    }
    for (unsigned group=0;group<fixture->groups;++group) for (unsigned word=0;word<64;++word) {
        unsigned index=group*OUTPUT_STRIDE+INNER+word;
        if(out[index]!=fixture->expected[index]) {
            if(fixture->records[group*2+1]) ++*safe_bad; else ++*diagnostic_bad;
        }
    }
    return 0;
}

static int check_frames(const Fixture *fixture,const uint8_t *input,const uint8_t *out,const uint8_t *markers,unsigned *safe_bad,unsigned *diagnostic_bad) {
    *safe_bad=0;*diagnostic_bad=0;
    const uint8_t *frames[3]={input,out,markers};
    for (unsigned i=0;i<3;++i) for (unsigned j=0;j<GUARD;++j) REQUIRE(frames[i][j]==0xa5 && frames[i][GUARD+fixture->counts[i]*4u+j]==0xa5);
    REQUIRE(!memcmp(input+GUARD,fixture->input,fixture->counts[0]*4u));
    return check_outputs(fixture,(const uint32_t *)(out+GUARD),(const uint32_t *)(markers+GUARD),safe_bad,diagnostic_bad);
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
    if (argc==2 && !strcmp(argv[1],"selftest")) { if(sha256_selftest()) return 1; puts("{\"sha256_selftest\":true,\"vulkan_instance_created\":false}"); return 0; }
    if (argc < 3) { fprintf(stderr, "Usage: warp32-batch-helper selftest | cpu fixture | check fixture output markers | frames fixture input output markers | properties/compile/dispatch fixture module output markers input\n"); return 2; }
    const int check_only=!strcmp(argv[1],"check"),frames_only=!strcmp(argv[1],"frames");
    const int cpu = !strcmp(argv[1], "cpu"), properties_only = !strcmp(argv[1], "properties");
    const int dispatch = !strcmp(argv[1], "dispatch"), compile = !strcmp(argv[1], "compile");
    REQUIRE((cpu && argc == 3) || (check_only && argc==5) || (frames_only && argc==6) || ((properties_only || compile || dispatch) && argc == 7));
    Fixture fixture = {0};
    if (load_fixture(argv[2], &fixture)) return 2;
    if (cpu) { printf("{\"profile\":\"" PROFILE "\",\"cpu_validated\":true,\"groups\":%u,\"safe_groups_independently_integer_validated\":%u,\"vulkan_instance_created\":false}\n",fixture.groups,fixture.safe_groups); free(fixture.storage); return 0; }
    if (check_only) {
        size_t output_size=0,marker_size=0;
        uint32_t *out=read_file(argv[3],fixture.counts[1]*4u,fixture.counts[1]*4u,&output_size);
        uint32_t *markers=read_file(argv[4],fixture.counts[2]*4u,fixture.counts[2]*4u,&marker_size);
        REQUIRE(out && markers && output_size==fixture.counts[1]*4u && marker_size==fixture.counts[2]*4u);
        unsigned safe_bad=0,diagnostic_bad=0;
        if(check_outputs(&fixture,out,markers,&safe_bad,&diagnostic_bad)) return 1;
        printf("{\"profile\":\"" PROFILE "\",\"cpu_output_validated\":true,\"groups\":%u,\"all_payload_words_marked\":true,\"verified_writtenness_markers\":%u,\"safe_mismatched_packed_words\":%u,\"diagnostic_mismatched_packed_words\":%u,\"vulkan_instance_created\":false}\n",fixture.groups,fixture.groups*64,safe_bad,diagnostic_bad);
        free(out);free(markers);free(fixture.storage);return safe_bad?1:0;
    }
    if (frames_only) {
        uint8_t *frames[3]={0};
        for (unsigned i=0;i<3;++i) {size_t size=0;frames[i]=read_file(argv[3+i],fixture.counts[i]*4u+GUARD*2,fixture.counts[i]*4u+GUARD*2,&size);REQUIRE(frames[i] && size==fixture.counts[i]*4u+GUARD*2);}
        unsigned safe_bad=0,diagnostic_bad=0;
        if(check_frames(&fixture,frames[0],frames[1],frames[2],&safe_bad,&diagnostic_bad)) return 1;
        printf("{\"profile\":\"" PROFILE "\",\"cpu_frame_validated\":true,\"all_payload_words_marked\":true,\"verified_writtenness_markers\":%u,\"safe_mismatched_packed_words\":%u,\"diagnostic_mismatched_packed_words\":%u,\"vulkan_instance_created\":false}\n",fixture.groups*64,safe_bad,diagnostic_bad);
        for(unsigned i=0;i<3;++i) free(frames[i]);
        free(fixture.storage);
        return safe_bad?1:0;
    }
    size_t code_size = 0;
    uint32_t *code = read_file(argv[3], 20, 65536, &code_size);
    REQUIRE(code && code_size % 4 == 0 && code[0] == 0x07230203);
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Open-Ocarina independent-warp32 finite batch", .apiVersion = VK_API_VERSION_1_3};
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
    REQUIRE(props.properties.limits.maxComputeSharedMemorySize >= 2304 &&
            props.properties.limits.maxComputeWorkGroupInvocations >= 32 && props.properties.limits.maxComputeWorkGroupSize[0] >= 32 &&
            props.properties.limits.maxComputeWorkGroupCount[0] >= fixture.groups &&
            props.properties.limits.maxStorageBufferRange >= fixture.counts[0]*4u && props.properties.limits.maxPerStageDescriptorStorageBuffers >= 3 &&
            props.properties.limits.maxDescriptorSetStorageBuffers >= 3);
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
    VkPhysicalDeviceFeatures2 features = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f12};
    vkGetPhysicalDeviceFeatures2(physical, &features);
    REQUIRE(features.features.robustBufferAccess && f12.shaderFloat16 && f12.vulkanMemoryModel &&
            f13.subgroupSizeControl && f13.computeFullSubgroups && cooperative.cooperativeMatrix && executable.pipelineExecutableInfo);
    if (properties_only) {
        printf("{\"profile\":\"" PROFILE "\",\"phase\":\"properties\",\"groups\":%u,\"requested_width\":%u,\"advertised_shape\":true,\"subgroup_min\":%u,\"subgroup_max\":%u,\"subgroup_default\":%u,\"pipeline_compiled\":false,\"gpu_executed\":false,\"device\":",
               fixture.groups, fixture.width, widths.minSubgroupSize, widths.maxSubgroupSize, sg.subgroupSize);
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
    VkPhysicalDeviceFeatures2 enabled = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &e12, .features = {.robustBufferAccess = VK_TRUE}};
    const char *device_extensions[] = {VK_KHR_COOPERATIVE_MATRIX_EXTENSION_NAME, VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME};
    float priority = 1;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &enabled,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info, .enabledExtensionCount = 2, .ppEnabledExtensionNames = device_extensions};
    VkDevice device; CHECK(vkCreateDevice(physical, &device_info, NULL, &device));
    VkDescriptorSetLayoutBinding bindings[3];
    for (uint32_t i = 0; i < 3; ++i) bindings[i] = (VkDescriptorSetLayoutBinding){.binding = i, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1, .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo set_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 3, .pBindings = bindings};
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
    unsigned safe_mismatches=0, diagnostic_mismatches=0; int intact=1;
    Buffer buffers[3] = {{0}};
    VkCommandPool command_pool = VK_NULL_HANDLE;
    VkDescriptorPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    if (dispatch) {
        const void *inputs[] = {fixture.input};
        for (unsigned i = 0; i < 3; ++i) {
            size_t bytes = fixture.counts[i] * 4u;
            if (make_buffer(physical, device, bytes, &buffers[i])) return 1;
            if (i < 1) memcpy(buffers[i].mapped + GUARD, inputs[i], bytes);
            else {
                uint32_t *out=(uint32_t *)(buffers[i].mapped+GUARD);
                for (unsigned j=0;j<fixture.counts[1];++j) out[j]=i==1?SENTINEL:0;
                for (unsigned g=0;g<fixture.groups;++g) for (unsigned j=0;j<INNER;++j) {out[g*OUTPUT_STRIDE+j]=INNER_GUARD;out[(g+1)*OUTPUT_STRIDE-INNER+j]=INNER_GUARD;}
            }
        }
        VkDescriptorPoolSize pool_size = {.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 3};
        VkDescriptorPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size};
        CHECK(vkCreateDescriptorPool(device, &pool_info, NULL, &pool));
        VkDescriptorSetAllocateInfo set_alloc = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = pool, .descriptorSetCount = 1, .pSetLayouts = &set_layout};
        VkDescriptorSet set; CHECK(vkAllocateDescriptorSets(device, &set_alloc, &set));
        VkDescriptorBufferInfo views[3]; VkWriteDescriptorSet writes[3];
        for (unsigned i = 0; i < 3; ++i) {
            views[i] = (VkDescriptorBufferInfo){.buffer = buffers[i].buffer, .offset = GUARD, .range = buffers[i].bytes};
            writes[i] = (VkWriteDescriptorSet){.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = set, .dstBinding = i, .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &views[i]};
        }
        vkUpdateDescriptorSets(device, 3, writes, 0, NULL);
        VkCommandPoolCreateInfo command_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family};
        CHECK(vkCreateCommandPool(device, &command_info, NULL, &command_pool));
        VkCommandBufferAllocateInfo alloc = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = command_pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
        VkCommandBuffer command; CHECK(vkAllocateCommandBuffers(device, &alloc, &command));
        VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
        CHECK(vkBeginCommandBuffer(command, &begin));
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &set, 0, NULL);
        vkCmdDispatch(command, fixture.groups, 1, 1);
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
        for (unsigned i = 0; i < 3; ++i) {
            for (unsigned j = 0; j < GUARD; ++j)
                if (buffers[i].mapped[j] != 0xa5 || buffers[i].mapped[GUARD + buffers[i].bytes + j] != 0xa5) intact = 0;
            if (i < 1 && memcmp(buffers[i].mapped + GUARD, inputs[i], buffers[i].bytes)) intact = 0;
        }
        REQUIRE(intact);
        if(check_frames(&fixture,buffers[0].mapped,buffers[1].mapped,buffers[2].mapped,&safe_mismatches,&diagnostic_mismatches)) return 1;
        FILE *file = fopen(argv[4], "wb"); REQUIRE(file);
        int failed = fwrite(buffers[1].mapped, 1, buffers[1].bytes+GUARD*2, file) != buffers[1].bytes+GUARD*2;
        if (fclose(file)) failed = 1;
        REQUIRE(!failed);
        file=fopen(argv[5],"wb");REQUIRE(file);
        failed=fwrite(buffers[2].mapped,1,buffers[2].bytes+GUARD*2,file)!=buffers[2].bytes+GUARD*2;
        if(fclose(file)) failed=1;
        REQUIRE(!failed);
        file=fopen(argv[6],"wb");REQUIRE(file);
        failed=fwrite(buffers[0].mapped,1,buffers[0].bytes+GUARD*2,file)!=buffers[0].bytes+GUARD*2;
        if(fclose(file)) failed=1;
        REQUIRE(!failed);
    }
    printf("{\"profile\":\"" PROFILE "\",\"phase\":\"%s\",\"groups\":%u,\"safe_groups\":%u,\"diagnostic_groups\":%u,\"requested_width\":%u,\"actual_width\":%u,\"width_source\":\"pipeline_executable_properties\",\"pipeline_compiled\":true,\"gpu_executed\":%s,\"dispatches\":%u,\"output_checked\":%s,\"writtenness_checked\":%s,\"all_payload_words_marked\":%s,\"verified_writtenness_markers\":%u,\"guards_and_inputs_unchanged\":%s,\"full_payload_packed_word_count\":%u,\"full_payload_half_element_count\":%u,\"guarded_output_word_count\":%u,\"safe_mismatched_packed_words\":%u,\"diagnostic_mismatched_packed_words\":%u,\"fence_timeout_ns\":2000000000,\"device\":",
           argv[1],fixture.groups,fixture.safe_groups,fixture.groups-fixture.safe_groups,fixture.width,compiled.subgroupSize,
           dispatch?"true":"false",dispatch?1:0,dispatch?"true":"false",dispatch?"true":"false",dispatch?"true":"null",dispatch?fixture.groups*64:0,dispatch?(intact?"true":"false"):"null",
           fixture.groups*64,fixture.groups*128,fixture.counts[1],safe_mismatches,diagnostic_mismatches);
    json_string(props.properties.deviceName); printf(",\"driver\":"); json_string(driver.driverInfo); puts("}");
    if (fence) vkDestroyFence(device, fence, NULL);
    if (command_pool) vkDestroyCommandPool(device, command_pool, NULL);
    if (pool) vkDestroyDescriptorPool(device, pool, NULL);
    for (unsigned i = 0; i < 3; ++i) if (buffers[i].buffer) {
        vkUnmapMemory(device, buffers[i].memory); vkDestroyBuffer(device, buffers[i].buffer, NULL); vkFreeMemory(device, buffers[i].memory, NULL);
    }
    vkDestroyPipeline(device, pipeline, NULL); vkDestroyShaderModule(device, module, NULL);
    vkDestroyPipelineLayout(device, layout, NULL); vkDestroyDescriptorSetLayout(device, set_layout, NULL);
    vkDestroyDevice(device, NULL); vkDestroyInstance(instance, NULL);
    free(code); free(fixture.storage);
    return !intact || (dispatch && safe_mismatches) ? 1 : 0;
}
