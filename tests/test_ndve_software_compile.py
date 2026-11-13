# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real helper's feature query/enable chain without a Vulkan device."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

MOCK = r'''
#include <vulkan/vulkan.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
VKAPI_ATTR VkResult VKAPI_CALL vkCreateInstance(const VkInstanceCreateInfo *i,
    const VkAllocationCallbacks *a, VkInstance *out) {
    (void)i; (void)a; *out=(VkInstance)(uintptr_t)1; return VK_SUCCESS;
}
VKAPI_ATTR VkResult VKAPI_CALL vkEnumeratePhysicalDevices(VkInstance i,
    uint32_t *n, VkPhysicalDevice *out) {
    (void)i; *n=1; if(out)out[0]=(VkPhysicalDevice)(uintptr_t)2; return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceProperties2(VkPhysicalDevice d,
    VkPhysicalDeviceProperties2 *p) {
    (void)d; p->properties.vendorID=0x8086; p->properties.deviceID=0x56a0;
    p->properties.apiVersion=VK_API_VERSION_1_3;
    VkPhysicalDeviceDriverProperties *driver=p->pNext;
    VkPhysicalDeviceSubgroupSizeControlProperties *sub=driver->pNext;
    sub->minSubgroupSize=8; sub->maxSubgroupSize=32;
    sub->requiredSubgroupSizeStages=VK_SHADER_STAGE_COMPUTE_BIT;
    VkPhysicalDeviceFloatControlsProperties *f=sub->pNext;
    f->shaderSignedZeroInfNanPreserveFloat16=getenv("MOCK_NO_PRESERVE16")?VK_FALSE:VK_TRUE;
    f->shaderSignedZeroInfNanPreserveFloat32=VK_TRUE;
    VkPhysicalDevicePushDescriptorPropertiesKHR *push=f->pNext;
    push->maxPushDescriptors=32;
}
VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateDeviceExtensionProperties(VkPhysicalDevice d,
    const char *layer, uint32_t *n, VkExtensionProperties *p) {
    (void)d; (void)layer; *n=2;
    if(p){strcpy(p[0].extensionName,VK_EXT_ROBUSTNESS_2_EXTENSION_NAME);
          strcpy(p[1].extensionName,VK_KHR_PUSH_DESCRIPTOR_EXTENSION_NAME);}
    return VK_SUCCESS;
}
VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFeatures2(VkPhysicalDevice d,
    VkPhysicalDeviceFeatures2 *f) {
    (void)d;
    f->features.robustBufferAccess=VK_TRUE; f->features.shaderInt16=VK_TRUE;
    f->features.shaderInt64=VK_TRUE;
    f->features.shaderStorageBufferArrayDynamicIndexing=VK_TRUE;
    f->features.shaderImageGatherExtended=VK_TRUE;
    VkPhysicalDeviceVulkan12Features *f12=f->pNext;
    f12->shaderFloat16=getenv("MOCK_NO_F16")?VK_FALSE:VK_TRUE;
    f12->uniformBufferStandardLayout=VK_TRUE; f12->bufferDeviceAddress=VK_TRUE;
    VkPhysicalDeviceVulkan13Features *f13=f12->pNext;
    f13->subgroupSizeControl=VK_TRUE; f13->computeFullSubgroups=VK_TRUE;
    VkPhysicalDeviceRobustness2FeaturesEXT *r=f13->pNext;
    r->robustBufferAccess2=VK_TRUE; r->nullDescriptor=VK_TRUE;
}
VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceQueueFamilyProperties(VkPhysicalDevice d,
    uint32_t *n, VkQueueFamilyProperties *p) {
    (void)d; *n=1; if(p)p[0].queueFlags=VK_QUEUE_COMPUTE_BIT;
}
VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice(VkPhysicalDevice d,
    const VkDeviceCreateInfo *i, const VkAllocationCallbacks *a, VkDevice *out) {
    (void)d; (void)a; (void)out;
    const VkPhysicalDeviceFeatures2 *f=i->pNext;
    const VkPhysicalDeviceVulkan12Features *f12=f->pNext;
    const VkPhysicalDeviceVulkan13Features *f13=f12->pNext;
    if(!f12->shaderFloat16 || !f12->bufferDeviceAddress ||
       !f12->uniformBufferStandardLayout || !f13->subgroupSizeControl ||
       !f13->computeFullSubgroups) { fputs("bad-enable-chain\n",stderr); return VK_ERROR_FEATURE_NOT_PRESENT; }
    fputs("feature-enable-chain-checked-no-device-created\n",stderr);
    return VK_ERROR_INITIALIZATION_FAILED;
}
'''


@unittest.skipUnless(shutil.which("cc") and shutil.which("pkg-config"), "C/Vulkan development tools unavailable")
class SoftwareCompileFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "vulkan"], capture_output=True, text=True)
        if flags.returncode:
            raise unittest.SkipTest("Vulkan development files unavailable")
        import shlex
        cls.directory = tempfile.TemporaryDirectory(prefix="ndve-feature-chain-")
        cls.root = Path(cls.directory.name)
        cls.helper = cls.root / "probe"
        subprocess.run(["cc", "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                        str(ROOT / "src/probes/ndve_software_compile.c"), *shlex.split(flags.stdout),
                        "-o", str(cls.helper)], check=True, capture_output=True, timeout=30)
        (cls.root / "mock.c").write_text(MOCK)
        cls.mock = cls.root / "mock.so"
        subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                        str(cls.root / "mock.c"), "-o", str(cls.mock)], check=True, capture_output=True, timeout=30)
        # Only the framing is read before the mocked device-create denial.
        (cls.root / "input.spv").write_bytes(struct.pack("<5I", 0x07230203, 0x10600, 0, 1, 0))
        (cls.root / "layout.txt").write_text("56 1\n0 0 6 1\n")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_probe(self, **options):
        env = os.environ.copy()
        for name in ("MOCK_NO_F16", "MOCK_NO_PRESERVE16"):
            env.pop(name, None)
        env.update(options, LD_PRELOAD=str(self.mock))
        return subprocess.run([str(self.helper), str(self.root / "input.spv"),
                               str(self.root / "layout.txt"), "pinned32", "0"],
                              env=env, capture_output=True, text=True, timeout=10)

    def test_supported_float16_is_enabled_in_actual_device_chain(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 1)
        self.assertIn("feature-enable-chain-checked-no-device-created", result.stderr)
        self.assertNotIn("bad-enable-chain", result.stderr)

    def test_missing_float16_denies_before_device_creation(self):
        result = self.run_probe(MOCK_NO_F16="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("required target shader feature is unavailable", result.stderr)
        self.assertNotIn("feature-enable-chain-checked", result.stderr)

    def test_missing_float16_preservation_denies_before_device_creation(self):
        result = self.run_probe(MOCK_NO_PRESERVE16="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("required target shader feature is unavailable", result.stderr)
        self.assertNotIn("feature-enable-chain-checked", result.stderr)


if __name__ == "__main__":
    unittest.main()
