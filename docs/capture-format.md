# Compute capture contract v1

`tools/replay_contract.py` validates a versioned **logical-buffer-only** capture manifest and compares all declared writable allocations byte for byte. It does not capture GPU state or execute Vulkan replay. A consistent manifest is not proof that a producer enumerated every dependency or synchronized its snapshots correctly; the result reports that limit explicitly.

The schema identifier is `open-ocarina.compute-capture/v1`. Unknown fields, unsupported schema versions, duplicate JSON keys and non-finite JSON values are rejected. This deliberately narrow version requires complete direct compute dispatches, ordinary buffer descriptors and a self-contained initial state. Images, GPU-produced indirect dispatch arguments, external temporal dependencies, physical pointers and implicit floating-point tolerances are not implemented.

## Actual target remains unsupported

The identified `cc62764061c44f6a` shader loads through physical addresses derived from a push-constant page-table root. Bound descriptor buffers alone do not describe all reachable data. The validator independently refuses `OpConvertUToPtr`, physical-storage pointer types and non-logical addressing models, even when a manifest falsely calls the shader logical-buffer-only. A capability declaration alone is not interpreted as pointer use.

The local target SPIR-V was checked and is rejected as intended. It cannot become an accepted v1 contract by omitting page-table allocations or setting a boolean. Supporting it requires a new, reviewed capture/address-replay implementation, not weakening this gate.

## Layout

Each private capture directory contains `manifest.json` and its referenced artifacts. Artifact paths are normalized relative paths beneath that directory. Absolute paths, traversal, symlinked components, missing files, wrong lengths, changed files and digest mismatches are rejected. No binary content is emitted by the tool.

Every artifact reference has exactly `path`, `size` and a lowercase full `sha256`. The manifest is bounded to 2 MiB; SPIR-V to 64 MiB; each allocation snapshot to 1 GiB; resource views to 4,096; descriptor elements to 65,536. These are explicit tooling limits, not device limits.

| Manifest field | Required contents |
|---|---|
| `producer` | Name, pinned revision, stage and hashed coverage-evidence artifact. Only `complete-buffer-capture` is accepted. A runtime `identity-only` event is incomplete. |
| `pipeline` | Runtime program and variant keys, SPIR-V artifact, compute entry, local size, specialization values, subgroup policy, complete buffer layout, translator/cache stamps, device/driver UUIDs and versions, enabled features/extensions and floating-point controls. |
| `dispatch` | Direct group count and present/submission/dispatch ordinal plus a producer event ID. |
| `allocations` | Stable allocation IDs, complete allocation sizes, full initial snapshots and complete expected snapshots for every allocation containing a writable view. |
| `resources` | Buffer views with stable IDs, allocation identity, byte offset/size, read/write mode, semantic role and explicit `byte_exact` comparison. |
| `bindings` | Every descriptor set/binding/array element, resource view and bound byte range. No missing array elements or unclassified/unbound views are accepted. |
| `aliases` | Every pair of overlapping views of the same backing allocation. The tool computes overlaps and checks this list exactly. |
| `push_constants` | A complete snapshot, including an explicitly hashed empty file when no bytes are used. |
| `address_model` | `logical-buffers` plus explanatory evidence. Actual SPIR-V framing, entry, local size, descriptor decorations and physical-pointer restrictions are checked independently. |
| `temporal` | `self-contained-dispatch`, no external dependencies, and explanatory producer evidence. |
| `synchronization` | Before/after snapshot records tied to the selected dispatch event, method `gpu-visible-command-position`, and synchronization evidence. Later CPU guest memory is not an accepted substitute. |

Device and driver identity are intentionally strict for the first same-device experiment. Cross-device oracle comparison requires an explicit future extension. Subgroup policy, SPIR-V, translator and cache stamps may differ between a reference and a candidate; all other compared input/resource contracts must agree.

### Coverage evidence

The hashed JSON evidence uses schema `open-ocarina.buffer-coverage/v1`. It contains the SPIR-V digest, the producer's enumeration method, complete descriptor slot triples, resource IDs and an empty `unresolved_accesses` list. The tool cross-checks those declarations against the manifest.

This checks evidence integrity and consistency, **not the truth of the producer's semantic coverage claim**. Explanatory address, temporal and synchronization evidence is also not a machine proof. Independent producer review, resource reachability analysis and baseline repetitions remain required. Reports therefore contain `producer_completeness_independently_proven: false` and `replay_implemented: false`; callers must not turn an exit-zero validation into a replay-readiness claim.

### Buffer and output rules

Views preserve allocation identity and overlap. Capturing two copies of aliased views as independent allocations is not equivalent to preserving their backing relationship; producer review must verify those identities against the runtime. View and descriptor ranges must fit their backing sizes. The SPIR-V's declared descriptor set/binding identities must exist in the pipeline layout; this parser does not fully reflect descriptor array types or prove access footprints.

Every allocation touched by a writable view needs a **full allocation** expected snapshot. Comparisons cover all of those bytes, including unnamed/padding regions outside the writable view, so an unexpected out-of-view change is not hidden by a narrow compare. Integer counters, indirect arguments and opaque data all use the same byte-exact comparator. There is no implicit epsilon or format-based float relaxation. A zero-output capture cannot pass vacuously.

The SPIR-V checker only establishes framing, selected compute entry/local-size consistency, descriptor identities and conservative addressing restrictions. It is not a replacement for `spirv-val`, alias analysis, data-race analysis or the producer's complete capture contract.

## Commands and interpretation

```sh
python3 tools/replay_contract.py validate local/captures/reference
python3 tools/replay_contract.py compare local/captures/reference local/captures/candidate
python3 -m unittest discover -s tests -p 'test_replay_contract.py' -v
```

`tests/test_replay_contract.py` contains a complete independently authored synthetic manifest factory. Its artifacts are framing/contract fixtures and are never submitted to a GPU. They are not game captures.

Comparison first validates every artifact, then requires equal initial-memory digests, push constants, launch geometry, layouts, view/alias relationships and device/float controls. It reads all expected writable allocations and rechecks their digests during the byte comparison. Results contain allocation IDs and first mismatch offsets, not original or candidate bytes. Changed shader digests and subgroup policies are reported explicitly.

Exit 0 means the declared manifest is internally consistent, or its declared output bytes agree. Exit 1 means output bytes differ. Exit 2 means malformed input, unsupported capture state, inconsistent inputs, missing content or another verification error. None of these outcomes establishes producer completeness, runtime rendering equivalence, whole-game correctness or an FPS gain.
