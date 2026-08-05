# M1: provision an AMD SEV-SNP confidential VM on Azure.
# Run from Windows PowerShell after `az login`.
# Cost: Standard_DC2as_v5 is about $0.09/hour. Deallocate or delete when done.

$ErrorActionPreference = "Stop"
$env:PATH += ";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"

$RG      = "ccverify-m1-rg"
$VM      = "ccverify-m1-vm"
$SIZE    = "Standard_DC2as_v5"
$IMAGE   = "Canonical:0001-com-ubuntu-confidential-vm-jammy:22_04-lts-cvm:latest"
$ADMIN   = "azureuser"
$KEY     = "$env:USERPROFILE\.ssh\ccverify_m1.pub"
$REGIONS = @("eastus2", "eastus", "westeurope", "northeurope", "westus", "centralus")

Write-Host "== finding a region that offers $SIZE =="
$LOC = $null
foreach ($r in $REGIONS) {
  $skus = az vm list-skus --location $r --size $SIZE --all -o json | ConvertFrom-Json
  $ok = $skus | Where-Object { $_.name -eq $SIZE -and -not ($_.restrictions | Where-Object { $_.type -eq "Location" }) }
  if ($ok) { $LOC = $r; Write-Host "  $r : AVAILABLE"; break } else { Write-Host "  $r : not available" }
}
if (-not $LOC) { throw "No region in the candidate list offers $SIZE for this subscription. Request quota or extend `$REGIONS." }

# A SKU can be "available" in a region while your quota for its family is 0, which is the
# default on many new subscriptions. Check now: the deployment error otherwise is cryptic.
# Note: free-trial subscriptions cannot raise quota. If this is 0, upgrade to pay-as-you-go.
Write-Host "== checking DCASv5 vCPU quota in $LOC =="
$usage = az vm list-usage --location $LOC -o json | ConvertFrom-Json
$fam = $usage | Where-Object { $_.name.value -eq "standardDCASv5Family" }
if ($fam) {
  Write-Host "  standardDCASv5Family: $($fam.currentValue) used of $($fam.limit)"
  if ($fam.limit -lt 2) {
    throw "DCASv5 quota is $($fam.limit) vCPUs in $LOC and $SIZE needs 2. Request a quota increase (Portal > Quotas > Compute), or if this is a free-trial subscription, upgrade to pay-as-you-go first since trials cannot raise quota."
  }
} else {
  Write-Host "  standardDCASv5Family quota not reported for $LOC; continuing, deployment will tell us."
}

Write-Host "== creating resource group $RG in $LOC =="
az group create --name $RG --location $LOC -o none

Write-Host "== creating confidential VM $VM (SEV-SNP, vTPM, secure boot) =="
az vm create `
  --resource-group $RG `
  --name $VM `
  --size $SIZE `
  --location $LOC `
  --image $IMAGE `
  --admin-username $ADMIN `
  --ssh-key-values $KEY `
  --security-type ConfidentialVM `
  --os-disk-security-encryption-type VMGuestStateOnly `
  --enable-vtpm true `
  --enable-secure-boot true `
  --public-ip-sku Standard `
  -o json | Out-File -Encoding utf8 "$PSScriptRoot\vm-create.json"

$ip = (az vm show -d -g $RG -n $VM --query publicIps -o tsv)
Write-Host ""
Write-Host "VM ready. Public IP: $ip"
Write-Host "SSH: ssh -i `"$env:USERPROFILE\.ssh\ccverify_m1`" $ADMIN@$ip"
$ip | Out-File -Encoding ascii "$PSScriptRoot\vm-ip.txt"
