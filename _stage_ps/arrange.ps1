$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

New-Item -ItemType Directory -Force -Path static | Out-Null
if (Test-Path _stage_py\main.py)        { Move-Item -Force _stage_py\main.py . }
if (Test-Path _stage_req\requirements.txt) { Move-Item -Force _stage_req\requirements.txt . }
if (Test-Path _stage_readme\README.md)  { Move-Item -Force _stage_readme\README.md . }
if (Test-Path _stage_html\index.html)   { Move-Item -Force _stage_html\index.html static\ }
if (Test-Path _stage_css\style.css)     { Move-Item -Force _stage_css\style.css static\ }
if (Test-Path _stage_js\app.js)         { Move-Item -Force _stage_js\app.js static\ }
Remove-Item -Recurse -Force _stage_py,_stage_req,_stage_readme,_stage_html,_stage_css,_stage_js -ErrorAction SilentlyContinue
Remove-Item -Force probe_ok.txt,static\test.txt -ErrorAction SilentlyContinue

if (!(Test-Path .env)) {
  if ($env:BACKBOARD_API_KEY) {
    "BACKBOARD_API_KEY=$($env:BACKBOARD_API_KEY)" | Out-File -Encoding ascii .env
  } else {
    "BACKBOARD_API_KEY=PUT_YOUR_KEY_HERE" | Out-File -Encoding ascii .env
  }
}
if (!(Test-Path .gitignore)) {
  ".venv/`n.env`n__pycache__/`nnexus_assistant.json`n*.txt_log" | Out-File -Encoding ascii .gitignore
}

if (!(Test-Path .venv)) { python -m venv .venv }
.venv\Scripts\python.exe -m pip install -q -r requirements.txt

# Inspect the installed backboard SDK so code follows actual signatures
.venv\Scripts\python.exe -c @"
import inspect
import backboard
from backboard import BackboardClient
print('backboard version:', getattr(backboard, '__version__', 'unknown'))
for name in ['send_message','submit_tool_outputs_simple','create_assistant','get_memories','delete_memory','add_message','submit_tool_outputs']:
    fn = getattr(BackboardClient, name, None)
    if fn is None:
        print(name, ': MISSING')
    else:
        try:
            print(name, inspect.signature(fn))
        except Exception as e:
            print(name, ': sig error', e)
"@ | Out-File -Encoding ascii sdk_report.txt

"SETUP_DONE" | Out-File -Encoding ascii setup_done.txt
Get-Content sdk_report.txt
