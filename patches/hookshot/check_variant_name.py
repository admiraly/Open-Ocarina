#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the variant-name patch: the full rename to Open-Ocarina, one name definition, and the Linux menu entry.

Without a checkout the checker parses the patch itself and compiles the extracted name header as C and C++.
With the applied pinned checkout it also loads the actual Linux builder, writes the menu entry into a temporary
XDG data folder and checks the stale-entry cleanup.

The rename reaches the executable, the renderer, the launcher, the game folder, the saves folder and the runtime
files. It stops at what upstream owns: the HOS_/HOOKSHOT_ environment prefix, the hidden .hookshot_* stamps, the
builder's own program and data folders, the update check (so an upstream release cannot replace this variant), the
Discord application and update URLs, the pipeline-share format, and the in-game Hookshot item.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types

PIN = "4cf785a0b143bb34026506c9a3fe4f4bb93c797d"
PATCH = Path(__file__).with_name("variant-name.patch")
NAME = "Open-Ocarina"
APP_ID = "io.github.OpenOcarina.OpenOcarina"
HEADER = "gpu/vk/vk_variant.h"
MENU_FILE = "open-ocarina.desktop"

# The trees the rename may touch: the game and its runtime, the tools that name its files, the issue form and the tests.
ALLOWED_TREES = ("host/", "gpu/", "nx2/", "builder/linux/", "tests/", "scripts/", ".github/ISSUE_TEMPLATE/")

# The files that carry the rename's identity, each of which must be in the patch.
REQUIRED_FILES = {
    "host/hos.h",
    "host/hos_main.c",
    "host/hos_paths.h",
    "host/posix/display_sdl.c",
    "host/discord_rpc.c",
    "host/rx_backend.c",
    HEADER,
    "gpu/vk/vk_menu.cpp",
    "gpu/vk/vk_main.cpp",
    "gpu/vk/menu_i18n.h",
    "gpu/vk/vk_bug_report.cpp",
    "nx2/build.py",
    "nx2/make_game.py",
    "nx2/pipeline_share.py",
    "builder/linux/hookshot_builder.py",
    "builder/linux/hookshot-folder",
    "builder/linux/linux_update.py",
    ".github/ISSUE_TEMPLATE/1-game-problem.yml",
    "scripts/prof.py",
    "scripts/hotlist.py",
    "scripts/crit.py",
    "scripts/chain.py",
    "scripts/cpu_categories.py",
    "scripts/cpu_report.py",
    "scripts/oot_saves.py",
    "scripts/hookshot_ctl.py",
}

