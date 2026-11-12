# Native mesh command identity observer

The optional [graphics identity patch](../patches/hookshot/graphics-identity.patch) observes actual native mesh command recording sites in Hookshot revision `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`. Hookshot's upstream source is [NotRexed/Hookshot](https://github.com/NotRexed/Hookshot); the overlay retains its GPL-3.0-or-later license and adds independently authored diagnostic code. Apply the compute identity, page-table snapshot and allocation capture patches first. The occurrence capture overlay also composes before this patch.

The observer records each stage's index, NVN and Vulkan role, translated SPIR-V SHA-256 and word count, requested subgroup policy, program/variant and pipeline keys, cache stamp, retained attachment formats/sample count and requested static state. Fresh translations and full graphics-cache loads both populate stage digests. A missing digest produces `identity_complete=false`, never a fabricated identity. These are requested subgroup values; the driver's actual SIMD width remains unknown.

Five actual Vulkan mesh command sites are covered: direct, indirect, indirect count, converted indirect and converted indirect count. Fallback rendering is excluded. Direct group counts come from the actual normalized API arguments. Indirect records identify the buffer handle, offset, stride, bounded byte range and API maximum count; they do not read argument buffers. GPU-generated group counts and count-buffer values remain null. A CPU-selected ordinal establishes a recording observation, not GPU execution or submission order.

`recording_part` and `logical_recording_value`, when available, come from Hookshot's page-table recording association. The latter is a logical submission value. It is not the actual recording timeline sequence or evidence of completion: `recording_sequence` and `gpu_completion_value` remain null. The observer performs no queue submission, resource readback or GPU synchronization.

## Opt-in configuration

Set `RX_VK_OO_GRAPHICS_IDENTITY_FILE` to a private output JSONL file. Without that switch the observer is disabled. Each line has schema `open-ocarina.graphics-identity/v1` and producer stage `identity-only`.

- `RX_VK_OO_GRAPHICS_PROGRAMS`: comma-separated exact 16-hex keys, at most 16. Defaults to `d8e59341cf4f629c,43ec86f35fd937b9`.
- `RX_VK_OO_GRAPHICS_VARIANT`: optional exact 16-hex variant. Omit it to observe selected programs' actual variants.
- `RX_VK_OO_GRAPHICS_LIMIT`: 1 through 256 records, default 128.

Empty, malformed or prefix filters disable the observer. Stage hashing is limited to selected native mesh programs. The patch does not change native draw arguments, subgroup requests, translator stamp or cache format. Diagnostic file I/O can affect CPU timing, so these runs cannot establish ordinary frame performance.

The emitted metadata is deliberately incomplete for replay: no resource or descriptor contents, physical-pointer reachability, indirect GPU values, temporal state, GPU outputs or complete effective dynamic state are captured. Handle identities are process-local. Retained NVN state is not a certificate of all effective Vulkan state. `replay_complete` and every coverage flag remain false, even when shader identity is complete.

## Verification

Run the game-free synthetic verifier and the composed source check:

```sh
python3 patches/hookshot/check_graphics_identity.py
python3 patches/hookshot/check_graphics_identity.py --checkout "$HOOKSHOT_SOURCE" --extra-patch patches/hookshot/occurrence-capture.patch
python3 -m unittest discover -s tests -v
```

The source check uses an isolated Git index and requires the exact pinned revision. It verifies all five API statements and arguments remain unchanged, all command sites are observed, subgroup policy is unchanged, and the metadata helper contains no GPU commands or resource reads. Synthetic C++20 checks run at `-O2 -DNDEBUG`, validate per-stage hashes, missing digests, exact filters, bounds, indirect unknowns and the logical-value/completion distinction. Clang and GCC checks pass. Production renderer compilation uses the existing Release build, bounded to four workers; no GPU or game execution is part of these checks. Runtime observations require a separately frozen patch/binary hash and an explicitly coordinated run.

## Isolated runtime observation

[Sanitized evidence](../research/runtime-graphics-identity-observation.json) pins
the final patch and optimized renderer exercised in a separate headless run. All
256 bounded events had complete stage identities matching the reviewed nightly
cache. Both selected programs appeared: mesh/fragment for `d8…` and
task/mesh/fragment for `43…`. Task and mesh requested 32 lanes; fragment had no
required-size request. The observed attachments were 1536×864, one sample,
`B10G11R11_UFLOAT_PACK32` color and `D32_SFLOAT` depth.

The observed commands were indirect. Their GPU argument/count contents and
actual executable widths remain unknown. The process stopped cleanly. These
events establish the selected runtime stage identities and requested policy;
they do not identify the variant in an earlier timing run, establish complete
graphics inputs or measure a performance improvement.
