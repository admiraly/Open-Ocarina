# Roadmap and exit tests

The initial milestone is a verifiable reconstruction and GPU-porting workflow. A playable translated build is useful reference behavior, but it is not a completed source reconstruction. Each milestone has an independent exit test.

## 1. Input identity and toolchain inventory

Record module SHA-256 digests, build IDs, section bounds and compression flags. Inventory the compiler, linker and ABI fingerprints that are present; distinguish an inference from a known toolchain version. Validate an analysis import against the module's addresses and section permissions.

**Exit test:** another contributor with the same local revision can generate the same metadata and import a bounded region at the same addresses. The procedure rejects truncated modules and mismatched input revisions. No original executable bytes are required in the repository.

## 2. Bounded function reconstruction and verification

Select small, identifiable functions with useful coverage of control flow, object access and calling conventions. Recover boundaries and dependencies before attempting a match. Preserve a known-good reference and record compilation settings.

**Exit test:** an independently authored function compiles through a documented toolchain and matches its verified reference extent, with relocation and symbol-identity checks where applicable. A deliberate instruction, reference revision or relocation error must fail. Track a standalone link and startup separately; a bounded byte comparison alone does not complete this milestone.

## 3. Shader identity and deterministic replay capture

Start with a program ranked by a full-workload timing capture. Establish the relationship between the profiler identifier, pipeline stages, translated shader, resource bindings and input data. Mixed graphics-pass endpoints are insufficient attribution. Keep captured shaders and game data private.

**Exit test:** a documented local procedure captures and replays the chosen workload with the same inputs and reference output. Metadata identifies the exact stages, compiler settings, resource layout and validity checks. The procedure detects a changed binding or input and does not rely on allocator-log chronology for identity.

## 4. Equivalent-scene benchmark and first measured shader change

Define one save, camera, scene state, input sequence, resolution and settings. Separate cold compilation, warm startup, unprofiled frame timing, and intrusive GPU attribution. Use the replay to test a targeted translation or algorithm change while preserving results.

**Exit test:** a small source change passes rendering/behavior validation and improves repeated warm measurements under equivalent conditions. Record the device, software versions, sample duration, distributions and regressions. A reduction in spills or instruction count is supporting evidence, not the performance result.

The first A770 performance objective is sustained real 60 FPS under the defined workload. Increase quality only after establishing a frame-time budget. Integrated-GPU work names specific devices and settings rather than promising a result for an entire hardware category.

## Work selection

Issues should name one concrete target, its current evidence, the files they may change, and their pass test. Verifier defects take priority over claims that depend on the defective check. Preserve earlier measurements and mark an invalid comparison as invalid instead of folding it into a progress figure.
