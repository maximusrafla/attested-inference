#!/usr/bin/env bash
# One-time setup on the confidential H100 box for the 2026-09-14 Tier 1 sequence.
#   tpm2-tools; Azure's attestation client (milestone1/vm-setup.sh); torch in ~/venv for the declared
#   stack; NVIDIA's Python attestation SDK in its own ~/nvenv, because it pins library versions.
set -euo pipefail
sudo apt-get update -q >/dev/null
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q tpm2-tools acl jq python3-venv >/dev/null
( python3 -m venv ~/venv && ~/venv/bin/pip install -q torch==2.13.0 && echo "torch ok: $(~/venv/bin/python -c 'import torch;print(torch.__version__, torch.cuda.is_available())')" ) > ~/setup-torch.log 2>&1 &
( python3 -m venv ~/nvenv && ~/nvenv/bin/pip install -q nv-attestation-sdk && echo "nv sdk ok: $(~/nvenv/bin/pip show nv-attestation-sdk | grep Version)" ) > ~/setup-nv.log 2>&1 &
bash ~/vm-setup.sh > ~/setup-attclient.log 2>&1 &
wait
tail -1 ~/setup-torch.log ~/setup-nv.log ~/setup-attclient.log
