# Phase A: a cheap vTPM-equipped VM to build and validate the Tier 1 machinery on.
#
# Everything Tier 1 needs (Linux IMA, the vTPM quote over PCR 10, the replay verifier) is
# CPU-side and needs a vTPM, not SEV-SNP. Building it here instead of on the H100 box at ~$9/hr
# is what keeps the GPU hours inside the cap. The SEV-SNP half of the chain is already proved by
# M1 and gets re-proved on the GPU box, which is itself an SEV-SNP CVM.
#
# Friction finding 10: the pay-as-you-go conversion left standardDCASv5Family at 0 vCPUs in
# every region, so M1's Standard_DC2as_v5 recipe no longer deploys without a new quota request.
# Only StandardNCCads2023Family (40, the GPU grant) and standardEBDSv5Family (10) have room.
# Hence a Trusted Launch Ebdsv5 box for Phase A.
$ErrorActionPreference = "Stop"
$env:PATH += ";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"

$RG    = "ccverify-gpudev-rg"
$VM    = "ccverify-gpudev-vm"
$SIZE  = "Standard_E2bds_v5"
$LOC   = "eastus2"
$IMAGE = "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest"
$ADMIN = "azureuser"
$KEY   = "$env:USERPROFILE\.ssh\ccverify_m1.pub"

Write-Host "== creating resource group $RG in $LOC =="
az group create --name $RG --location $LOC -o none

Write-Host "== creating Trusted Launch VM $VM (vTPM, secure boot) =="
az vm create `
  --resource-group $RG --name $VM --size $SIZE --location $LOC --image $IMAGE `
  --admin-username $ADMIN --ssh-key-values $KEY `
  --security-type TrustedLaunch --enable-vtpm true --enable-secure-boot true `
  --public-ip-sku Standard `
  -o json | Out-File -Encoding utf8 "$PSScriptRoot\dev-vm-create.json"

$ip = (az vm show -d -g $RG -n $VM --query publicIps -o tsv)
Write-Host "dev VM ready. Public IP: $ip"
$ip | Out-File -Encoding ascii "$PSScriptRoot\dev-vm-ip.txt"
