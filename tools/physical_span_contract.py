#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU-only load-span proof for one digest-pinned physical-address shader.

This proves bounded byte extents, not capture atomicity, alias provenance, races,
original address alignment, or complete Vulkan device-state equivalence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

TARGET_SHA256 = "b9af52c0ba07f64eb8b0ca2905c432d094999fdf669aa95182ff7754b510e97b"
U64 = 1 << 64
PAGE = 4096
PHYSICAL = 5349
TYPED = {41, 42, 43, 44, 54, 55, 57, 59, 61, 65, 120, 128, 130,
         166, 167, 169, 171, 176, 178, 194, 196, 199}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def edge_dominates(edges, entry, true_source, true_target, site):
    """An edge must be taken to reach site, including with loops and bypasses."""
    def reachable(excluded):
        seen, pending = set(), [entry]
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            pending.extend(target for target in edges.get(node, ())
                           if (node, target) != excluded)
        return seen
    return site in reachable(None) and site not in reachable((true_source, true_target))


class Module:
    def __init__(self, data):
        require(len(data) >= 20 and len(data) % 4 == 0, "Invalid SPIR-V framing")
        words = struct.unpack(f"<{len(data) // 4}I", data)
        require(words[0] == 0x07230203, "Not little-endian SPIR-V")
        self.nodes, self.functions, self.locations, self.offsets = {}, {}, {}, {}
        self.edges, self.branches, self.entries, self.block_contents = {}, {}, {}, {}
        current, block, entrypoints = None, None, []
        at = 5
        while at < len(words):
            count, op = words[at] >> 16, words[at] & 65535
            require(count and at + count <= len(words), "Truncated SPIR-V instruction")
            args = list(words[at + 1:at + count]); result = None
            if 19 <= op <= 38 or op == 248:
                require(bool(args), "Missing result ID")
                result, node = args[0], (op, args[1:], None)
            elif op in TYPED:
                require(len(args) >= 2, "Missing typed result ID")
                result, node = args[1], (op, args[2:], args[0])
            if result is not None:
                require(result not in self.nodes, "Duplicate relevant result ID")
                self.nodes[result] = node
            if op == 15:
                entrypoints.append((args[0], args[1], struct.pack(f"<{len(args)-2}I", *args[2:]).split(b"\0", 1)[0]))
            if op == 72 and len(args) == 4 and args[2] == 35:
                self.offsets[(args[0], args[1])] = args[3]
            if op == 54:
                current, block = result, None
                self.functions[current] = []
                self.edges[current], self.branches[current] = {}, []
                self.block_contents[current] = {}
            if op == 248:
                block = result
                self.entries.setdefault(current, block)
                self.edges[current][block] = []
                self.block_contents[current][block] = []
            if current is not None:
                self.functions[current].append((op, args, result))
                if block is not None:
                    self.block_contents[current][block].append((op, args, result))
                if result is not None:
                    self.locations[result] = (current, block)
                if op == 249:
                    self.edges[current][block] = [args[0]]
                elif op == 250:
                    self.edges[current][block] = args[1:3]
                    self.branches[current].append((block, args[0], args[1], args[2]))
            if op == 56:
                current, block = None, None
            at += count
        mains = [fid for model, fid, name in entrypoints if model == 5 and name == b"main"]
        require(len(mains) == 1, "Expected one compute main entry")
        self.main = mains[0]
        pending, self.reachable = [mains[0]], set()
        while pending:
            function = pending.pop()
            if function in self.reachable:
                continue
            require(function in self.functions, "Unknown called function")
            self.reachable.add(function)
            pending.extend(args[2] for op, args, _ in self.functions[function] if op == 57)

    def node(self, result, op=None):
        require(result in self.nodes, "Missing relevant instruction")
        actual, args, kind = self.nodes[result]
        require(op is None or op == actual, "Physical address structure differs")
        return args, kind

    def constant(self, result):
        args, kind = self.node(result, 43)
        width, signed = self.node(kind, 21)[0]
        require(not signed and width in (32, 64) and len(args) == width // 32, "Unsupported integer constant")
        return sum(value << (32 * index) for index, value in enumerate(args))

    def width(self, kind):
        op, args, _ = self.nodes[kind]
        if op == 21:
            require(args == [32, 0] or args == [64, 0], "Unsupported physical integer type")
            return args[0] // 8
        require(op == 23 and args[1] == 4, "Unsupported physical vector type")
        return self.width(args[0]) * args[1]

    def operands(self, result, op, count=2):
        args, _ = self.node(result, op)
        require(len(args) == count, "Unexpected address operand count")
        return args

    def pointer_address(self, pointer):
        args, kind = self.node(pointer, 65)
        require(len(args) == 2 and self.constant(args[1]) == 0, "Physical load is not first struct member")
        converted, converted_type = self.node(args[0], 120)
        storage, structure = self.node(converted_type, 32)[0]
        require(storage == PHYSICAL and self.offsets.get((structure, 0)) == 0, "Physical member offset is not zero")
        return converted[0]

    def flatten_and(self, result):
        op, args, _ = self.nodes[result]
        return self.flatten_and(args[0]) + self.flatten_and(args[1]) if op == 167 else [result]

    def guarded(self, function, condition, site):
        _, block = self.locations[site]
        return any(predicate == condition and edge_dominates(self.edges[function], self.entries[function], source, yes, block)
                   for source, predicate, yes, _ in self.branches[function])


def validate_windows(windows):
    """Derive maximum entry index without trusting a producer's window flags."""
    require(len(windows) == 2, "Expected exactly two guest windows")
    next_index = 0
    for window in windows:
        base, size, first = window
        require(0 <= base < U64 and size > 0 and base + size <= U64 and size % PAGE == 0,
                "Guest window overflows or has a partial page")
        require(first == next_index, "Guest page-index ranges have a gap or overlap")
        next_index += size // PAGE
    require(windows[0][0] + windows[0][1] <= windows[1][0], "Guest windows overlap")
    return next_index


def acyclic(edges):
    """Kahn's algorithm: reject residual cycles without relying on DFS ordering."""
    nodes = set(edges) | {target for targets in edges.values() for target in targets}
    incoming = {node: 0 for node in nodes}
    for targets in edges.values():
        for target in targets:
            incoming[target] += 1
    pending = [node for node, count in incoming.items() if count == 0]
    consumed = 0
    while pending:
        node = pending.pop(); consumed += 1
        for target in edges.get(node, ()):
            incoming[target] -= 1
            if incoming[target] == 0:
                pending.append(target)
    return consumed == len(nodes)


def finite_work(module):
    """Check the reviewed counted loops rather than treating a fence as proof."""
    calls = {function: [args[2] for op, args, _ in module.functions[function] if op == 57]
             for function in module.reachable}
    require(acyclic(calls), "Recursive shader call graph")
    loops, total_calls, no_loop_functions = [], 0, 0
    for function in module.reachable:
        total_calls += len(calls[function])
        graph = {source: list(targets) for source, targets in module.edges[function].items()}
        function_loops = []
        for header, instructions in module.block_contents[function].items():
            merges = [raw for op, raw, _ in instructions if op == 246]
            if not merges:
                continue
            require(len(merges) == 1, "Multiple loop merges in one block")
            merge, continuation, _ = merges[0]
            require(len(graph[header]) == 1, "Unexpected loop header")
            check = graph[header][0]
            branches = [row for row in module.branches[function] if row[0] == check]
            require(len(branches) == 1 and branches[0][3] == merge, "Loop false edge does not exit")
            condition = branches[0][1]
            loaded_counter, bound_id = module.operands(condition, 176)
            counter_load, counter_type = module.node(loaded_counter, 61)
            require(module.width(counter_type) == 4 and len(counter_load) == 1, "Unexpected loop counter load")
            counter = counter_load[0]
            variable_args, variable_type = module.node(counter, 59)
            require(variable_args == [7] and module.node(variable_type, 32)[0][0] == 7,
                    "Loop counter is not private function storage")
            bound = module.constant(bound_id)
            require(bound == 3, "Descriptor search bound differs from three entries")
            stores = []
            for source, content in module.block_contents[function].items():
                for op, raw, _ in content:
                    if counter not in raw:
                        continue
                    require(op in (59, 61, 62), "Loop counter address escapes or is aliased")
                    if op == 62 and raw[0] == counter:
                        stores.append((source, raw[1]))
            require(len(stores) == 2, "Loop counter has extra writers")
            init = [(source, value) for source, value in stores if source != continuation]
            progress = [(source, value) for source, value in stores if source == continuation]
            require(len(init) == len(progress) == 1 and module.constant(init[0][1]) == 0,
                    "Loop counter is not initialized once to zero")
            lhs, rhs = module.operands(progress[0][1], 128)
            require(lhs == loaded_counter and module.constant(rhs) == 1 and graph[continuation] == [header],
                    "Loop lacks unit progress at the unique backedge")
            init_source = init[0][0]
            require(graph[init_source] == [header] and
                    edge_dominates(graph, module.entries[function], init_source, header, header),
                    "Initialization does not dominate loop entry")
            require(all(source == continuation or source == init_source
                        for source, targets in graph.items() if header in targets), "Alternate loop entry/backedge")
            graph[continuation] = []
            function_loops.append({"iterations_at_most": bound, "counter_bits": 32, "step": 1})
        require(acyclic(graph), "Unresolved control-flow cycle outside reviewed counted loops")
        if function == module.main:
            require(not function_loops, "Reviewed main is not acyclic")
        if not function_loops:
            no_loop_functions += 1
        loops.extend(function_loops)
    require(len(loops) == 2 and total_calls == 72 and no_loop_functions == 1,
            "Reviewed finite-work inventory differs")
    return {"reachable_functions": len(module.reachable), "main_has_backedges": False,
            "static_helper_call_sites": total_calls, "counted_descriptor_search_loops": loops,
            "recursive_calls": False, "unresolved_cycles": [],
            "scope": "Finite shader instruction control flow; not a deadline, memory-validity or GPU scheduling guarantee"}


def inspect_module(spirv_bytes):
    require(hashlib.sha256(spirv_bytes).hexdigest() == TARGET_SHA256, "Unreviewed shader digest")
    module = Module(spirv_bytes)
    helpers, physical_count = [], 0
    for function in module.reachable:
        physical = []
        for op, raw, result in module.functions[function]:
            if op != 61:
                continue
            pointer = raw[2]
            _, pointer_type = module.node(pointer)
            if pointer_type and module.nodes.get(pointer_type, (None,))[0] == 32:
                if module.node(pointer_type, 32)[0][0] == PHYSICAL:
                    physical.append((result, raw))
        if not physical:
            continue
        physical_count += len(physical)
        require(len(physical) == 2, "Expected entry-load/data-load physical pair")
        entry, data = physical
        entry_id, entry_raw = entry; data_id, data_raw = data
        require(module.width(entry_raw[0]) == 8 and entry_raw[3:] == [2, 8], "Table load width/alignment differs")
        width = module.width(data_raw[0])
        require(width in (4, 16) and data_raw[3:] == [2, 4], "Data load width/alignment differs")
        parameters = [result for op, raw, result in module.functions[function] if op == 55]
        require(len(parameters) == 1 and module.width(module.node(parameters[0])[1]) == 8, "Unexpected helper signature")
        parameter = parameters[0]
        root, shifted = module.operands(module.pointer_address(entry_raw[2]), 128)
        index, scale = module.operands(shifted, 196)
        require(module.constant(scale) == 3, "Table entry stride differs")
        first_condition, first_index, second_index = module.operands(index, 169, 3)
        delta1, size1 = module.operands(first_condition, 176)
        addr1, base1 = module.operands(delta1, 130)
        require(addr1 == parameter, "First window does not inspect the helper address")
        shift_delta1, shift1 = module.operands(first_index, 194)
        shifted2, first2 = module.operands(second_index, 128)
        delta2, shift2 = module.operands(shifted2, 194)
        addr2, base2 = module.operands(delta2, 130)
        require(addr2 == parameter and shift_delta1 == delta1 and module.constant(shift1) == 12 and module.constant(shift2) == 12,
                "Guest page-index expression differs")
        root_pointer = module.node(root, 61)[0][0]
        access, _ = module.node(root_pointer, 65)
        require(len(access) == 2, "Root push access differs")
        variable = access[0]
        variable_args, variable_type = module.node(variable, 59)
        storage, structure = module.node(variable_type, 32)[0]
        member = module.constant(access[1])
        require(storage == 9 and variable_args[0] == 9 and module.offsets.get((structure, member)) == 48,
                "Guest-page root is not the reviewed push member")
        loaded_base, low_offset = module.operands(module.pointer_address(data_raw[2]), 128)
        require(loaded_base == entry_id, "Data load does not use the loaded page base")
        masked_address, mask = module.operands(low_offset, 199)
        require(masked_address == parameter and module.constant(mask) == PAGE - 1, "Data page offset mask differs")
        matches = []
        for _, condition, _, _ in module.branches[function]:
            if not module.guarded(function, condition, entry_id):
                continue
            leaves = module.flatten_and(condition)
            ranges, limits, roots = [], [], []
            for leaf in leaves:
                op, args, _ = module.nodes[leaf]
                if op == 166 and first_condition in args:
                    other = args[1] if args[0] == first_condition else args[0]
                    other_delta, other_size = module.operands(other, 176)
                    if other_delta == delta2:
                        ranges.append(other_size)
                elif op == 178:
                    low, high = args
                    low_addr, low_mask = module.operands(low, 199)
                    if low_addr == parameter and module.constant(low_mask) == PAGE - 1:
                        limits.append(module.constant(high))
                elif op == 171 and args[0] == root and module.constant(args[1]) == 0:
                    roots.append(leaf)
            if len(ranges) == len(limits) == len(roots) == 1:
                require(limits[0] + width <= PAGE, "Physical data load can cross a page")
                windows = [(module.constant(base1), module.constant(size1), 0),
                           (module.constant(base2), module.constant(ranges[0]), module.constant(first2))]
                entries = validate_windows(windows)
                matches.append((windows, entries, limits[0]))
        require(len(matches) == 1, "No unique dominating address/range/width guard")
        nonzero = [condition for _, condition, _, _ in module.branches[function]
                   if module.nodes[condition][0] == 171 and module.nodes[condition][1][0] == entry_id
                   and module.constant(module.nodes[condition][1][1]) == 0
                   and module.guarded(function, condition, data_id)]
        require(len(nonzero) == 1, "Data load is not dominated by nonzero page base")
        windows, entries, maximum_offset = matches[0]
        helpers.append({"data_width": width, "data_alignment_operand": 4, "entry_width": 8,
                        "entry_alignment_operand": 8, "maximum_offset": maximum_offset,
                        "windows": windows, "table_entries": entries})
    require(physical_count == 4 and sorted(row["data_width"] for row in helpers) == [4, 16], "Reachable physical-load inventory differs")
    require(helpers[0]["windows"] == helpers[1]["windows"], "Helpers use different page domains")
    return {"spirv_sha256": TARGET_SHA256, "reachable_physical_loads": physical_count,
            "page_size": PAGE, "table_bytes": helpers[0]["table_entries"] * 8,
            "root_push_offset": 48, "helpers": sorted(helpers, key=lambda row: row["data_width"]),
            "finite_work": finite_work(module)}


def validate_page_owners(table_bytes, owners, required_table_bytes):
    require(len(table_bytes) == required_table_bytes and len(table_bytes) % 8 == 0, "Complete table extent differs from shader domain")
    require(isinstance(owners, list) and owners, "Original buffer owners are required")
    intervals = []
    for row in owners:
        require(isinstance(row, dict), "Captured owners must be interval objects")
        base, size = row.get("address"), row.get("size")
        if isinstance(base, str):
            base = int(base, 16)
        require(type(base) is int and type(size) is int and 0 <= base < U64 and size > 0 and base + size <= U64,
                "Invalid captured VkBuffer owner interval")
        if base:
            intervals.append((base, base + size))
    require(intervals, "No physical-address buffer owners")
    minimum_tail, count = U64, 0
    for index, (address,) in enumerate(struct.iter_unpack("<Q", table_bytes)):
        if not address:
            continue
        require(address % 16 == 0 and address + PAGE <= U64, f"Entry {index} has incompatible base alignment or wraps")
        containing = [(lo, hi) for lo, hi in intervals if lo <= address < hi]
        require(containing, f"Entry {index} has no original captured VkBuffer owner")
        # All possible owners must cover the whole page: a later mapper cannot
        # choose a short view while citing a different complete owner.
        require(all(address + PAGE <= hi for _, hi in containing), f"Entry {index} has a partial-page original owner")
        minimum_tail = min(minimum_tail, *(hi - address for _, hi in containing))
        count += 1
    require(count > 0, "No nonzero page mappings")
    canonical = sorted((lo, hi - lo) for lo, hi in intervals)
    return {"nonzero_entries": count, "minimum_owner_tail_bytes": minimum_tail,
            "table_sha256": hashlib.sha256(table_bytes).hexdigest(),
            "owner_intervals_sha256": hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()}


def validate_target_access_spans(spirv_bytes, original_table, original_buffer_owners):
    """Prove byte extents for all addresses, not just observed invocations.

    Caller must also bind this exact table to the reviewed push root, preserve
    original buffer ownership/canonical offsets, and check returned replay BDAs.
    """
    static = inspect_module(spirv_bytes)
    coverage = validate_page_owners(original_table, original_buffer_owners, static["table_bytes"])
    return {"schema": "open-ocarina.physical-load-spans/v1", "load_spans_safe": True,
            "static": static, "coverage": coverage,
            "executor_requirements": {"table_root_alignment": 8, "rebased_page_alignment": 4,
                "complete_rebased_page_extent": PAGE, "push_root_equals_replay_table_address": True,
                "table_root_no_address_wrap": True,
                "preserve_original_owner_and_canonical_offset": True},
            "scope": "Universal physical-load byte extents for this shader and owner/table snapshot only; original data alignment, historical atomicity, aliases, races and device-state equivalence are separate"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spirv", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    try:
        directory = args.capture.resolve()
        allocations = json.loads((directory / "allocations.json").read_text())
        manifest = json.loads((directory / "manifest.json").read_text())
        table = (directory / "page-table.bin").read_bytes()
        require(hashlib.sha256(table).hexdigest() == manifest["table"]["sha256"], "Table artifact digest differs")
        push = (directory / "push-constants.bin").read_bytes()
        require(len(push) >= 56, "Push data lacks the complete root member")
        root, = struct.unpack_from("<Q", push, 48)
        require(root > 0 and root + len(table) <= U64 and
                root == int(manifest["table"]["root_device_address"], 16) and root % 8 == 0,
                "Captured root identity/alignment differs")
        owners = allocations["buffers"]
        for row in owners:
            require(type(row["size"]) is int and row["size"] > 0, "Invalid owner size")
            for phase in ("before", "after"):
                name = row[phase]["artifact"]
                require(Path(name).name == name, "Owner artifact must be a plain filename")
                artifact = directory / name
                require(not artifact.is_symlink() and artifact.is_file(), "Owner payload must be an ordinary local file")
                require(artifact.stat().st_size == row["size"], "Captured owner payload extent differs")
        report = validate_target_access_spans(args.spirv.read_bytes(), table, owners)
        report["capture_associations"] = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("manifest.json", "allocations.json", "dispatch-parameters.json", "push-constants.bin")}
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
