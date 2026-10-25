# Find the adb.exe that ships with MuMu, wherever MuMu is installed.
# Called by start.bat / start_full.bat when adb is not in the usual places.
# Prints the folder that contains adb.exe, or nothing if not found.
#
# Where we look:
#   1. Folders of running MuMu processes (works for any install path,
#      as long as MuMu is open - which it must be anyway).
#   2. MuMu entries in the Windows "installed programs" registry.
# From each of these we go up one folder and search a few levels down,
# because adb.exe sits in different subfolders in different MuMu versions.

$ErrorActionPreference = 'SilentlyContinue'
$roots = New-Object System.Collections.Generic.List[string]

Get-Process | Where-Object { $_.Name -like 'MuMu*' -or $_.Name -like 'Nemu*' } |
  ForEach-Object { if ($_.Path) { $roots.Add((Split-Path $_.Path)) } }

$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
Get-ItemProperty $keys | Where-Object { $_.DisplayName -like '*MuMu*' } | ForEach-Object {
  if ($_.InstallLocation) { $roots.Add($_.InstallLocation.Trim('"').TrimEnd('\')) }
  foreach ($s in @($_.DisplayIcon, $_.UninstallString)) {
    if ($s -and $s -match '^"?([^"]+?\.exe)') { $roots.Add((Split-Path $matches[1])) }
  }
}

$seen = @{}
$best = $null
foreach ($r in $roots) {
  if (-not $r) { continue }
  $base = Split-Path $r
  if (-not $base) { $base = $r }
  if ($seen.ContainsKey($base)) { continue }
  $seen[$base] = $true
  $hits = Get-ChildItem -Path $base -Filter adb.exe -Recurse -Depth 4 -File |
          Where-Object { $_.FullName -match 'mumu' }
  foreach ($h in $hits) {
    # prefer an adb.exe that has its two dlls next to it
    if (Test-Path (Join-Path $h.DirectoryName 'AdbWinApi.dll')) {
      Write-Output $h.DirectoryName
      exit 0
    }
    if (-not $best) { $best = $h.DirectoryName }
  }
}
if ($best) { Write-Output $best }
