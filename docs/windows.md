# Open-Ocarina on Windows

Status: game-free Windows builds are compiled and smoke-tested on GitHub Actions (`windows-2022`). No game executable or game data is published; the game itself is built on your own PC from your own copy.

## What you need

- Your own copy of the game at the supported version: program `0400486024DEC000`, update `v131072`, build `e0f3ccfa`. Either one `.nup` file holding the game and that update, or the extracted `romfs` and `exefs` folders. A complete `romfs` is about 23.8 GB; other versions are refused.
- Windows 10 or 11 (64-bit) with a Vulkan 1.3+ driver (GPUs with hardware mesh shaders are fastest; older Vulkan 1.3+ GPUs such as Pascal / Quadro P-series run with automatic compute fallback and on-demand shader compilation):
  - **NVIDIA:** current GeForce Game Ready / Studio driver. Optionally tick **NVIDIA DLSS** in the builder (downloads NVIDIA's DLSS SDK under NVIDIA's license at build time).
  - **AMD:** current Adrenalin Vulkan 1.3 driver. Optionally tick **AMD FSR 4** in the builder (compiles FSR 4 support and downloads AMD's signed FSR runtime under AMD's license at build time).
- Around **45 GB** of free disk space on an SSD is recommended during the build.

## Recommended: Portable Windows Builder (`Open-Ocarina-<version>-windows-x64.zip`)

Everything required to build and play on Windows is bundled inside `Open-Ocarina-<version>-windows-x64.zip`:
- `HookshotBuilder.exe` (self-contained .NET 8 GUI — no separate .NET or Python install needed)
- Bundled Python 3.12 + build packages
- Bundled `llvm-mingw` (`ucrt-x86_64`) compiler toolchain
- Vulkan / SPIR-V / VMA headers, LGPL FFmpeg + `dav1d` (for AV1/video cutscenes), `SDL2.dll` + `SDL3.dll` (for controllers), and pipeline state lists

Steps:
1. Download `Open-Ocarina-<version>-windows-x64.zip` from the release page and extract it to a normal folder (for example `C:\Tools\Open-Ocarina`, avoiding OneDrive-synced folders if possible).
2. Double-click `HookshotBuilder.exe`.
3. Choose your `.nup` file (or `romfs` + `exefs` folders) — Open-Ocarina automatically detects your GPU & Vulkan capabilities, asks only if a system choice is needed (such as NVIDIA DLSS on RTX GPUs, AMD FSR 4 on RDNA 4 GPUs, or skipping slow upfront shader compilation on non-mesh-shader GPUs), and builds into `%USERPROFILE%\Games\Open-Ocarina`.
5. Click **Build the game**, then **Play** once finished (`open-ocarina.exe`).

> Note: If Windows Defender or SmartScreen warns on an unsigned zip download, extract to an allowed folder; if `tools\python\python.exe` is quarantined, restore it or extract again into an excluded folder.

## Advanced / Source Package (`Open-Ocarina-<version>-windows-source.zip`)

If you prefer building from source on the command line instead of using the portable builder zip:
- `Open-Ocarina-<version>-windows-source.zip` contains the patched source tree only.
- You will need Python 3 (`pip install -r requirements.txt`), `llvm-mingw` (`ucrt-x86_64`), FFmpeg Windows headers/libs in `cpm/ffmpeg/<ver>`, and either Vulkan/SPIR-V/VMA headers in `cpm/` or `HOOKSHOT_VK_PREBUILT` pointing to `open-ocarina_vk.dll` (plus `SDL2.dll` and `SDL3.dll` in `third_party/sdl2/` for controller support).

```powershell
python -I nx2/make_game.py --check --nup "C:\path\your-game.nup"
python -I nx2/make_game.py --nup "C:\path\your-game.nup" --out "C:\Games\Open-Ocarina" --language en-US
```

## Limits

- The Intel Arc work in this project so far is on Linux with a locally patched Mesa driver. None of it applies to Windows drivers.
