# First bounded CPU matching experiment

Date: 2026-11-12. Status: two authored leaf probes match; one six-instruction probe does not. These are selected experiments, not a project completion percentage.

## Reference and boundary evidence

The input is the user's local `main` NSO module. Its complete-file SHA-256 is:

```
2591fb99008d0814f8056c3f36b36790048ed857ff0ae2bb3a4c1e37b960a4de
```

The NSO text segment is uncompressed, starts at file offset `0x104`, and has memory offset zero. Virtual addresses below use Hookshot's main-module analysis base `0x80020000`; this is an analysis layout, not a claim about the console's runtime ASLR address.

The original `.eh_frame_hdr` is at module memory offset `0x3f864c8`. Its encoding is version 1, pcrel/sdata4 frame pointer, udata4 count, and datarel/sdata4 search-table entries. The selected FDEs refer to a version-1 `zR` CIE at `0x4169c40` with pcrel/sdata4 function addresses. Each table start agrees with its FDE's initial location, and the FDE supplies the full byte extent. `tools/matching_probe.py` checks this evidence directly from the supplied NSO; it does not infer function size from the next start, alignment padding, or a successful short prefix match.

Hookshot's existing local translation and discovered-function metadata supplied starting leads. The tracked candidates were independently written as small C expressions; no generated translation or original instruction-byte array is included. Names and object layouts are analysis descriptions, not recovered original names/types.

## Observed results

| Authored probe | Analysis address | Original extent | Original file offset | FDE offset | Result |
|---|---|---|---|---|---|
| `src/probes/clear_first_byte.c` | `0x80020080` | 8 bytes | `0x184` | `0x4172084` | Complete byte match |
| `src/probes/upper_two_bits.c` | `0x8002c800` | 12 bytes | `0xc904` | `0x41cb0f0` | Complete byte match |
| `src/probes/lookup_payload.c` | `0x8002a5b0` | 24 bytes | `0xa6b4` | `0x41836a0` | Does not match; first difference at byte zero |

The lookup candidate and original both contain six instructions and use the same observed operation sequence: signed stride load, base-pointer load, signed multiply, indexed pointer load, payload-pointer load, return. Their temporary-register assignments differ. This identifies a concrete code-generation mismatch to investigate, not a proof that all runtime semantics or original C types have been recovered.

The compiler used was Fedora Clang `22.1.8` (`22.1.8-4.fc44`), targeting `aarch64-none-elf`. Shared flags:

```
-O2 -ffreestanding -fno-stack-protector -fno-builtin
-fno-asynchronous-unwind-tables -fno-unwind-tables
```

A bounded additional sweep of `-O1`, `-O2`, `-O3`, `-Os`, and `-Oz`, each with generic, Cortex-A57, Cortex-A78, and Cortex-A78C code-generation settings, did not match the lookup function. These are experimental compiler settings; they do not identify the original target CPU or compiler.

## Reproduce locally

Prerequisites: Python 3.11+, AArch64-capable Clang, and `llvm-objcopy`. No console SDK is required for these freestanding probes. Point `GAME_DIR` at an existing local install; do not download or commit game files.

```sh
python3 -m unittest discover -s tests -p 'test_matching_probe.py' -v
python3 tools/matching_probe.py \
  --reference "$GAME_DIR/.hookshot_exefs/main" \
  --out-dir local/cpu-matching/repro
```

The command pins the reference hash, validates original unwind extents, compiles the tracked C candidates, extracts each candidate's `.text` section, requires its length to equal the full original function extent, and invokes the byte comparator. Candidate objects, extracted candidate text, and `report.json` stay in ignored local storage. The output report contains metadata and mismatch positions, not original instruction bytes. The default exit code is zero when the experiments execute successfully; `--require-all-matched` makes the known lookup mismatch return one. Input/tool/format errors return two. A different compiler version may change the observed matches.

## What this establishes

- Two independently authored, relocation-free leaf functions can already be rebuilt to their original complete bytes with an available modern compiler.
- Function-boundary validation can use original unwind metadata instead of trusting generated-C chunk boundaries.
- A slightly richer leaf exposes a reproducible register-allocation mismatch suitable for the next compiler/source investigation.

It does **not** identify the original toolchain, recover original source, validate functions with calls/global references/relocations, produce a whole-program link, or establish gameplay correctness. No larger reconstruction total is credited from these probes. Compiler identification and representative non-leaf probes remain open work.

## Provenance

- Existing [Hookshot](https://github.com/NotRexed/Hookshot) local analysis provides the initial module layout and function leads.
- [NSO format description](https://switchbrew.org/wiki/NSO) describes the executable container. The parser implementation and regression fixtures in this project are independently authored.
- Original FDE metadata, compiler output, and disassembly used for this experiment are private local analysis artifacts; they are intentionally absent from the repository.
