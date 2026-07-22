<#
  UePaths.ps1 -- shared discovery helpers, dot-sourced by ue.ps1 / ue_live.ps1.

  Nothing here is hardcoded to a machine. Resolution order, most explicit first:

    project : -UProject arg  ->  $env:UPROJECT  ->  the single *.uproject found
              by walking up from the current directory
    engine  : -UeCmd/-UeEditor arg  ->  $env:UE_CMD / $env:UE_EDITOR
              ->  $env:UE_ROOT + platform-relative binary path
              ->  Windows registry (Epic launcher + source builds)
              ->  common install roots, newest version wins

  Works under PowerShell 7 on Windows, macOS and Linux.
#>

# NB: deliberately no `Set-StrictMode` here -- this file is dot-sourced into the
# caller's session, and flipping strict mode on for someone else's shell breaks
# unrelated things (any unset variable, $LASTEXITCODE included, starts throwing).

function Get-UeBinaryRelativePath {
  param([ValidateSet('Editor', 'Cmd')][string]$Kind = 'Editor')
  if ($IsWindows -or $null -eq $IsWindows) {
    $exe = if ($Kind -eq 'Cmd') { 'UnrealEditor-Cmd.exe' } else { 'UnrealEditor.exe' }
    return "Engine/Binaries/Win64/$exe"
  }
  elseif ($IsMacOS) {
    # The .app wrapper is the GUI editor; the Cmd binary sits beside it.
    if ($Kind -eq 'Cmd') { return 'Engine/Binaries/Mac/UnrealEditor-Cmd' }
    return 'Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor'
  }
  else {
    $exe = if ($Kind -eq 'Cmd') { 'UnrealEditor-Cmd' } else { 'UnrealEditor' }
    return "Engine/Binaries/Linux/$exe"
  }
}

function Get-UeInstallRoots {
  <#  Every plausible engine root on this machine, newest-looking first. #>
  $roots = New-Object System.Collections.Generic.List[string]

  # 1. Windows registry -- launcher installs and registered source builds.
  if ($IsWindows -or $null -eq $IsWindows) {
    foreach ($key in @(
        'HKLM:\SOFTWARE\EpicGames\Unreal Engine',
        'HKLM:\SOFTWARE\WOW6432Node\EpicGames\Unreal Engine')) {
      try {
        Get-ChildItem $key -ErrorAction Stop | ForEach-Object {
          $dir = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).InstalledDirectory
          if ($dir) { $roots.Add($dir) }
        }
      } catch { }
    }
    try {
      $builds = Get-ItemProperty 'HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds' -ErrorAction Stop
      foreach ($p in $builds.PSObject.Properties) {
        if ($p.Name -notmatch '^PS' -and $p.Value -is [string]) { $roots.Add($p.Value) }
      }
    } catch { }
  }

  # 2. Conventional install locations per platform.
  $guesses = @()
  if ($IsWindows -or $null -eq $IsWindows) {
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
      $guesses += (Join-Path $drive.Root 'Program Files/Epic Games')
      $guesses += (Join-Path $drive.Root 'Epic Games')
    }
  }
  elseif ($IsMacOS) {
    $guesses += '/Users/Shared/Epic Games'
    $guesses += "$HOME/Epic Games"
  }
  else {
    $guesses += "$HOME/UnrealEngine"
    $guesses += '/opt/UnrealEngine'
  }
  foreach ($g in $guesses) {
    if (Test-Path $g) {
      Get-ChildItem $g -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^UE_?\d' -or (Test-Path (Join-Path $_.FullName 'Engine')) } |
        ForEach-Object { $roots.Add($_.FullName) }
    }
  }

  # Newest version first, judged by the trailing number in the folder name.
  $roots | Where-Object { $_ } | Select-Object -Unique | Sort-Object -Descending {
    if ($_ -match '(\d+)\.(\d+)') { [double]("{0}.{1}" -f $Matches[1], $Matches[2]) } else { 0 }
  }
}

function Resolve-UeBinary {
  <#
    .SYNOPSIS  Absolute path to UnrealEditor / UnrealEditor-Cmd.
  #>
  param(
    [string]$Explicit,
    [ValidateSet('Editor', 'Cmd')][string]$Kind = 'Editor'
  )

  if ($Explicit) {
    if (-not (Test-Path $Explicit)) { throw "engine binary not found: $Explicit" }
    return (Resolve-Path $Explicit).Path
  }

  $envVar = if ($Kind -eq 'Cmd') { $env:UE_CMD } else { $env:UE_EDITOR }
  if ($envVar) {
    if (-not (Test-Path $envVar)) {
      throw ("$(if ($Kind -eq 'Cmd') { 'UE_CMD' } else { 'UE_EDITOR' }) points at a " +
             "missing file: $envVar")
    }
    return (Resolve-Path $envVar).Path
  }

  $rel = Get-UeBinaryRelativePath -Kind $Kind
  $candidates = @()
  if ($env:UE_ROOT) { $candidates += $env:UE_ROOT }
  $candidates += (Get-UeInstallRoots)

  foreach ($root in $candidates) {
    $bin = Join-Path $root $rel
    if (Test-Path $bin) { return (Resolve-Path $bin).Path }
  }

  throw @"
could not locate $rel.

Point the tools at your engine with ONE of:
  `$env:UE_ROOT   = 'D:/Program Files/Epic Games/UE_5.5'    # engine root
  `$env:UE_CMD    = '<root>/$(Get-UeBinaryRelativePath -Kind Cmd)'
  `$env:UE_EDITOR = '<root>/$(Get-UeBinaryRelativePath -Kind Editor)'
or pass -UeCmd / -UeEditor explicitly.

Searched: $($candidates -join '; ')
"@
}

function Resolve-UProject {
  <#
    .SYNOPSIS  Absolute path to the .uproject to operate on.
  #>
  param([string]$Explicit)

  if ($Explicit) {
    if (-not (Test-Path $Explicit)) { throw ".uproject not found: $Explicit" }
    return (Resolve-Path $Explicit).Path
  }
  if ($env:UPROJECT) {
    if (-not (Test-Path $env:UPROJECT)) { throw "UPROJECT points at a missing file: $env:UPROJECT" }
    return (Resolve-Path $env:UPROJECT).Path
  }

  # Walk up from the working directory looking for exactly one .uproject.
  $dir = (Get-Location).Path
  while ($dir) {
    $found = @(Get-ChildItem -Path $dir -Filter '*.uproject' -File -ErrorAction SilentlyContinue)
    if ($found.Count -eq 1) { return $found[0].FullName }
    if ($found.Count -gt 1) {
      throw "several .uproject files in ${dir}: $($found.Name -join ', ') -- pass -UProject."
    }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }

  throw @"
no .uproject found walking up from $((Get-Location).Path).

Run these tools from inside your project, or set:
  `$env:UPROJECT = '<path>/YourGame.uproject'
or pass -UProject explicitly.
"@
}
