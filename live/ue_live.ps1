<#
  ue_live.ps1 -- open the GUI editor with the live Python bridge running.

  This is a FALLBACK launcher. If you installed the editor hook
  (`python install.py --project <YourGame.uproject>`), the bridge already starts
  on every normal editor launch and you never need this script.

  Launches the full UnrealEditor (not -Cmd) and auto-runs ue_live_server.py via
  -ExecutePythonScript, which opens a local socket server. -ExecutePythonScript
  implicitly enables the Python plugin, so no manual plugin toggling is needed.

  The editor stays open. Talk to it from another shell:
    python live/ue_live.py --ping
    python live/ue_live.py -c "unreal.SystemLibrary.get_engine_version()"

  Usage:
    pwsh live/ue_live.ps1
    pwsh live/ue_live.ps1 -Wait                      # block until the editor exits
    pwsh live/ue_live.ps1 -UProject D:/Game/My.uproject -Port 6768

  Engine + project are auto-discovered (see lib/UePaths.ps1); override with
  -UeEditor / -UProject or the UE_ROOT / UE_EDITOR / UPROJECT env vars.
#>
[CmdletBinding()]
param(
  [string]$UeEditor = '',
  [string]$UProject = '',
  # Port the in-editor server listens on. Give each project its own port if you
  # want two editors drivable at the same time.
  [int]$Port = 0,
  # Block until the editor closes (default: launch detached and return).
  [switch]$Wait
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path -Parent $here) 'lib/UePaths.ps1')

$server  = Join-Path $here 'ue_live_server.py'
if (-not (Test-Path $server)) { throw "not found: $server" }

$editor  = Resolve-UeBinary -Explicit $UeEditor -Kind Editor
$project = Resolve-UProject -Explicit $UProject

if ($Port -gt 0) { $env:UE_BRIDGE_PORT = "$Port" }
$effectivePort = if ($env:UE_BRIDGE_PORT) { $env:UE_BRIDGE_PORT } else { '6767' }

$ueArgs = @(
  $project,
  "-ExecutePythonScript=$server",
  '-EnablePlugins=PythonScriptPlugin'
)

Write-Host "[ue_live] launching GUI editor with the live bridge..."
Write-Host "[ue_live]   engine : $editor"
Write-Host "[ue_live]   project: $project"
Write-Host "[ue_live]   server : $server (listens on 127.0.0.1:$effectivePort)"
Write-Host "[ue_live] editor boot takes ~30-90s; then: python live/ue_live.py --ping"

if ($Wait) {
  & $editor @ueArgs
} else {
  Start-Process -FilePath $editor -ArgumentList $ueArgs | Out-Null
  Write-Host "[ue_live] editor launched (detached). This shell is free."
}
