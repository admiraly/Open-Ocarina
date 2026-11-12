# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic regression coverage: no game files or external analysis tools needed."""
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from tools import check_public_tree, inventory, profile, verify_bytes


ROOT = Path(__file__).resolve().parents[1]


def timing_table(dispatches=150):
    """A fabricated profile with different recording/upload denominators."""
    return "\n".join([
        "[thread] vk: GPU pass timing, 600 presents, per frame (synthetic):",
        "[thread] vk: 100.00 ms a frame in all (recording + uploads)",
        "[thread] vk: by kind:",
        "[thread] vk: m pass 40.000 ms 80.0% 20.0 spans 30.0 items 0.00 MB",
        f"[thread] vk: m dispatch 10.000 ms 20.0% {dispatches}.0 spans {dispatches}.0 items 0.00 MB",
        "[thread] vk: u msync 50.000 ms 100.0% 2.0 spans 4.0 items 1.00 MB",
        "[thread] vk: the 3 spans that took the most (synthetic):",
        "[thread] vk: m 40.000 ms 1.0 x 40000.0 us 30.0 items 0.00 MB pass 16x16, first prog aaaaaaaaaaaaaaaa, last prog bbbbbbbbbbbbbbbb",
        "[thread] vk: m 10.000 ms 150.0 x 66.7 us 150.0 items 0.00 MB dispatch prog cccccccccccccccc",
        "[thread] vk: u 50.000 ms 2.0 x 25000.0 us 4.0 items 1.00 MB msync copies",
    ]) + "\n"


def nso_bytes(*, flags=0, text_size=8, stored_size=8, payload=b"abcdefgh"):
    """Construct only a minimal synthetic header and synthetic section data."""
    header = bytearray(256)
    header[:4] = b"NSO0"
    struct.pack_into("<I", header, 12, flags)
    struct.pack_into("<III", header, 16, 256, 0, text_size)
    struct.pack_into("<III", header, 32, 256 + len(payload), 4096, 0)
    struct.pack_into("<III", header, 48, 256 + len(payload), 8192, 0)
    header[64:96] = bytes(range(32))
    struct.pack_into("<III", header, 96, stored_size, 0, 0)
    return bytes(header) + payload


