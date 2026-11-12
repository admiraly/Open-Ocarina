# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sampled_image_check", ROOT / "patches/hookshot/check_sampled_image_capture.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class SampledImageCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.image = {"handle": "0000000000000042", "memory": "0000000000000080", "memory_offset": 0,
            "flags": 0, "type": 1, "format": 9, "extent": [5, 3, 1], "mip_levels": 3, "array_layers": 2,
            "samples": 1, "tiling": 0, "usage": 5, "sharing_mode": 0, "initial_layout": 0,
            "queue_families": [], "pnext_types": [], "format_list": [], "complete": True}
        self.view = {"handle": "0000000000000050", "image": self.image["handle"], "flags": 0, "type": 5,
            "format": 9, "components": [0, 0, 0, 0], "aspect": 1, "base_mip": 0, "mip_count": 3,
            "base_layer": 0, "layer_count": 2, "usage": 4, "explicit_usage": True,
            "pnext_types": [1000117002], "complete": True}
        self.sampler = {"handle": "0000000000000090", "core_words": [0] * 16, "complete": True, "pnext": []}
        plan, error = CHECK.plan(self.image, self.view, 1)
        self.assertIsNone(error)
        payload = bytearray(plan["bytes"])
        for mip in plan["mips"]:
            payload[mip["offset"]:mip["offset"] + mip["bytes"]] = b"x" * mip["bytes"]
        rows = []
        for index in range(4):
            name = f"image-{index}-before.bin"
            (self.root / name).write_bytes(payload)
            view = copy.deepcopy(self.view)
            view["handle"] = f"{0x50 + index:016x}"
            rows.append({"id": index, "binding": 8 + index, "element": 0, "descriptor_type": 1,
                "descriptor_layout": 1, "image_view": view["handle"], "sampler_handle": self.sampler["handle"],
                "image": copy.deepcopy(self.image), "view": view, "sampler": copy.deepcopy(self.sampler),
                "copy": copy.deepcopy(plan), "before": {"artifact": name, "sha256": hashlib.sha256(payload).hexdigest()}, "failures": []})
        self.document = {"schema": "open-ocarina.sampled-images/v1", "replay_complete": False,
            "image_inputs_complete": True, **CHECK.TARGET, "recording_part": 3, "recording_sequence": 7,
            "completed_recording_sequence": 8, "selected_occurrence": 64, "image_budget_bytes": 256 << 20,
            "aggregate_budget_bytes": 5120 << 20, "buffer_staging_bytes": 128 << 20,
            "command_position": "before selected compute dispatch", "mapped_memory_invalidated": True,
            "coverage": copy.deepcopy(CHECK.COVERAGE), "failures": ["image_physical_aliases_unresolved",
                "cpu_writers_not_quiesced", "temporal_state_not_captured"], "images": rows}

    def validate(self, document=None, allocation=None):
        (self.root / "images.json").write_text(json.dumps(self.document if document is None else document))
        return CHECK.validate_images(self.root, allocation)

    def test_aliasing_views_preserve_one_backing_image_and_component_scope(self):
        report = self.validate()
        self.assertTrue(report["image_inputs_complete"])
        self.assertEqual(report["unique_image_count"], 1)
        self.assertEqual(report["image_payload_bytes"], 160)
        self.assertFalse(report["replay_complete"])
        self.assertFalse(report["coverage"]["complete_push_constants"])

    def test_complete_mip_layer_block_footprint_is_recomputed(self):
        self.assertEqual(self.document["images"][0]["copy"]["bytes"], 40)
        for mutate in (lambda d: d["images"][0]["copy"]["mips"][1].update(offset=31),
                       lambda d: d["images"][0]["view"].update(base_layer=2),
                       lambda d: d["images"][0]["image"].update(mip_levels=32)):
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.assertRaises(ValueError): self.validate(document)
        bc, view = copy.deepcopy(self.image), copy.deepcopy(self.view)
        bc["format"] = view["format"] = 137
        plan, reason = CHECK.plan(bc, view, 1)
        self.assertIsNone(reason)
        self.assertEqual(plan["bytes"], 128)
        self.assertEqual(plan["block"], [4, 4, 16])

    def test_unsupported_layout_aspect_multisampling_and_usage_remain_explicit(self):
        for image_change, view_change, layout in (({}, {}, 5), ({}, {"aspect": 2}, 1), ({"samples": 4}, {}, 1),
                                                ({"usage": 4}, {}, 1), ({}, {"usage": 8}, 1), ({"flags": 1}, {}, 1)):
            image, view = dict(self.image, **image_change), dict(self.view, **view_change)
            plan, reason = CHECK.plan(image, view, layout)
            self.assertIsNone(plan)
            self.assertIsInstance(reason, str)
            document = copy.deepcopy(self.document)
            row = document["images"][0]
            row.update(image=image, view=view, descriptor_layout=layout, copy={"supported": False, "reason": reason}, before=None)
            row["failures"].append(reason)
            document["image_inputs_complete"] = False
            # Other aliases must carry identical backing-image facts.
            for other in document["images"][1:]: other["image"] = copy.deepcopy(image)
            if image_change:
                continue  # Pure footprint cases checked above; other rows would also need replanning.
            self.assertFalse(self.validate(document)["image_inputs_complete"])

    def test_unknown_pnext_cannot_claim_complete_metadata(self):
        document = copy.deepcopy(self.document)
        document["images"][0]["sampler"]["pnext"] = [{"type": 999, "words": []}]
        with self.assertRaisesRegex(ValueError, "Unsupported sampler"):
            self.validate(document)
        document["images"][0]["sampler"]["complete"] = False
        document["images"][0]["failures"] = ["sampler_pnext_unsupported"]
        document["image_inputs_complete"] = False
        self.assertFalse(self.validate(document)["image_inputs_complete"])
        document = copy.deepcopy(self.document)
        document["images"][0]["view"]["pnext_types"] = []
        with self.assertRaisesRegex(ValueError, "usage extension"):
            self.validate(document)

    def test_sampler_core_and_known_custom_border_reduction_are_exact_words(self):
        row = self.document["images"][0]
        row["sampler"]["pnext"] = [{"type": 1000130001, "words": [2]},
            {"type": 1000287000, "words": [0x3F800000, 0, 0, 0x3F800000, 0]}]
        self.assertTrue(self.validate()["image_inputs_complete"])
        row["sampler"]["pnext"][1]["words"].pop()
        with self.assertRaisesRegex(ValueError, "sampler pNext"):
            self.validate()
        row["sampler"]["pnext"] = []
        row["sampler"]["core_words"].pop()
        with self.assertRaisesRegex(ValueError, "core creation"):
            self.validate()

    def test_budget_failure_is_metadata_only_and_never_complete(self):
        self.document.update(aggregate_budget_bytes=32 << 20, buffer_staging_bytes=32 << 20,
                             image_inputs_complete=False, mapped_memory_invalidated=False)
        self.document["failures"].append("image_or_aggregate_staging_budget_exceeded")
        for row in self.document["images"]:
            row["before"] = None
            row["failures"] = ["image_payload_missing"]
        self.assertEqual(self.validate()["image_payload_bytes"], 0)
        self.document["failures"].remove("image_or_aggregate_staging_budget_exceeded")
        with self.assertRaisesRegex(ValueError, "budget gap"):
            self.validate()

    def test_payload_hash_padding_path_symlink_and_truncation_are_checked(self):
        artifact = self.root / "image-0-before.bin"
        payload = bytearray(artifact.read_bytes())
        payload[30] = 1
        artifact.write_bytes(payload)
        self.document["images"][0]["before"]["sha256"] = hashlib.sha256(payload).hexdigest()
        with self.assertRaisesRegex(ValueError, "padding"):
            self.validate()
        artifact.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.validate()
        artifact.unlink();artifact.symlink_to(self.root / "image-1-before.bin")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.validate()
        self.document["images"][0]["before"]["artifact"] = "../escape"
        with self.assertRaisesRegex(ValueError, "artifact path"):
            self.validate()

    def test_same_source_cannot_have_conflicting_creation_facts(self):
        self.document["images"][1]["image"]["extent"] = [8, 8, 1]
        with self.assertRaisesRegex(ValueError, "Aliased views disagree"):
            self.validate()

    def test_completion_pipeline_and_buffer_link_are_exact(self):
        allocation = {key: self.document[key] for key in (*CHECK.TARGET, "recording_part", "recording_sequence", "completed_recording_sequence", "selected_occurrence")}
        allocation.update(budget_bytes=5120 << 20, required_staging_bytes=128 << 20)
        self.assertTrue(self.validate(allocation=allocation)["linked_buffer_capture_checked"])
        allocation["recording_part"] = 4
        with self.assertRaisesRegex(ValueError, "timelines differ"):
            self.validate(allocation=allocation)
        self.document["completed_recording_sequence"] = 6
        with self.assertRaisesRegex(ValueError, "precedes"):
            self.validate()

    def test_incomplete_push_aliases_and_temporal_state_are_never_promoted(self):
        for key in CHECK.COVERAGE:
            document = copy.deepcopy(self.document)
            document["coverage"][key] = True
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Overstated"):
                self.validate(document)
        self.document["image_inputs_complete"] = False
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.validate()


if __name__ == "__main__": unittest.main()
