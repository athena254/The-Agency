"""Forge tool catalog for reverse-engineering (and related) tools.

This is a *catalog*, not an installer. Forge never installs tools on its own:
when a tool is missing, callers get a clear ``needs_install`` result with the
install command, and the user decides whether to run it. This keeps The Agency
and the Forge usable on hosts where only a subset of tools is present.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: str
    command: str | None  # executable name (None for MCP bridges / docs only)
    description: str
    install: str
    args_hint: str = ""


CATALOG: list[ToolSpec] = [
    ToolSpec("rea", "analysis core", "npx", "REA MCP analysis core", "npm i -g rea-agents (or npx rea-agents@latest setup)", "rea-agents setup / doctor --json"),
    ToolSpec("ghidra", "analysis core", "ghidra", "Deep open decompiler/disassembler", "Download from ghidra-sre.org; set GHIDRA_HOME", "analyzeHeadless <project> <name> -import <binary>"),
    ToolSpec("ghidramcp", "analysis core", None, "MCP bridge so agents drive Ghidra", "See LaurieWired/GhidraMCP", "MCP server, not a CLI"),
    ToolSpec("ida-pro-mcp", "analysis core", None, "MCP bridge for licensed IDA Pro", "See mrexodia/ida-pro-mcp (requires IDA Pro license)", "MCP server, not a CLI"),
    ToolSpec("jadx", "mobile & apps", "jadx", "DEX/APK -> Java/Kotlin decompile", "brew install jadx  OR  https://github.com/skylot/jadx", "jadx -d out app.apk"),
    ToolSpec("apktool", "mobile & apps", "apktool", "Decode/rebuild Android resources & smali", "brew install apktool  OR  https://apktool.org", "apktool d app.apk -o out"),
    ToolSpec("frida", "mobile & apps", "frida", "Dynamic instrumentation (authorized targets only)", "pip install frida-tools", "frida -U -f <pkg> -l hook.js"),
    ToolSpec("ilspy", "mobile & apps", "ilspycmd", ".NET decompiler", "dotnet tool install -g ilspycmd", "ilspycmd -p -o out app.dll"),
    ToolSpec("radare2", "binary & low-level", "r2", "CLI reverse-engineering framework", "brew install radare2  OR  https://rada.re", "r2 -qc 'aaa; pdf @ main' binary"),
    ToolSpec("x64dbg", "binary & low-level", "x64dbg", "Windows user-mode debugger", "https://x64dbg.com", "GUI debugger (Windows)"),
    ToolSpec("imhex", "binary & low-level", "imhex", "Hex editor + pattern language", "brew install imhex  OR  https://imhex.werwolv.net", "imhex file.bin"),
    ToolSpec("angr", "binary & low-level", "python", "Symbolic execution / binary analysis", "pip install angr", "python -c 'import angr; ...'"),
    ToolSpec("mitmproxy", "network & runtime", "mitmproxy", "HTTPS intercept & scripting (authorized traffic only)", "brew install mitmproxy  OR  pip install mitmproxy", "mitmproxy -p 8080"),
    ToolSpec("dnspy", "network & runtime", "dnSpy", ".NET debugger / assembly editor", "https://github.com/dnSpyEx/dnSpy (Windows)", "GUI debugger (Windows)"),
    ToolSpec("rizin", "network & runtime", "rizin", "radare2-family framework", "brew install rizin  OR  https://rizin.re", "rizin -qc 'aaa; pdf @ main' binary"),
    ToolSpec("raddebugger", "network & runtime", "raddbg", "Native debugger (Epic, Windows-focused)", "https://github.com/EpicGames/raddebugger", "raddbg binary"),
    ToolSpec("awesome-ai-re", "rebuild & verify", None, "Graded map of AI x RE repos", "git clone https://github.com/zhuyansen/awesome-ai-reverse-engineering", "reference list only"),
    ToolSpec("awesome-re", "rebuild & verify", None, "REA author's awesome list", "git clone https://github.com/morluto/awesome-reverse-engineering", "reference list only"),
]


def list_tools() -> list[ToolSpec]:
    return list(CATALOG)


def get_tool(name: str) -> ToolSpec | None:
    needle = name.strip().lower()
    for spec in CATALOG:
        if spec.name.lower() == needle:
            return spec
    return None


def is_available(spec: ToolSpec) -> bool:
    if spec.command is None:
        return False
    return shutil.which(spec.command) is not None


def missing_tools() -> list[ToolSpec]:
    return [s for s in CATALOG if s.command is not None and not is_available(s)]


def available_tools() -> list[ToolSpec]:
    return [s for s in CATALOG if s.command is not None and is_available(s)]


def install_hint(name: str) -> str:
    spec = get_tool(name)
    if spec is None:
        return f"Unknown tool: {name}"
    return spec.install