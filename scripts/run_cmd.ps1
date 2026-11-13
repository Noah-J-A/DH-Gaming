$ErrorActionPreference = "Continue"
$out = & {
  $conns = Get-NetTCPConnection -LocalPort 8077 -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) { "killing " + $c.OwningProcess; Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
  "done"
} 2>&1 | Out-String
[System.IO.File]::WriteAllText("$PSScriptRoot\out.txt", $out)
