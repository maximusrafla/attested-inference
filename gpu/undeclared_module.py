"""Undeclared code loaded as DATA by the already-declared interpreter.

Nothing here executes a new file, so under the IMA tcb policy a non-root run never measures it.
This is the probe for the policy-dependence limit, not an evasion technique: the mitigation is
in the policy (measure reads by the workload uid, or run the stack as root so the tcb euid=0
read rule fires), and both halves of the experiment get reported.
"""


def run():
    return {"undeclared_module": True, "did": "work the declaration never mentioned"}
