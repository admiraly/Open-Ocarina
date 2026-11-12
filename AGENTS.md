# Open-Ocarina development rules

## Objective
Build a verifiable source-reconstruction and native-porting workflow for Ocarina of Time's Switch 2 executable and GPU workloads. Target correct rendering and real 60 FPS on Arc A770 before claiming a performance port. Hardware targets are goals, not achieved results.

## Boundaries
- This repository contains tools, independently authored source, metadata and appropriately licensed patches. Never add game executables, generated translations of game code, assets, shader binaries, screenshots, saves, keys, packages, SDKs, credentials or raw logs.
- User-supplied material and tool outputs belong under ignored `local/`, `work/` or `artifacts/`, or outside this repository. No absolute machine paths in tracked content.
- Read game inputs without changing them. Keep the existing working install and driver intact. Never signal unrelated processes or run concurrent GPU experiments. Coordinate GPU use with the primary agent.
- A passing byte-range comparison is not source provenance, relocation verification, whole-image matching, a successful link, or a playable game. Track each separately. Never manufacture progress through embedded original bytes or silently weaken a verifier.
- Changes to verifiers require regression tests. Keep synthetic tests independent of any private game files or installed proprietary tools. Standard tools require Python 3.11+ and no third-party Python packages unless explicitly documented.
- Mark observations, inferences and hypotheses. Timing-table timestamps distort execution; do not equate summed spans with normal FPS. A profiler program hash does not establish an algorithm name. Interleaved allocator logs do not prove shader identity.
- Performance claims require the same save, camera, settings and workload, completed shader compilation, repeated runs, no competing GPU workload, and rendering checks. Record limitations rather than comparing unlike captures.

## Work and validation
Read README.md and docs/status.md when available. Work on the assigned issue; leave unrelated files alone. Keep changes small and reviewable. Run `python3 -m unittest discover -s tests -v` and `python3 tools/check_public_tree.py` before committing. The publication check examines Git's index, so stage only intended public files first.

The user has authorized subagents. A child agent works its assigned scope itself; it must not delegate again without an explicit task instruction. State what changed, what was tested, what remains unknown, and any external tool/version dependencies. Do not publish or push from a child agent; the primary agent reviews and publishes.
