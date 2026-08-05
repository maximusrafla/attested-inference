# The GPU box: Standard_NCC40ads_H100_v5, one H100 NVL inside an AMD SEV-SNP confidential VM.
#
# The image is the documented community-gallery VMI from Azure/az-cgpu-onboarding, not a
# marketplace image: it ships the NVIDIA CC driver, CUDA, and the attestation tooling, and it is
# provisioned as a ConfidentialVM with secure boot and vTPM, the same shape as M1.
#
# Meter check before renting: eastus2 carries this SKU at $6.98 and $8.82/hr depending on meter.
# Budget cap for this session is $150, so track hours and tear down as soon as the tiers are in.
$ErrorActionPreference = "Stop"
$env:PATH += ";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"

$RG    = "ccverify-gpu-rg"
$VM    = "ccverify-gpu-vm"
$SIZE  = "Standard_NCC40ads_H100_v5"
$LOC   = "eastus2"
$IMAGE = "/CommunityGalleries/cgpuimage-db870bae-5bcf-4120-9415-b841adef61d3/Images/cgpu-NCC-2204-base-image/Versions/latest"
$ADMIN = "azureuser"
$KEY   = "$env:USERPROFILE\.ssh\ccverify_m1.pub"

Write-Host "== quota check =="
az vm list-usage --location $LOC --query "[?name.value=='StandardNCCads2023Family'].{used:currentValue,limit:limit}" -o table

Write-Host "== creating resource group $RG in $LOC =="
az group create --name $RG --location $LOC -o none

Write-Host "== creating confidential GPU VM $VM (SEV-SNP CVM + H100 in CC mode) =="
$t0 = Get-Date
az vm create `
  --resource-group $RG --name $VM --size $SIZE --location $LOC --image $IMAGE `
  --admin-username $ADMIN --ssh-key-values $KEY `
  --security-type ConfidentialVM --os-disk-security-encryption-type VMGuestStateOnly `
  --enable-secure-boot true --enable-vtpm true `
  --os-disk-size-gb 128 --public-ip-sku Standard --accept-term `
  -o json | Out-File -Encoding utf8 "$PSScriptRoot\gpu-vm-create.json"

$ip = (az vm show -d -g $RG -n $VM --query publicIps -o tsv)
Write-Host "GPU VM ready in $([int]((Get-Date) - $t0).TotalSeconds)s. Public IP: $ip"
Write-Host "BILLING CLOCK STARTED: $(Get-Date -Format o)"
$ip | Out-File -Encoding ascii "$PSScriptRoot\gpu-vm-ip.txt"
(Get-Date -Format o) | Out-File -Encoding ascii "$PSScriptRoot\gpu-vm-start.txt"
