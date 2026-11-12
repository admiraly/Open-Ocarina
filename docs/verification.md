# Verification before progress claims

Open-Ocarina takes inspiration from Open-BFME's small verified work units. Its numbers are not imported as our progress. This project starts with tools and observations, not a recovered game source tree.

## Separate gates

1. **Input identity:** hash every original module and pin the exact revision. `tools/inventory.py` records NSO header metadata, not game bytes. A filename or title ID is insufficient.
2. **Boundaries:** establish complete function extents, entry points, tail calls and shared code. A matching prefix is not a matching function.
3. **Byte identity:** compare compiled code with the hash-pinned original. `tools/verify_bytes.py` provides only a bounded byte comparison. It does not compile source, resolve symbols, validate relocations or establish provenance.
4. **Source ownership:** identify independently authored reconstruction, generated translation, assembly, library code and original-byte embeddings separately. Embedded original bytes never count as reconstructed source.
5. **Relocations and linking:** validate relocation types, addends, symbol ownership, layout and linking without stubs or forced unresolved references. This gate is not implemented yet.
6. **Behavior:** boot, gameplay, save/load and deterministic subsystem checks. Matching isolated functions does not establish a working executable.
7. **Rendering:** compare identical inputs, camera and temporal history. A lighting pass omitted during compilation is a failure, even if FPS rises.
8. **Performance:** repeated unprofiled runs at the same save, camera, options, input sequence and completed workload, without competing GPU use. Profile separately for attribution. Include hardware, driver, commits, cache state and sample-selection rules.

Only evidence satisfying a gate may advance that gate. Keep failed experiments and unresolved objections. Do not weaken a gate in the same change just to pass a reconstruction.

## Local byte comparison

```sh
python3 tools/verify_bytes.py --reference "$REFERENCE" --candidate "$CANDIDATE" \
  --reference-sha256 "$REFERENCE_SHA256" \
  --reference-offset 0x100 --candidate-offset 0 --size 0x20
```

Offsets refer to file bytes. A compressed NSO section cannot be compared directly with decompressed compiled code. First produce a local, version-pinned analysis image and document its relationship to the original. Reference hashes, extents and provenance must not be silently changed after a mismatch.

Exit codes: 0 for equal requested bytes, 1 for differing bytes, 2 for invalid inputs. A matching result must not be converted into a project-wide matching percentage.

## Shader evidence

Runtime program keys, shader-seed IDs, variants, stage identities and SPIR-V digests are distinct. Use explicit cache records or instrumentation to establish relationships. Interleaved allocator records are not enough. A graphics pass's first and last hashes do not identify every program within it.

Replay capture must include the exact compiled input, pipeline specialization, subgroup assumptions, descriptors and resource contents, launch dimensions, barriers and relevant temporal state. Existing bounded resource debug dumps are not automatically replay captures.

## Publication

Stage only intended public files, inspect the diff, run the synthetic suite and `python3 tools/check_public_tree.py`, then inspect the committed tree before pushing. The checker rejects common artifacts and token shapes; it is not a complete secret scanner or proof of redistributability. Keep raw logs and captures private. Publish explicitly selected metadata and independently authored tools.
