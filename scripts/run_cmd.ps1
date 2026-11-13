$ErrorActionPreference = "Continue"
$out = & {
  "ENVVAR: " + [bool]$env:BACKBOARD_API_KEY
  "USERENV: " + [bool][Environment]::GetEnvironmentVariable('BACKBOARD_API_KEY','User')
  "---ide---"
  Get-ChildItem -Recurse -Force C:\Users\super\.backboard_ide -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName | Select-Object -First 40
  "---bb---"
  Get-ChildItem -Recurse -Force C:\Users\super\Backboard -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName | Select-Object -First 40
  "---grep---"
  Get-ChildItem C:\Users\super\.bashrc, C:\Users\super\.bash_profile -ErrorAction SilentlyContinue | ForEach-Object { Select-String -Path $_ -Pattern 'BACKBOARD' -ErrorAction SilentlyContinue }
} 2>&1 | Out-String
[System.IO.File]::WriteAllText("$PSScriptRoot\out.txt", $out)
