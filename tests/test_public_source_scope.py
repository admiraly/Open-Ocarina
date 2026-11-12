import unittest

from tools.check_public_tree import audit


class SourceScopeTests(unittest.TestCase):
    def test_reconstruction_and_ghidra_script_scopes(self):
        self.assertEqual(audit("src/probes/leaf.c", b"int leaf(void) { return 0; }"), [])
        self.assertEqual(audit("tools/ghidra_scripts/Probe.java", b"class Probe {}"), [])
        self.assertEqual(audit("tools/vulkan_compile_probe.c", b"int main(void) { return 0; }"), [])

    def test_source_suffixes_do_not_allow_arbitrary_imports(self):
        self.assertTrue(audit("generated/leaf.c", b"int leaf(void) { return 0; }"))
        self.assertTrue(audit("imports/Probe.java", b"class Probe {}"))
        self.assertTrue(audit("tools/arbitrary_blob.c", b"int main(void) { return 0; }"))
        self.assertTrue(audit("src/probes/leaf.c", b"\x00\x01"))


if __name__ == "__main__":
    unittest.main()
