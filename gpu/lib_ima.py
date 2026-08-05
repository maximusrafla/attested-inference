"""Parse the Linux IMA binary measurement log and replay it into a PCR value.

Why this file exists: the measurement log lives in the guest and the guest is the party we do
not trust. The log on its own proves nothing. What makes it non-repudiable is that replaying it
must reproduce the PCR value the vTPM signed in a quote. Trim an entry and the replay diverges.

Binary format (security/integrity/ima/ima_fs.c), one entry after another, native endian:

    u32  pcr
    u8   template_digest[20]        the SHA-1 template hash (what the SHA-1 bank was extended with)
    u32  template_name_len
    char template_name[]            "ima-ng" in every modern kernel
    u32  template_data_len
    u8   template_data[]            the fields, each as [u32 len][bytes]

For the SHA-256 bank the kernel extends with sha256 over the same per-field [len][bytes] stream
(ima_calc_field_array_hash_tfm), which is byte for byte the template_data blob above for any
template other than the legacy "ima" one. So the SHA-256 template hash is recomputable here even
though the ASCII log only ever shows the SHA-1 one.

Violations are the exception, and getting them wrong is why a first attempt at this replay
failed. When IMA cannot measure a file cleanly (ToMToU: someone held it open for write), it
stores a template entry whose digest was never computed, so the log shows ALL ZEROS, and it
extends every PCR bank with ALL ONES to poison the register. The log value and the extended
value are therefore different, and they are different in opposite directions. On an Azure Ubuntu
guest this fires on /var/lib/hyperv/.kvp_pool_* and the systemd journal, so any real log has
them. Rule confirmed empirically against both the SHA-1 and SHA-256 banks of a live vTPM.

No em dashes in output text.

"""

import hashlib
import struct

TEMPLATE_IMA_LEGACY = "ima"


class ImaEntry:
    def __init__(self, pcr, sha1_digest, name, data):
        self.pcr = pcr
        self.sha1_digest = sha1_digest
        self.name = name
        self.data = data

    @property
    def is_violation(self):
        return self.sha1_digest == b"\x00" * 20

    def bank_digest(self, alg):
        """What the kernel extended the given PCR bank with, for this entry."""
        size = {"sha1": 20, "sha256": 32}[alg]
        if self.is_violation:
            return b"\xff" * size
        if alg == "sha1":
            return self.sha1_digest
        if self.name == TEMPLATE_IMA_LEGACY:
            raise ValueError("legacy 'ima' template: per-field hashing differs, not supported")
        return hashlib.sha256(self.data).digest()

    def fields(self):
        """Split template_data into its [u32 len][bytes] fields."""
        out, off = [], 0
        while off + 4 <= len(self.data):
            (n,) = struct.unpack_from("<I", self.data, off)
            off += 4
            out.append(self.data[off:off + n])
            off += n
        return out

    def file_digest_and_path(self):
        """ima-ng: field 0 is 'sha256:\\0'+digest, field 1 is the path plus a NUL."""
        f = self.fields()
        if len(f) < 2:
            return None, None
        d = f[0]
        if b"\x00" in d:
            algo, raw = d.split(b"\x00", 1)
            digest = f"{algo.decode(errors='replace')}:{raw.hex()}"
        else:
            digest = d.hex()
        path = f[1].split(b"\x00", 1)[0].decode(errors="replace")
        return digest, path


def parse_binary_log(blob):
    entries, off, n = [], 0, len(blob)
    while off < n:
        if off + 4 + 20 + 4 > n:
            break
        (pcr,) = struct.unpack_from("<I", blob, off)
        off += 4
        sha1_digest = blob[off:off + 20]
        off += 20
        (name_len,) = struct.unpack_from("<I", blob, off)
        off += 4
        name = blob[off:off + name_len].split(b"\x00", 1)[0].decode(errors="replace")
        off += name_len
        (data_len,) = struct.unpack_from("<I", blob, off)
        off += 4
        data = blob[off:off + data_len]
        off += data_len
        entries.append(ImaEntry(pcr, sha1_digest, name, data))
    return entries


def replay(entries, alg="sha256", pcr_index=10):
    """Extend a zeroed PCR with every entry for that index, in order. Returns hex."""
    size = {"sha1": 20, "sha256": 32}[alg]
    h = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}[alg]
    pcr = b"\x00" * size
    for e in entries:
        if e.pcr != pcr_index:
            continue
        pcr = h(pcr + e.bank_digest(alg)).digest()
    return pcr.hex()


def measured_set(entries, start=0):
    """The (digest, path) pairs from entry `start` onward. boot_aggregate has no real path."""
    out = []
    for e in entries[start:]:
        digest, path = e.file_digest_and_path()
        if digest is None:
            continue
        out.append((digest, path))
    return out
