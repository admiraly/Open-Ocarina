# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.mesh_warp32_transform import audit, checked_source, transform_assembly


def fixture():
    """Independently authored mesh control-flow and builtin fixture; no game data."""
    return '''
; Independently authored synthetic module.
OpCapability Shader
OpCapability MeshShadingEXT
OpCapability GroupNonUniform
OpCapability GroupNonUniformBallot
OpExtension "SPV_EXT_mesh_shader"
OpMemoryModel Logical GLSL450
OpEntryPoint MeshEXT %main "main" %local %lane %eq %lt %le %gt %ge
OpExecutionMode %main LocalSize 32 1 1
OpExecutionMode %main OutputVertices 3
OpExecutionMode %main OutputPrimitivesEXT 1
OpExecutionMode %main OutputTrianglesEXT
OpDecorate %local BuiltIn LocalInvocationId
OpDecorate %lane BuiltIn SubgroupLocalInvocationId
OpDecorate %eq BuiltIn SubgroupEqMask
OpDecorate %lt BuiltIn SubgroupLtMask
OpDecorate %le BuiltIn SubgroupLeMask
OpDecorate %gt BuiltIn SubgroupGtMask
OpDecorate %ge BuiltIn SubgroupGeMask
%void = OpTypeVoid
%bool = OpTypeBool
%uint = OpTypeInt 32 0
%vec3 = OpTypeVector %uint 3
%vec4 = OpTypeVector %uint 4
%p3 = OpTypePointer Input %vec3
%p4 = OpTypePointer Input %vec4
%pu = OpTypePointer Input %uint
%fn = OpTypeFunction %void
%zero = OpConstant %uint 0
%three = OpConstant %uint 3
%true = OpConstantTrue %bool
%local = OpVariable %p3 Input
%lane = OpVariable %pu Input
%eq = OpVariable %p4 Input
%lt = OpVariable %p4 Input
%le = OpVariable %p4 Input
%gt = OpVariable %p4 Input
%ge = OpVariable %p4 Input
%main = OpFunction %void None %fn
%entry = OpLabel
%original_lane = OpLoad %uint %lane
OpSelectionMerge %join None
OpBranchConditional %true %arm %join
%arm = OpLabel
OpBranch %join
%join = OpLabel
%ballot0 = OpGroupNonUniformBallot %vec4 %three %true
%word0 = OpCompositeExtract %uint %ballot0 0
%ballot1 = OpGroupNonUniformBallot %vec4 %three %true
%word1 = OpCompositeExtract %uint %ballot1 0
%mask = OpLoad %vec4 %lt
OpSetMeshOutputsEXT %zero %zero
OpReturn
OpFunctionEnd
'''


