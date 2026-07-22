<#
  ue.ps1 -- host-side driver for the headless UE asset bridge.

  Launches a headless UnrealEditor-Cmd against your .uproject and runs bridge.py
  (via the PythonScriptPlugin), which reads _exports/_cmd.json, does the work, and
  writes _exports/_result.json.

  THE EDITOR MUST BE CLOSED -- a running editor holds the project lock.

  Usage:
    pwsh headless/ue.ps1 probe
    pwsh headless/ue.ps1 manifest -ArgsJson '{"class":"Material","contains":"Wall"}'
    pwsh headless/ue.ps1 texture  -ArgsJson '{"asset":"/Game/Art/T_Foo_D"}'
    pwsh headless/ue.ps1 material -ArgsJson '{"asset":"/Game/Art/M_Foo"}'
    pwsh headless/ue.ps1 batch    -ArgsFile my_steps.json

  Each invocation pays the full ~30-90s engine boot, so batch related work.

  Engine + project are auto-discovered (see lib/UePaths.ps1); override with
  -UeCmd / -UProject or the UE_ROOT / UE_CMD / UPROJECT env vars.

  Exit code mirrors bridge success. Prints _result.json to stdout at the end.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$Command,

  # JSON object of args for the command (see bridge.py docstrings / docs/COMMANDS.md).
  [string]$ArgsJson = '{}',

  # Alternative to -ArgsJson: path to a .json file holding the args object.
  # Use for big/nested payloads (e.g. batch) where shell quoting is painful.
  [string]$ArgsFile = '',

  [string]$UeCmd = '',
  [string]$UProject = '',

  # Headless render backend. NullRHI is fastest but some exporters need a real
  # RHI; flip this off (-NullRhi:$false) if a texture/mesh export comes back empty.
  [switch]$NullRhi = $true
)

$ErrorActionPreference = 'Stop'
# Pass native-exe args literally; let PowerShell quote elements with spaces itself.
# (Pre-wrapping paths in quotes + splatting double-quotes them and corrupts the path.)
$PSNativeCommandArgumentPassing = 'Standard'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path -Parent $here) 'lib/UePaths.ps1')

$exports = if ($env:UE_BRIDGE_EXPORTS) { $env:UE_BRIDGE_EXPORTS } else { Join-Path $here '_exports' }
$bridge  = Join-Path $here 'bridge.py'
$cmdFile = Join-Path $exports '_cmd.json'
$resFile = Join-Path $exports '_result.json'

New-Item -ItemType Directory -Force -Path $exports | Out-Null

# --- resolve engine + project ---------------------------------------------- #
if (-not (Test-Path $bridge)) { throw "not found: $bridge" }
$ueCmdPath = Resolve-UeBinary -Explicit $UeCmd -Kind Cmd
$project   = Resolve-UProject -Explicit $UProject

# -ArgsFile wins over -ArgsJson when supplied.
if ($ArgsFile) {
  if (-not (Test-Path $ArgsFile)) { throw "ArgsFile not found: $ArgsFile" }
  $ArgsJson = Get-Content $ArgsFile -Raw
}

# validate ArgsJson early so we fail before a 60s engine boot
try { $null = $ArgsJson | ConvertFrom-Json } catch { throw "args is not valid JSON: $ArgsJson" }

# --- write the command file the bridge reads ------------------------------- #
$cmdObj = [ordered]@{ command = $Command; args = ($ArgsJson | ConvertFrom-Json) }
$cmdObj | ConvertTo-Json -Depth 20 | Set-Content -Path $cmdFile -Encoding UTF8

# clear any stale result so we never read a previous run's output
if (Test-Path $resFile) { Remove-Item $resFile -Force }

# --- launch headless UE ---------------------------------------------------- #
# Args passed raw (no manual quoting); paths may contain spaces and PS quotes them.
$ueLog = Join-Path $exports '_ue.log'
$ueArgs = @(
  $project,
  "-ExecutePythonScript=$bridge",
  '-unattended', '-nopause', '-nosplash', '-nosound',
  '-stdout', '-FullStdOutLogOutput',
  "-abslog=$ueLog"
)
if ($NullRhi) { $ueArgs += '-nullrhi' }

Write-Host "[ue.ps1] $Command  args=$ArgsJson"
Write-Host "[ue.ps1]   engine : $ueCmdPath"
Write-Host "[ue.ps1]   project: $project"
Write-Host "[ue.ps1] launching headless UE (this takes ~30-90s on first load)..."

$env:UE_BRIDGE_EXPORTS = $exports

$sw = [System.Diagnostics.Stopwatch]::StartNew()
& $ueCmdPath @ueArgs *> (Join-Path $exports '_ue_stdout.log')
$sw.Stop()
Write-Host ("[ue.ps1] UE exited after {0:n1}s (exit={1})" -f $sw.Elapsed.TotalSeconds, $LASTEXITCODE)

# --- report ---------------------------------------------------------------- #
if (-not (Test-Path $resFile)) {
  Write-Host "[ue.ps1] NO RESULT FILE. Tail of UE log ($ueLog):"
  if (Test-Path $ueLog) { Get-Content $ueLog -Tail 50 }
  else { Write-Host "(no UE log written) stdout capture:"; Get-Content (Join-Path $exports '_ue_stdout.log') -Tail 50 }
  Write-Host "[ue.ps1] Common causes: the editor is still open (project locked), or"
  Write-Host "[ue.ps1] PythonScriptPlugin is not enabled in the .uproject."
  exit 1
}

$res = Get-Content $resFile -Raw
Write-Host "---RESULT---"
Write-Host $res
$ok = ($res | ConvertFrom-Json).ok
exit ([int](-not $ok))
