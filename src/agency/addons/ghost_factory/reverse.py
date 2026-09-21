"""Reverse engineering — produce source-level reconstructions from binaries.

Static analysis only: headers, printable strings, imports, and heuristic
disassembly. Never executes the target binary.
"""

from __future__ import annotations

import hashlib
import re
import struct
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

_MIN_STRING_LEN = 4
_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")


class BinaryAnalysis(BaseModel):
    """Static analysis report for a binary file."""

    model_config = ConfigDict(extra="forbid")

    path: str
    size_bytes: int = Field(ge=0)
    sha256: str
    format: str = Field(default="unknown")
    architecture: str = Field(default="unknown")
    strings: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    sections: list[dict[str, object]] = Field(default_factory=list)
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecompiledUnit(BaseModel):
    """Best-effort source reconstruction for one binary."""

    model_config = ConfigDict(extra="forbid")

    source_path: str
    language: str = Field(default="python")
    code: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class ReverseEngineer:
    """Static-only reverse engineering harness."""

    def __init__(self, min_string_len: int = _MIN_STRING_LEN) -> None:
        if min_string_len < 1:
            raise ValueError("min_string_len must be >= 1")
        self.min_string_len = min_string_len
        self._log = logger.bind(component="ReverseEngineer")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze_binary(self, binary_path: Path | str) -> BinaryAnalysis:
        """Identify format/arch and collect sections, imports, and strings."""
        path = Path(binary_path)
        if not path.is_file():
            raise FileNotFoundError(f"binary not found: {path}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        fmt, arch = _identify(data)
        analysis = BinaryAnalysis(
            path=str(path),
            size_bytes=len(data),
            sha256=digest,
            format=fmt,
            architecture=arch,
            strings=self.extract_strings(path),
            imports=_extract_imports(data, fmt),
            sections=_extract_sections(data, fmt),
        )
        self._log.info("reverse.analyzed", path=str(path), format=fmt, arch=arch, size=len(data))
        return analysis

    def extract_strings(self, binary: Path | str | bytes) -> list[str]:
        """Extract printable ASCII strings (``strings``-like, static)."""
        data = Path(binary).read_bytes() if isinstance(binary, (Path, str)) else bytes(binary)
        found = _PRINTABLE.findall(data)
        strings = sorted({m.decode("ascii") for m in found if len(m) >= self.min_string_len})
        self._log.info("reverse.strings", count=len(strings))
        return strings

    def decompile(self, binary: Path | str) -> DecompiledUnit:
        """Produce a heuristic pseudo-source listing (offsets + mnemonics)."""
        analysis = self.analyze_binary(binary)
        data = Path(binary).read_bytes()
        lines = [
            f"# Decompiled reconstruction of {Path(binary).name}",
            f"# format={analysis.format} arch={analysis.architecture} sha256={analysis.sha256[:16]}...",
            "# WARNING: heuristic static listing — verify before use.",
            "",
        ]
        for offset in range(0, min(len(data), 4096), 16):
            chunk = data[offset : offset + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{offset:08x}: {hexpart:<48} |{asc}|  ; {_mnemonic(chunk)}")
        if len(data) > 4096:
            lines.append(f"# ... truncated ({len(data) - 4096} further bytes)")
        if analysis.imports:
            lines.append("")
            lines.append("# detected imports/symbols:")
            lines.extend(f"#   - {name}" for name in analysis.imports[:50])
        return DecompiledUnit(
            source_path=str(binary),
            language="asm-pseudo",
            code="\n".join(lines) + "\n",
            confidence=0.35,
            notes=["Heuristic disassembly; not semantically equivalent source."],
        )

    def reconstruct_source(self, binary: Path | str, language: str = "python") -> DecompiledUnit:
        """Reconstruct an editable source skeleton in ``language`` from a binary."""
        analysis = self.analyze_binary(binary)
        strings = [s for s in analysis.strings if _interesting(s)][:30]
        if language.strip().lower() == "python":
            code = _python_skeleton(Path(binary).name, analysis, strings)
        else:
            code = _generic_skeleton(Path(binary).name, language, analysis, strings)
        self._log.info("reverse.reconstructed", binary=str(binary), language=language)
        return DecompiledUnit(
            source_path=str(binary),
            language=language,
            code=code,
            confidence=0.25,
            notes=[
                "Behavioural skeleton inferred from static strings/imports.",
                "Dynamic behaviour, control flow, and data structures are NOT recovered.",
            ],
        )

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    def full_report(self, binary: Path | str, language: str = "python") -> dict[str, object]:
        """Analyze + decompile + reconstruct in one call."""
        return {
            "analysis": self.analyze_binary(binary).model_dump(),
            "decompiled": self.decompile(binary).model_dump(),
            "reconstructed": self.reconstruct_source(binary, language).model_dump(),
        }


# ---------------------------------------------------------------------- #
# Format helpers (static parsing only)
# ---------------------------------------------------------------------- #


def _identify(data: bytes) -> tuple[str, str]:
    if data[:4] == b"\x7fELF":
        arch = {3: "x86", 62: "x86-64", 40: "arm", 183: "aarch64"}.get(data[18], "unknown")
        return "ELF", arch
    if data[:2] == b"MZ":
        return "PE", "unknown"
    if data[:4] in (b"\xcaf\xbab\xe7", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return "Mach-O", "unknown"
    if data.startswith(b"#!"):
        return "script", "interpreter"
    return "unknown", "unknown"


def _extract_sections(data: bytes, fmt: str) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    if fmt == "ELF" and len(data) > 64:
        try:
            _, _, _, _, _, shoff, _, _, _, shentsize, shnum, shstrndx = struct.unpack(
                "<16sHHIQQQIHHHHHH", data[:64]
            )
            for i in range(min(shnum, 32)):
                off = shoff + i * shentsize
                entry = data[off : off + 64]
                if len(entry) < 64:
                    break
                _, stype, _, _, _, _, _, _, _, _ = struct.unpack("<IIQQQQIIQQ", entry)
                sections.append({"index": i, "type": stype, "offset": off})
            _ = shstrndx
        except struct.error:
            pass
    else:
        # Generic chunking so callers always get a section map.
        for i in range(0, min(len(data), 65536), 4096):
            sections.append({"index": i // 4096, "offset": i, "size": min(4096, len(data) - i)})
    return sections


def _extract_imports(data: bytes, fmt: str) -> list[str]:
    candidates = sorted({m.decode("ascii") for m in _PRINTABLE.findall(data) if len(m) >= 4})
    if fmt in ("ELF", "PE", "Mach-O"):
        interesting = [
            s
            for s in candidates
            if re.match(r"^[A-Za-z_][\w.]*$", s)
            and ("." in s or s.startswith(("GLIBC", "KERNEL", "msvc", "objc", "__")))
        ]
        dlls = [s for s in candidates if s.lower().endswith(".dll")]
        return sorted(set(interesting[:80] + dlls[:20]))
    return [s for s in candidates if s.endswith((".py", ".so", ".dylib"))][:20]


def _mnemonic(chunk: bytes) -> str:
    if not chunk:
        return "nop?"
    first = chunk[0]
    table = {
        0x00: "add?",
        0x55: "push rbp?",
        0x48: "rex.W prefix?",
        0xE8: "call rel?",
        0xE9: "jmp rel?",
        0xC3: "ret?",
        0xCC: "int3",
    }
    return table.get(first, "db")


def _interesting(s: str) -> bool:
    if len(s) < 5:
        return False
    lowered = s.lower()
    if lowered in {"hello", "test", "data"}:
        return False
    return any(ch.isalpha() for ch in s) and (
        any(
            k in lowered
            for k in (
                "error",
                "fail",
                "http",
                "sql",
                "config",
                "auth",
                "usage",
                "/",
                ".",
                "version",
            )
        )
        or len(s) > 8
    )


def _python_skeleton(name: str, analysis: BinaryAnalysis, strings: list[str]) -> str:
    consts = "\n".join(f"{_const_name(s)} = {s!r}" for s in strings) or 'NO_STRINGS_FOUND = ""'
    return (
        f'"""Reconstructed skeleton of {name} (static analysis only)."""\n\n'
        "from __future__ import annotations\n\n"
        "import structlog\n\n"
        "logger = structlog.get_logger(__name__)\n\n"
        f"# format={analysis.format} arch={analysis.architecture} sha256={analysis.sha256}\n"
        f"{consts}\n\n\n"
        "class ReconstructedBinary:\n"
        '    """Behavioural placeholder — fill in logic from dynamic analysis."""\n\n'
        "    def run(self, *args: object) -> int:\n"
        '        """Entry-point placeholder."""\n'
        '        logger.info("reconstructed.run", args=len(args))\n'
        '        raise NotImplementedError("reconstructed skeleton — implement behaviour")\n\n\n'
        'if __name__ == "__main__":\n'
        "    raise SystemExit(ReconstructedBinary().run())\n"
    )


def _generic_skeleton(
    name: str, language: str, analysis: BinaryAnalysis, strings: list[str]
) -> str:
    commented = "\n".join(f"// {s}" for s in strings[:20])
    return (
        f"// Reconstructed skeleton of {name} [{language}] (static analysis only)\n"
        f"// format={analysis.format} arch={analysis.architecture} sha256={analysis.sha256}\n"
        f"{commented}\n"
        "// TODO: implement behaviour from dynamic analysis\n"
    )


def _const_name(s: str) -> str:
    name = re.sub(r"\W+", "_", s).strip("_").upper()[:40] or "STR"
    if name[0].isdigit():
        name = "S_" + name
    return name


__all__ = ["BinaryAnalysis", "DecompiledUnit", "ReverseEngineer"]
