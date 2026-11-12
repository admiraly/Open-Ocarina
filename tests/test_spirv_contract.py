from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.spirv_contract import analyze_assembly, main


PREFIX = '''
OpCapability Shader
OpCapability PhysicalStorageBufferAddresses
OpEntryPoint GLCompute %10 "main" %30 %31
OpDecorate %30 DescriptorSet 0
OpDecorate %30 Binding 6
OpDecorate %31 DescriptorSet 0
OpDecorate %31 Binding 6
OpMemberDecorate %40 2 Offset 48
%1 = OpTypeVoid
%2 = OpTypeInt 32 0
%3 = OpTypeInt 64 0
%4 = OpTypeFunction %1
%5 = OpTypePointer StorageBuffer %2
%6 = OpTypePointer PhysicalStorageBuffer %2
%7 = OpTypePointer PushConstant %40
%8 = OpTypePointer PushConstant %3
%9 = OpConstant %2 2
%30 = OpVariable %5 StorageBuffer
%31 = OpVariable %5 StorageBuffer
%40 = OpTypeStruct %2 %2 %3
%41 = OpVariable %7 PushConstant
'''


class SpirvContractTests(unittest.TestCase):
    def test_unreachable_physical_helper_is_not_reachable_use(self):
        report = analyze_assembly(PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
OpReturn
OpFunctionEnd
%20 = OpFunction %1 None %4
%21 = OpLabel
%22 = OpConvertUToPtr %6 %9
%23 = OpLoad %2 %22 Aligned 4
OpReturn
OpFunctionEnd
''')
        self.assertEqual(report["physical_pointer_instruction_count"], 1)
        self.assertEqual(report["reachable_physical_pointer_instruction_count"], 0)
        self.assertEqual(report["reachable_physical_memory_accesses"], {})
        self.assertFalse(report["race_freedom_proven"])
        self.assertFalse(report["resource_footprints_complete"])

    def test_callgraph_physical_load_and_push_address_root(self):
        report = analyze_assembly(PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
%12 = OpFunctionCall %1 %20
OpReturn
OpFunctionEnd
%20 = OpFunction %1 None %4
%21 = OpLabel
%22 = OpAccessChain %8 %41 %9
%23 = OpLoad %3 %22
%24 = OpConvertUToPtr %6 %23
%25 = OpLoad %2 %24 Aligned 4
OpReturn
OpFunctionEnd
''')
        self.assertEqual(report["reachable_functions"], ["%10", "%20"])
        self.assertEqual(report["reachable_physical_pointer_instruction_count"], 1)
        self.assertEqual(report["reachable_physical_memory_accesses"], {"OpLoad": 1})
        self.assertEqual(report["physical_pointer_push_constant_offsets"], [48])

    def test_descriptor_aliases_merge_identity_without_merging_memory_proof(self):
        report = analyze_assembly(PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
%12 = OpLoad %2 %30
OpStore %31 %12
OpReturn
OpFunctionEnd
''')
        self.assertEqual(len(report["descriptors"]), 1)
        binding = report["descriptors"][0]
        self.assertEqual(binding["variables"], ["%30", "%31"])
        self.assertEqual(binding["loads"], 1)
        self.assertEqual(binding["stores"], 1)
        self.assertEqual((binding["set"], binding["binding"]), (0, 6))

    def test_unknown_call_is_not_silently_accepted(self):
        report = analyze_assembly(PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
%12 = OpFunctionCall %1 %999
OpReturn
OpFunctionEnd
''')
        self.assertEqual(report["unresolved_calls"], ["%999"])
        self.assertFalse(report["race_freedom_proven"])

    def test_function_declaration_without_body_is_not_assumed_empty(self):
        report = analyze_assembly(PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
%12 = OpFunctionCall %1 %20
OpReturn
OpFunctionEnd
%20 = OpFunction %1 None %4
OpFunctionEnd
''')
        self.assertIn("missing-function-body:%20", report["unresolved_calls"])

    def test_atomic_and_copy_accesses_remain_explicit_unknowns(self):
        report = analyze_assembly(PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
OpCopyMemory %30 %31
%12 = OpAtomicIAdd %2 %30 %9 %9 %9
OpReturn
OpFunctionEnd
''')
        self.assertEqual(report["reachable_atomic_instruction_count"], 1)
        self.assertEqual(report["unresolved_access_origins"]["unsupported-memory-operation:OpCopyMemory"], 1)
        self.assertEqual(report["unresolved_access_origins"]["unsupported-memory-operation:OpAtomicIAdd"], 1)

    def test_truncated_or_duplicate_disassembly_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            analyze_assembly(PREFIX + "%10 = OpFunction %1 None %4")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            analyze_assembly(PREFIX + "%30 = OpVariable %5 StorageBuffer")

    def test_wrong_entry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "entry point"):
            analyze_assembly(PREFIX, "absent")

    def test_tools_analyze_pinned_snapshot_if_original_changes(self):
        assembly = PREFIX + '''
%10 = OpFunction %1 None %4
%11 = OpLabel
OpReturn
OpFunctionEnd
'''
        binary = bytes.fromhex("03022307") + bytes(16)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.spv"
            source.write_bytes(binary)
            seen = []

            def external(arguments, **kwargs):
                snapshot = Path(arguments[-1])
                self.assertNotEqual(snapshot, source)
                self.assertEqual(snapshot.read_bytes(), binary)
                seen.append(snapshot)
                source.write_bytes(bytes(len(binary)))
                return subprocess.CompletedProcess(arguments, 0, assembly if arguments[0] == "spirv-dis" else "", "")

            output = io.StringIO()
            with patch("sys.argv", ["spirv_contract.py", "--spirv", str(source), "--expected-sha256", hashlib.sha256(binary).hexdigest()]), \
                    patch("tools.spirv_contract.shutil.which", side_effect=lambda name: name), \
                    patch("tools.spirv_contract.subprocess.run", side_effect=external), redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0], seen[1])
            self.assertFalse(seen[0].exists())
            self.assertEqual(json.loads(output.getvalue())["spirv_sha256"], hashlib.sha256(binary).hexdigest())


if __name__ == "__main__":
    unittest.main()
