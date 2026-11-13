/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Exact synthetic fixture executor. CPU validation precedes vkCreateInstance. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <openssl/evp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>

#define REQUIRE(x) do { if (!(x)) { fprintf(stderr,"Fixture rejected at line %d\n",__LINE__); return 2; } } while (0)
#define CHECK(x) do { VkResult status=(x); if (status!=VK_SUCCESS) { fprintf(stderr,"%s failed: %d\n",#x,status); return 3; } } while (0)
#define N 10
#define G 64
#define LANES 64
#define ROWS 37
#define S UINT64_C(0x1000010000)
#define T UINT64_C(0x1000020000)
#define U UINT64_C(0x1000030000)
#define F UINT64_C(0x1000040000)

static const char *names[N]={"shared","short","zero_prefix","mapped_elsewhere","physical","ordinary_miss","records","queries","results","page_table"};
static const uint32_t payload_sizes[N]={8192,10,32,8192,8192,4096,352,5632,37952,528};
static const uint32_t seeds[6]={0x11000000,0x22000000,0x33000000,0x44000000,0x55000000,0x66000000};
static const uint32_t root_alloc[7]={1,0,0,2,6,7,8};
static const uint32_t ranges[7]={10,8192,8192,32,352,5632,37952};
static const uint32_t slots[7]={16,17,32,33,48,64,65};
static const uint32_t targets[7]={3,3,3,3,5,4,4};
static const uint32_t page_offsets[7]={0,4096,0,4096,0,0,4096};
static const char *modules[2]={"aa9816b41e458151632308a64d9df58aee0fcc5df287b70f47e6b067b1c76531","bf1c2e5a1379ced5b8c068b0b47498384322255bf2cab235c31ce9cc0f027cdc"};
static const char *driver_sha="e5a1305ea3b423a18fccbb945aaedd32f8add303926fcc39276ee01032cd456f";
static const char *icd_sha="14f4eda1b6fd6cbf07d61edeeffdaaede166f4c09d165fec9f661e9f90556ad3";

typedef struct Allocation { unsigned char *initial,*expected; VkBuffer buffer; VkDeviceMemory memory; void *mapped; VkDeviceAddress address; } Allocation;
static Allocation allocations[N];
static FILE *control;
static int bad;
static uint32_t u32(void) { unsigned char x[4]={0}; if(fread(x,1,4,control)!=4)bad=1; return (uint32_t)x[0]|(uint32_t)x[1]<<8|(uint32_t)x[2]<<16|(uint32_t)x[3]<<24; }
static char *text(void) { uint32_t n=u32(); if(!n||n>4095){bad=1;return NULL;} char *p=calloc(n+1,1); if(!p||fread(p,1,n,control)!=n||memchr(p,0,n)){bad=1;free(p);return NULL;}return p; }
static void put32(void *p,uint32_t x){ memcpy(p,&x,4); }
static void put64(void *p,uint64_t x){ memcpy(p,&x,8); }

