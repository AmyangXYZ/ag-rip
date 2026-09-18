// Ghidra headless script: parse AG_cache/re/pipeline.h into the program's data types
// and apply the IL2CPP signatures in signatures.txt, so the decompiler shows real
// field names (__this->fields._fogColor) instead of raw offsets.
//   args: <pipeline.h> <signatures.txt>
// @category AG
import ghidra.app.script.GhidraScript;
import ghidra.app.util.cparser.C.CParser;
import ghidra.app.util.cparser.C.CParserUtils;
import ghidra.app.cmd.function.ApplyFunctionSignatureCmd;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.program.model.address.*;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import java.io.*;
import java.nio.file.*;

public class AGTypes extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        DataTypeManager dtm = currentProgram.getDataTypeManager();
        CParser parser = new CParser(dtm, true, null);
        String hdr = Files.readString(Paths.get(a[0]));
        parser.parse(new ByteArrayInputStream(hdr.getBytes("UTF-8")));
        println("parsed types; msgs: " + (parser.getParseMessages() == null ? "" : parser.getParseMessages().substring(0, Math.min(2000, parser.getParseMessages().length()))));
        long base = currentProgram.getImageBase().getOffset();
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        Listing listing = currentProgram.getListing();
        int ok = 0, bad = 0;
        for (String line : Files.readAllLines(Paths.get(a[1]))) {
            int bar = line.indexOf('|');
            if (bar < 0) continue;
            Address ad = sp.getAddress(base + Long.parseLong(line.substring(0, bar), 16));
            String sig = line.substring(bar + 1);
            Function f = listing.getFunctionAt(ad);
            if (f == null) {
                new DisassembleCommand(ad, null, true).applyTo(currentProgram, monitor);
                new CreateFunctionCmd(ad).applyTo(currentProgram, monitor);
                f = listing.getFunctionAt(ad);
            }
            if (f == null) { bad++; continue; }
            try {
                FunctionDefinitionDataType def = CParserUtils.parseSignature((ghidra.app.services.DataTypeManagerService) null, currentProgram, sig, false);
                if (def == null) { bad++; continue; }
                String keep = f.getName();
                new ApplyFunctionSignatureCmd(ad, def, SourceType.USER_DEFINED).applyTo(currentProgram, monitor);
                f.setName(keep, SourceType.USER_DEFINED);
                ok++;
            } catch (Exception e) {
                bad++;
                if (bad < 10) println("sig fail " + sig + " : " + e.getMessage());
            }
        }
        println("signatures applied: " + ok + ", failed: " + bad);
    }
}
