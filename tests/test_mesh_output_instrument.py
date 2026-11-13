from collections import Counter
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import mesh_output_instrument as instrument


def fixture_with_payload():
    source = instrument.synthetic_fixture_assembly()
    lines = source.splitlines()
    index = next(index for index, line in enumerate(lines) if line.startswith("OpEntryPoint"))
    lines[index] += " %payload"
    index = lines.index("%main = OpFunction %void None %fn")
    lines[index:index] = ["%payloadarray = OpTypeArray %u32 %k32",
                          "%payloadptr = OpTypePointer TaskPayloadWorkgroupEXT %payloadarray",
                          "%payloadwordptr = OpTypePointer TaskPayloadWorkgroupEXT %u32",
                          "%payload = OpVariable %payloadptr TaskPayloadWorkgroupEXT"]
    index = lines.index("%gx = OpCompositeExtract %u32 %group 0")
    lines[index:index + 1] = ["%rawgx = OpCompositeExtract %u32 %group 0",
        "%payloadaccess = OpAccessChain %payloadwordptr %payload %k0",
        "%payloadload = OpLoad %u32 %payloadaccess", "%payloadcondition = OpIEqual %bool %k0 %k0",
        "%gx = OpSelect %u32 %payloadcondition %rawgx %payloadload"]
    return "\n".join(lines) + "\n"


