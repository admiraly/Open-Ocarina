# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import struct
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from tools.compute_interval_lowering import (
    U32, U64, TARGET, RECORD, Assembly, Emitter, Row, View, artifact, audit_lookup_shape, binary_identity,
    build_partitions, certify_partitions, frozen_views, interval_identity,
    legacy_identity, pack_records, route, source_interval,
    verify_preservation,
)


def authored_helper_fixture(width):
    """Independent tiny typed-root fixture; no instructions from a game module."""
    result_type = "%7" if width == 4 else "%10"
    element_pointer = "%54" if width == 4 else "%79"
    annotations = ["OpDecorate %2011 ArrayStride " + str(width), "OpDecorate %2012 Block",
                   "OpMemberDecorate %2012 0 Offset 0"]
    for index, variable in enumerate(("%55", "%56", "%57", "%58", "%59", "%63"), 2):
        annotations += [f"OpDecorate {variable} DescriptorSet 0", f"OpDecorate {variable} Binding {index}"]
    globals_text = f"""%1 = OpTypeVoid
%2 = OpTypeBool
%7 = OpTypeInt 32 0
%23 = OpTypeInt 64 0
%10 = OpTypeVector %7 4
%26 = OpConstant %7 0
%60 = OpConstant %7 3
%54 = OpTypePointer StorageBuffer %7
%79 = OpTypePointer StorageBuffer %10
%110 = OpTypePointer Function %7
%106 = OpTypeFunction {result_type} %23
%1207 = OpTypeFunction %1
%2011 = OpTypeRuntimeArray {result_type}
%2012 = OpTypeStruct %2011
%2013 = OpTypePointer StorageBuffer %2012
%2014 = OpTypeArray %2012 %60
%2015 = OpTypePointer StorageBuffer %2014
%2016 = OpConstant %23 0
%2017 = OpConstantComposite %10 %26 %26 %26 %26
%55 = OpVariable %2013 StorageBuffer
%56 = OpVariable %2013 StorageBuffer
%57 = OpVariable %2013 StorageBuffer
%58 = OpVariable %2013 StorageBuffer
%59 = OpVariable %2013 StorageBuffer
%63 = OpVariable %2015 StorageBuffer
"""
    module = Assembly(globals_text)
    emitter = Emitter(module); emitter.layout()
    helper = {"function": "%107", "parameter": "%108", "entry": "%109", "width": width,
              "return_type": result_type, "function_header": f"%107 = OpFunction {result_type} DontInline %106",
              "parameter_line": "%108 = OpFunctionParameter %23", "miss_label": "%247", "miss_line": 0, "end_line": 3,
              "roots": [{"variable": variable, "element_pointer_type": element_pointer} for variable in ("%55", "%56", "%57", "%58", "%59", "%63")]}
    original = SimpleNamespace(lines=["%247 = OpLabel", "OpReturnValue " + ("%26" if width == 4 else "%2017"), "OpFunctionEnd"])
    body = emitter.helper(original, helper)
    text = "\n".join(["OpCapability Shader", "OpCapability Int64", "OpMemoryModel Logical GLSL450",
        'OpEntryPoint GLCompute %1209 "main" %55 %56 %57 %58 %59 %63 ' + emitter.record_variable,
        "OpExecutionMode %1209 LocalSize 64 1 1", *annotations, *emitter.annotations,
        globals_text, *emitter.globals, *body, "%1209 = OpFunction %1 None %1207", "%1210 = OpLabel",
        f"%1211 = OpFunctionCall {result_type} %107 %2016", "OpReturn", "OpFunctionEnd"]) + "\n"
    return text, helper


