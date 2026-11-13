# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import ndve_stage_contract as contract


# Independently authored declarations/accesses for the layout inspector. Binary
# reflection has its own synthetic suite; no private stage or SDK is required.
ASSEMBLY = '''
OpCapability Shader
OpEntryPoint GLCompute %100 "main" %20 %21 %22 %23
OpExecutionMode %100 LocalSize 32 1 1
OpDecorate %7 ArrayStride 4
OpDecorate %8 ArrayStride 8
OpDecorate %20 DescriptorSet 0
OpDecorate %20 Binding 0
OpDecorate %21 DescriptorSet 0
OpDecorate %21 Binding 1
OpDecorate %23 DescriptorSet 0
OpDecorate %23 Binding 5
OpMemberDecorate %9 0 Offset 0
OpMemberDecorate %10 0 Offset 0
OpMemberDecorate %11 0 Offset 0
OpMemberDecorate %11 1 Offset 48
%1 = OpTypeVoid
%2 = OpTypeInt 32 0
%3 = OpTypeInt 64 0
%4 = OpTypeFloat 32
%5 = OpTypeVector %2 4
%6 = OpConstant %2 4
%7 = OpTypeArray %2 %6
%8 = OpTypeRuntimeArray %3
%9 = OpTypeStruct %7
%10 = OpTypeStruct %8
%11 = OpTypeStruct %5 %3
%12 = OpTypePointer Uniform %9
%13 = OpTypePointer StorageBuffer %10
%14 = OpTypePointer PushConstant %11
%15 = OpTypeImage %4 2D 0 0 0 1 Unknown
%16 = OpTypeSampledImage %15
%17 = OpTypePointer UniformConstant %16
%18 = OpTypeFunction %1
%20 = OpVariable %12 Uniform
%21 = OpVariable %13 StorageBuffer
%22 = OpVariable %14 PushConstant
%23 = OpVariable %17 UniformConstant
%100 = OpFunction %1 None %18
%101 = OpLabel
%102 = OpLoad %3 %21
OpStore %21 %102
OpReturn
OpFunctionEnd
'''


class NDVEStageContractTests(unittest.TestCase):
    def test_layout_and_writable_access_are_explicit(self):
        reflected = {"workgroup": [32, 1, 1], "push_constant_bytes": 56}
        with patch.object(contract, "reflect", return_value=reflected):
            result = contract.inspect_module(b"synthetic reflection input", ASSEMBLY)
        layouts = {row["binding"]: row for row in result["declared_descriptor_layouts"]}
        self.assertEqual(layouts[0]["declared_block_bytes_before_runtime_tail"], 16)
        self.assertEqual(layouts[1]["members"][0]["array_stride"], 8)
        self.assertEqual(layouts[1]["declared_block_bytes_before_runtime_tail"], 0)
        self.assertEqual(layouts[5]["image_type"], {"scalar_type": "OpTypeFloat", "scalar_bits": 32,
            "dimension": "2D", "depth": 0, "arrayed": 0, "multisampled": 0, "sampled": 1, "format": "Unknown"})
        self.assertEqual(result["push_members"], [{"member": 0, "offset": 0, "declared_bytes": 16},
                                                {"member": 1, "offset": 48, "declared_bytes": 8}])
        writable = next(row for row in result["resource_summary"]["descriptors"] if row["binding"] == 1)
        self.assertEqual((writable["loads"], writable["stores"]), (1, 1))
        self.assertFalse(result["resource_summary"]["race_freedom_proven"])
        self.assertFalse(result["resource_summary"]["resource_footprints_complete"])

    def test_unknown_image_declaration_is_not_guessed(self):
        with patch.object(contract, "reflect", return_value={}):
            with self.assertRaisesRegex(ValueError, "image declaration"):
                contract.inspect_module(b"synthetic", ASSEMBLY.replace("%16 = OpTypeSampledImage %15", "%16 = OpTypeSampler"))

    def make_export(self, root):
        exported = root / "export"
        module = exported / "translated/spv" / (contract.PROGRAM + ".5.spv")
        module.parent.mkdir(parents=True)
        module.write_bytes(b"independently authored digest fixture")
        queries = [{"kind": "texture_integer", "answer": 0, "resolved": 0, "calls": 1}]
        query_path = exported / "environment-queries.json"
        query_path.write_text(json.dumps(queries))
        report = {"identity": {"program": contract.PROGRAM, "native_control_code_sha256": contract.NATIVE_SHA},
            "wrapper_sha256": contract.file_sha(contract.ROOT / "tools/ndve_stage_export.py"),
            "conditional_on_offline_query_configuration": True,
            "environment_queries_sha256": contract.file_sha(query_path), "unresolved_compiler_queries": queries,
            "module_sha256": contract.file_sha(module)}
        (exported / "export-report.json").write_text(json.dumps(report))
        return exported, module, report

    def test_changed_module_is_rejected_before_external_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported, _, _ = self.make_export(root)
            with patch.object(contract.subprocess, "run") as child:
                with self.assertRaisesRegex(ValueError, "digest differs"):
                    contract.audit(exported, root / "output")
                child.assert_not_called()

    def test_changed_environment_audit_or_export_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported, _, report = self.make_export(root)
            for key, value in (("wrapper_sha256", "0" * 64), ("environment_queries_sha256", "0" * 64),
                               ("unresolved_compiler_queries", []), ("conditional_on_offline_query_configuration", False)):
                changed = dict(report, **{key: value})
                (exported / "export-report.json").write_text(json.dumps(changed))
                with self.subTest(key=key), patch.object(contract.subprocess, "run") as child:
                    with self.assertRaises(ValueError):
                        contract.audit(exported, root / "output")
                    child.assert_not_called()

    def test_disassembler_and_inspector_share_hash_pinned_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported, module, report = self.make_export(root)
            original = module.read_bytes()
            candidate = {"static_main_call_sites": 72, "extended_instruction_counts": {"Fma": 8},
                         "instruction_counts": {"OpLoopMerge": 1}}
            inspected = {"main_callees": [candidate], "reflection": {"workgroup": [32, 1, 1]}}
            def disassemble(arguments, **kwargs):
                snapshot = Path(arguments[-1])
                self.assertNotEqual(snapshot, module)
                self.assertEqual(snapshot.read_bytes(), original)
                module.write_bytes(b"concurrent changed source")
                return subprocess.CompletedProcess(arguments, 0, "synthetic disassembly", "")
            with patch.object(contract, "MODULE_SHA", hashlib.sha256(original).hexdigest()), \
                    patch.object(contract.subprocess, "run", side_effect=disassemble), \
                    patch.object(contract, "inspect_module", return_value=copy.deepcopy(inspected)) as inspector:
                result = contract.audit(exported, root / "output")
            inspector.assert_called_once_with(original, "synthetic disassembly")
            self.assertEqual(result["compiler_queries"], report["unresolved_compiler_queries"])
            self.assertFalse(result["Vulkan_calls"])
            self.assertFalse(result["live_compiler_variant_equality"])
            self.assertFalse(result["complete_replay_inputs"])


if __name__ == "__main__":
    unittest.main()
