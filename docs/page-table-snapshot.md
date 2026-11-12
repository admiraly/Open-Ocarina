# GPU-visible guest page-table snapshot

This is the second, opt-in Hookshot capture patch. It snapshots the **actual GPU buffer** containing guest-page device addresses immediately before one selected compute dispatch. It is page-table-only evidence, not complete physical-memory capture, a shader replay, a rendering comparison or a benchmark.

A frozen optimized build has now been tested in an isolated headless run. The artifact's framing/hash and declared completion passed the checker, and source-reviewed recording completion was observed before export. [Sanitized runtime evidence](../research/gpu-page-table-observation.json) identifies the exact patch/binary and missing coverage. Original installs and saves were kept separate, and the test process stopped cleanly. This validates this capture stage, not complete shader replay or independent GPU attestation.

Apply `patches/hookshot/compute-identity.patch` first, then `patches/hookshot/page-table-snapshot.patch`, against upstream `4cf785a0b143bb34026506c9a3fe4f4bb93c797d`. Existing upstream notices apply; the new helper and checker are independently authored under GPL-3.0-or-later. No game code or snapshot bytes are in these patches.

## What the patch records

- A one-shot copy of all 3,145,728 table entries: 25,165,824 bytes, eight little-endian bytes per 4 KiB guest page.
- The exact selected runtime key, variant and SPIR-V digest, root device address and the actual `GUEST_PAGES_PUSH_OFFSET` value. The source does not hard-code that push offset.
- Direct dispatch dimensions, or explicitly unresolved indirect dimensions; observed native-present count; exact recording part, its successful submission sequence and the completed recording sequence.
- The table artifact's SHA-256 and a manifest committed only after the binary write completes.

Referenced imported/mirrored/retired allocation contents, descriptor contents, outputs and temporal state remain **uncaptured**. The manifest explicitly marks those gaps and `replay_complete=false`. This stage cannot pass the complete replay validator. Table addresses alone do not prove which bytes a physical pointer would read.

## Synchronization and lifetime

When explicitly enabled before table creation, the page table gains `TRANSFER_SRC` usage. All ordinary usage bits remain; with capture disabled, its usage is unchanged.

At the selected dispatch, after descriptors/push constants are bound, the patch records a prior-write-to-copy barrier, `vkCmdCopyBuffer` into a cached host-visible staging allocation, a copy-to-host barrier and ordering of later table accesses. The source is `guest_pages_buffer`, not the mapped `guest_pages` array. This preserves the fact that pending table updates are emitted into the recording's upload commands before the main command buffer executes.

The snapshot is tied to the exact command buffer **and recording part**. Only a successful direct queue submission associates it with that recording's dedicated timeline sequence. Export polls that timeline and invalidates mapped memory only after the recorded sequence completes. The game's logical submission timeline is not substituted for exact recording completion.

This first implementation deliberately refuses batched and parallel replay and requires the recording timeline. A failed submission releases staging without publishing anything. An unsubmitted recording discarded at safe slot reuse releases its staging before command-pool reset. Successfully submitted staging remains alive until completion and is destroyed after collection, including export failures. A still-live recording must retain its resources; the backend has no explicit renderer teardown/device-destroy API, so abrupt process exit uses ordinary OS/driver process cleanup rather than an unsafe `atexit` GPU wait.

Recording/present metadata can correlate this snapshot with the selected pipeline, but `identity_event_id` is explicitly null: the existing identity observer's private event counter is not falsely reused. Recording ordinals are not shader timing or a claim of queue-order equivalence across unrelated queues.

## Local diagnostic use

Use a separately built renderer and an isolated test install. Never replace the established working installation for this diagnostic. Configure a **new, empty, existing** private snapshot directory before starting the process:

```sh
mkdir -p local/page-table-run
export RX_VK_OO_IDENTITY_FILE="$PWD/local/identity-run.jsonl"
export RX_VK_OO_IDENTITY_PROGRAM=cc62764061c44f6a
export RX_VK_OO_IDENTITY_VARIANT=095931037888515a
export RX_VK_OO_PT_SNAPSHOT_DIR="$PWD/local/page-table-run"
export RX_VK_OO_PT_SNAPSHOT_SHA256=b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b
export RX_VK_BATCH=0
export RX_VK_PARALLEL=0
```

The identity observer must be enabled so the selected actual stage digest is available. Both key/variant filters are full identifiers; the page snapshot additionally checks the entire digest. A missing/mismatched digest, absent table or unsupported recording mode produces no snapshot. A directory containing previous capture artifacts disables capture rather than overwriting them.

The binary is written as `page-table.bin.part`, then renamed; `manifest.json` is published last. A binary or `.part` file without a committed valid manifest is incomplete. Keep all these artifacts private. The source adds diagnostic barriers, transfer work and file I/O; do not use this run for performance measurements.

## Game-free checks and artifact validation

```sh
python3 patches/hookshot/check_page_table_patch.py --checkout local/hookshot-source
python3 patches/hookshot/check_page_table_patch.py --compiler g++
python3 -m unittest discover -s tests -p 'test_page_table_snapshot.py' -v
python3 patches/hookshot/check_page_table_patch.py --validate-snapshot local/page-table-run
```

The checker applies both patches to a temporary Git index at the pinned revision without changing the source checkout's real index. It compiles the standalone helper with release optimization, rejects premature/unsubmitted/short/null-root snapshots, checks disabled behavior, validates a complete synthetic artifact and rejects overwrites. On POSIX it also forces a short write and verifies that no committed manifest appears. The pure Python tests reject changed hashes, truncated or symlinked artifacts, malformed bounds/completion evidence, stale-host-table declarations and overstated coverage.

Artifact validation checks framing, hashes and **producer declarations**. It does not cryptographically attest GPU execution or independently prove that the producer recorded the correct command. That still requires review of this patch and a controlled runtime observation. The full renderer compiles separately with `-O2 -DNDEBUG`, release settings and bounded workers. No shader subgroup policy is changed.

## Next stage

Associate each nonzero captured page entry with its owning live or retained allocation and correct offset, copy complete reachable payloads and writable outputs at the same command boundary, and preserve aliasing and table updates. A standalone replay may reconstruct the page table with relocated backing allocations only after all physical conversions are shown to use that translation path. Until then this snapshot is one capture prerequisite, not evidence that the target is safe to unpin.
