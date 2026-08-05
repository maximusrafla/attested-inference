"""Minimal TPM 2.0 quote parsing, shared by the enclave checker and the off-machine verifier.

Only what this scheme needs: the attestation structure's extraData (the regulator's challenge)
and its pcrDigest (what the vTPM signed about the register). Both are plain big-endian TPM
structures, so no TSS dependency is needed on the verifier's side, which matters because the
regulator should not have to install a vendor stack to check an artifact.

Why pcrDigest and not a PCR read: on a live Azure confidential-GPU guest, PCR 10 never holds
still. The provider's in-guest security agent continuously reads files that keep changing, and
the IMA tcb policy measures every one of those reads, so the register advances between any two
commands. A ceremony that reads the PCR and then quotes it cannot be made atomic on that host.
Deriving the quoted value from the quote itself removes the race entirely: with one PCR
selected, pcrDigest is sha256 of that PCR's value, so replaying the log and hashing each
candidate finds exactly the prefix the quote covers.
"""

import struct

TPM_GENERATED_VALUE = 0xFF544347
TPM_ST_ATTEST_QUOTE = 0x8018
ALG_RSASSA, ALG_RSAPSS, ALG_ECDSA = 0x0014, 0x0016, 0x0018


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0], o + 2


def _tpm2b(b, o):
    n, o = _u16(b, o)
    return b[o:o + n], o + n


def parse_attest(blob):
    o = 0
    magic, = struct.unpack_from(">I", blob, o); o += 4
    typ, o = _u16(blob, o)
    _signer, o = _tpm2b(blob, o)
    extra, o = _tpm2b(blob, o)
    o += 17          # TPMS_CLOCK_INFO
    o += 8           # firmwareVersion
    count, = struct.unpack_from(">I", blob, o); o += 4
    selections = []
    for _ in range(count):
        alg, o = _u16(blob, o)
        size = blob[o]; o += 1
        selections.append((alg, blob[o:o + size])); o += size
    pcr_digest, o = _tpm2b(blob, o)
    return {"magic": magic, "type": typ, "extra_data": extra,
            "selections": selections, "pcr_digest": pcr_digest}


def parse_signature(blob):
    o = 0
    sig_alg, o = _u16(blob, o)
    hash_alg, o = _u16(blob, o)
    if sig_alg in (ALG_RSASSA, ALG_RSAPSS):
        sig, o = _tpm2b(blob, o)
        return sig_alg, hash_alg, sig
    if sig_alg == ALG_ECDSA:
        r, o = _tpm2b(blob, o)
        s, o = _tpm2b(blob, o)
        return sig_alg, hash_alg, (r, s)
    raise ValueError(f"unsupported TPM signature algorithm 0x{sig_alg:04x}")
