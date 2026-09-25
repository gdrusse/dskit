# Early "SWEEP THE REPO BEFORE BUILDING" reminder for the Codex hook
# (Codex on Windows). Same contract as remind.sh: reads the hook payload
# on stdin, reminds once per session when the payload (prompt, cwd,
# command, path) mentions dskit, prints Codex's additionalContext JSON.
# Never blocks; always exits 0.
#   powershell -NoProfile -ExecutionPolicy Bypass -File remind.ps1 [EVENT]
param([string]$HookEvent = "UserPromptSubmit")
try {
    $payload = [Console]::In.ReadToEnd()
    if ($payload -notmatch 'dskit') { exit 0 }
    $sid = "nosession"
    if ($payload -match '"session_id"\s*:\s*"([^"]+)"') { $sid = $Matches[1] }
    $mark = Join-Path $env:TEMP ("dskit-sweep-reminded-" + $sid)
    if (Test-Path $mark) { exit 0 }
    New-Item -ItemType File -Path $mark -Force | Out-Null
    $msg = "dskit owner rule -- SWEEP THE REPO BEFORE BUILDING: we keep redoing work that already exists. " +
        "Before writing a new file, public class/function, node kind or config in dskit, run " +
        "tools/sweep/sweep <name> <synonyms> from a dskit worktree (WSL: wsl.exe -e bash -lc 'cd ~/wt/<your-worktree> && tools/sweep/sweep <term>'). " +
        "It searches origin/main, every branch and every worktree. Extend what exists instead of rebuilding it. " +
        "Commits that add new files/symbols/kinds need a trailer: Sweep: <what you searched; why this is not a duplicate> " +
        "-- the commit-msg hook refuses them otherwise."
    $out = @{ hookSpecificOutput = @{ hookEventName = $HookEvent; additionalContext = $msg } }
    $out | ConvertTo-Json -Compress
} catch { }
exit 0
