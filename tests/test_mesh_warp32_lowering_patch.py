# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
import shutil
import unittest

CHECKER = Path(__file__).resolve().parents[1] / 'patches/hookshot/check_mesh_warp32_lowering.py'
spec = importlib.util.spec_from_file_location('mesh_warp32_lowering_checker', CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
COMPILER = shutil.which('clang++') or shutil.which('g++')
SPIRV_HEADERS = Path('/usr/include/spirv/unified1/spirv.hpp').is_file()


class MeshWarp32LoweringPatchTests(unittest.TestCase):
    @unittest.skipUnless(COMPILER and SPIRV_HEADERS, 'External CPU check needs C++20 and public SPIRV-Headers')
    def test_production_core_acceptance_and_malformed_refusals(self):
        result = checker.check_helper(COMPILER)
        self.assertTrue(result['synthetic_positive_lowering'])
        self.assertTrue(result['malformed_known_opcodes_fail_closed'])
        self.assertGreaterEqual(result['negative_cases'], 11)

    @unittest.skipUnless(COMPILER, 'External CPU policy mock check needs C++20')
    def test_production_device_policy_and_stage_policy(self):
        result = checker.check_policy(COMPILER)
        self.assertTrue(result['production_device_stage_and_compiler_policy_mocks_checked'])
        self.assertTrue(result['mock_only_no_vulkan_link_or_device_calls'])

    def test_missing_authored_source_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            checker.added_file('gpu/vk/absent.cpp')

    def test_core_contains_no_gpu_execution_or_external_tool_path(self):
        source = checker.added_file('gpu/vk/mesh_warp32_lowering.cpp')
        for forbidden in ('vkCmd', 'vkQueueSubmit', 'vkGetDeviceQueue', 'system(', 'popen('):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
