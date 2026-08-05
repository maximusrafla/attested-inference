"""The review step (mechanism 1c): a MINIMAL deterministic source scanner.

Not the contribution. The AI-reviewer mechanism (1c.2) is cited to Agentic Witnessing and deliberately NOT rebuilt.
This is the smallest deterministic check that makes the xz demonstration concrete: it catches an in-source payload
pattern (the part Kettle's artifact attestation faithfully passes through). Its job in the can/can't map is to be the
review-layer row, with its limit stated plainly: it catches KNOWN patterns, not novel obfuscation.

Rules (deterministic, so a verifier can reproduce the verdict):
  R1  a long encoded blob (base64/hex literal) that is decoded, i.e. an obfuscated embedded payload
  R2  a build script performing network I/O or writing an executable (build-time exfiltration/injection surface)

Usage: python review_scan.py <source-dir>   ->   prints findings, exits 1 if any rule fires
"""

import re
import sys
from pathlib import Path

# A base64/hex run long enough to hide a payload, not an incidental short constant.
LONG_BLOB = re.compile(r'["\']([A-Za-z0-9+/=]{200,}|[0-9a-fA-F]{200,})["\']')
DECODE_CALL = re.compile(r'\b(base64|b64|from_base64|BASE64|hex)\s*(::)?\s*(decode|Decode|from)\b')
BUILD_NET = re.compile(r'\b(TcpStream|reqwest|ureq|curl|wget|http[s]?://)\b')
BUILD_WRITE_EXEC = re.compile(r'set_mode\(0o7|PermissionsExt|Command::new|chmod \+x')

SOURCE_EXT = {".rs", ".py", ".js", ".ts", ".c", ".cc", ".cpp", ".go", ".sh", ".m4"}


def scan_file(path):
    findings = []
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return findings
    is_build = path.name in ("build.rs", "build.py") or path.suffix in (".sh", ".m4")

    has_blob = LONG_BLOB.search(text)
    has_decode = DECODE_CALL.search(text)
    if has_blob and has_decode:
        line = text[: has_blob.start()].count("\n") + 1
        findings.append(("R1", "obfuscated embedded payload (long encoded blob + decode call)", line))

    if is_build:
        if BUILD_NET.search(text):
            m = BUILD_NET.search(text)
            findings.append(("R2a", "build script performs network I/O", text[: m.start()].count("\n") + 1))
        if BUILD_WRITE_EXEC.search(text):
            m = BUILD_WRITE_EXEC.search(text)
            findings.append(("R2b", "build script writes/executes code", text[: m.start()].count("\n") + 1))
    return [(str(path), *f) for f in findings]


def main():
    if len(sys.argv) != 2:
        print("usage: python review_scan.py <source-dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    findings = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and (p.suffix in SOURCE_EXT):
            if "target" in p.parts or ".git" in p.parts:
                continue
            findings.extend(scan_file(p))

    if findings:
        print(f"REVIEW: REJECT ({len(findings)} finding(s))")
        for f, rule, desc, line in findings:
            print(f"  [{rule}] {f}:{line}  {desc}")
        return 1
    print("REVIEW: ACCEPT (no known-pattern findings)")
    print("  limit: deterministic, catches known patterns only, not novel obfuscation (see DESIGN.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
