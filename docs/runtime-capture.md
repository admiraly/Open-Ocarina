# Runtime compute identity and the remaining capture boundary

The first renderer patch is [compute-identity.patch](../patches/hookshot/compute-identity.patch), against the exact upstream commit documented in [its README](../patches/hookshot/README.md). It is a diagnostic source change, not a performance optimization.

The optimized patched renderer has now run in a separate headless test install. All 256 bounded events matched the selected key, variant and exact SPIR-V digest while retaining the required 32-lane policy. [Sanitized runtime metadata](../research/runtime-identity-observation.json) records the observed direct grids, binary/source identities and explicit missing coverage. This confirms the variant in that current run, not retrospectively in the older timing capture. The test stopped cleanly and does not establish rendering equivalence or an FPS gain.

## What is recorded

At the successful compute-command recording site, after descriptor binding and push constants and immediately before `vkCmdDispatch` or `vkCmdDispatchIndirect`, an opt-in observer records:

- Runtime program key and texture-answer variant.
- SHA-256 of the actual stage SPIR-V, serialized canonically as little-endian words. Missing identity is represented as null, never a fabricated digest.
- Shader workgroup size, no provided specialization overrides, requested subgroup size and full-subgroup flag, and the program-cache stamp.
- Direct dispatch geometry. An indirect dispatch records its guest argument address, but its GPU-produced argument contents and actual group count remain unknown.
- An NVN-slot/registration-generation fingerprint. This is not a descriptor snapshot, input-content digest, complete resource identity or memory-reachability proof.
- Device vendor, device and driver version IDs.
- A per-process event ID, selected recording ordinal and observed native-present counter. The ordinal describes CPU command recording order for selected compute commands; it is not a queue-submission sequence or proof of GPU completion. Submission identity is explicitly null.

The stage digest is populated on fresh translation/save, full cache reads and light compute-cache completion. For the selected opt-in target only, an opaque module-identifier cache shortcut is bypassed to ensure the ordinary SPIR-V-bearing path runs. Normal driver pipeline caching still works. Other programs and default subgroup policy are unchanged.

## Local use

Build the patched renderer separately and use an isolated local test installation. Configure the output path before changing into that installation:

```sh
mkdir -p local
export RX_VK_OO_IDENTITY_FILE="$PWD/local/compute-identity.jsonl"
export RX_VK_OO_IDENTITY_PROGRAM=cc62764061c44f6a
export RX_VK_OO_IDENTITY_VARIANT=095931037888515a
```

Both filters require exactly sixteen hexadecimal digits; prefixes and malformed values disable observation. If unset, the program and variant default to the first investigated compute target. The file switch is required. At most 256 selected events are written per process. Keep the JSONL output local: it can include guest memory addresses and run-specific state.

These are diagnostic runs, not performance measurements. Hashing, file I/O and altered cache-loading behavior are overhead. The patch never changes subgroup selection, inserts a resource snapshot, executes an extra dispatch or measures actual hardware SIMD width.

## Why this is not complete capture

The investigated shader genuinely follows physical storage-buffer pointers. Its physical guest-page root comes through a push constant, and the table maps guest pages to device addresses of imported or mirrored pools. Explicit descriptor bindings alone cannot enumerate all data it reads.

Hookshot's `vk_memory.cpp` maintains `guest_pages`, `guest_pages_buffer`, `guest_pages_set`, `memory_table_begin` and `memory_table_flush`. Table changes made during a recording can be deferred into upload-command `vkCmdUpdateBuffer` operations after mirror copies. Therefore the current host-mapped table and later guest CPU memory are not automatically the table and bytes visible to the selected GPU dispatch. `push_constants` supplies `memory_guest_pages()` at the guest-page-root push offset.

The next capture producer must snapshot the actual GPU-visible table at the selected command position, include every reachable allocation with alias relationships, preserve pending page-table updates and upload/dispatch barriers, and obtain complete before/after writable data. It must also resolve GPU-produced indirect dimensions and relevant temporal state. A same-address in-process comparison could avoid cross-process device-address relocation, but it still needs complete write/alias coverage and identical restored inputs.

Until those requirements are implemented and validated, these events stay `identity-only`. The buffer-only replay-contract validator must refuse this physical-pointer target rather than treating empty or partial resources as complete.