class TemporaryInputs(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def write(self, name, data):
        path = self.directory / name
        path.write_bytes(data)
        return path

    def cli(self, script, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools" / script), *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, check=False)


class ProfileTests(TemporaryInputs):
    def test_combined_denominator_and_ranking(self):
        report = profile.summarize(profile.parse(io.StringIO(timing_table()))[0])
        self.assertEqual(report["dispatches_per_frame"], 150)
        kinds = {row["kind"]: row for row in report["kinds"]}
        self.assertEqual(kinds["pass"]["share_of_total_pct"], 40)
        self.assertEqual(kinds["dispatch"]["share_of_total_pct"], 10)
        self.assertEqual(kinds["msync"]["share_of_total_pct"], 50)
        self.assertEqual([row["ms"] for row in report["spans"]], [50, 40, 10])
        self.assertEqual(sum(row["share_of_total_pct"] for row in report["kinds"]), 100)

    def test_hashes_remain_pass_endpoints_not_stage_or_algorithm_labels(self):
        report = profile.summarize(profile.parse(io.StringIO(timing_table()))[0])
        graphics = next(row for row in report["spans"] if row["description"].startswith("pass"))
        self.assertEqual(graphics["program_hashes"], ["a" * 16, "b" * 16])
        self.assertNotIn("stage", graphics)
        self.assertNotIn("algorithm", graphics)
        self.assertTrue(any("endpoints" in text for text in report["interpretation"]))

    def test_incomplete_last_table_does_not_silently_fall_back(self):
        incomplete = "\n".join(timing_table().splitlines()[:-1]) + "\n"
        blocks = profile.parse(io.StringIO(timing_table() + incomplete))
        self.assertEqual(len(blocks), 2)
        self.assertEqual(profile.summarize(blocks[0])["total_ms"], 100)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            profile.summarize(blocks[-1])
        path = self.write("timing.txt", (timing_table() + incomplete).encode())
        result = self.cli("profile.py", path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_explicit_completed_block_is_selectable(self):
        path = self.write("timing.txt", (timing_table() + timing_table().splitlines()[0]).encode())
        result = self.cli("profile.py", path, "--block", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["source_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(report["source_line"], 1)

    def test_absent_profile_is_an_error(self):
        path = self.write("ordinary.txt", b"ordinary synthetic log\n")
        self.assertEqual(profile.parse(path.read_text().splitlines()), [])
        result = self.cli("profile.py", path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No GPU timing tables", result.stderr)

    def test_loading_profile_is_excluded_unless_explicitly_requested(self):
        block = profile.parse(io.StringIO(timing_table(dispatches=2)))[0]
        with self.assertRaisesRegex(ValueError, "likely loading"):
            profile.summarize(block)
        self.assertEqual(profile.summarize(block, minimum_dispatches=0)["dispatches_per_frame"], 2)

    def test_invalid_negative_threshold_is_refused(self):
        path = self.write("timing.txt", timing_table().encode())
        result = self.cli("profile.py", path, "--min-dispatches", "-1")
        self.assertNotEqual(result.returncode, 0)

    def test_nonfinite_threshold_cannot_bypass_loading_exclusion(self):
        path = self.write("loading.txt", timing_table(dispatches=2).encode())
        for threshold in ("nan", "inf", "-inf"):
            with self.subTest(threshold=threshold):
                result = self.cli("profile.py", path, "--min-dispatches=" + threshold)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


class InventoryTests(TemporaryInputs):
    def test_uncompressed_sections_and_hash(self):
        data = nso_bytes(stored_size=1)
        path = self.write("module", data)
        report = inventory.describe(path)
        self.assertEqual(report["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(report["build_id"], bytes(range(32)).hex())
        self.assertEqual(report["sections"]["text"]["stored_size"], 8)
        self.assertFalse(report["sections"]["text"]["compressed"])
        self.assertNotIn("bytes", report)

    def test_compressed_bounds_use_stored_not_memory_size(self):
        path = self.write("module", nso_bytes(flags=1, text_size=4096, stored_size=8))
        report = inventory.describe(path)
        self.assertTrue(report["sections"]["text"]["compressed"])
        self.assertEqual(report["sections"]["text"]["memory_size"], 4096)
        self.assertEqual(report["sections"]["text"]["stored_size"], 8)

    def test_truncated_uncompressed_section_is_refused(self):
        path = self.write("module", nso_bytes(text_size=9))
        with self.assertRaisesRegex(ValueError, "Truncated text"):
            inventory.describe(path)

    def test_truncated_compressed_section_is_refused(self):
        path = self.write("module", nso_bytes(flags=1, text_size=4096, stored_size=9))
        with self.assertRaisesRegex(ValueError, "Truncated text"):
            inventory.describe(path)

    def test_empty_compressed_payload_for_nonempty_section_is_refused(self):
        path = self.write("module", nso_bytes(flags=1, text_size=8, stored_size=0, payload=b""))
        with self.assertRaises(ValueError):
            inventory.describe(path)

    def test_invalid_magic_and_short_header_are_refused(self):
        for data in (b"NSO0", b"NOT!" + bytes(260)):
            with self.subTest(size=len(data)):
                with self.assertRaisesRegex(ValueError, "Not an NSO"):
                    inventory.describe(self.write("module", data))

    def test_symlinked_module_is_refused(self):
        actual = self.write("actual", nso_bytes())
        link = self.directory / "link"
        link.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, "regular module"):
            inventory.describe(link)


class ByteVerifierTests(TemporaryInputs):
    def setUp(self):
        super().setUp()
        self.reference = self.write("reference", b"prefix-abcdefgh-suffix")
        self.candidate = self.write("candidate", b"zzabcdefghyy")
        self.digest = hashlib.sha256(self.reference.read_bytes()).hexdigest()

    def compare(self, reference_offset=7, candidate_offset=2, size=8, digest=None):
        return verify_bytes.verify(self.reference, self.candidate, reference_offset, candidate_offset,
                                   size, self.digest if digest is None else digest)

    def test_exact_range_match_does_not_claim_full_reconstruction(self):
        result = self.compare(digest=self.digest.upper())
        self.assertTrue(result["byte_match"])
        self.assertIn("relocations", result["scope"])
        self.assertIn("not verified", result["scope"])
        self.assertEqual(result["reference_sha256"], self.digest)

    def test_difference_offset_is_relative_to_requested_range(self):
        self.candidate.write_bytes(b"zzabcXefghyy")
        result = self.compare()
        self.assertFalse(result["byte_match"])
        self.assertEqual(result["first_difference"], 3)

    def test_difference_after_chunk_boundary(self):
        data = b"a" * (1024 * 1024 + 5)
        self.reference.write_bytes(data)
        self.candidate.write_bytes(data[:-1] + b"b")
        digest = hashlib.sha256(data).hexdigest()
        result = self.compare(reference_offset=0, candidate_offset=0, size=len(data), digest=digest)
        self.assertEqual(result["first_difference"], len(data) - 1)

    def test_wrong_reference_revision_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Reference hash mismatch"):
            self.compare(digest="0" * 64)

    def test_missing_or_malformed_digest_is_refused(self):
        for digest in ("", "abc", "g" * 64):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "full SHA-256"):
                    self.compare(digest=digest)

    def test_negative_offsets_and_nonpositive_sizes_are_refused(self):
        for offsets in ((-1, 0, 1), (0, -1, 1), (0, 0, 0), (0, 0, -1)):
            with self.subTest(offsets=offsets):
                with self.assertRaisesRegex(ValueError, "Offsets"):
                    self.compare(*offsets)

    def test_either_input_must_contain_whole_requested_range(self):
        for offsets in ((len(self.reference.read_bytes()), 0, 1), (0, len(self.candidate.read_bytes()), 1)):
            with self.subTest(offsets=offsets):
                with self.assertRaisesRegex(ValueError, "beyond an input"):
                    self.compare(*offsets)

    def test_cli_distinguishes_mismatch_from_invalid_reference(self):
        args = ["--reference", self.reference, "--candidate", self.candidate,
                "--reference-offset", "7", "--candidate-offset", "2", "--size", "8"]
        self.candidate.write_bytes(b"zzabcXefghyy")
        mismatch = self.cli("verify_bytes.py", *args, "--reference-sha256", self.digest)
        self.assertEqual(mismatch.returncode, 1)
        self.assertFalse(json.loads(mismatch.stdout)["byte_match"])
        invalid = self.cli("verify_bytes.py", *args, "--reference-sha256", "0" * 64)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")


class PublicationGuardTests(unittest.TestCase):
    def test_text_source_is_allowed(self):
        self.assertEqual(check_public_tree.audit("tools/example.py", b"print('synthetic')\n"), [])

    def test_private_artifact_paths_and_binary_types_are_rejected(self):
        for path in ("local/report.json", "artifacts/output.txt", "romfs/readme.md", "exefs/main.py",
                     "user/save.json", "image.bin", "package.nup", "shader.spv", "runtime.so"):
            with self.subTest(path=path):
                self.assertIn("unapproved artifact path/type", check_public_tree.audit(path, b"synthetic"))

    def test_binary_or_large_content_cannot_hide_in_allowed_suffix(self):
        for content in (b"synthetic\0binary", b"x" * (256 * 1024 + 1)):
            with self.subTest(size=len(content)):
                self.assertIn("large or binary content", check_public_tree.audit("research/report.txt", content))

    def test_symlinks_are_rejected_even_for_allowed_names(self):
        self.assertIn("symlink", check_public_tree.audit("docs/link.md", b"destination", symlink=True))

    def test_credential_like_content_is_rejected_without_publishing_a_token(self):
        samples = [b"gh" + b"p_" + b"x" * 40,
                   b"github_" + b"pat_" + b"x" * 40,
                   b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----",
                   b"/sso" + b"Token=" + b"synthetic-value",
                   b"/refresh" + b"Token=" + b"synthetic-value"]
        for content in samples:
            with self.subTest(prefix=content[:8]):
                self.assertIn("credential-like content", check_public_tree.audit("docs/report.md", content))

    def test_machine_specific_paths_are_rejected(self):
        for prefix in (b"home", b"mnt"):
            content = b"/" + prefix + b"/example/private-input"
            self.assertIn("machine-specific absolute path", check_public_tree.audit("docs/report.md", content))

    def test_test_source_itself_is_public_safe(self):
        self.assertEqual(check_public_tree.audit("tests/test_core_tools.py", Path(__file__).read_bytes()), [])


if __name__ == "__main__":
    unittest.main()