static int hash_bytes(const void *data,size_t size,char output[65]) {
    unsigned char bytes[32];unsigned count=0;
    EVP_MD_CTX *context=EVP_MD_CTX_new();
    if(!context)return 0;
    int ok=EVP_DigestInit_ex(context,EVP_sha256(),NULL)&&EVP_DigestUpdate(context,data,size)&&EVP_DigestFinal_ex(context,bytes,&count);
    EVP_MD_CTX_free(context);if(!ok||count!=32)return 0;
    for(unsigned i=0;i<32;++i)snprintf(output+2*i,3,"%02x",bytes[i]);
    return 1;
}
static int hash_file(const char *path,char output[65]) {
    FILE *f=fopen(path,"rb");if(!f)return 0;
    EVP_MD_CTX *context=EVP_MD_CTX_new();if(!context){fclose(f);return 0;}
    int ok=EVP_DigestInit_ex(context,EVP_sha256(),NULL);unsigned char block[65536],bytes[32];size_t count;unsigned length=0;
    while(ok&&(count=fread(block,1,sizeof(block),f)))ok=EVP_DigestUpdate(context,block,count);
    ok=ok&&!ferror(f)&&EVP_DigestFinal_ex(context,bytes,&length);fclose(f);EVP_MD_CTX_free(context);
    if(!ok||length!=32)return 0;
    for(unsigned i=0;i<32;++i)snprintf(output+2*i,3,"%02x",bytes[i]);
    return 1;
}
static void *load_file(const char *path,size_t size) {
    FILE *f=fopen(path,"rb");if(!f)return NULL;void *data=malloc(size?size:1);
    if(!data||fread(data,1,size,f)!=size||fgetc(f)!=EOF){free(data);data=NULL;}fclose(f);return data;
}
static void *artifact(size_t size) {
    char *path=text();unsigned char expected[32];if(!path||fread(expected,1,32,control)!=32){bad=1;return NULL;}
    void *data=load_file(path,size);free(path);if(!data){bad=1;return NULL;}char actual[65],wanted[65];
    for(unsigned i=0;i<32;++i)snprintf(wanted+2*i,3,"%02x",expected[i]);
    if(!hash_bytes(data,size,actual)||strcmp(actual,wanted)){bad=1;free(data);return NULL;}return data;
}

static void seeded(unsigned char *out,unsigned index) {
    for(unsigned byte=0;byte<payload_sizes[index];++byte) {
        uint32_t x=seeds[index]^((byte/4)*UINT32_C(0x01010101));out[byte]=(unsigned char)(x>>(8*(byte%4)));
    }
}
static void interval(unsigned char *p,uint64_t low,uint64_t high,uint64_t base,uint32_t binding,uint32_t element) {
    put64(p,low);put64(p+8,high);put64(p+16,base);put32(p+24,binding);put32(p+28,element);
}
static void expected_metadata(unsigned char *records,unsigned char *queries,unsigned char push[48],unsigned follow) {
    unsigned row=0;
    for(unsigned width=4;width<=16;width*=2){
        if(width<16)interval(records+32*row++,S,S+8,S,0,0);
        interval(records+32*row++,S+(width<16?8:0),S+8192,S,1,0);
        interval(records+32*row++,T,T+8192,T,0,1);
        interval(records+32*row++,U,U+5,U,1,1);
    }
    for(unsigned lane=0;lane<LANES;++lane){
        uint64_t offset=256+64*lane,primary=(lane%2?T:S),alias=(lane%2?S:T);
        uint64_t q[11]={primary+offset,primary+offset+8,primary+offset+16,alias+offset,F+offset,S+4,U+4,S+4092,F+4092,T+128,128};
        memcpy(queries+lane*88,q,sizeof(q));
    }
    uint32_t p[12]={0,4,8,0,4,4,3,0,0,0,follow,0};memcpy(push,p,48);
}
static void emit(unsigned char *out,unsigned *row,const unsigned char *data,unsigned count) {
    memcpy(out+16*(*row),data,count);++*row;
}
static void value_words(unsigned char out[16],uint32_t kind,unsigned width,unsigned lane,unsigned follow) {
    memset(out,0,16);for(unsigned component=0;component<width/4;++component)put32(out+4*component,kind|((width/4)<<24)|(component<<20)|(follow<<16)|lane);
}
static int exact_cpu_graph(unsigned follow,const unsigned char push[48]) {
    unsigned char records[352],queries[5632],wanted_push[48];expected_metadata(records,queries,wanted_push,follow);
    REQUIRE(!memcmp(push,wanted_push,48));
    for(unsigned i=0;i<N;++i){
        unsigned size=payload_sizes[i]+2*G;unsigned char *initial=calloc(size,1),*expected=calloc(size,1);REQUIRE(initial&&expected);
        memset(initial,0xBD,G);memset(initial+G+payload_sizes[i],0xDB,G);
        if(i<6)seeded(initial+G,i);
        else if(i==6)memcpy(initial+G,records,sizeof(records));
        else if(i==7)memcpy(initial+G,queries,sizeof(queries));
        else if(i==8)memset(initial+G,0xE5,payload_sizes[i]);
        memcpy(expected,initial,size);
        REQUIRE(!memcmp(initial,allocations[i].initial,size));free(initial);
        if(i==0||i==4){
            if(i==0||follow)for(unsigned lane=0;lane<LANES;++lane)for(unsigned width=4,delta=0;width<=16;width*=2,delta=(width==8?8:16)){
                unsigned char v[16];value_words(v,i==0?0xA0000000:0xB0000000,width,lane,follow);memcpy(expected+G+256+64*lane+delta,v,width);
            }
        } else if(i==8){
            memset(expected+G,0,LANES*ROWS*16);
            for(unsigned lane=0;lane<LANES;++lane){
                unsigned row=lane*ROWS;unsigned char *out=expected+G;uint32_t header[4]={0xDA010000|lane,lane,follow,ROWS};emit(out,&row,(unsigned char*)header,16);
                uint32_t lengths[12]={2,1,0,2048,1024,512,8,4,2,2048,1024,512};for(unsigned j=0;j<3;++j)emit(out,&row,(unsigned char*)(lengths+4*j),16);
                unsigned base=256+64*lane;
                for(unsigned width=4,delta=0;width<=16;width*=2,delta=(width==8?8:16))emit(out,&row,allocations[0].initial+G+base+delta,width);
                for(unsigned group=0;group<3;++group)for(unsigned width=4;width<=16;width*=2){unsigned char v[16];value_words(v,0xA0000000,width,lane,follow);emit(out,&row,v,width);}
                for(unsigned width=4,delta=0;width<=16;width*=2,delta=(width==8?8:16))emit(out,&row,allocations[4].initial+G+base+delta,width);
                for(unsigned width=4,delta=0;width<=16;width*=2,delta=(width==8?8:16)){
                    unsigned char v[16];value_words(v,0xB0000000,width,lane,follow);emit(out,&row,follow?v:allocations[4].initial+G+base+delta,width);
                }
                emit(out,&row,allocations[1].initial+G+4,4);emit(out,&row,allocations[1].initial+G,8);emit(out,&row,allocations[0].initial+G,16);
                unsigned char pair[8];memcpy(pair,allocations[2].initial+G+4,4);memcpy(pair+4,allocations[5].initial+G+8,4);emit(out,&row,pair,8);emit(out,&row,allocations[2].initial+G,16);
                for(unsigned width=4;width<=16;width*=2)emit(out,&row,allocations[0].initial+G+128,width);
                for(unsigned width=4;width<=16;width*=2)emit(out,&row,allocations[0].initial+G+(4092/width)*width,width);
                emit(out,&row,allocations[4].initial+G+4092,4);row+=3;
                REQUIRE(row==(lane+1)*ROWS);
            }
        }
        REQUIRE(!memcmp(expected,allocations[i].expected,size));free(expected);
    }
    return 0;
}

