#!/usr/bin/env bash
# Stands in for an undeclared workload the operator runs alongside the approved one.
# It is an EXECUTED file, so IMA's BPRM_CHECK rule measures it and PCR 10 moves.
# tier1.sh appends a run-unique line before executing it, so its digest is never one the
# regulator has already approved, which is what an undeclared workload looks like.
echo "undeclared workload running as $(id -un)"
: "$(( 1 + 1 ))"
