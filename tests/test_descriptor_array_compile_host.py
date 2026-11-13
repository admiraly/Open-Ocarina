# SPDX-License-Identifier: GPL-3.0-or-later
from argparse import Namespace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.descriptor_array_compile_host import (
    BASE_HOST_SHA, CONTROLLED_OPTIONS, LAYOUT, NATIVE_SAFE, ROOT, adapted_host,
    digest, driver_identity, native_safe_environment, prepare, reflect_profile,
)


def instruction(opcode, *args):
    return [((len(args) + 1) << 16) | opcode, *args]


def profile_fixture(pointer=True):
    """Metadata-only reflection fixture; not claimed to be a valid shader."""
    words = [0x07230203, 0x10500, 0, 1000, 0]
    def emit(opcode, *args):
        words.extend(instruction(opcode, *args))
    for capability in (1, 11, 5301, 5308, 5347) + ((4441,) if pointer else ()):
        emit(17, capability)
    emit(14, 5348, 1)
    emit(15, 5, 900, *struct.unpack('<2I', b'main\0\0\0\0'))
    emit(16, 900, 17, 64, 1, 1)
    emit(21, 1, 32, 0)
    emit(21, 2, 64, 0)
    emit(23, 3, 1, 2)
    emit(23, 4, 1, 4)
    emit(43, 1, 5, 2)
    emit(30, 6, 2, 2, 2, 1, 1)
    for index, offset in enumerate((0, 8, 16, 24, 28)):
        emit(72, 6, index, 35, offset)
    next_id = 10
    pointers = []
    for binding, elements in ((0, ((1, 4), (3, 8), (4, 16))),
                              (1, ((1, 4), (3, 8), (4, 16))),
                              (2, ((6, 32),)), (3, ((2, 8),)), (4, ((4, 16),))):
        for element_type, stride in elements:
            array, block, descriptor_array, pointer_type, variable = range(next_id, next_id + 5)
            next_id += 5
            emit(29, array, element_type)
            emit(71, array, 6, stride)
            emit(30, block, array)
            emit(71, block, 2)
            emit(72, block, 0, 35, 0)
            if binding < 2:
                emit(28, descriptor_array, block, 5)
            emit(32, pointer_type, 12, descriptor_array if binding < 2 else block)
            emit(71, variable, 33, binding)
            emit(71, variable, 34, 0)
            emit(59, pointer_type, variable, 12)
    emit(30, 100, 4, 4, 2, 1, 1)
    for index, offset in enumerate((0, 16, 32, 40, 44)):
        emit(72, 100, index, 35, offset)
    emit(32, 101, 9, 100)
    emit(59, 101, 102, 9)
    emit(32, 103, 1, 4)
    emit(59, 103, 104, 1)
    for index in range(6):
        emit(68, 1, 110 + index, 120, 0)
    for index, value_type in enumerate((1, 3, 4)):
        pointer_type = 130 + index
        emit(32, pointer_type, 12, value_type)
        pointers.append(pointer_type)
    if pointer:
        for index, pointer_type in enumerate(pointers):
            emit(54, pointer_type, 140 + index, 0, 150)
    return struct.pack(f'<{len(words)}I', *words)


def replace_instruction(data, opcode, transform):
    words = list(struct.unpack(f'<{len(data)//4}I', data))
    at = 5
    while at < len(words):
        count, candidate = words[at] >> 16, words[at] & 65535
        if candidate == opcode:
            replacement = transform(words[at+1:at+count])
            if replacement is not None:
                words[at+1:at+count] = replacement
                return struct.pack(f'<{len(words)}I', *words)
        at += count
    raise AssertionError('Expected mutable fixture instruction')