static int native_policy(const char *icd) {
    const char *keys[]={"HS_CS32_MAX_INSTS","HS_SPILL_BATCH","HS_PUSH_SHADOW","shader_spilling_rate","INTEL_DEBUG","HS_SPILL_PCT","HS_RA_DEBUG","HS_FORCE_SIMD8","HS_LARGE_GRF","HS_SUBGROUP8","HS_LARGE_GRF_BIT","RX_VK_WARP64","RX_VK_NOOPT"};
    for(unsigned i=0;i<13;++i){const char *v=getenv(keys[i]);if(i>=1&&i<=3){if(!v||strcmp(v,"1"))return 0;}else if(v)return 0;}
    const char *selected=getenv("VK_DRIVER_FILES"),*cache=getenv("MESA_SHADER_CACHE_DISABLE"),*cache_path=getenv("MESA_SHADER_CACHE_DIR");
    return selected&&!strcmp(selected,icd)&&cache&&!strcmp(cache,"true")&&cache_path&&*cache_path&&!getenv("VK_ICD_FILENAMES")&&!getenv("VK_ADD_DRIVER_FILES")&&!getenv("VK_LOADER_DRIVERS_SELECT")&&!getenv("VK_LOADER_DRIVERS_DISABLE");
}
static int make_buffer(VkPhysicalDevice physical,VkDevice device,unsigned index) {
    Allocation *a=allocations+index;VkDeviceSize size=payload_sizes[index]+2*G;
    VkBufferCreateInfo info={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=size,.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,.sharingMode=VK_SHARING_MODE_EXCLUSIVE};
    CHECK(vkCreateBuffer(device,&info,NULL,&a->buffer));VkMemoryRequirements needs;vkGetBufferMemoryRequirements(device,a->buffer,&needs);
    VkPhysicalDeviceMemoryProperties memory;vkGetPhysicalDeviceMemoryProperties(physical,&memory);unsigned type=UINT32_MAX;
    for(unsigned i=0;i<memory.memoryTypeCount;++i)if((needs.memoryTypeBits&(1u<<i))&&(memory.memoryTypes[i].propertyFlags&(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))==(VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)){type=i;break;}
    REQUIRE(type!=UINT32_MAX);VkMemoryAllocateFlagsInfo flags={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO,.flags=VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT};
    VkMemoryAllocateInfo allocation={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.pNext=&flags,.allocationSize=needs.size,.memoryTypeIndex=type};
    CHECK(vkAllocateMemory(device,&allocation,NULL,&a->memory));CHECK(vkBindBufferMemory(device,a->buffer,a->memory,0));CHECK(vkMapMemory(device,a->memory,0,VK_WHOLE_SIZE,0,&a->mapped));
    VkBufferDeviceAddressInfo address={.sType=VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO,.buffer=a->buffer};a->address=vkGetBufferDeviceAddress(device,&address);
    REQUIRE(a->address&&a->address%16==0&&a->address<=UINT64_MAX-size);memcpy(a->mapped,a->initial,size);return 0;
}
static int output_file(const char *directory,const char *name,const char *suffix,const void *data,size_t size) {
    char path[8192];int count=snprintf(path,sizeof(path),"%s/%s-%s.bin",directory,name,suffix);REQUIRE(count>0&&(size_t)count<sizeof(path));
    FILE *f=fopen(path,"wb");REQUIRE(f&&fwrite(data,1,size,f)==size&&fclose(f)==0);return 0;
}

