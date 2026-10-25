# Diagnose what happens when the lid closes. Run as administrator.
$report = Join-Path $PSScriptRoot "lid_report.txt"
"started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Out-File -FilePath $report -Encoding utf8

$out = @()

# 1. What does the ACTIVE power scheme say about lid close?
$out += "===== powercfg /query (lid close action) ====="
$out += (powercfg /query SCHEME_CURRENT SUB_BUTTONS LIDACTION | Out-String)

# 2. Is hibernate really off?
$out += "===== hibernate state ====="
$out += (powercfg /a | Out-String)

# 3. Sleep/hibernate/wake events in the last 24 hours
$out += "===== Power events (last 24h): Kernel-Power 42=sleep 107=resume 507-508=modern standby ====="
$out += (Get-WinEvent -FilterHashtable @{LogName='System'; Id=42,107,506,507,508; StartTime=(Get-Date).AddHours(-24)} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, @{N='Message';E={$_.Message -replace "`r?`n"," "}} |
    Format-Table -AutoSize -Wrap | Out-String)

# 4. The current effective sleep timeouts
$out += "===== effective sleep timeouts ====="
$out += (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Out-String)

# 5. What woke/put the system last time
$out += "===== lastwake ====="
$out += (powercfg /lastwake | Out-String)

$out | Out-File -FilePath $report -Encoding utf8 -Append
Write-Host "OK - report written to $report"
