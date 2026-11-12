# NSO-to-ELF analysis adapter

`tools/nso_elf.py` converts a privately supplied NSO module into an ELF64
little-endian AArch64 analysis container for tools such as Ghidra. It is a
segment-mapping adapter, not a reconstructed original ELF, a console loader,
or a native PC executable.

```bash
python3 tools/nso_elf.py \
  --input "$GAME_DIR/.hookshot_exefs/main" \
  --output local/nso/main.elf \
  --base-address 0x80020000 > local/nso-main-metadata.json
readelf -h -l -S local/nso/main.elf
```

The base address is explicit: the inspected Hookshot image map places `rtld`
at `0x80000000` and `main` at `0x80020000`. These are observed mappings for the
local reference build, not a promise about another revision or load layout.
`--base` is an alias for `--base-address`.

The default ELF entry is the start of `.text`, a conventional analysis entry,
not a recovered original ELF entry point. An explicit `--entry-address` must
identify a complete aligned instruction within `.text`.

## What is preserved

- Decompressed `.text`, `.rodata` and `.data` bytes and their NSO memory offsets.
- The header's BSS size, as a zero-initialized memory tail of the RW segment
  and an ELF `SHT_NOBITS` section. BSS bytes are not written into the output.
- RX/R/RW segment permissions. The program-header file and virtual offsets
  satisfy the ELF page-alignment congruence rule, including non-page-aligned
  but instruction-aligned bases.
- Metadata describing input SHA-256, NSO build ID, flags, mappings, and output
  SHA-256. This metadata does not contain executable instructions.

All hashes requested by NSO flag bits 3–5 are checked against the decompressed
segments. Bits 0–2 select independent LZ4 blocks; uncompressed NSOs need no
third-party Python dependency. Compressed NSOs require the optional `lz4`
package in the Python environment running the tool. Missing dependencies and
invalid compressed sizes are reported explicitly. ZBIC (flag bit 7) is not
supported: provide an already unpacked local reference instead. The tool never
rewrites its input. Bit 6 is preserved as uninterpreted metadata; no loader
behavior is inferred from it.

The parser rejects incomplete headers, unknown header versions, unknown flags,
truncated or overlapping file ranges, overlapping memory ranges including BSS,
misaligned text, corrupt declared segment hashes, and excessive image sizes.
The default analysis limit is 512 MiB; `--max-image-bytes` changes that explicit
resource limit. A source-path alias or symlink cannot be used as the output.
Inside the repository, outputs must be under ignored `local/`, `work/`, or
`artifacts/`; those files must never be published.

## Limits that matter for analysis

No MOD0 dynamic relocations are applied and no imports are resolved. Imported
references may therefore be zero or unbound, and decompiler output can be
incomplete or misleading until relocation-aware module linking is implemented.
No symbols, function boundaries, SDK provenance, ABI choices or original
compiler flags are recovered by this conversion. Establish function extents
independently before using the byte comparator as evidence of a function match.

Do not execute the resulting ELF. `file` may describe it as an executable or
statically linked because it has `ET_EXEC` headers and no dynamic loader; those
labels do not establish that the console program is runnable in that form.

## Evidence and reproducibility

The field layout and compression handling were checked against Hookshot's
[`third_party/a64-kit/tools/nso.py`](https://github.com/NotRexed/Hookshot/blob/4cf785a0/third_party/a64-kit/tools/nso.py)
and its
[`nx2/unpack.py`](https://github.com/NotRexed/Hookshot/blob/4cf785a0/nx2/unpack.py)
ZBIC adapter. This implementation is independently authored and deliberately
does not copy the game's code, loader scripts, or SDK files into this repository.

The local `main` module converted successfully with its declared segment hashes
verified. `readelf` recognized machine AArch64, three PT_LOAD entries, the
expected section mappings, and a BSS memory tail. The generated ELF and detailed
local tool output remain private. This is evidence that the adapter handles
the inspected input, not evidence of recovered source or matching progress.

Synthetic tests run without any game input or Ghidra:

```bash
python3 -m unittest discover -s tests -p test_nso_elf.py -v
```