int main(int argc,char **argv) {
    REQUIRE(argc==3||argc==4);int execute=!strcmp(argv[1],"--execute");REQUIRE(execute?argc==4:argc==3&&!strcmp(argv[1],"--validate"));
    uint32_t endian=1;REQUIRE(*(unsigned char*)&endian==1);
    control=fopen(argv[2],"rb");REQUIRE(control);char magic[8];REQUIRE(fread(magic,1,8,control)==8&&!memcmp(magic,"DARUN1\0\0",8));
    unsigned variant=u32(),follow=u32();REQUIRE(variant<2&&follow<2&&u32()==N);
    char *icd=text(),*library=text();REQUIRE(icd&&library);char hashed[65];REQUIRE(hash_file(icd,hashed)&&!strcmp(hashed,icd_sha));REQUIRE(hash_file(library,hashed)&&!strcmp(hashed,driver_sha));
    unsigned code_size=u32();REQUIRE(code_size>=20&&code_size<=65536&&code_size%4==0);uint32_t *code=artifact(code_size);REQUIRE(code&&!bad&&hash_bytes(code,code_size,hashed)&&!strcmp(hashed,modules[variant]));
    unsigned char *push=artifact(48);REQUIRE(push);
    for(unsigned i=0;i<N;++i){REQUIRE(u32()==payload_sizes[i]+2*G);allocations[i].initial=artifact(payload_sizes[i]+2*G);allocations[i].expected=artifact(payload_sizes[i]+2*G);REQUIRE(allocations[i].initial&&allocations[i].expected);}
    REQUIRE(u32()==7);
    for(unsigned i=0;i<7;++i){REQUIRE(u32()==root_alloc[i]&&u32()==G&&u32()==ranges[i]);}
    REQUIRE(u32()==7);
    for(unsigned i=0;i<7;++i){REQUIRE(u32()==G+slots[i]*8&&u32()==targets[i]&&u32()==G+page_offsets[i]);}
    REQUIRE(!bad&&fgetc(control)==EOF);fclose(control);int validation=exact_cpu_graph(follow,push);if(validation)return validation;
    if(!execute){puts("{\"native_cpu_validated\":true,\"Vulkan_instance_created\":false,\"allocations\":10,\"bytes\":74458,\"module_restricted\":true}");return 0;}
    REQUIRE(native_policy(icd));char used[8192];REQUIRE(snprintf(used,sizeof(used),"%s.used",argv[2])<(int)sizeof(used));int claim=open(used,O_CREAT|O_EXCL|O_WRONLY,0600);REQUIRE(claim>=0);close(claim);alarm(25);
    VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="Open-Ocarina typed-array fixture",.apiVersion=VK_API_VERSION_1_3};
    VkInstanceCreateInfo instance_info={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};VkInstance instance;CHECK(vkCreateInstance(&instance_info,NULL,&instance));
    uint32_t device_count=0;CHECK(vkEnumeratePhysicalDevices(instance,&device_count,NULL));REQUIRE(device_count&&device_count<=16);VkPhysicalDevice devices[16];CHECK(vkEnumeratePhysicalDevices(instance,&device_count,devices));VkPhysicalDevice physical=devices[0];
    VkPhysicalDeviceRobustness2PropertiesEXT robust_properties={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_PROPERTIES_EXT};
    VkPhysicalDevicePushDescriptorPropertiesKHR push_properties={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PUSH_DESCRIPTOR_PROPERTIES_KHR,.pNext=&robust_properties};
    VkPhysicalDeviceSubgroupSizeControlProperties subgroup={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SUBGROUP_SIZE_CONTROL_PROPERTIES,.pNext=&push_properties};
    VkPhysicalDeviceProperties2 properties={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,.pNext=&subgroup};vkGetPhysicalDeviceProperties2(physical,&properties);
    VkPhysicalDeviceLimits limits=properties.properties.limits;
    REQUIRE(properties.properties.apiVersion>=VK_API_VERSION_1_3&&properties.properties.vendorID==0x8086&&properties.properties.deviceID==0x56a0);
    REQUIRE(limits.minStorageBufferOffsetAlignment&&G%limits.minStorageBufferOffsetAlignment==0&&limits.maxStorageBufferRange>=37952&&limits.maxPushConstantsSize>=48&&limits.maxPerStageDescriptorStorageBuffers>=7&&limits.maxDescriptorSetStorageBuffers>=7&&limits.maxComputeWorkGroupInvocations>=64&&limits.maxComputeWorkGroupSize[0]>=64);
    REQUIRE(subgroup.minSubgroupSize<=32&&subgroup.maxSubgroupSize>=32&&(subgroup.requiredSubgroupSizeStages&VK_SHADER_STAGE_COMPUTE_BIT)&&push_properties.maxPushDescriptors>=7);
    uint32_t extension_count=0;CHECK(vkEnumerateDeviceExtensionProperties(physical,NULL,&extension_count,NULL));REQUIRE(extension_count<=1024);VkExtensionProperties extensions[1024];CHECK(vkEnumerateDeviceExtensionProperties(physical,NULL,&extension_count,extensions));
    int robust=0,push_extension=0,executable_extension=0;for(unsigned i=0;i<extension_count;++i){if(!strcmp(extensions[i].extensionName,VK_EXT_ROBUSTNESS_2_EXTENSION_NAME))robust=1;if(!strcmp(extensions[i].extensionName,VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME))push_extension=1;if(!strcmp(extensions[i].extensionName,VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME))executable_extension=1;}REQUIRE(robust&&push_extension&&executable_extension);
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    VkPhysicalDeviceRobustness2FeaturesEXT robust_features={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,.pNext=&executable};
    VkPhysicalDeviceVulkan13Features f13={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,.pNext=&robust_features};
    VkPhysicalDeviceVulkan12Features f12={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,.pNext=&f13};
    VkPhysicalDeviceVulkan11Features f11={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,.pNext=&f12};
    VkPhysicalDeviceFeatures2 features={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,.pNext=&f11};vkGetPhysicalDeviceFeatures2(physical,&features);
    REQUIRE(f11.variablePointersStorageBuffer&&f12.shaderStorageBufferArrayNonUniformIndexing&&f12.bufferDeviceAddress&&f13.subgroupSizeControl&&f13.computeFullSubgroups&&robust_features.robustBufferAccess2&&features.features.robustBufferAccess&&features.features.shaderInt64&&features.features.shaderStorageBufferArrayDynamicIndexing&&executable.pipelineExecutableInfo);
    executable.pipelineExecutableInfo=VK_TRUE;robust_features.robustBufferAccess2=VK_TRUE;robust_features.robustImageAccess2=VK_FALSE;robust_features.nullDescriptor=VK_FALSE;
    VkPhysicalDeviceVulkan13Features e13={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,.pNext=&robust_features,.subgroupSizeControl=VK_TRUE,.computeFullSubgroups=VK_TRUE};
    VkPhysicalDeviceVulkan12Features e12={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,.pNext=&e13,.bufferDeviceAddress=VK_TRUE,.shaderStorageBufferArrayNonUniformIndexing=VK_TRUE};
    VkPhysicalDeviceVulkan11Features e11={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,.pNext=&e12,.variablePointersStorageBuffer=VK_TRUE};
    VkPhysicalDeviceFeatures2 enabled={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,.pNext=&e11,.features={.robustBufferAccess=VK_TRUE,.shaderInt64=VK_TRUE,.shaderStorageBufferArrayDynamicIndexing=VK_TRUE}};
    uint32_t queue_count=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&queue_count,NULL);REQUIRE(queue_count&&queue_count<=64);VkQueueFamilyProperties queues[64];vkGetPhysicalDeviceQueueFamilyProperties(physical,&queue_count,queues);unsigned queue_family=UINT32_MAX;for(unsigned i=0;i<queue_count;++i)if(queues[i].queueFlags&VK_QUEUE_COMPUTE_BIT){queue_family=i;break;}REQUIRE(queue_family!=UINT32_MAX);
    float priority=1;VkDeviceQueueCreateInfo queue_info={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=queue_family,.queueCount=1,.pQueuePriorities=&priority};
    const char *extension_names[]={VK_EXT_ROBUSTNESS_2_EXTENSION_NAME,VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME,VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME};
    VkDeviceCreateInfo device_info={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.pNext=&enabled,.queueCreateInfoCount=1,.pQueueCreateInfos=&queue_info,.enabledExtensionCount=3,.ppEnabledExtensionNames=extension_names};VkDevice device;CHECK(vkCreateDevice(physical,&device_info,NULL,&device));
    VkQueue queue;vkGetDeviceQueue(device,queue_family,0,&queue);
    for(unsigned i=0;i<N;++i){int status=make_buffer(physical,device,i);if(status)return status;}
    for(unsigned i=0;i<7;++i){uint64_t value=allocations[targets[i]].address+G+page_offsets[i];REQUIRE(G+page_offsets[i]+4096<=G+payload_sizes[targets[i]]);put64((unsigned char*)allocations[9].mapped+G+8*slots[i],value);put64(allocations[9].initial+G+8*slots[i],value);put64(allocations[9].expected+G+8*slots[i],value);}
    put64(push+32,allocations[9].address+G);
    VkMappedMemoryRange mapped_ranges[N];for(unsigned i=0;i<N;++i)mapped_ranges[i]=(VkMappedMemoryRange){.sType=VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,.memory=allocations[i].memory,.offset=0,.size=VK_WHOLE_SIZE};CHECK(vkFlushMappedMemoryRanges(device,N,mapped_ranges));
    char upload_path[8192];REQUIRE(snprintf(upload_path,sizeof(upload_path),"%s/upload.json",argv[3])<(int)sizeof(upload_path));FILE *upload=fopen(upload_path,"wb");REQUIRE(upload);char push_hash[65];REQUIRE(hash_bytes(push,48,push_hash));fprintf(upload,"{\"push_sha256\":\"%s\",\"allocations\":[",push_hash);
    for(unsigned i=0;i<N;++i){unsigned size=payload_sizes[i]+2*G;char a[65],b[65];REQUIRE(hash_bytes(allocations[i].mapped,size,a)&&hash_bytes(allocations[i].expected,size,b));if(i)fputc(',',upload);fprintf(upload,"{\"name\":\"%s\",\"address\":%llu,\"bytes\":%u,\"uploaded_sha256\":\"%s\",\"expected_sha256\":\"%s\"}",names[i],(unsigned long long)allocations[i].address,size,a,b);if(output_file(argv[3],names[i],"uploaded",allocations[i].mapped,size)||output_file(argv[3],names[i],"expected-relocated",allocations[i].expected,size))return 2;}
    fprintf(upload,"]}\n");REQUIRE(fclose(upload)==0);if(output_file(argv[3],"push","uploaded",push,48))return 2;
    VkDescriptorSetLayoutBinding bindings[5];for(unsigned i=0;i<5;++i)bindings[i]=(VkDescriptorSetLayoutBinding){.binding=i,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=i<2?2:1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo set_info={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,.flags=VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR,.bindingCount=5,.pBindings=bindings};VkDescriptorSetLayout set;CHECK(vkCreateDescriptorSetLayout(device,&set_info,NULL,&set));
    VkPushConstantRange push_range={VK_SHADER_STAGE_COMPUTE_BIT,0,48};VkPipelineLayoutCreateInfo layout_info={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=1,.pSetLayouts=&set,.pushConstantRangeCount=1,.pPushConstantRanges=&push_range};VkPipelineLayout layout;CHECK(vkCreatePipelineLayout(device,&layout_info,NULL,&layout));
    VkShaderModuleCreateInfo module_info={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=code_size,.pCode=code};VkShaderModule module;CHECK(vkCreateShaderModule(device,&module_info,NULL,&module));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo width={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO,.requiredSubgroupSize=32};
    VkComputePipelineCreateInfo pipeline_info={.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,.flags=VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR,.stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.pNext=&width,.flags=VK_PIPELINE_SHADER_STAGE_CREATE_REQUIRE_FULL_SUBGROUPS_BIT,.stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=module,.pName="main"},.layout=layout};VkPipeline pipeline;CHECK(vkCreateComputePipelines(device,VK_NULL_HANDLE,1,&pipeline_info,NULL,&pipeline));
    PFN_vkGetPipelineExecutablePropertiesKHR get_properties=(PFN_vkGetPipelineExecutablePropertiesKHR)vkGetDeviceProcAddr(device,"vkGetPipelineExecutablePropertiesKHR");REQUIRE(get_properties);VkPipelineInfoKHR query={.sType=VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR,.pipeline=pipeline};uint32_t count=0;CHECK(get_properties(device,&query,&count,NULL));REQUIRE(count==1);VkPipelineExecutablePropertiesKHR property={.sType=VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR};CHECK(get_properties(device,&query,&count,&property));REQUIRE(property.subgroupSize==32&&property.stages==VK_SHADER_STAGE_COMPUTE_BIT);
    VkDescriptorBufferInfo descriptor_info[7];for(unsigned i=0;i<7;++i)descriptor_info[i]=(VkDescriptorBufferInfo){allocations[root_alloc[i]].buffer,G,ranges[i]};REQUIRE(descriptor_info[1].buffer==descriptor_info[2].buffer&&descriptor_info[1].offset==descriptor_info[2].offset&&descriptor_info[1].range==descriptor_info[2].range);
    VkWriteDescriptorSet writes[5];for(unsigned i=0,at=0;i<5;++i){writes[i]=(VkWriteDescriptorSet){.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstBinding=i,.descriptorCount=i<2?2:1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=descriptor_info+at};at+=i<2?2:1;}
    PFN_vkCmdPushDescriptorSetKHR push_descriptors=(PFN_vkCmdPushDescriptorSetKHR)vkGetDeviceProcAddr(device,"vkCmdPushDescriptorSetKHR");REQUIRE(push_descriptors);
    VkCommandPoolCreateInfo pool_info={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.queueFamilyIndex=queue_family};VkCommandPool pool;CHECK(vkCreateCommandPool(device,&pool_info,NULL,&pool));VkCommandBufferAllocateInfo command_info={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=pool,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};VkCommandBuffer command;CHECK(vkAllocateCommandBuffers(device,&command_info,&command));VkCommandBufferBeginInfo begin={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};CHECK(vkBeginCommandBuffer(command,&begin));
    VkMemoryBarrier before={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_HOST_WRITE_BIT,.dstAccessMask=VK_ACCESS_SHADER_READ_BIT|VK_ACCESS_SHADER_WRITE_BIT};vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_HOST_BIT,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&before,0,NULL,0,NULL);
    vkCmdBindPipeline(command,VK_PIPELINE_BIND_POINT_COMPUTE,pipeline);push_descriptors(command,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,5,writes);vkCmdPushConstants(command,layout,VK_SHADER_STAGE_COMPUTE_BIT,0,48,push);vkCmdDispatch(command,1,1,1);
    VkMemoryBarrier after={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT};vkCmdPipelineBarrier(command,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&after,0,NULL,0,NULL);CHECK(vkEndCommandBuffer(command));
    VkFenceCreateInfo fence_info={.sType=VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};VkFence fence;CHECK(vkCreateFence(device,&fence_info,NULL,&fence));VkSubmitInfo submit={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&command};VkResult submitted=vkQueueSubmit(queue,1,&submit,fence);if(submitted!=VK_SUCCESS){fprintf(stderr,"Submission failed; no idle wait\n");_Exit(3);}VkResult waited=vkWaitForFences(device,1,&fence,VK_TRUE,UINT64_C(2000000000));if(waited!=VK_SUCCESS){fprintf(stderr,"Fence failed: %d; no idle wait\n",waited);fflush(stderr);_Exit(3);}
    CHECK(vkInvalidateMappedMemoryRanges(device,N,mapped_ranges));int match=1;printf("{\"schema\":1,\"dispatches\":1,\"actual_subgroup_size\":32,\"actual_stage_flags\":32,\"both_pointer_features_enabled\":true,\"enabled_features\":{\"variablePointersStorageBuffer\":true,\"shaderStorageBufferArrayNonUniformIndexing\":true,\"shaderInt64\":true,\"bufferDeviceAddress\":true,\"robustBufferAccess\":true,\"robustBufferAccess2\":true,\"subgroupSizeControl\":true,\"computeFullSubgroups\":true},\"follow_stores\":%s,\"allocations\":[",follow?"true":"false");
    for(unsigned i=0;i<N;++i){unsigned size=payload_sizes[i]+2*G;uint32_t difference=UINT32_MAX;for(unsigned j=0;j<size;++j)if(((unsigned char*)allocations[i].mapped)[j]!=allocations[i].expected[j]){difference=j;match=0;break;}if(output_file(argv[3],names[i],"actual",allocations[i].mapped,size))return 2;if(i)putchar(',');printf("{\"name\":\"%s\",\"address\":%llu,\"bytes\":%u,\"first_difference\":",names[i],(unsigned long long)allocations[i].address,size);if(difference==UINT32_MAX)printf("null");else printf("%u",difference);putchar('}');}
    printf("],\"all_bytes_match\":%s,\"compared_allocations\":10,\"compared_bytes\":74458}\n",match?"true":"false");
    vkDestroyFence(device,fence,NULL);vkDestroyCommandPool(device,pool,NULL);vkDestroyPipeline(device,pipeline,NULL);vkDestroyShaderModule(device,module,NULL);vkDestroyPipelineLayout(device,layout,NULL);vkDestroyDescriptorSetLayout(device,set,NULL);
    for(unsigned i=0;i<N;++i){vkUnmapMemory(device,allocations[i].memory);vkDestroyBuffer(device,allocations[i].buffer,NULL);vkFreeMemory(device,allocations[i].memory,NULL);}vkDestroyDevice(device,NULL);vkDestroyInstance(instance,NULL);return match?0:4;
}
