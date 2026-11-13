import unittest

from tools.check_public_tree import audit


class SourceScopeTests(unittest.TestCase):
    def test_reconstruction_and_ghidra_script_scopes(self):
        self.assertEqual(audit("src/probes/leaf.c", b"int leaf(void) { return 0; }"), [])
        self.assertEqual(audit("tools/ghidra_scripts/Probe.java", b"class Probe {}"), [])
        self.assertEqual(audit("tools/vulkan_compile_probe.c", b"int main(void) { return 0; }"), [])
        self.assertEqual(audit("tools/vulkan_execution_probe.c", b"int main(void) { return 0; }"), [])
        self.assertEqual(audit("tools/vulkan_graphics_compile_probe.c", b"int main(void) { return 0; }"), [])
        self.assertEqual(audit("tools/vulkan_mesh_execution_probe.c", b"int main(void) { return 0; }"), [])
        self.assertEqual(audit("tools/vulkan_physical_replay.c", b"int main(void) { return 0; }"), [])
        self.assertEqual(audit("tools/vulkan_mesh_replay.c", b"int main(void) { return 0; }"), [])

    def test_source_suffixes_do_not_allow_arbitrary_imports(self):
        self.assertTrue(audit("generated/leaf.c", b"int leaf(void) { return 0; }"))
        self.assertTrue(audit("imports/Probe.java", b"class Probe {}"))
        self.assertTrue(audit("tools/arbitrary_blob.c", b"int main(void) { return 0; }"))
        self.assertTrue(audit("src/probes/leaf.c", b"\x00\x01"))

    def test_reviewed_interval_header_is_an_exact_path_allowance(self):
        name = "src/probes/compute_interval_records.h"
        source = b"// Authored C++ record builder\n#pragma once\n"
        self.assertEqual(audit(name, source), [])
        for other in ("src/probes/other.h", "src/probes/compute_interval_records.hpp",
                      "imports/compute_interval_records.h", "local/compute_interval_records.h",
                      "src/probes/imports/compute_interval_records.h"):
            with self.subTest(path=other):
                self.assertIn("unapproved artifact path/type", audit(other, source))
        self.assertIn("symlink", audit(name, source, symlink=True))
        self.assertIn("large or binary content", audit(name, source + b"\0"))
        self.assertIn("large or binary content", audit(name, b"x" * (256 * 1024 + 1)))
        self.assertIn("credential-like content", audit(name, b"ghp_" + b"x" * 24))


if __name__ == "__main__":
    unittest.main()
