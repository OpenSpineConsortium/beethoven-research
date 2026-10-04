# bee for PowerShell (Windows PowerShell 5.1 or PowerShell 7). Dot-source it from $PROFILE:
#
#   . C:\path\to\beethoven-research\src\beethoven_research\bee\shell\bee.ps1
#
#   bee <args>          the bee command, from the engine checkout's pixi environment
#   bee-use <label>     switch this shell to an account: CLAUDE_CONFIG_DIR and BEE_ACCOUNT set,
#                       every credential variable (an API key, a token) removed
#   bee-which           which account this shell is on
#
# For the default account it removes CLAUDE_CONFIG_DIR rather than pointing it at ~\.claude
# (docs/bee/STATE_HANDOFF.md). Beethoven runs Claude Code and is not an Anthropic product.

if ($env:BEE_ENGINE) {
    $script:BeeEngine = $env:BEE_ENGINE
} else {
    $script:BeeEngine = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
}

function bee {
    $py = Join-Path $script:BeeEngine '.pixi\envs\default\python.exe'
    if (-not (Test-Path $py)) { $py = Join-Path $script:BeeEngine '.pixi/envs/default/bin/python' }
    if (-not (Test-Path $py)) {
        Write-Error "bee: run 'pixi install --locked' in $($script:BeeEngine) first"
        return
    }
    $saved = $env:PYTHONPATH
    $savedIo = $env:PYTHONIOENCODING
    $savedOut = [Console]::OutputEncoding
    $env:PYTHONPATH = Join-Path $script:BeeEngine 'src'
    # UTF-8 both ways: otherwise python writes the ANSI code page and PowerShell reads the OEM one,
    # and a path with an accented letter comes back as another directory.
    $env:PYTHONIOENCODING = 'utf-8'
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    try {
        & $py -m beethoven_research.bee @args
    } finally {
        $env:PYTHONPATH = $saved
        $env:PYTHONIOENCODING = $savedIo
        [Console]::OutputEncoding = $savedOut
    }
}

function bee-use {
    param([Parameter(Mandatory = $true)][string]$Label)
    $lines = bee account env $Label --shell powershell
    if ($LASTEXITCODE -ne 0) { return }
    Invoke-Expression ($lines -join "`n")
    $where = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "Claude Code's default place" }
    Write-Host "This shell now uses the account $Label ($where)."
}

function bee-which {
    $who = if ($env:BEE_ACCOUNT) { $env:BEE_ACCOUNT } else { 'no account chosen' }
    $where = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "Claude Code's default place" }
    "$who ($where)"
}
