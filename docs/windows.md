# Open-Ocarina on Windows

Status: the game-free Windows pieces are built on GitHub; the game itself is built on your own PC from your own copy. The Windows build has not been run on Windows hardware by this project yet.

## What GitHub builds

The [Windows build workflow](../.github/workflows/windows.yml) produces one artifact, `open-ocarina-windows`, without any game data:

- `Open-Ocarina-<version>-windows-source.zip`: Hookshot at the pinned commit with [the variant patch](../patches/hookshot/variant-name.patch) applied, exactly the tracked source.
- `open-ocarina_vk.dll`: the Vulkan back end compiled from that source with llvm-mingw. It shows the tree compiles for Windows; your own build compiles it again.
- `SHA256SUMS`.

`open-ocarina.exe` is not built there. It is translated from the game's own code on the PC that owns the game, and it is not published.

## What you need

- Your own copy of the game at the supported version: program `0400486024DEC000`, update v131072, build `e0f3ccfa`. Either one `.nup` file holding the game and that update, or the extracted `romfs` and `exefs` folders. A complete `romfs` is about 23.8 GB; other versions are refused.
- Windows 10 or 11, 64-bit, with a Vulkan 1.3 driver that has mesh shaders.
- Python 3 from python.org.
- [llvm-mingw](https://github.com/mstorsjo/llvm-mingw), the `ucrt-x86_64` build, unpacked as `llvm-mingw` inside the source folder or named by `HOOKSHOT_MINGW`.

## Build

Unpack the source zip, open PowerShell in it, then:

```powershell
pip install -r requirements.txt
python -I nx2/make_game.py --check --nup "C:\path\your-game.nup"
python -I nx2/make_game.py --nup "C:\path\your-game.nup" --out "C:\Games\Open-Ocarina" --language en-US
```

With folders instead of a `.nup`, use `--romfs "C:\path\romfs" --exefs "C:\path\exefs"`. `--check` only verifies the game's version and builds nothing.

The result is `C:\Games\Open-Ocarina` with `bin\open-ocarina.exe` and `bin\open-ocarina_vk.dll`. The game files the build read stay in that folder.

## Limits

- Upstream's installer and builder app are not built for this variant: they download upstream's releases by a fixed address.
- The Intel Arc work in this project so far is on Linux with a patched Mesa driver. None of it applies to Windows drivers.
