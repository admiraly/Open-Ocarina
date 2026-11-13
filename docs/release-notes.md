Game-free builds of Open-Ocarina: the tools that build the game on your own PC from your own copy. No game data is included, and the game executable is not distributed.

**You need your own copy of the game** at the supported version: program `0400486024DEC000`, update `v131072`, build `e0f3ccfa`, as one `.nup` file or as `romfs` and `exefs` folders. Other versions are refused.

**Linux:** download `Open-Ocarina-<version>-x86_64.AppImage`, make it executable, run it, choose your `.nup` file (or game folders), answer any system prompts if needed, and Play. A Vulkan 1.3+ driver is required.

**Windows:** download `Open-Ocarina-<version>-windows-x64.zip`, extract it, and run `HookshotBuilder.exe` (Open-Ocarina's self-contained builder: includes Python, `llvm-mingw`, Vulkan headers, FFmpeg + `dav1d`, `SDL2`/`SDL3`, automatic GPU/Vulkan detection, guided NVIDIA DLSS / AMD FSR 4 prompts, and automatic on-demand shader handling for older GPUs without hardware mesh shaders). See `WINDOWS.md` for details.

**What is and is not tested:** built and smoke-tested on GitHub Actions without game data; verified in real gameplay on Linux (Intel Arc A770 with patched Mesa) and Windows 11 (NVIDIA Quadro P600 Vulkan 1.4 compute-fallback). NVIDIA RTX / AMD RDNA gameplay is ready for testing.

Built from the Hookshot source snapshot (GPL-3.0) on the `hookshot-upstream` branch with this repository's `variant-name` and `release-source` patches.
