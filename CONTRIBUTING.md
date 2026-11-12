# Contributing

Read [AGENTS.md](AGENTS.md) first. Work in small units with explicit evidence and a pass test. A useful contribution makes a reconstruction, verifier, replay, or measured rendering path better and leaves its result reproducible.

## Work cycle

1. Choose an open issue or describe a bounded experiment. Record the input revision, question, proposed change, and acceptance criteria.
2. Inspect the current implementation and available evidence. Separate observations from hypotheses. A program hash alone does not identify an algorithm.
3. Make one independently reviewable change. Verifier changes need regression tests that demonstrate the failure they fix.
4. Run the synthetic checks and the task's pass test. Record tool versions and relevant limitations. Use local game material only when the pass test requires it.
5. Stage only intended public files, inspect the staged diff, and run the publication check. Commit a concrete result with its validation; include a `Verifier-Change: <reason>` trailer when changing a gate or its acceptance rules.

```sh
python3 -m unittest discover -s tests -v
git diff --check
git diff --cached
python3 tools/check_public_tree.py
```

The public-tree check examines Git's index. It is a bounded artifact and credential check, not a complete secret scanner or proof that a file is appropriate to publish. Review new files before staging them.

## Reconstruction evidence

Pin reference modules by their complete SHA-256 digest. Record exact function/range boundaries, load addresses, compilation settings, and tool versions when known. Keep unknown compiler settings explicitly unknown.

Report these independently:

- a byte-range match;
- the provenance and readability of reconstructed source;
- function identity, boundaries, and relocation correctness;
- successful linking without stubs or forced unresolved symbols;
- startup and gameplay behavior;
- rendering equivalence;
- measured performance.

Do not claim source reconstruction from a copied binary, an embedded byte array, a generated instruction translation, or a comparison against the wrong revision. Do not silently mask mismatches or weaken a gate to improve a progress figure.

## Performance experiments

Coordinate GPU use before running an experiment. Preserve the working game install and driver; never stop unrelated processes. Use an isolated build and copied saves where appropriate.

A comparison needs the same save, camera, scene state, settings, resolution, workload and input sequence. Complete shader compilation, close competing GPU workloads, repeat runs, and inspect rendering. Collect ordinary frame timing separately from intrusive GPU-pass attribution. Report distributions and sample durations, not only a peak FPS reading.

Changing dispatch counts, missing draws, incomplete lighting, a different camera, or an altered quality level invalidates an otherwise attractive comparison unless that difference is the declared experiment. A compiler regression fix can be valuable without demonstrating a gameplay speedup.

## Public material

Keep private inputs and analysis output in ignored `local/`, `work/`, or `artifacts/` directories, or outside the repository. Do not publish game packages, executable bytes, generated game C, assets, compiled shaders, screenshots, saves, keys, SDKs, raw logs, credentials, or machine-specific absolute paths.

Prefer small, reviewed metadata reports: module hashes, tool versions, aggregate timings, shader identifiers, source references, and limitations. A report should explain how another contributor can regenerate the evidence using their own inputs. Third-party source and patches need their original license and attribution.
