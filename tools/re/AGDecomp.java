// Ghidra headless script: label GameAssembly.dll with Il2CppDumper names, then
// decompile requested methods to C.
//   args: <symbols.txt> <targets.txt> <outdir>
//   symbols.txt: "F|S|M <rva hex> <name>" (from tools/re/make_symbols in codephil notes)
//   targets.txt: one rva (hex) or method name per line
// @category AG
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import java.io.*;
import java.nio.file.*;
import java.util.*;

public class AGDecomp extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        Path symFile = Paths.get(a[0]), targets = Paths.get(a[1]), out = Paths.get(a[2]);
        Files.createDirectories(out);
        long base = currentProgram.getImageBase().getOffset();
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        SymbolTable st = currentProgram.getSymbolTable();
        Map<String, Long> byName = new HashMap<>();
        boolean labelled = st.getSymbols("AG_LABELLED").hasNext();
        int n = 0;
        for (String line : Files.readAllLines(symFile)) {
            String[] p = line.split(" ", 3);
            if (p.length < 3) continue;
            long rva = Long.parseLong(p[1], 16);
            if (p[0].equals("F")) byName.putIfAbsent(p[2], rva);
            if (labelled) continue;
            try {
                st.createLabel(sp.getAddress(base + rva), p[2], SourceType.IMPORTED);
                n++;
            } catch (Exception e) { }
        }
        if (!labelled) {
            st.createLabel(sp.getAddress(base), "AG_LABELLED", SourceType.USER_DEFINED);
            println("labels: " + n);
        }
        DecompInterface di = new DecompInterface();
        DecompileOptions opt = new DecompileOptions();
        di.setOptions(opt);
        di.openProgram(currentProgram);
        Listing listing = currentProgram.getListing();
        for (String t : Files.readAllLines(targets)) {
            t = t.trim();
            if (t.isEmpty()) continue;
            Long rva = t.matches("(0x)?[0-9a-fA-F]+") ? Long.parseLong(t.replace("0x", ""), 16) : byName.get(t);
            if (rva == null) { println("no such method: " + t); continue; }
            Address ad = sp.getAddress(base + rva);
            Function f = listing.getFunctionAt(ad);
            if (f == null) {
                new DisassembleCommand(ad, null, true).applyTo(currentProgram, monitor);
                new CreateFunctionCmd(ad).applyTo(currentProgram, monitor);
                f = listing.getFunctionAt(ad);
            }
            if (f == null) { println("could not create function at " + ad); continue; }
            DecompileResults r = di.decompileFunction(f, 180, monitor);
            String name = st.getPrimarySymbol(ad) != null ? st.getPrimarySymbol(ad).getName() : ad.toString();
            String safe = name.replaceAll("[^0-9A-Za-z_.$]", "_");
            if (safe.length() > 140) safe = safe.substring(0, 140);
            safe = safe + "@" + Long.toHexString(rva);     // overloads share a name
            String c = r.decompileCompleted() ? r.getDecompiledFunction().getC() : ("// failed: " + r.getErrorMessage());
            Files.writeString(out.resolve(safe + ".c"), "// " + name + " @ rva 0x" + Long.toHexString(rva) + "\n" + c);
            println("ok " + name);
        }
        di.dispose();
    }
}
