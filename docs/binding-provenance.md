# Vulkan binding provenance observer

`patches/hookshot/binding-provenance.patch` records the original import and
successful binding relationships used by the selected buffer/image capture. It
applies after compute identity, page-table snapshot, allocation capture,
occurrence capture, graphics identity, sampled images, dispatch parameters and
backing leases, at the pinned Hookshot revision used by those patches.

This is metadata evidence for the selected catalog. It does not execute replay,
freeze CPU writers, prove an atomic content snapshot, or establish rendering or
performance equivalence. Earlier captures cannot recover these relationships
retroactively.

A [reviewed occurrence-64 observation](../research/gpu-binding-provenance-observation.json)
now pins the actual host, renderer and patches for one isolated runtime capture.
Recording 7498 contains 126 catalog objects backed by 87 Vulkan-memory
lifetimes. The stricter current checker accepts their creation/bind/selection
ordering, original host leases and matching capture catalogs. Independent
interval analysis maps 132 binding segments into 24 canonical groups and finds
no overlapping segments. This closes the recorded binding-provenance gap for
that catalog. It does not exercise overlapping-alias payload agreement or
establish CPU atomicity, historical shader replay or a performance result.

Enable the existing serial, nonbatched selected-dispatch capture and add:

```sh
HOS_OO_BACKING_LEASE=1 RX_VK_OO_BINDING_PROVENANCE=1
```

The host executable must include the backing-lease patch and export its ABI 1
functions. The renderer resolves them dynamically. Missing, disabled or stale
host provenance remains an explicit gap; it does not change import success or
host discard behavior. Both options default off. Keep the output directory and
all capture files private.

The observer gives every observed buffer, image, Vulkan memory object and VMA
allocation a monotonic process-local lifetime ID. A reused Vulkan handle gets a
new lifetime. VMA's real allocation/free callbacks identify whole Vulkan-memory
lifetimes; successful VMA resource creation records the actual memory handle and
`VmaAllocationInfo.offset`. Buffer creation size remains separate from allocation
padding. Images retain their allocation interval, which is not a linear image
payload layout.

For ordinary imported buffers, the successful `vkBindBufferMemory` relationship
retains the resource extent and binding offset. For sparse imports, every
original `VkSparseMemoryBind` retains `resourceOffset`, `size`, `memoryOffset`
and flags, including a padded final piece beyond the true buffer extent. Edges
are committed only after the queue bind and fence wait succeed. Separate queue
result and fence result events retain that ordering. An incomplete sparse import
publishes no successful binding edges.

The original host backing lease is acquired **before** creating a DMA-BUF export
or allocating imported host-pointer Vulkan memory. The temporary DMA-BUF file
descriptor does not identify the original backing. A failed allocation releases
its local lease. A successful memory lifetime owns the lease until its actual
free; all raw rollback, background, prepublication, retired, evicted and deferred
free paths use one wrapper. Snapshot copies also retain that lease through the
selected recording's completion. This preserves diagnostic lifetime coverage
without changing discard/zero-fill decisions or pinning extra GPU resources.

The selected catalog is copied under the ledger lock before buffer capture
staging allocations. Existing capture source retention remains responsible for
GPU resource lifetime. The ledger snapshot and its lease references survive
until the exact successful recording sequence completes, or release on canceled
recording/failed submission through the existing capture cleanup. Export validates
all retained host leases and collects host backing events through an explicit
endpoint. Per-lease validation and that endpoint are diagnostic observations;
they are not an atomic writer epoch or a lock held through publication.

A disjoint discard can advance the backing's global generation while a lease
remains valid. Validation checks the host status and immutable lease fields;
it does not reject solely because the current generation advanced. An overlapping
invalidation, lost event history, invalid host ABI or unknown owner leaves an
explicit gap. Consumers must retain the interval/event evidence when normalizing
generations; equal guest ranges or bytes never establish alias identity.

The private `binding-provenance.json` uses
`open-ocarina.binding-provenance/v1`. It includes:

- The program, variant and shader digest, selected occurrence, recording part,
  logical value, exact submitted/completed recording sequence and ledger event.
- Catalog buffer/image indices, raw handles, lifetime IDs, true resource extents,
  VMA allocation IDs and committed binding edges.
- Referenced Vulkan-memory lifetimes, import kind and original host lease fields,
  with separate acquire and export validation results.
- Ordered Vulkan observer events through selection, host backing events and
  endpoint, and unresolved gaps. No source payloads are added by this observer.

`backing_provenance_complete` describes binding coverage for the enumerated
catalog only. `replay_complete` and `cpu_writers_quiesced` remain false. The
existing allocation/image manifests keep their conservative gates; this patch
does not silently promote their older schema. Mirrors and writeback shadows
retain their own Vulkan memory/suballocation identities, even when they represent
the same guest interval as an external import. A later normalizer must check
canonical alias payload agreement, resource reachability and writer/temporal
state separately.

The ledger bounds live resource/memory registries to 65,536 entries and history
to 131,072 events. Overflow or observation allocation failure fails provenance
closed. Host history is independently bounded by the backing-lease producer.
Export uses an exclusive temporary file and commits the JSON last; disabled,
premature, failed or short writes do not commit an artifact.

Run the game-free checks:

```sh
python3 patches/hookshot/check_binding_provenance.py --checkout local/hookshot-source
python3 -m unittest discover -s tests -p test_binding_provenance.py -v
python3 patches/hookshot/check_binding_provenance.py --validate-snapshot local/private-capture
```

The checker composes the patch chain in a disposable Git index, audits the real
hook sites and compiles the exact new helper with C++20, `-O2 -DNDEBUG`, warnings
as errors. Synthetic cases cover handle reuse, nonzero bind offsets, sparse
pieces/padded tails, rollback, lease retention, invalidation and disjoint
generation changes, unknown owners, overlap/overflow, distinct mirror allocations,
completion, disabled capture, no overwrite and short-write cleanup. The current
metadata checker also rejects selected lifetimes freed or destroyed before
selection, bindings outside their creation/selection order, sparse submission
before memory creation, and original host mappings that do not cover the imported
extent. Its digest is recorded in the observation so a stricter offline check
is distinguishable from the frozen runtime producer.

Metadata validation is not independent GPU or host attestation. Each new runtime
observation must identify its exact host/renderer and patch hashes. The observed
run's successful leases and complete host event history describe backing
identity; arbitrary CPU writes remain outside that diagnostic registry.
