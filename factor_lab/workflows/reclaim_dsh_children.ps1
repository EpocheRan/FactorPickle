param(
    [switch]$Apply,
    [string]$Workspace = ""
)

if (-not $Workspace) {
    $Workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

# DSH has no public "close subagent" tool.  Workflow children are stored as
# non-"session-*" directories; archive them outside the sessions tree.
$dshHome = Join-Path $env:USERPROFILE ".dsh"
$workspaceKey = "--" + (($Workspace -replace ':', '') -replace '\\', '-') + "--"
$root = Join-Path $dshHome "sessions\$workspaceKey"
if (-not (Test-Path -LiteralPath $root)) {
    throw "DSH workspace sessions not found: $root"
}

$children = Get-ChildItem -LiteralPath $root -Directory |
    Where-Object { $_.Name -notlike "session-*" }
if (-not $children) {
    Write-Output "no child sessions to reclaim"
    exit 0
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $dshHome "reclaimed_sessions\$workspaceKey\$stamp"
Write-Output ("candidates: " + $children.Count + " -> " + $target)
foreach ($child in $children) {
    Write-Output (($(if ($Apply) { "MOVE" } else { "DRY " })) + " " + $child.Name)
    if ($Apply) {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        Move-Item -LiteralPath $child.FullName -Destination (Join-Path $target $child.Name) -Force
    }
}
if ($Apply) {
    Write-Output "archived; refresh the DSH page to clear the child-agent panel"
}