class MeshWarp32TransformTests(unittest.TestCase):
    def test_uniform_prefix_and_complete_inventory(self):
        report, _ = audit(fixture())
        self.assertTrue(report["all_entry_paths_reach_ballots"])
        self.assertEqual(report["prefix_blocks_excluding_ballot"], 2)
        self.assertEqual(report["reachable_subgroup_ops"], {"OpGroupNonUniformBallot": 2})
        self.assertEqual(report["subgroup_builtin_loads"], {"SubgroupLocalInvocationId": 1, "SubgroupLtMask": 1})
        self.assertFalse(report["lane_mapping_verified"])
        self.assertFalse(report["source_runtime_required_width_verified"])
        self.assertFalse(report["equivalence_proven"])
        self.assertFalse(report["runtime_execution_authorized"])

    def test_new_scratch_interface_and_uniform_barrier_contract(self):
        candidate, report = transform_assembly(fixture())
        self.assertNotIn("Subgroup", candidate)
        self.assertEqual(candidate.count("OpControlBarrier %ow32_c2 %ow32_c2 %ow32_sem"), 2)
        self.assertIn("%ow32_sem = OpConstant %uint 264", candidate)
        self.assertIn("%ow32_array = OpTypeArray %uint %ow32_c32", candidate)
        entry = next(line for line in candidate.splitlines() if line.startswith("OpEntryPoint"))
        self.assertIn("%ow32_scratch0 %ow32_scratch1", entry)
        self.assertIn("%ballot0 = OpCompositeConstruct %vec4", candidate)
        self.assertIn("%ballot1 = OpCompositeConstruct %vec4", candidate)
        self.assertEqual(report["added_workgroup_bytes"], 256)
        self.assertEqual(sum(line.startswith("%ow32_ballot") and " = OpBitwiseOr" in line
                             for line in candidate.splitlines()), 64)

    def test_all_mask_formulas_have_no_shift_by_32(self):
        source = fixture().replace("%mask = OpLoad %vec4 %lt", "\n".join(
            f"%mask_{mask} = OpLoad %vec4 %{mask}" for mask in ("eq", "lt", "le", "gt", "ge")))
        candidate, _ = transform_assembly(source)
        self.assertEqual(candidate.count("= OpCompositeConstruct %vec4 %ow32_builtin"), 5)
        for lane in range(32):
            eq = 1 << lane
            lt = eq - 1
            formulas = {"eq": eq, "lt": lt, "le": eq | lt,
                        "gt": (~(eq | lt)) & 0xffffffff, "ge": (~lt) & 0xffffffff}
            for name, predicate in (("eq", lambda i: i == lane), ("lt", lambda i: i < lane),
                                    ("le", lambda i: i <= lane), ("gt", lambda i: i > lane),
                                    ("ge", lambda i: i >= lane)):
                self.assertEqual(formulas[name], sum(1 << i for i in range(32) if predicate(i)))

    def test_unsupported_module_shapes_fail(self):
        source = fixture()
        changes = [("MeshEXT %main", "GLCompute %main"), ("LocalSize 32 1 1", "LocalSize 64 1 1"),
                   ("Logical GLSL450", "Logical VulkanKHR"),
                   ("SubgroupEqMask", "SubgroupSize"),
                   ("%three %true", "%zero %true"),
                   ("%three %true", "%three %zero"),
                   ("OpLoad %vec4 %lt", "OpCopyObject %p4 %lt"),
                   ("OpCapability Shader", "%ow32_reserved = OpTypeBool\nOpCapability Shader")]
        for before, after in changes:
            with self.subTest(before=before), self.assertRaises(ValueError):
                audit(source.replace(before, after))

    def test_early_exit_cycle_and_reentry_fail(self):
        source = fixture()
        changes = [("%arm = OpLabel\nOpBranch %join", "%arm = OpLabel\nOpReturn"),
                   ("%arm = OpLabel\nOpBranch %join", "%arm = OpLabel\nOpBranch %entry"),
                   ("OpSetMeshOutputsEXT %zero %zero\nOpReturn", "OpBranch %join")]
        for before, after in changes:
            with self.subTest(after=after), self.assertRaises(ValueError):
                audit(source.replace(before, after))

    def test_calls_before_or_between_ballots_fail(self):
        helper = "\n%helper = OpFunction %void None %fn\n%helper_entry = OpLabel\nOpReturn\nOpFunctionEnd\n"
        for insertion in ("OpSelectionMerge %join None", "%ballot1 = OpGroupNonUniformBallot"):
            source = fixture().replace(insertion, "%call = OpFunctionCall %void %helper\n" + insertion) + helper
            with self.assertRaisesRegex(ValueError, "call"):
                audit(source)

    def test_other_group_ops_and_extra_ballot_consumers_fail(self):
        for instruction in ("%shuffle = OpGroupNonUniformShuffle %uint %three %zero %zero",
                            "%extra = OpCompositeExtract %uint %ballot0 1"):
            with self.assertRaises(ValueError):
                audit(fixture().replace("%mask = OpLoad", instruction + "\n%mask = OpLoad"))

    def test_unexpected_source_digest_prevents_external_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.spv"
            path.write_bytes(b"synthetic module")
            with patch("tools.mesh_warp32_transform.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "digest"):
                    checked_source(path)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
