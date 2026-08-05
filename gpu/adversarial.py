#!/usr/bin/env python3
"""Bounded adversarial pass against the Tier 1 completeness mechanism, and only that.

Each row is one concrete attempt to do undeclared work without the measurement seeing it, run
for real, with a yes/no answer. Not a general adversarial evaluation of the scheme: this attacks
the specific claim Tier 1 makes, which is the claim that has to be attacked.

Scope, stated so nobody reads more in: a cooperative operator running these probes on its own
machine, five probes, one platform. Not an evaluation against a motivated adversary with time.

**Detector design, and the first version got this wrong.** The obvious test is "did PCR 10 move
during the probe", and it is useless here: PCR 10 moves constantly on a live cloud guest because
the provider's own agents keep executing and reading things, so every probe reads as caught.
The detector used here instead asks the precise question: **did the payload's own digest appear
in the measurement log.** That is immune to background noise, because it identifies the payload
rather than timing it. Probes with no file at all report the new entries they did see, so the
reader can confirm those are background and not the payload.

Probes:
  trim_log             delete an entry from the quoted prefix and re-derive. The replay must
                       break, which is what denies the log any authority of its own.
  jit_anon             the DECLARED program writes machine code into anonymous RWX memory and
                       jumps to it. No file exists, so there is no digest to measure. The
                       Python-import result generalised past Python.
  memfd_exec           execute a binary that never exists in the filesystem (memfd_create plus
                       execv on /proc/self/fd). Open either way: IMA hooks exec, not the
                       filesystem, so it may hash the contents regardless of backing.
  post_approval_edit   edit the declared program after approval and run it. Must be caught.
  other_user           run undeclared code as a different user. Must be caught.
"""

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_ima import parse_binary_log, replay  # noqa: E402
from lib_tpm import parse_attest  # noqa: E402

IMA_BIN = "/sys/kernel/security/ima/binary_runtime_measurements"


def read_log():
    return subprocess.run(["sudo", "cat", IMA_BIN], capture_output=True).stdout


def measured_pairs(blob=None):
    """Every (digest, path) currently in the log."""
    entries = parse_binary_log(blob if blob is not None else read_log())
    out = []
    for e in entries:
        d, p = e.file_digest_and_path()
        if d:
            out.append((d, p))
    return out


def digest_of(data):
    return "sha256::" + hashlib.sha256(data).hexdigest()


def run_probe(fn, *a, **kw):
    """Run one probe and decide whether its payload reached the measurement.

    Two detector bugs were found and fixed here, and both are worth knowing about because each
    produced a confidently wrong answer:

      1. The first version asked "did PCR 10 move during the probe". Useless: on a live cloud
         guest the provider's agents move it constantly, so everything read as caught.
      2. The second version asked "did the payload's digest appear among the entries NEW since
         the probe started". That gave a FALSE NEGATIVE on post_approval_edit, because the edit
         appended a constant string, so an earlier run had already put that exact digest in the
         log and it was no longer new. It would have supported the claim that editing an
         approved file goes unmeasured, which is false and would have been a serious error.

    The detector now asks the plain question, "is the payload's digest in the log at all", and
    every payload is made unique per run so cross-run contamination cannot recur.
    """
    before_set = set(measured_pairs())
    payload_digest, extra = fn(*a, **kw)
    after = measured_pairs()
    after_digests = {d for d, _ in after}
    new = [x for x in after if x not in before_set]
    caught = payload_digest is not None and payload_digest in after_digests
    return {
        "payload_digest": payload_digest[:24] + "..." if payload_digest else None,
        "payload_is_a_file": payload_digest is not None,
        "new_entries_during_probe": len(new),
        "new_entry_paths": [p for _, p in new][:8],
        "payload_digest_appears_in_log": caught,
        **extra,
    }


def p_jit_anon():
    """Declared program executes code it wrote into anonymous memory. No file involved."""
    libc = ctypes.CDLL(None, use_errno=True)
    mmap_ = libc.mmap
    mmap_.restype = ctypes.c_void_p
    mmap_.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_long]
    size = 4096
    addr = mmap_(None, size, 0x7, 0x22, -1, 0)          # PROT_RWX, MAP_PRIVATE|MAP_ANONYMOUS
    if not addr or addr == ctypes.c_void_p(-1).value:
        return None, {"error": "mmap failed"}
    # mov eax, <unique value> ; ret   (inert, and unique per run so the digest cannot collide
    # with anything measured in an earlier run)
    marker = int.from_bytes(os.urandom(2), "little")
    code = b"\xb8" + marker.to_bytes(4, "little") + b"\xc3"
    ctypes.memmove(addr, code, len(code))
    value = ctypes.CFUNCTYPE(ctypes.c_int)(addr)()
    # there is no file, so there is no digest that could appear; report the code's hash anyway
    # so the reader can confirm it is absent
    return digest_of(code), {"code_executed": value == marker, "code_bytes": len(code)}


