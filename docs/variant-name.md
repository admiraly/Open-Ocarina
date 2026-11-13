# Variant name: Open-Ocarina

Status: the patch renames the variant end to end — executable, renderer, launcher, game folder, saves folder and runtime files — and keeps only what upstream owns. It is game-free tested; the rebuilt pair was installed in the playable folder and started on the local display, where it presented frames and exited normally (an identity check, not a rendering or performance result).

This variant presents itself as **Open-Ocarina**. One definition, `HOS_VARIANT_NAME` in `gpu/vk/vk_variant.h`, feeds the window title and its app id, the applications menu's entry, the Insert menu's banner and Discord help text, the log's first line, the Discord status, the console title and the message boxes. The Vulkan back end is built as its own library without the host's headers, so the name lives in a small header both sides include; the host's `host/hos.h` includes it and keeps `HOS_GAME_TITLE` as its alias.

## What is renamed

The same name in lower case names everything the variant builds and writes:

- the executable: `open-ocarina` on Linux, `open-ocarina.exe` on Windows (`nx2/build.py`'s `EXE`);
- the Vulkan back end: `open-ocarina_vk.so` / `open-ocarina_vk.dll` (`VK_LIB`), loaded by `host/rx_backend.c`;
- the game folder's launcher: `open-ocarina` (from `builder/linux/hookshot-folder`), which starts `bin/open-ocarina`;
- the Linux game folder: `Open-Ocarina`;
- the saves folder: `$XDG_DATA_HOME/open-ocarina` (`~/.local/share/open-ocarina`); the folder this variant used before the rename (`hookshot`) is moved there once, whole, on the first start (`host/hos_main.c`);
- the runtime files beside the game: `open-ocarina_options.ini`, `open-ocarina_env.txt`, `open-ocarina_log.txt` and its rotations (`open-ocarina_log.1.txt` … `.4.txt`), `open-ocarina_version.txt`, the program cache (`open-ocarina_programs.bin`, `.idx`, `.lock`, `.zst.bin`), the shader list (`open-ocarina_shaders.bin`), `open-ocarina_prepared.bin`, the pipeline notes (`open-ocarina_pipelines.bin` with its sidecars), `open-ocarina_pipeline_states_shipped.bin`, `open-ocarina_computes.bin`, `open-ocarina_compute_variants.bin`, `open-ocarina_load_start.txt`, `open-ocarina_module_ids.bin`, `open-ocarina_damaged_files.txt`, `open-ocarina_driver_faults.txt`, `open-ocarina_frames.csv`, the window icon `open-ocarina.png` and the linker map `open-ocarina.map`;
- the bug report's text and log names (`gpu/vk/vk_bug_report.cpp`), the issue form's file names (`.github/ISSUE_TEMPLATE/1-game-problem.yml`), and the developer tools that name build artifacts (`scripts/prof.py`, `hotlist.py`, `crit.py`, `chain.py`, `cpu_categories.py`, `cpu_report.py`), which still read the old names of a build made before the rename;
- the save-transfer tools (`scripts/oot_saves.py`, `scripts/hookshot_ctl.py`): their `--open-ocarina` and launch paths prefer the new names and keep the old ones as fallbacks.

## What keeps its name

What upstream owns, or what other tools and services address by name, is unchanged:

- the environment prefix `HOS_*` / `HOOKSHOT_*`, and the hidden stamps `.hookshot_exefs`, `.hookshot_romfs`, `.hookshot_romfs_checked`: the builder and the scripts set and read them;
- the builder's own program and data names (`hookshot_builder.py`, `~/.cache/hookshot-builder`, `~/.config/hookshot`, `$XDG_DATA_HOME/hookshot/builder`), and the AppImage's packaging paths (`usr/lib/hookshot`, `usr/share/hookshot`): they are the builder's distribution, not the game's files;
- the update check (`host/hos_update.c`) and its URLs. It still reads `hookshot_version.txt`, which a renamed folder no longer has, so the upstream update offer stays quiet and an upstream release cannot replace this variant. `builder/linux/linux_update.py` does read either version file, so the builder still recognises a renamed folder;
- the Discord application id and its art asset (`host/discord_rpc.c`), so Discord shows the upstream application with an Open-Ocarina state line;
- the shared pipeline list's file names and fields (`hookshot_pipeline_states.bin`, `hookshot_pipeline_states_v1.bin`, `hookshot_pipeline_report.txt`, `Hookshot version`), which mirror upstream's issue template;
- the in-game item named Hookshot, which is the game's own, and the source asset `assets/hookshot.png` (the build copies it into the folder as `open-ocarina.png`);
- the build intermediates (`work/hookshot_game.dll`, `hookshot_manifest.res`, `hookshot_pieces.prof`), `host/hookshot_env.h`, `scripts/hookshot_ctl.py` and the `hookshot_logo*` generated headers.

## Patch and checks

The patch is against Hookshot commit `4cf785a0b143bb34026506c9a3fe4f4bb93c797d` and touches the variant's trees (`host/`, `gpu/`, `nx2/`, `builder/linux/`, `scripts/`, `tests/`, `.github/ISSUE_TEMPLATE/`). It does not depend on the GPU capture patches. Existing upstream notices apply.

```sh
git -C local/hookshot-source apply "$PWD/patches/hookshot/variant-name.patch"
python3 patches/hookshot/check_variant_name.py --checkout local/hookshot-source
python3 -m unittest tests.test_variant_name -v
```

The checker verifies the pinned revision and that the patch applies to the index, that it stays inside the variant's trees, that it contains the identity sites (executable, renderer, launcher, saves folder, options, log, bug report) and that it leaves upstream's files (`host/hos_update.c`, the AppImage's `AppRun` and `scripts/linux/*`) alone. It checks that the game's `HOS_APP_ID` and the builder's `APP_ID` agree, that the builder's menu entry is `open-ocarina.desktop`, and compiles the extracted name header as C11 and C++20 with `-O2 -DNDEBUG -Wall -Wextra -Werror` (narrow and wide concatenation, no newline or `%` in the name). With the checkout it also loads the actual Linux builder with a stubbed Tk, writes the entry into a temporary `XDG_DATA_HOME`, checks the name, the window class and the stale/foreign `hookshot.desktop` cases, and runs `desktop-file-validate` when installed. The upstream tests that assert the renamed files, logs, saves folder, launcher, profiler names and update/cache paths were updated with the patch and pass.

The patched translation units are not rebuilt by these checks: the header reaches every host object that includes `host/hos.h`, so a rebuild recompiles the host objects and the two Vulkan files. No game assets, saves or GPU work are needed for the checks.

## Local build and run

A clean pinned checkout with only this patch applied was built privately, without touching the working install:

- Vulkan back end: `open-ocarina_vk.so`, 13,290,032 bytes, SHA-256 `47540689421e257219a29537c8750278a643ffa1a8d929cfe276452e8a138980`, 47 s, 0 failed files.
- Host: 46 host objects recompiled, 234 cached objects and all 80 game chunks reused, linked `bin/open-ocarina`, 495,854,488 bytes, SHA-256 `c9c61b0be1fa73f4d40d835dd21d4742658482372de7fddec1415784cd43e409`, Clang 23.1.5, `-O2 -DNDEBUG -march=x86-64-v3`, patch SHA-256 `53539a3daed569ecd1a623dae2efd1b21959d70de885a97b2978d755c98dccf9`.

That pair was installed in the playable folder (the old binaries kept beside them as `*.pre-open-ocarina`), the runtime files and the folder were renamed, and the game was started once on the local display. The log's first line was `Open-Ocarina v2.1.0-rc.3-306-g4cf785a-dirty (source 51cd62f2)`; it read the renamed `open-ocarina_options.ini` and moved the old saves folder to `~/.local/share/open-ocarina`. The run presented 8,711 frames at 60 fps and exited normally. This is an identity check, not gameplay, rendering-equivalence or FPS evidence.

`host/display.c` and `host/mf_av1.c` are Windows-only and were not compiled by this Linux build; their changes are the same name substitution and are covered by the patch text check.

## Rebuild and install

Build the patched source with the builder (or `nx2/build.py`) and use that build's `bin/open-ocarina` and `bin/open-ocarina_vk.so`. A folder built before this patch keeps working: the launchers and the builder's update path fall back to `bin/hookshot`, and the game's saves folder is taken along from `hookshot` once. The working install must be rebuilt, not edited: the strings are compiled into the host executable and the renderer.
