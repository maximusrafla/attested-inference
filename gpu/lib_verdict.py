"""The completeness verdict, computed the same way on both sides of the boundary.

The operator's pre-check (completeness_check.py) and the regulator's verifier (verify_completeness.py)
both import this, so the regulator's recomputation is the definition of the verdict and the operator's
copy is only a rehearsal of it. Everything here is a function of four inputs the regulator holds for
itself: the exported measurement log, the quote, the approved baseline and the approved declaration.
Nothing is read from the operator's disclosure.

Why weights and configuration are judged from the log. Until 2026-09-14 the checker hashed the declared
files at their declared paths and wrote the answer into the disclosure. That answer came from code the
regulator could not identify, about a file the program was not shown to have read. The ceremony now runs
the declared stack as a dedicated serving account whose every read is measured
(`measure func=FILE_CHECK mask=^MAY_READ uid=<serving uid>`). So the declared weights and configuration
must appear in the attested window by digest, and any other file that account read, a swapped weight file
or an edited configuration included, appears as an undeclared digest.
"""

import hashlib

from lib_tpm import composite_digest, selected_pcrs


def find_window(entries, att, boot_values):
    """Return (entries_covered, pcr10_hex) for the log prefix the quote signed, or (None, None).

    With PCR 10 alone selected, pcrDigest is sha256 of PCR 10. With PCRs 0 to 10 selected, it is sha256
    over their values in index order, so the boot registers the operator supplies must also be right or no
    prefix matches. boot_values maps index to 32 raw bytes for every selected index other than 10.
    """
    sel = selected_pcrs(att["selections"])
    if 10 not in sel:
        return None, None
    missing = [i for i in sel if i != 10 and i not in boot_values]
    if missing:
        return None, None
    target = att["pcr_digest"]

    def digest(pcr10):
        return composite_digest([pcr10 if i == 10 else boot_values[i] for i in sel])

    pcr = bytes(32)
    if digest(pcr) == target:
        return 0, pcr.hex()
    for i, e in enumerate(entries):
        if e.pcr != 10:
            continue
        pcr = hashlib.sha256(pcr + e.bank_digest("sha256")).digest()
        if digest(pcr) == target:
            return i + 1, pcr.hex()
    return None, None


def boot_aggregate_matches(entries, boot_values):
    """IMA's first entry hashes PCRs 0 to 9 (sha256 bank, kernel 5.8 and later). True, False, or None if
    the quote did not cover all ten registers."""
    if not entries or any(i not in boot_values for i in range(10)):
        return None
    digest, path = entries[0].file_digest_and_path()
    if path != "boot_aggregate" or digest is None:
        return False
    expected = hashlib.sha256(b"".join(boot_values[i] for i in range(10))).hexdigest()
    return digest.split(":")[-1] == expected


def judge(entries, covered, baseline, declaration):
    """The verdict over the attested window, as a dict with the same field names the disclosure uses."""
    approved = set(baseline["digests"]) | set(declaration["digests"])
    weights = ("sha256::" + declaration["weights_sha256"]) if declaration.get("weights_sha256") else None
    config = ("sha256::" + declaration["config_sha256"]) if declaration.get("config_sha256") else None
    approved |= {d for d in (weights, config) if d}

    measured, undeclared, violations, checked = set(), [], [], 0
    for e in entries[:covered or 0]:
        digest, path = e.file_digest_and_path()
        if digest is None:
            continue
        checked += 1
        # A violation is IMA saying it could not measure this file: the log records an all-zero
        # digest and the register is extended with all ones, whatever the file held. Both recorded
        # fields are therefore worthless, and the same all-zero digest appears for every violation,
        # so letting one into an approved set approves every unmeasurable file at once. They are
        # counted separately and never matched against the approved set.
        if e.is_violation:
            violations.append((digest, path))
            continue
        measured.add(digest)
        if digest not in approved:
            undeclared.append((digest, path))

    weights_seen = (weights in measured) if weights else None
    config_seen = (config in measured) if config else None

    rejected_by = []
    if covered is None:
        rejected_by.append("log_integrity")
    if undeclared:
        rejected_by.append("undeclared_measurement")
    if violations:
        rejected_by.append("measurement_violation")
    if weights_seen is False:
        rejected_by.append("payload_identity")
    if config_seen is False:
        rejected_by.append("configuration_identity")
    return {
        "verdict": "accept" if not rejected_by else "reject",
        "rejected_by": rejected_by,
        "ima_entries_checked": checked,
        "undeclared_entries": len(undeclared),
        "weights_in_measured_window": weights_seen,
        "config_in_measured_window": config_seen,
        "violation_entries": len(violations),
        "undeclared": undeclared,
        "violations": violations,
    }