def p_memfd_exec():
    """Execute a binary that never touches the filesystem."""
    d = Path(tempfile.mkdtemp())
    src = d / "u.c"
    src.write_text('#include <stdio.h>\nint main(){puts("undeclared fileless ran '
                   + os.urandom(6).hex() + '");return 0;}\n')
    binp = d / "u"
    if subprocess.run(["gcc", "-O0", "-o", str(binp), str(src)],
                      capture_output=True).returncode != 0:
        return None, {"error": "no compiler available"}
    payload = binp.read_bytes()
    binp.unlink()                                        # nothing on disk from here on
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.memfd_create(b"anon", 0)
    if fd < 0:
        return None, {"error": "memfd_create unavailable"}
    os.write(fd, payload)
    pid = os.fork()
    if pid == 0:
        try:
            os.execv(f"/proc/self/fd/{fd}", ["anon"])
        except Exception:
            os._exit(3)
    _, status = os.waitpid(pid, 0)
    return digest_of(payload), {"child_status": status, "payload_bytes": len(payload)}


def p_post_approval_edit(declared_path):
    p = Path(declared_path)
    original = p.read_bytes()
    try:
        edited = original + b"\n# appended after approval " + os.urandom(6).hex().encode() + b"\n"
        p.write_bytes(edited)
        subprocess.run([str(p), "--help"], capture_output=True)
        return digest_of(edited), {"original_digest": digest_of(original)[:24] + "...",
                                   "edited": True}
    finally:
        p.write_bytes(original)


def p_other_user():
    d = Path(tempfile.mkdtemp())
    os.chmod(d, 0o755)
    s = d / "u.sh"
    body = f"#!/bin/sh\necho undeclared as $(id -un)\n# unique {os.urandom(8).hex()}\n"
    s.write_text(body)
    s.chmod(0o755)
    r = subprocess.run(["sudo", "-u", "nobody", str(s)], capture_output=True, text=True)
    return digest_of(body.encode()), {"ran": r.returncode == 0, "as_user": r.stdout.strip()[-30:]}


def probe_trim_log(quote_path):
    """Delete an entry from the prefix the quote covers and show the derivation breaks.

    Uses the quote's own pcrDigest rather than a separate PCR read, for the same reason the
    checker does: the register moves between any two commands on this platform.
    """
    if not quote_path or not Path(quote_path).exists():
        return {"probe": "trim_log", "error": "no quote supplied", "caught": None}
    att = parse_attest(Path(quote_path).read_bytes())
    target = att["pcr_digest"].hex()
    entries = parse_binary_log(read_log())

    covered, pcr = None, bytes(32)
    for i, e in enumerate(entries):
        if e.pcr != 10:
            continue
        pcr = hashlib.sha256(pcr + e.bank_digest("sha256")).digest()
        if hashlib.sha256(pcr).hexdigest() == target:
            covered = i + 1
            break
    if covered is None:
        return {"probe": "trim_log", "error": "quote does not match this log (stale quote)",
                "caught": None}

    prefix = entries[:covered]
    trimmed = prefix[:-3] + prefix[-2:]
    ok_full = hashlib.sha256(bytes.fromhex(replay(prefix, "sha256"))).hexdigest() == target
    ok_trim = hashlib.sha256(bytes.fromhex(replay(trimmed, "sha256"))).hexdigest() == target
    return {
        "probe": "trim_log",
        "question": "can the operator delete a measurement and still satisfy the quote?",
        "entries_covered_by_quote": covered,
        "intact_prefix_reproduces_quote": ok_full,
        "trimmed_prefix_reproduces_quote": ok_trim,
        "caught": (not ok_trim) and ok_full,
        "note": "the log has no authority of its own; only agreement with the quoted value does",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--declared", default=str(Path.home() / "t1/bin/declared_stack.py"))
    ap.add_argument("--quote", help="a quote.msg taken from THIS boot, for the trim probe")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    results = [
        probe_trim_log(args.quote),
        {"probe": "jit_anon",
         "question": "does executing code from anonymous memory reach the measurement?",
         **run_probe(p_jit_anon),
         "note": ("the declared, approved interpreter executed code that never existed as a "
                  "file. Generalises the Python-import result: file-backed measurement cannot "
                  "see execution that is not file-backed")},
        {"probe": "memfd_exec",
         "question": "is a binary that never exists on disk measured when executed?",
         **run_probe(p_memfd_exec)},
        {"probe": "post_approval_edit",
         "question": "does editing the approved program after approval change the measurement?",
         **run_probe(p_post_approval_edit, args.declared)},
        {"probe": "other_user",
         "question": "does running undeclared code as another user avoid measurement?",
         **run_probe(p_other_user)},
    ]
    for r in results:
        if "caught" not in r:
            r["caught"] = r.get("payload_digest_appears_in_log")

    Path(args.out).write_text(json.dumps(results, indent=2, sort_keys=True))
    print(f"{'probe':<20}{'caught?':<13}{'new entries':<13}detail")
    for r in results:
        c = {True: "CAUGHT", False: "NOT CAUGHT", None: "n/a"}[r.get("caught")]
        n = r.get("new_entries_during_probe", "-")
        print(f"{r['probe']:<20}{c:<13}{str(n):<13}{r.get('error','')}")
    print()
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
