# Open-Ocarina

Source reconstruction and native PC porting research for **The Legend of Zelda: Ocarina of Time's Nintendo Switch 2 release**.

The first performance target is correct rendering at a sustained, real **60 FPS on Intel Arc A770**, with a defined scene and settings. Higher quality follows a measured frame-time budget. Modern integrated GPUs are a later, device-specific target.

**Current state:** working analysis tools, bounded Ghidra decompilation, and the first independently authored leaf-function matching probes. There is no complete reconstructed game or validated performance gain yet. [Status](docs/status.md) records the evidence and limitations; [the roadmap](docs/roadmap.md) defines the next exit tests.

**Development strategy:** [Arc-first targeted reconstruction](docs/strategy.md). Full CPU matching is a supporting long-term track, not a prerequisite for fixing the existing open renderer and shader translator.

## Approach

We are pursuing two connected tracks:

- **Executable reconstruction:** identify exact input revisions and toolchain behavior, reconstruct small units, and verify their output before integrating them.
- **GPU porting:** identify expensive programs and their inputs, preserve their results, and replace inefficient translation paths with implementations suited to PC GPUs.

The verification workflow takes inspiration from [Open-BFME-2](https://github.com/Open-BFME/Open-BFME-2). [Hookshot](https://github.com/NotRexed/Hookshot) provides the existing translation pipeline being investigated. Byte matching, authored source, relocations, linking, playability, rendering correctness, and performance are separate milestones. Reviewed [module metadata](research/cpu-module-inventory.json) and [GPU attribution metadata](research/gpu-profile-initial.json) establish the initial research inputs.

## Start working

Python 3.11 or newer is required for the standard tools. Their synthetic test suite needs no game files or third-party Python packages.

```sh
python3 -m unittest discover -s tests -v
python3 tools/check_public_tree.py
```

For local research, point `GAME_DIR` at your existing game installation. Keep inputs and outputs outside the tracked tree or in the ignored `local/`, `work/`, or `artifacts/` directories.

```sh
mkdir -p local
python3 tools/inventory.py --game-dir "$GAME_DIR" > local/modules.json
python3 tools/profile.py local/gpu-timing.log > local/gpu-profile.json
python3 tools/verify_bytes.py --help
```

`inventory.py` reads module hashes and NSO header metadata. `profile.py` ranks captured GPU timing spans and rejects incomplete or loading-only tables by default. `verify_bytes.py` compares an explicitly bounded range against a hash-pinned reference; it does not establish source provenance or validate relocations.

Additional working tools:

- [NSO-to-ELF analysis conversion](docs/nso.md), with segment hash validation and synthetic tests.
- [Bounded headless Ghidra probes](docs/ghidra.md), validated on two original entry points with private outputs.
- [Reproducible authored matching probes](research/cpu-matching-probe.md): two complete leaf matches and an honestly reported lookup mismatch.
- [Metadata-only shader identity catalog](research/shader-target.md) and the [complete replay contract](docs/shader-replay.md).

## Contribute

Read [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md), choose a bounded issue, and state its pass test before changing code. Public contributions contain tools, independently authored source, metadata, and appropriately licensed patches. Game packages, executable bytes, generated game translations, assets, shader binaries, SDKs, saves, screenshots, and raw logs stay local.

The project tools are licensed under [GPL-3.0-or-later](LICENSE). Third-party patches retain their stated licenses and attribution. Open-Ocarina is an independent community project and is not affiliated with Nintendo.