# What upstream owns and the rename must not touch: the update check and its URLs (an upstream release must not
# replace this variant), and the AppImage's own packaging names.
FORBIDDEN_FILES = {
    "host/hos_update.c",
    "builder/linux/AppRun",
    "scripts/linux/appimage.sh",
    "scripts/linux/make_parts.py",
    "scripts/linux/check_player_build.sh",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def patch_files(patch=None):
    patch = patch or PATCH
    return [line.split(" b/", 1)[1].strip()
            for line in patch.read_text().splitlines()
            if line.startswith("diff --git ") and " b/" in line]


def added_lines(path, patch=None):
    """The lines a patch adds to one file, without the leading '+'."""
    patch = patch or PATCH
    capture, lines = False, []
    for line in patch.read_text().splitlines(keepends=True):
        if line.startswith("diff --git "):
            capture = line.rstrip().endswith(" b/" + path)
        elif capture and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    if not lines:
        raise ValueError("No added lines for %s in the patch" % path)
    return "".join(lines)


def header_from_patch():
    header = added_lines(HEADER)
    require("#ifndef HOS_VARIANT_NAME" in header, "The name is not overridable at build time")
    require('#define HOS_VARIANT_NAME "%s"' % NAME in header, "The name header does not define the variant name")
    return header


def compile_check(header, compiler):
    """The header as C and C++20 sees it: narrow and wide string concatenation, one name."""
    with tempfile.TemporaryDirectory(prefix="oo-variant-") as temporary:
        work = Path(temporary)
        (work / "vk_variant.h").write_text(header)
        c_source = r'''#include "vk_variant.h"
#include <stdio.h>
#include <string.h>
#include <wchar.h>
static const char *narrow = HOS_VARIANT_NAME " can't open its files";
static const wchar_t *wide = HOS_VARIANT_NAME L" can't open its files";
int main(void) {
    if (strcmp(HOS_VARIANT_NAME, "__NAME__") != 0) return 1;
    if (strstr(narrow, HOS_VARIANT_NAME) != narrow) return 2;
    if (wcsstr(wide, L"__NAME__") == NULL) return 3;
    if (strchr(HOS_VARIANT_NAME, '\n') || strchr(HOS_VARIANT_NAME, '%')) return 4;
    printf("%s\n", HOS_VARIANT_NAME);
    return 0;
}
'''.replace("__NAME__", NAME)
        (work / "test.c").write_text(c_source)
        c = work / "test_c"
        subprocess.run([compiler, "-std=c11", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                        str(work / "test.c"), "-o", str(c)], check=True, timeout=60)
        require(subprocess.check_output([str(c)], text=True).strip() == NAME, "The C name check printed another name")
        cxx_source = r'''#include "vk_variant.h"
#include <cstring>
#include <iostream>
#include <string>
int main() {
    const std::string banner = std::string(HOS_VARIANT_NAME) + " " + "v1";
    const std::wstring wide = HOS_VARIANT_NAME L" needs a GPU";
    if (banner.rfind(std::string(HOS_VARIANT_NAME) + " ", 0) != 0) return 1;
    if (wide.find(L"__NAME__") == std::wstring::npos) return 2;
    std::cout << HOS_VARIANT_NAME << "\n";
    return 0;
}
'''.replace("__NAME__", NAME)
        (work / "test.cpp").write_text(cxx_source)
        cxx = work / "test_cxx"
        cxx_compiler = shutil.which("clang++") or shutil.which("g++")
        if cxx_compiler:
            subprocess.run([cxx_compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                            str(work / "test.cpp"), "-o", str(cxx)], check=True, timeout=60)
            require(subprocess.check_output([str(cxx)], text=True).strip() == NAME,
                    "The C++ name check printed another name")
    return {"c": True, "cxx": cxx_compiler is not None}


def load_builder(checkout):
    """The actual Linux builder, without a display."""
    for name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    tk = sys.modules["tkinter"]
    tk.Tk = type("Tk", (), {})
    tk.filedialog = sys.modules["tkinter.filedialog"]
    tk.messagebox = sys.modules["tkinter.messagebox"]
    tk.ttk = sys.modules["tkinter.ttk"]
    spec = importlib.util.spec_from_file_location("oo_variant_builder", checkout / "builder" / "linux" / "hookshot_builder.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def menu_check(checkout):
    """add_to_menu writes the variant's entry, replaces only the builder's old one, and validates."""
    builder = load_builder(checkout)
    require(builder.APP_ID == APP_ID, "The builder's app id does not match the game's window")
    require(builder.MENU_NAME == NAME, "The menu entry's name is not the variant name")
    require(builder.MENU_FILE == MENU_FILE, "The menu entry's file name is not the variant's")
    with tempfile.TemporaryDirectory(prefix="oo-menu-") as temporary:
        root = Path(temporary)
        apps = root / "applications"
        folder = root / "game"
        folder.mkdir()
        old_environ = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(root)
        try:
            builder.add_to_menu(str(folder))
            entry = apps / MENU_FILE
            require(entry.is_file(), "add_to_menu wrote no variant entry")
            body = entry.read_text()
            require("Name=%s\n" % NAME in body, "The entry's name is not the variant name")
            require("StartupWMClass=%s\n" % APP_ID in body, "The entry's window class is not the game's app id")
            require(sorted(p.name for p in apps.iterdir()) == [MENU_FILE], "add_to_menu wrote an unexpected entry")

            stale = apps / "hookshot.desktop"
            stale.write_text("[Desktop Entry]\nType=Application\nName=Hookshot\nExec=x\n"
                             "StartupWMClass=io.github.NotRexed.Hookshot\n")
            builder.add_to_menu(str(folder))
            require(not stale.exists(), "The builder's old menu entry was left behind")

            foreign = apps / "hookshot.desktop"
            foreign.write_text("[Desktop Entry]\nType=Application\nName=Something else\nExec=x\n")
            builder.add_to_menu(str(folder))
            require(foreign.read_text() == "[Desktop Entry]\nType=Application\nName=Something else\nExec=x\n",
                    "Another program's hookshot.desktop was removed")

            validate = shutil.which("desktop-file-validate")
            if validate:
                result = subprocess.run([validate, str(entry)], text=True, capture_output=True)
                require(result.returncode == 0, "desktop-file-validate rejected the entry: " + result.stdout + result.stderr)
        finally:
            if old_environ is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_environ
    return {"menu_file": MENU_FILE, "menu_name": NAME, "app_id": APP_ID,
            "stale_removed": True, "foreign_kept": True}


def check(checkout=None, compiler=None):
    files = set(patch_files())
    outside = sorted(p for p in files if not p.startswith(ALLOWED_TREES))
    require(not outside, "The patch touches files outside the variant's trees: %s" % outside)
    missing = sorted(REQUIRED_FILES - files)
    require(not missing, "The patch misses identity files: %s" % missing)
    forbidden = sorted(FORBIDDEN_FILES & files)
    require(not forbidden, "The patch touches what upstream owns: %s" % forbidden)

    header = header_from_patch()

    hos_h = added_lines("host/hos.h")
    require('#include "../gpu/vk/vk_variant.h"' in hos_h, "host/hos.h does not take the one name")
    require("#define HOS_GAME_TITLE HOS_VARIANT_NAME" in hos_h, "The window title does not use the variant name")
    sdl = added_lines("host/posix/display_sdl.c")
    require('#define HOS_APP_ID "%s"' % APP_ID in sdl, "The window's app id is not the variant's")
    require("window_title(HOS_VARIANT_NAME)" in sdl, "The window's title is not the variant name")
    builder = added_lines("builder/linux/hookshot_builder.py")
    require('APP_ID = "%s"' % APP_ID in builder, "The builder's app id is not the variant's")
    require('MENU_NAME = "%s"' % NAME in builder, "The builder's menu name is not the variant name")
    require('MENU_FILE = "%s"' % MENU_FILE in builder, "The builder's menu file is not the variant's")
    require('OLD_MENU_FILE = "hookshot.desktop"' in builder, "The builder no longer replaces its old menu entry")
    menu = added_lines("gpu/vk/vk_menu.cpp")
    require("std::string(HOS_VARIANT_NAME) + \" \" + HOOKSHOT_VERSION" in menu, "The menu banner does not use the variant name")
    main = added_lines("gpu/vk/vk_main.cpp")
    require("SetConsoleTitleA(HOS_VARIANT_NAME)" in main, "The console title does not use the variant name")
    require("HOS_VARIANT_NAME \": graphics card\"" in main, "The graphics-card message does not use the variant name")
    discord = added_lines("host/discord_rpc.c")
    require('\\"state\\":\\"%s %s\\"' in discord and "HOS_VARIANT_NAME, version" in discord,
            "The Discord status does not use the variant name")

    # The game's own files take the same name in lower case, from one place each.
    build = added_lines("nx2/build.py")
    require('EXE = "open-ocarina" if LINUX else "open-ocarina.exe"' in build, "The executable is not renamed")
    require('VK_LIB = "open-ocarina_vk.so" if LINUX else "open-ocarina_vk.dll"' in build, "The renderer is not renamed")
    make_game = added_lines("nx2/make_game.py")
    require('BESIDE = ["open-ocarina", "open-ocarina_vk.so"' in make_game, "The game folder's binaries are not renamed")
    saves = added_lines("host/hos_main.c")
    require('"/open-ocarina"' in saves, "The Linux saves folder is not renamed")
    require('"%.*s/hookshot"' in saves, "The saves folder is not taken along from its old name")
    launcher = added_lines("builder/linux/hookshot-folder")
    require("bin/open-ocarina" in launcher, "The folder launcher does not start the renamed game")
    update = added_lines("builder/linux/linux_update.py")
    require("bin/open-ocarina" in update and "bin/hookshot" in update,
            "The builder's update path does not prefer the new name and keep the old")
    paths = added_lines("host/hos_paths.h")
    require("open-ocarina" in paths, "The runtime's own path does not use the new name")
    report = added_lines("gpu/vk/vk_bug_report.cpp")
    require("open-ocarina_log.txt" in report, "The bug report does not read the renamed log")
    i18n = added_lines("gpu/vk/menu_i18n.h")
    require("open-ocarina_options.ini" in i18n, "The Insert menu does not read the renamed options file")

    compiler = compiler or shutil.which("cc") or shutil.which("clang")
    require(compiler is not None, "A C compiler is required for the name header checks")
    cxx = compile_check(header, compiler)

    result = {"patch_files": sorted(files), "variant_name": NAME, "app_id": APP_ID,
              "c_string_check": True, "cxx_string_check": cxx["cxx"], "checkout_verified": False}
    if checkout is not None:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
        require(head == PIN, "The checkout is not the documented pinned revision")
        subprocess.run(["git", "apply", "--cached", "--check", str(PATCH.resolve())], cwd=checkout, check=True)
        applied = (Path(checkout) / "host" / "hos_main.c").read_text()
        require('"/open-ocarina"' in applied, "The applied checkout does not use the new saves folder")
        require('EXE = "open-ocarina"' in (Path(checkout) / "nx2" / "build.py").read_text(),
                "The applied checkout does not build the renamed executable")
        require("./bin/open-ocarina" in (Path(checkout) / "builder" / "linux" / "hookshot-folder").read_text(),
                "The applied checkout's launcher does not start the renamed game")
        require(NAME in (Path(checkout) / "host" / "hos.h").read_text() or
                NAME in (Path(checkout) / HEADER).read_text(), "The applied checkout has no variant name")
        result.update(menu_check(Path(checkout)))
        result["checkout_verified"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, help="the applied pinned Hookshot checkout")
    parser.add_argument("--compiler", help="the C compiler for the name header checks")
    arguments = parser.parse_args()
    import json
    print(json.dumps(check(arguments.checkout, arguments.compiler), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