class MeshOutputInstrumentTests(unittest.TestCase):
    def setUp(self):
        self.source = instrument.synthetic_fixture_assembly()

    def test_stage_outputs_collectives_and_invariant_survive(self):
        candidate, report = instrument.instrument_assembly(self.source, "synthetic-fixture")
        self.assertIn('OpEntryPoint MeshEXT %main "main"', candidate)
        self.assertIn("OpDecorate %position Invariant", candidate)
        before = Counter(line for line in self.source.splitlines() if line.startswith("OpStore ")
                         or line.startswith("OpSetMeshOutputsEXT") or line.startswith("OpControlBarrier"))
        after = Counter(candidate.splitlines())
        self.assertTrue(all(after[line] == count for line, count in before.items()))
        self.assertEqual(report["original_output_store_sites"], 168)
        self.assertEqual(report["original_barriers"], 2)
        self.assertFalse(report["equivalence_proven"])
        self.assertFalse(report["runtime_execution_authorized"])

    def test_exact_payload_cleanup_preserves_selected_group_value(self):
        candidate, report = instrument.instrument_assembly(fixture_with_payload(), "synthetic-fixture")
        self.assertTrue(report["dead_payload_cleanup"]["proved_dead"])
        self.assertIn("%gx = OpCopyObject %u32 %rawgx", candidate)
        self.assertNotIn("%payload = OpVariable", candidate)
        self.assertNotIn("%payloadload =", candidate)
        entry = next(line for line in candidate.splitlines() if line.startswith("OpEntryPoint"))
        self.assertNotIn("%payload", entry)

    def test_live_or_changed_payload_is_rejected(self):
        source = fixture_with_payload()
        with self.assertRaisesRegex(ValueError, "constant true"):
            instrument.instrument_assembly(source.replace("%payloadcondition = OpIEqual %bool %k0 %k0",
                                                         "%payloadcondition = OpIEqual %bool %k0 %k1"), "synthetic-fixture")
        with self.assertRaisesRegex(ValueError, "solely"):
            instrument.instrument_assembly(source.replace("%gx = OpSelect", "%extra = OpIAdd %u32 %payloadload %k1\n%gx = OpSelect"),
                                           "synthetic-fixture")
        with self.assertRaisesRegex(ValueError, "index"):
            instrument.instrument_assembly(source.replace("%payloadwordptr %payload %k0", "%payloadwordptr %payload %k1"),
                                           "synthetic-fixture")

    def test_output_reads_and_pointer_escapes_are_rejected(self):
        marker = "OpStore %p_f0_0_0 %f_f0_0_0"
        for injected in ("%illegal = OpLoad %f32 %p_f0_0_0", "%illegal = OpCopyObject %fptr %p_f0_0_0"):
            with self.assertRaisesRegex(ValueError, "pointer read/escape"):
                instrument.instrument_assembly(self.source.replace(marker, marker + "\n" + injected), "synthetic-fixture")

    def test_changed_component_coverage_and_vertex_index_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            instrument.instrument_assembly(self.source.replace("%fptr %generic0 %vertex0 %k3",
                                                               "%fptr %generic0 %vertex0 %k2"), "synthetic-fixture")
        with self.assertRaisesRegex(ValueError, "lane-plus-block"):
            instrument.instrument_assembly(self.source.replace("%vertex0 = OpIAdd %u32 %x %k0",
                                                               "%vertex0 = OpIAdd %u32 %x %k1"), "synthetic-fixture")

    def test_primitive_writes_must_keep_count_guard(self):
        with self.assertRaisesRegex(ValueError, "guarded"):
            instrument.instrument_assembly(self.source.replace("%vertex0 %primitive_count", "%vertex0 %k128"),
                                           "synthetic-fixture")

    def test_unsupported_builtin_and_workgroup_pointer_escape_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "builtin"):
            instrument.instrument_assembly(self.source.replace("%wg BuiltIn WorkgroupId", "%wg BuiltIn NumWorkgroups"),
                                           "synthetic-fixture")
        with self.assertRaisesRegex(ValueError, "WorkgroupId pointer"):
            instrument.instrument_assembly(self.source.replace("%group = OpLoad", "%escaped = OpCopyObject %iptr %wg\n%group = OpLoad"),
                                           "synthetic-fixture")

    def test_input_write_is_rejected(self):
        source = self.source.replace("%main = OpFunction", "%sptr = OpTypePointer StorageBuffer %u32\n%main = OpFunction")
        source = source.replace("%entry = OpLabel", "%entry = OpLabel\n%bad = OpAccessChain %sptr %ssbo0 %k0 %k0\nOpStore %bad %k1")
        with self.assertRaisesRegex(ValueError, "side effects"):
            instrument.instrument_assembly(source, "synthetic-fixture")

    def test_barrier_count_and_set_output_order_are_checked(self):
        with self.assertRaisesRegex(ValueError, "barrier inventory"):
            instrument.instrument_assembly(self.source.replace("OpControlBarrier %k2 %k2 %k264\n", "", 1), "synthetic-fixture")
        with self.assertRaisesRegex(ValueError, "barrier after"):
            instrument.instrument_assembly(self.source.replace("OpSetMeshOutputsEXT %k128 %primitive_count",
                "OpSetMeshOutputsEXT %k128 %primitive_count\nOpControlBarrier %k2 %k2 %k264").replace(
                    "OpControlBarrier %k2 %k2 %k264\n", "", 1), "synthetic-fixture")

    def test_extra_return_and_control_flow_cycle_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "return"):
            instrument.instrument_assembly(self.source.replace("OpSetMeshOutputsEXT", "OpReturn\nOpSetMeshOutputsEXT"), "synthetic-fixture")
        with self.assertRaisesRegex(ValueError, "cycle"):
            instrument.instrument_assembly(self.source.replace("OpBranch %merge0", "OpBranch %write0"), "synthetic-fixture")

    def test_digest_refusal_precedes_external_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "changed.spv"
            source.write_bytes(bytes.fromhex("03022307") + bytes(16))
            with patch.object(instrument, "_tool") as tool:
                with self.assertRaisesRegex(ValueError, "digest"):
                    instrument.prepare(source, Path(directory) / "out")
                tool.assert_not_called()
            self.assertFalse((Path(directory) / "out").exists())

    def test_private_output_is_required_before_fixture_tools(self):
        with patch.object(instrument, "_tool") as tool:
            with self.assertRaises(ValueError):
                instrument.prepare_fixture(instrument.ROOT / "research" / "not-private")
            tool.assert_not_called()

    def test_initial_capture_guards_and_marker_ranges(self):
        data = instrument.initial_capture(2)
        self.assertEqual(len(data), instrument.RECORD_BYTES * 2)
        words = struct.unpack(f"<{len(data) // 4}I", data)
        for group in range(2):
            base = group * instrument.RECORD_WORDS
            self.assertEqual(words[base:base + 16], (instrument.INNER_GUARD,) * 16)
            self.assertEqual(words[base + 11216:base + 11232], (instrument.INNER_GUARD,) * 16)
            self.assertEqual(words[base + 5712:base + 11216], (0,) * 5504)
            self.assertEqual(words[base + 16], instrument.VALUE_SENTINEL)

    def test_fixture_golden_includes_nonzero_origin_and_position_z_overwrite(self):
        data = instrument.fixture_expected_capture((3, 2, 1), (2, 1, 1))
        for group in range(2):
            base = group * instrument.RECORD_BYTES
            tag = 3 + group + 257 * 2 + 65537
            counts = struct.unpack_from("<64I", data, base + 64)
            self.assertEqual(counts, (128, tag % 129) * 32)
            self.assertEqual(struct.unpack_from("<f", data, base + 320 + 8)[0], float(tag + 1002))
            self.assertEqual(struct.unpack_from("<I", data, base + 22848)[0], 1)
            self.assertEqual(struct.unpack_from("<I", data, base + 20800 + 12)[0], instrument.VALUE_SENTINEL)

    def test_empty_primitive_case_stays_untouched(self):
        data = instrument.fixture_expected_capture((0, 0, 0), (1, 1, 1))
        self.assertEqual(struct.unpack_from("<64I", data, 64), (128, 0) * 32)
        self.assertEqual(data[20800:22848], struct.pack("<I", instrument.VALUE_SENTINEL) * 512)
        self.assertEqual(data[22848 + 5120 * 4:44864], bytes(384 * 4))

    def test_bad_window_is_refused(self):
        for origin, groups in (((0, 0, 0), (33, 1, 1)), ((-1, 0, 0), (1, 1, 1)),
                               ((0, 0, 0), (0, 1, 1)), ((0xFFFFFFFF, 0, 0), (2, 1, 1)),
                               ((True, 0, 0), (1, 1, 1))):
            with self.subTest(origin=origin, groups=groups), self.assertRaises(ValueError):
                instrument.fixture_expected_capture(origin, groups)

    @unittest.skipUnless(all(shutil.which(name) for name in ("spirv-as", "spirv-dis", "spirv-val")), "optional SPIRV-Tools")
    def test_authored_fixture_assembles_and_instruments_without_device_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            report = instrument.prepare_fixture(Path(directory) / "fixture")
            self.assertEqual(report["source_kind"], "synthetic-fixture")
            self.assertTrue(report["structural_validation_passed"])
            self.assertEqual(report["record_layout"]["stride_bytes"], 44928)
            self.assertFalse(report["runtime_execution_authorized"])


if __name__ == "__main__":
    unittest.main()
