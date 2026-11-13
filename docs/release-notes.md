Game-free builds of Open-Ocarina: the tools that build the game on your own PC from your own copy. No game data is included, and the game executable is not distributed.

**You need your own copy of the game** at the supported version: program `0400486024DEC000`, update v131072, build `e0f3ccfa`, as one `.nup` file or as `romfs` and `exefs` folders. Other versions are refused.

**Linux:** download the `.AppImage`, make it executable, run it, choose your game files and a folder, then Play. A Vulkan 1.3 driver with mesh shaders is required.

**Windows:** this release has the source package and the prebuilt Vulkan back end, not an installer. Follow `WINDOWS.md`.

**What is and is not tested:** built on GitHub without a game; the Linux build path is the one this project plays on (Intel Arc A770, with a locally patched Mesa driver that is not part of this download). Windows, NVIDIA and AMD are untested by this project. Intel Arc without the patched driver stalls or renders wrongly.

Built from the Hookshot source snapshot (GPL-3.0) on the `hookshot-upstream` branch with this repository's `variant-name` and `release-source` patches.
