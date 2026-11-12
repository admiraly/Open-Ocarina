# Mesa adjacency-index backport

`adjacency-index-width.patch` changes the set/clear adjacency-bit indices from `unsigned` to `uint64_t`, consistent with the existing test accessor. Above roughly 92,000 graph nodes, a triangular adjacency index can exceed 32 bits. Truncation can cause the same interference edge to be inserted twice.

This is a backport of an upstream-proposed correction, not a new Open-Ocarina discovery:

- Upstream proposal: https://gitlab.freedesktop.org/mesa/mesa/-/merge_requests/46233
- Proposed commit: https://gitlab.freedesktop.org/mesa/mesa/-/commit/6c3f81a0f43866af8aac2181b52c54e9ceaa661d
- Local base: Mesa 26.2.4 with pre-existing private patches. The patch only includes the two index declarations, not that private driver stack.

Upstream merge status has not been revalidated here. The affected Mesa file carries Intel's MIT notice, reproduced in `LICENSE.MIT`. This directory is covered by that notice rather than the project's GPL license.

## Validation performed

A local C regression allocated a graph with 92,683 nodes and added interference between its last two nodes twice. Before the change, the first node's adjacency array held two entries; afterward it held one. The privately rebuilt Vulkan driver compiled and reached gameplay. The known-working installed driver was preserved.

This does not prove a frame-rate improvement, solve spilling, or enable additional physical registers. Do not apply unrelated large-GRF changes or globally narrow the game's subgroups on the strength of this fix.
