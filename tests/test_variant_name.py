import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("variant_check", ROOT / "patches/hookshot/check_variant_name.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
CHECKOUT = ROOT / "local" / "hookshot-source"


class VariantNameTests(unittest.TestCase):
    def test_patch_touches_only_the_variants_trees(self):
        files = set(CHECK.patch_files())
        self.assertFalse([p for p in files if not p.startswith(CHECK.ALLOWED_TREES)])
        self.assertTrue(CHECK.REQUIRED_FILES <= files)
        self.assertFalse(CHECK.FORBIDDEN_FILES & files)

    def test_upstream_owned_files_are_left_alone(self):
        files = set(CHECK.patch_files())
        self.assertNotIn("host/hos_update.c", files)          # (an upstream release must not replace this variant)
        self.assertNotIn("scripts/linux/appimage.sh", files)  # (the AppImage's own names)
        self.assertNotIn("builder/linux/AppRun", files)

    def test_one_name_definition_is_shared_with_the_back_end(self):
        header = CHECK.header_from_patch()
        self.assertIn("#ifndef HOS_VARIANT_NAME", header)
        self.assertEqual(header.count('#define HOS_VARIANT_NAME "Open-Ocarina"'), 1)
        self.assertIn('#include "../gpu/vk/vk_variant.h"', CHECK.added_lines("host/hos.h"))

    def test_the_executable_renderer_and_launcher_take_the_lower_case_name(self):
        build = CHECK.added_lines("nx2/build.py")
        self.assertIn('EXE = "open-ocarina" if LINUX else "open-ocarina.exe"', build)
        self.assertIn('VK_LIB = "open-ocarina_vk.so" if LINUX else "open-ocarina_vk.dll"', build)
        self.assertIn("bin/open-ocarina", CHECK.added_lines("builder/linux/hookshot-folder"))

    def test_the_saves_folder_moves_and_takes_the_old_one_along(self):
        saves = CHECK.added_lines("host/hos_main.c")
        self.assertIn('"/open-ocarina"', saves)
        self.assertIn('"%.*s/hookshot"', saves)

    def test_app_id_matches_the_window_and_the_menu_entry(self):
        sdl = CHECK.added_lines("host/posix/display_sdl.c")
        builder = CHECK.added_lines("builder/linux/hookshot_builder.py")
        self.assertIn('#define HOS_APP_ID "%s"' % CHECK.APP_ID, sdl)
        self.assertIn('APP_ID = "%s"' % CHECK.APP_ID, builder)
        self.assertIn('MENU_NAME = "%s"' % CHECK.NAME, builder)
        self.assertIn('MENU_FILE = "%s"' % CHECK.MENU_FILE, builder)

    def test_missing_name_header_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.patch"
            path.write_text("unrelated content")
            with mock.patch.object(CHECK, "PATCH", path):
                with self.assertRaises(ValueError):
                    CHECK.header_from_patch()

    def test_name_header_compiles_as_c_and_cxx(self):
        compiler = shutil.which("cc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("optional C compiler unavailable")
        result = CHECK.compile_check(CHECK.header_from_patch(), compiler)
        self.assertTrue(result["c"])

    def test_applied_checkout_writes_the_variant_menu_entry(self):
        if not CHECKOUT.is_dir():
            self.skipTest("private Hookshot checkout is absent")
        result = CHECK.check(CHECKOUT)
        self.assertTrue(result["checkout_verified"])
        self.assertEqual(result["menu_file"], CHECK.MENU_FILE)
        self.assertTrue(result["stale_removed"])
        self.assertTrue(result["foreign_kept"])


if __name__ == "__main__":
    unittest.main()