class ComputeIntervalLoweringTests(unittest.TestCase):
    def test_authored_helper_shape_and_counter_mutation(self):
        for width in (4, 16):
            text, helper = authored_helper_fixture(width)
            rows = {4: [], 16: []}; rows[width] = [Row(0x1000, 0x1040, 0x1000, 2, 0)]
            proof = audit_lookup_shape(text, [helper], rows)
            self.assertEqual(proof[0]["initial_high"], 1)
            self.assertNotIn("OpPhi", text)
            self.assertNotIn("NonUniform", text)
            parsed = Assembly(text)
            body = parsed.functions["%107"]
            bounds = next(row for row in body if row[1] == "OpULessThan")
            lower_pointer = parsed.args(bounds[2][1], "OpLoad")[1]
            lower_store = next(row for row in body if row[1] == "OpStore" and row[2][0] == lower_pointer
                               and parsed.definitions[row[2][1]][1] == "OpIAdd")
            increment = parsed.definitions[lower_store[2][1]]
            changed = text.replace(parsed.lines[increment[3]], parsed.lines[increment[3]].replace("OpIAdd", "OpISub"))
            with self.assertRaises(ValueError):
                audit_lookup_shape(changed, [helper], rows)

    @unittest.skipUnless(shutil.which("spirv-as") and shutil.which("spirv-val"), "Optional SPIRV-Tools fixture check")
    def test_authored_scalar_and_vector_helpers_validate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for width in (4, 16):
                text, _ = authored_helper_fixture(width)
                source = directory / f"helper-{width}.spvasm"; binary = source.with_suffix(".spv")
                source.write_text(text)
                for command in (["spirv-as", "--target-env", "vulkan1.3", "--preserve-numeric-ids", str(source), "-o", str(binary)],
                                ["spirv-val", "--target-env", "vulkan1.3", str(binary)]):
                    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("spirv-as"), "Optional authored binary preservation check")
    def test_main_and_miss_instruction_mutations_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); text, helper = authored_helper_fixture(4)
            source = directory / "helper.spvasm"; binary = directory / "helper.spv"; source.write_text(text)
            subprocess.run(["spirv-as", "--target-env", "vulkan1.3", "--preserve-numeric-ids", str(source), "-o", str(binary)], check=True, capture_output=True)
            original = binary.read_bytes(); words = list(struct.unpack(f"<{len(original)//4}I", original))
            for target in ("main", "miss"):
                changed = words.copy(); at = 5; function = None; block = None; edited = False
                while at < len(changed):
                    count, opcode = changed[at] >> 16, changed[at] & 65535
                    if opcode == 54:
                        function = changed[at+2]
                    if opcode == 248:
                        block = changed[at+1]
                    if target == "main" and function == 1209 and opcode == 253:
                        changed[at] = (count << 16) | 255; edited = True; break
                    if target == "miss" and function == 107 and block == 247 and opcode == 254:
                        changed[at+1] = 60; edited = True; break
                    at += count
                self.assertTrue(edited)
                with self.subTest(target=target), self.assertRaises(ValueError):
                    verify_preservation(original, struct.pack(f"<{len(changed)}I", *changed), [helper])

    def test_source_order_selects_identity_not_equal_payload(self):
        views = [View(0x1000, 64, 64, 2, 0, "single"), View(0x1000, 64, 64, 3, 0, "single")]
        rows = build_partitions(views)
        self.assertEqual(interval_identity(rows[4], 0x1008), (2, 0, 0x1000))
        rows[4] = [Row(0x1000, 0x1040, 0x1000, 3, 0)]
        with self.assertRaisesRegex(ValueError, "identity"):
            certify_partitions(views, rows)

    def test_widths_get_independent_partitions(self):
        views = [View(0x1000, 64, 10, 2, 0, "single"), View(0x1000, 64, 64, 3, 0, "single")]
        rows = build_partitions(views)
        self.assertEqual(legacy_identity(views, 0x1004, 4), (2, 0, 0x1000))
        self.assertEqual(interval_identity(rows[16], 0x1004), (3, 0, 0x1000))
        certify_partitions(views, rows)

    def test_zero_size_ordinary_does_not_borrow_table_rule(self):
        single = View(0x1000, 0, 64, 2, 0, "single")
        table = View(0x1000, 0, 64, 7, 0, "table")
        self.assertEqual(source_interval(single, 16), (0x1000, 0x1000))
        self.assertEqual(source_interval(table, 16), (0x1000, 0x1040))
        self.assertEqual(legacy_identity([single, table], 0x1000, 16), (7, 0, 0x1000))

    def test_zero_size_unaligned_ordinary_keeps_prefix(self):
        single = View(0x1003, 0, 64, 2, 0, "single")
        rows = build_partitions([single])
        self.assertEqual(interval_identity(rows[16], 0x1002), (2, 0, 0x1000))
        self.assertIsNone(interval_identity(rows[16], 0x1003))
        certify_partitions([single], rows)

    def test_typed_vector_address_is_floored_not_exact_bytes(self):
        views = [View(0x1000, 64, 64, 2, 0, "single")]
        binding, element, base = legacy_identity(views, 0x100f, 16)
        self.assertEqual((binding, element, (((0x100f-base) & U32) >> 4) * 16), (2, 0, 0))

    def test_u32_length_shift_wrap_is_preserved(self):
        for width in (4, 16):
            view = View(0x1000, 0, (1 << 32) + 16, 7, 0, "table")
            self.assertEqual(source_interval(view, width), (0x1000, 0x1010))
            self.assertIsNone(legacy_identity([view], 0x1010, width))

    def test_u32_array_length_result_wrap_is_preserved(self):
        view = View(0x1000, 0, 1 << 34, 7, 0, "table")
        self.assertEqual(source_interval(view, 4), (0x1000, 0x1000))
        self.assertEqual(source_interval(view, 16), (0x1000, 0x1000))

    def test_u64_nvn_end_wrap_yields_empty_source_claim(self):
        view = View(U64 - 15, 32, 64, 2, 0, "single")
        self.assertIsNone(legacy_identity([view], U64 - 15, 4))
        self.assertEqual(build_partitions([view]), {4: [], 16: []})

    def test_null_table_descriptor_does_not_claim_stale_guest_words(self):
        view = View(0x1000, 64, 0, 7, 1, "table")
        rows = build_partitions([view])
        self.assertEqual(rows, {4: [], 16: []})
        certify_partitions([view], rows)

    def test_target_has_no_candidate_phase(self):
        with self.assertRaisesRegex(ValueError, "No candidate"):
            build_partitions([View(0x1000, 64, 64, 2, 0, "candidate")])

    def test_complete_domain_includes_gaps_and_uint64_tails(self):
        views = [View(0x1000, 32, 32, 2, 0, "single"), View(0x2000, 16, 16, 7, 0, "table")]
        proof = certify_partitions(views, build_partitions(views))
        self.assertTrue(proof[4]["complete_uint64_selection_domain"])
        self.assertEqual(proof[4]["selection_cells"], 5)

    def test_record_abi_is_separate_from_existing_push_bytes(self):
        views = [View(0x1000, 64, 64, 7, 2, "table")]
        rows = build_partitions(views); blob = pack_records(rows)
        self.assertEqual(struct.unpack_from("<IIII", blob), (1, 1, 0, 1))
        self.assertEqual(len(blob), 16 + 2 * 48)
        self.assertEqual(RECORD.unpack_from(blob, 16), (0x1000, 0x1040, 0x1000, 7, 2, 4, 7, 0, 0))

    def test_binary_search_recurs_strictly_smaller_and_handles_misses(self):
        rows = [Row(0x1000 + index * 32, 0x1010 + index * 32, 0x1000 + index * 32, 2, 0) for index in range(64)]
        for address in range(0x0fff, 0x1810):
            result, steps = binary_identity(rows, address)
            self.assertEqual(result, interval_identity(rows, address))
            self.assertLessEqual(steps, 7)

    def test_route_cannot_change_descriptor_class(self):
        self.assertEqual([route(binding, 0) for binding in range(2, 7)], list(range(5)))
        self.assertEqual([route(7, element) for element in range(3)], [5, 6, 7])
        for binding, element in ((0, 0), (6, 1), (7, 3), (12, 0)):
            with self.subTest(binding=binding, element=element), self.assertRaises(ValueError):
                route(binding, element)

    def test_unordered_and_overlapping_records_rejected(self):
        views = [View(0x1000, 64, 64, 2, 0, "single")]
        rows = {4: [Row(0x1000, 0x1030, 0x1000, 2, 0), Row(0x1020, 0x1040, 0x1000, 2, 0)], 16: []}
        with self.assertRaisesRegex(ValueError, "overlap"):
            certify_partitions(views, rows)

    def test_frozen_artifact_digest_and_containment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); path = directory / "input.bin"; path.write_bytes(b"independent synthetic bytes")
            record = {"path": path.name, "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertEqual(artifact(directory, record), path)
            with self.assertRaises(ValueError):
                artifact(directory, {**record, "path": "../input.bin"})
            path.write_bytes(b"x" * record["size"])
            with self.assertRaisesRegex(ValueError, "digest"):
                artifact(directory, record)

    def test_owned_descriptor_builder_reads_only_hash_pinned_visible_ubo(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); data = bytearray(4096)
            selectors = [{"binding": binding, "kind": "single", "byte_offset": 192 + index * 16} for index, binding in enumerate(range(2, 7))]
            selectors.append({"binding": 7, "kind": "table", "byte_offset": 2176})
            for index, selector in enumerate(selectors):
                struct.pack_into("<QI", data, 64 + selector["byte_offset"], 0x1000 + 0x1000 * index, 32)
            path = directory / "ubo.bin"; path.write_bytes(data)
            record = {"path": "ubo.bin", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            bindings = [{"binding": 0, "element": 0, "type": 6, "range": 2224, "offset": 64, "view": "u"}]
            bindings += [{"binding": binding, "element": 0, "type": 7, "range": 32, "offset": 0, "view": "s"} for binding in range(2, 8)]
            bindings += [{"binding": 7, "element": element, "type": 7, "range": 0, "offset": 0, "view": None} for element in (1, 2)]
            document = {"schema": "open-ocarina.physical-compute-replay/v1", "kind": "target", "mode": "frozen-diagnostic",
                "pipeline": {"program": "cc62764061c44f6a", "variant": "095931037888515a", "spirv": {"sha256": TARGET}},
                "allocations": [{"id": "u", "size": len(data), "initial": record}, {"id": "s", "size": 4096}],
                "views": [{"id": "u", "allocation": "u", "offset": 0, "size": 4096}, {"id": "s", "allocation": "s", "offset": 0, "size": 4096}],
                "bindings": bindings}
            manifest = directory / "manifest.json"; manifest.write_text(json.dumps(document))
            views, evidence = frozen_views(manifest, selectors)
            self.assertEqual(len(views), 8)
            self.assertEqual(views[0].guest, 0x1000)
            self.assertEqual(evidence["ubo0_visible_bytes"], 2224)
            original_writable_view = bindings[5]["view"]
            bindings[5]["view"] = "u"; manifest.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "writable"):
                frozen_views(manifest, selectors)
            bindings[5]["view"] = original_writable_view
            document["mode"] = "historical-dispatch"; manifest.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "owned frozen"):
                frozen_views(manifest, selectors)


if __name__ == "__main__":
    unittest.main()
