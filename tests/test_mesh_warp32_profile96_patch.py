# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

CHECKER = Path(__file__).resolve().parents[1] / 'patches/hookshot/check_mesh_warp32_profile96.py'
spec = importlib.util.spec_from_file_location('mesh_warp32_profile96_checker', CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
COMPILER = shutil.which('clang++') or shutil.which('g++')
TOOLS = all(shutil.which(t) for t in ('spirv-as','spirv-dis','spirv-val'))


class NativeProfile96PatchTests(unittest.TestCase):
    @unittest.skipUnless(COMPILER, 'C++20 compiler required')
    def test_production_target_default_identity_cache_and_device_limits(self):
        result = checker.check_header(COMPILER)
        self.assertTrue(result['strict_profile96_default_off'])
        self.assertTrue(result['both_target_identity_negatives'])
        self.assertTrue(result['per_target_shared_and_output_limits'])
        self.assertTrue(result['revision3_cache_and_pipeline_tags'])

    @unittest.skipUnless(COMPILER and TOOLS and Path('/usr/include/spirv/unified1/spirv.hpp').is_file(), 'C++20 and public SPIR-V tools/headers required')
    def test_two_authored_shapes_match_independent_python_word_id_goldens(self):
        result = checker.check_authored_goldens(COMPILER)
        self.assertTrue(result['two_authored_shapes_cpp_python_word_id_goldens'])
        self.assertTrue(result['authored_candidates_spirv_validated'])

    def test_no_shader_bytes_or_native_rewriter_edits(self):
        source = checker.header()
        for forbidden in ('vkCmd','vkCreate','std::vector<uint32_t>','0x07230203','OpGroup','system(','popen('):
            self.assertNotIn(forbidden, source)
        text = checker.PATCH.read_text()
        self.assertNotIn('diff --git a/gpu/vk/mesh_warp32_lowering.cpp', text)
        self.assertNotIn('diff --git a/gpu/vk/mesh_warp32_lowering.h', text)
        self.assertNotIn('diff --git a/gpu/vk/mesh_input_capture.h', text)
        self.assertNotIn('diff --git a/gpu/vk/mesh_input_profile96.h', text)

    def test_missing_registry_rejected(self):
        with patch.object(checker.FROZEN,'added_file',side_effect=ValueError('missing')):
            with self.assertRaisesRegex(ValueError,'missing'):
                checker.header()

    def test_frozen_overlay_dependencies_are_exact(self):
        for name, expected in checker.PINNED.items():
            self.assertEqual(checker.digest(checker.HERE/name), expected)
        self.assertEqual(len(checker.BASE),11)


if __name__=='__main__':unittest.main()
