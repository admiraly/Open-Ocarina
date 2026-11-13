# Variant name: Open-Ocarina

Status: the patch, the single name definition and the Linux menu entry are implemented, game-free tested, and built from a clean pinned checkout. A short local run of that build showed the window titled Open-Ocarina with the variant's app id; it is not a rendering or performance result.

This variant presents itself as **Open-Ocarina**. One definition, `HOS_VARIANT_NAME` in `gpu/vk/vk_variant.h`, feeds the window title and its app id, the applications menu's entry, the Insert menu's banner and Discord help text, the log's first line, the Discord status, the console title and the message boxes. The Vulkan back end is built as its own library without the host's headers, so the name lives in a small header both sides include; the host's `host/hos.h` includes it and keeps `HOS_GAME_TITLE` as its alias.

## What keeps its name

The executable (`hookshot`, `bin/hookshot`, `hookshot_vk.so`), the `HOS_*` environment prefix, `hookshot_options.ini`, the cache and user folders and the builder's own file names are unchanged: scripts, the benchmark tools and the builder use them. The applications menu entry is the exception a user sees, so it is written as `open-ocarina.desktop` with `Name=Open-Ocarina`; `add_to_menu` removes the entry this builder wrote before (`hookshot.desktop` naming Hookshot and the old app id) and leaves a file of that name from another program alone.

Upstream integration keeps upstream's identity, because it talks to upstream's services:

- the release check and the update dialogs (`host/hos_update.c`) name Hookshot and fetch `NotRexed/Hookshot` releases;
- the Discord application id and its art asset are upstream's (`host/discord_rpc.c`), so Discord still shows the Hookshot application with an Open-Ocarina state line;
- the bug report's field names and the shared pipeline list's fields and file names mirror upstream's issue template (`gpu/vk/pipeline_share.h`, `nx2/pipeline_share.py`).

The in-game item named Hookshot is the game's own.

## Patch and checks

The patch is against Hookshot commit `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` and touches only the identity sites above. It does not depend on the GPU capture patches. Existing upstream notices apply.

```sh
git -C local/hookshot-source apply "$PWD/patches/hookshot/variant-name.patch"
python3 patches/hookshot/check_variant_name.py --checkout local/hookshot-source
python3 -m unittest tests.test_variant_name -v
```

The checker verifies the pinned revision and that the patch applies to the index, that the patch adds no user-visible `"Hookshot` literal, that the game's `HOS_APP_ID` and the builder's `APP_ID` agree, and compiles the extracted name header as C11 and C++20 with `-O2 -DNDEBUG -Wall -Wextra -Werror` (narrow and wide concatenation, no newline or `%` in the name). With the checkout it also loads the actual Linux builder with a stubbed Tk, writes the entry into a temporary `XDG_DATA_HOME`, checks the name, the window class and the stale/foreign `hookshot.desktop` cases, and runs `desktop-file-validate` when installed. The upstream builder tests (`tests/test_desktop_exec.py`, `tests/test_builder_free_space.py`, `tests/test_builder_status.py`, `tests/test_builder_scale.py`) still pass.

The patched translation units are not rebuilt by these checks: the header reaches every host object that includes `host/hos.h`, so a rebuild recompiles the host objects and the two Vulkan files. No game assets, saves or GPU work are needed for the checks.

## Local build and run

A clean copy of the pinned checkout with only this patch applied was built privately, without touching the working install:

- Vulkan back end: `hookshot_vk.so`, 26,111,408 bytes, SHA-256 `d7e3939635fdc0a2cf044d8d6f9b9180860059d26ff516f2f6bd7b60b477533f`, 45 s, 0 failed files.
- Host: 46 changed host objects recompiled, 234 cached objects and all 80 game chunks reused, linked `bin/hookshot`, 495,854,232 bytes, SHA-256 `edde04a96fadfa9274cbce337cd10a25fae81b769d383fdc56b3a0e288d0cd04`, Clang 23.1.5, `-O2 -DNDEBUG -march=x86-64-v3`, patch SHA-256 `5e7b3892dc61ee2ab6d77c18ba81ac30a9af253f9d5d30230ad0f6bf58ecf1db`.

That pair was copied into a throwaway copy of a built game folder and started once on the local display. The log's first line was `Open-Ocarina v2.1.0-rc.3-306-g4cf785a-dirty (source 4bc4e773)`; the window's `_NET_WM_NAME` and `WM_NAME` were `Open-Ocarina`, and `WM_CLASS` was `"hookshot", "io.github.OpenOcarina.OpenOcarina"` (the instance string is the executable's name, which stays). The run presented frames and exited normally. This is an identity check, not gameplay, rendering-equivalence or FPS evidence.

`host/display.c` and `host/mf_av1.c` are Windows-only and were not compiled by this Linux build; their changes are the same macro substitution and are covered by the patch text check.

## Rebuild and install

A playable folder built before this patch keeps the old name. To present the variant, build the patched source with the builder (or `nx2/build.py`) and use that build's folder; the shader caches and the `.nup` read/lift are reused. The working install must be rebuilt, not edited: the strings are compiled into the host executable and `hookshot_vk.so`.
