# Bounded Ghidra analysis

The first analysis target is one explicitly chosen ARM64 entry. Import the module with global analysis disabled, disassemble only a bounded address window, and run the decompiler for that entry with a separate timeout. This proves that the local binary can enter a repeatable analysis workflow. It does not prove source matching or correct recovered function boundaries.

## Requirements

- Python 3.11+ for the wrapper and NSO exporter.
- An official [Ghidra release](https://github.com/NationalSecurityAgency/ghidra/releases), with the JDK version its installation guide requires.
- A locally supplied NSO module and sufficient private disk space for its ELF representation and temporary project.

The initial local smoke run uses Ghidra 12.2, obtained from the official `Ghidra_12.2_build` release, with OpenJDK 25. That release's `application.properties` requires Java 25 or newer. Its release asset `ghidra_12.2_PUBLIC_20261016.zip` has SHA-256 `2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67`, verified before extraction. Preserve executable permission bits when extracting the archive: Python's `ZipFile.extractall()` alone does not restore Unix executable permissions. Ghidra and Java are not repository dependencies and are not redistributed here.

## Local workflow

Set `GAME_DIR` to your existing local install and `GHIDRA_HOME` to your extracted Ghidra directory. Choose a new output directory for each run; the wrapper never overwrites a previous probe.

```sh
python3 tools/nso_elf.py --input "$GAME_DIR/.hookshot_exefs/main" \
  --output local/main.elf --base-address 0x80020000
python3 tools/ghidra_probe.py --input local/main.elf \
  --address 0x80020080 --max-bytes 64 \
  --decompile-seconds 20 --timeout 120 --output local/probes/main-small
```

The address and base above belong to the initially inventoried revision, not a universal game revision. Check the module hash and mapping before reusing them. The selected entry was independently present among Hookshot's recovered function boundaries; that does not prove an original function name, type or full boundary.

The wrapper invokes `analyzeHeadless` with `-noanalysis`, `-max-cpu 2`, one `BoundedProbe.java` post-script and `-deleteProject`. The script also disables incremental code analysis. It limits disassembly to 4–4096 bytes and decompilation to at most 60 seconds. The wrapper imposes an independent wall-time limit and terminates only its own child process group on timeout. Ghidra's default headless heap limit is separate; consult the installed release's launcher when changing it.

## Outputs and interpretation

All detailed outputs remain in the chosen private directory:

- `request.json`: input SHA-256 and requested bounds.
- `probe.json`: tool version, instruction count, whether control flow leaves the window, and decompilation status.
- `disassembly.txt` and `decompiled.c`: private, game-derived analysis output. Never commit these.
- `console.log`, `ghidra.log`, `script.log`: private diagnostic logs.

Only a compact metadata summary is printed to stdout. The stdout schema deliberately has no decompiled source. A completed decompilation can still contain incorrect types, unresolved calls and guessed control flow. A probe window can truncate a function. `flow_outside_window` is a warning signal, not proof of full recovery when false. The NSO-to-ELF representation does not apply the game's dynamic relocations or resolve imports, so pointer-heavy functions require additional reconstruction.

Ghidra API references used for the bounded script: [DisassembleCommand](https://github.com/NationalSecurityAgency/ghidra/blob/Ghidra_12.2_build/Ghidra/Features/Base/src/main/java/ghidra/app/cmd/disassemble/DisassembleCommand.java) and the installed release's `support/analyzeHeadlessREADME.html`.

## First completed local smoke test

On 2026-11-12, the validated ELF representation of the initially inventoried `main` module was imported with ELF SHA-256 `f4a869fdc0d082875e5829b0a040253b9475751084e66f29e7f3250679961789`. The probe above at `0x80020080` completed under Ghidra 12.2 with the `AARCH64:LE:64:v8A` language and default compiler specification. It disassembled two instructions, reported no control-flow exit from the 64-byte window, and completed decompilation without a reported error. A second probe at `0x8002a5b0`, also restricted to 64 bytes, completed with six instructions and no control-flow exit. Original unwind records independently identify extents of 8 and 24 bytes for these two entries, respectively; that separate evidence comes from `tools/matching_probe.py`. Global analysis was disabled throughout. Detailed outputs were retained locally, not published.

This is verified evidence that the import/decompile pipeline works on one bounded entry. It is not evidence for a matching toolchain, original function signatures, global source recovery, whole-program linking, or runtime performance.

## Tests

`python3 -m unittest discover -s tests -p 'test_ghidra_probe.py' -v` uses synthetic headers and mocked child processes. It checks command bounds, private output placement, architecture validation and timeout behavior without Ghidra or game files. The real local smoke run is separate evidence; it must not be counted as matched source.