class DescriptorArrayHostTests(unittest.TestCase):
    def test_exact_profiles_merge_aliases_without_multiplying_descriptors(self):
        for pointer, variant in ((True, 'pointer'), (False, 'value')):
            row = reflect_profile(profile_fixture(pointer), variant)
            self.assertEqual(len(row['descriptor_alias_declarations']), 9)
            self.assertEqual([binding['count'] for binding in row['bindings']], [2, 2, 1, 1, 1])
            self.assertEqual(row['local_size'], [64, 1, 1])
            self.assertEqual(row['push_constant_bytes'], 48)
            self.assertEqual(row['shared_memory_bytes'], 0)
            self.assertEqual(row['storage_pointer_return_functions'], 3 if pointer else 0)

    def test_wrong_stage_local_size_descriptor_count_and_alias_stride_fail(self):
        fixture = profile_fixture()
        mutations = [
            replace_instruction(fixture, 15, lambda args: [0, *args[1:]]),
            replace_instruction(fixture, 16, lambda args: [*args[:2], 32, 1, 1]),
            replace_instruction(fixture, 43, lambda args: [*args[:2], 3]),
            replace_instruction(fixture, 71, lambda args: [*args[:2], 32] if args[1] == 6 and len(args) == 3 else None),
            replace_instruction(fixture, 59, lambda args: [*args[:2], 4]),
            replace_instruction(fixture, 72, lambda args: [*args[:3], 56] if args[0] == 100 and args[1] == 4 else None),
        ]
        for data in mutations:
            with self.assertRaises(ValueError):
                reflect_profile(data, 'pointer')

    def test_optional_capability_and_variant_mismatches_fail(self):
        with self.assertRaises(ValueError):
            reflect_profile(profile_fixture(), 'value')
        with self.assertRaises(ValueError):
            reflect_profile(profile_fixture(False), 'pointer')
        with self.assertRaises(ValueError):
            reflect_profile(b'bad', 'pointer')
        changed = replace_instruction(profile_fixture(), 17, lambda args: [4442] if args == [4441] else None)
        with self.assertRaises(ValueError):
            reflect_profile(changed, 'pointer')

    def test_native_safe_clears_all_thirteen_options_and_leaves_max_absent(self):
        env, policy = native_safe_environment({**{key: 'unsafe' for key in CONTROLLED_OPTIONS}, 'other': 'retained'})
        self.assertEqual(env, {**NATIVE_SAFE, 'other': 'retained'})
        self.assertIsNone(policy['controlled_options']['HS_CS32_MAX_INSTS'])
        self.assertEqual(len(policy['controlled_options']), 13)

    def test_derived_native_host_requires_both_features_and_contains_no_execution_calls(self):
        original = (ROOT / 'tools/vulkan_compile_probe.c').read_text()
        self.assertEqual(digest(original.encode()), BASE_HOST_SHA)
        derived = adapted_host(original)
        for expected in ('!supported11.variablePointersStorageBuffer', '!supported12.shaderStorageBufferArrayNonUniformIndexing',
                         '.variablePointersStorageBuffer = VK_TRUE', '.shaderStorageBufferArrayNonUniformIndexing = VK_TRUE',
                         'VK_PIPELINE_CREATE_CAPTURE_INTERNAL_REPRESENTATIONS_BIT_KHR', 'vkGetPipelineExecutableInternalRepresentationsKHR',
                         'alarm(25)', 'native_safe_policy()'):
            self.assertIn(expected, derived)
        for forbidden in ('vkCmd', 'vkQueueSubmit', 'vkDeviceWaitIdle', 'vkQueueWaitIdle', 'vkGetDeviceQueue'):
            self.assertNotIn(forbidden, derived)
        self.assertLess(derived.index('if (validate_only) { puts('), derived.index('CHECK(vkCreateInstance'))
        self.assertEqual((ROOT / 'tools/vulkan_compile_probe.c').read_text(), original)
        with self.assertRaises(ValueError):
            adapted_host(original + '\n')

    def test_wrong_icd_stops_before_build_or_native_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wrong.json'
            path.write_text('{}')
            with self.assertRaises(ValueError):
                driver_identity(path)
        output = ROOT / 'local' / 'array-host-test-never-created'
        args = Namespace(output=output, driver=Path('not-used'))
        with patch('tools.descriptor_array_compile_host.driver_identity', side_effect=ValueError('wrong driver')), \
             patch('tools.descriptor_array_compile_host.subprocess.run') as run:
            with self.assertRaises(ValueError):
                prepare(args)
            run.assert_not_called()
        self.assertFalse(output.exists())

    def test_fixed_host_layout_is_exact(self):
        self.assertEqual(LAYOUT.splitlines(), ['48 5', '0 0 7 2', '0 1 7 2', '0 2 7 1', '0 3 7 1', '0 4 7 1'])


if __name__ == '__main__':
    unittest.main()
