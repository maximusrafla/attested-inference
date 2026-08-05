#!/usr/bin/env bash
# M1 step 2: on the SEV-SNP confidential VM, install the Azure guest attestation
# client and prove the hardware is what it claims to be.
set -euo pipefail

echo "== sanity: is this actually an SEV-SNP guest? =="
sudo dmesg | grep -i -E "sev|snp" | head -20 || true
ls -l /dev/tpm0 /dev/tpmrm0 2>/dev/null || echo "WARNING: no vTPM device"
if [ -e /dev/sev-guest ]; then echo "/dev/sev-guest present"; else echo "NOTE: /dev/sev-guest absent (client uses the vTPM path)"; fi

echo "== build deps =="
sudo apt-get update -q
sudo apt-get install -y -q build-essential cmake git wget \
  libcurl4-openssl-dev libjsoncpp-dev libboost-all-dev nlohmann-json3-dev \
  jq python3

echo "== install azguestattestation1 (the attestation library) =="
BASE="https://packages.microsoft.com/repos/azurecore/pool/main/a/azguestattestation1"
DEB=$(wget -qO- "$BASE/" | grep -o 'azguestattestation1_[^"<]*_amd64\.deb' | sort -V | tail -1)
echo "latest package: $DEB"
wget -q "$BASE/$DEB" -O /tmp/azguestattestation1.deb
sudo dpkg -i /tmp/azguestattestation1.deb || sudo apt-get -f install -y

echo "== build the sample attestation client =="
cd "$HOME"
[ -d confidential-computing-cvm-guest-attestation ] || \
  git clone --depth 1 https://github.com/Azure/confidential-computing-cvm-guest-attestation.git
cd confidential-computing-cvm-guest-attestation/cvm-attestation-sample-app
cmake . && make
echo "built: $(pwd)/AttestationClient"
sudo cp AttestationClient /usr/local/bin/
echo "== vm-setup done =="
