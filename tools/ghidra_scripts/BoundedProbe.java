// SPDX-License-Identifier: GPL-3.0-or-later
// One bounded ARM64 entry, with global/incremental analysis disabled.
// @category OpenOcarina
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.framework.Application;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import com.google.gson.GsonBuilder;

public class BoundedProbe extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 4) {
            throw new IllegalArgumentException("Expected entry, max bytes, decompiler seconds, output directory");
        }
        long entryValue = Long.decode(args[0]);
        int maximum = Integer.parseInt(args[1]);
        int seconds = Integer.parseInt(args[2]);
        Path output = Path.of(args[3]);
        if (entryValue < 0 || entryValue % 4 != 0 || maximum < 4 || maximum > 4096 || maximum % 4 != 0
                || seconds < 1 || seconds > 60) {
            throw new IllegalArgumentException("Invalid bounded ARM64 probe limits");
        }
        Address start = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(entryValue);
        Address end = start.addNoWrap(maximum - 1);
        MemoryBlock block = currentProgram.getMemory().getBlock(start);
        if (block == null || !block.isExecute() || !block.contains(end)) {
            throw new IllegalArgumentException("Probe window is not mapped executable memory");
        }
        AddressSet restriction = new AddressSet(start, end);
        DisassembleCommand command = new DisassembleCommand(start, restriction, true);
        command.enableCodeAnalysis(false);
        if (!command.applyTo(currentProgram, monitor)) {
            throw new IllegalStateException("Bounded disassembly failed: " + command.getStatusMsg());
        }
        AddressSet body = command.getDisassembledAddressSet();
        if (body.isEmpty() || !body.contains(start)) {
            throw new IllegalStateException("No instruction at requested entry");
        }
        Function previous = currentProgram.getFunctionManager().getFunctionAt(start);
        if (previous != null) {
            currentProgram.getFunctionManager().removeFunction(start);
        }
        Function function = currentProgram.getFunctionManager().createFunction(
            "probe_" + Long.toHexString(entryValue), start, body, SourceType.USER_DEFINED);
        StringBuilder listing = new StringBuilder();
        int count = 0;
        boolean exitsWindow = false;
        InstructionIterator instructions = currentProgram.getListing().getInstructions(body, true);
        while (instructions.hasNext()) {
            monitor.checkCancelled();
            Instruction instruction = instructions.next();
            listing.append(instruction.getAddress()).append("  ").append(instruction).append("\n");
            count++;
            for (Address flow : instruction.getFlows()) {
                if (!restriction.contains(flow)) {
                    exitsWindow = true;
                }
            }
            Address fallThrough = instruction.getFallThrough();
            if (fallThrough != null && !restriction.contains(fallThrough)) {
                exitsWindow = true;
            }
        }
        Files.writeString(output.resolve("disassembly.txt"), listing);
        DecompInterface decompiler = new DecompInterface();
        DecompileResults result;
        try {
            if (!decompiler.openProgram(currentProgram)) {
                throw new IllegalStateException("Decompiler could not open program");
            }
            result = decompiler.decompileFunction(function, seconds, monitor);
            if (result.decompileCompleted()) {
                Files.writeString(output.resolve("decompiled.c"), result.getDecompiledFunction().getC());
            }
        } finally {
            decompiler.dispose();
        }
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("schema", 1);
        metadata.put("ghidra_version", Application.getApplicationVersion());
        metadata.put("entry", "0x" + Long.toHexString(entryValue));
        metadata.put("max_bytes", maximum);
        metadata.put("instruction_count", count);
        metadata.put("flow_outside_window", exitsWindow);
        metadata.put("global_analysis", false);
        metadata.put("decompile_completed", result.decompileCompleted());
        metadata.put("error", result.getErrorMessage());
        metadata.put("source_matched", false);
        metadata.put("scope", "Bounded disassembly/decompilation; original boundaries, types and source identity unverified");
        Files.writeString(output.resolve("probe.json"), new GsonBuilder().setPrettyPrinting().create().toJson(metadata));
        println("Open-Ocarina bounded probe completed; outputs remain private.");
    }
}
