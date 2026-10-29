#!/bin/sh
# Which machine is this agent session on? The owner works from a Mac, a
# Windows PC and WSL on that PC, and an instruction written on one of them
# (wsl.exe, ~/wt/, /home/... data paths, "work in WSL2") is wrong on
# another. Harness hooks run this at session start so the agent is TOLD
# the host instead of inferring it from whichever doc it read first.
#
#   machine.sh --os      one word: macos | wsl | linux | windows | unknown
#   machine.sh --text    one line for an agent or a human
#   machine.sh [EVENT]   Claude Code hook JSON (EVENT defaults to
#                        SessionStart): {"hookSpecificOutput":
#                        {"hookEventName": EVENT, "additionalContext": ...}}
#
# POSIX sh + sed + tr only, like tools/sweep/remind.sh: it must run on
# macOS, in WSL and in Windows Git Bash. Detection reads `uname` (plus the
# WSL launcher's WSL_DISTRO_NAME) and nothing else, so a fake `uname` first
# on PATH exercises every host family (tests/tools/test_session_machine.py).
# --os is also what remind.sh asks, so the host rule has this one owner.

machine_os() {
    case "$(uname -s 2>/dev/null)" in
        Darwin) echo macos ;;
        Linux)
            # WSL1 kernels report "...-Microsoft", WSL2 "...-microsoft-
            # standard-WSL2"; WSL_DISTRO_NAME covers a custom WSL kernel.
            case "$(uname -r 2>/dev/null)" in
                *[Mm]icrosoft*) echo wsl ;;
                *) if [ -n "${WSL_DISTRO_NAME:-}" ]; then echo wsl
                   else echo linux; fi ;;
            esac ;;
        MINGW*|MSYS*|CYGWIN*) echo windows ;;
        *) echo unknown ;;
    esac
}

# The label says what the host IS; the note says which repo instructions
# do and do not apply on it. Neither names a user or a personal path, so
# the same line is true for anyone who clones the repo.
machine_text() {
    case "$(machine_os)" in
        macos)
            label="macOS"
            note="No WSL or Windows here: where a step says to use wsl.exe or to work in WSL, drop only that part and do the rest of the step here (fetch, worktree from origin/main and so on still apply); run repo tools directly. Absolute paths written on another machine (/home/..., /mnt/...) will not exist here; check before running a config that names one." ;;
        wsl)
            label="WSL (Linux on a Windows PC)"
            note="Run repo tools directly; Windows drives are under /mnt/<letter>." ;;
        linux)
            label="Linux (not WSL)"
            note="Run repo tools directly; skip any wsl.exe step." ;;
        windows)
            label="Windows (Git Bash/MSYS)"
            note="Run repo tools inside WSL: wsl.exe -e bash -lc 'cd <the worktree's WSL-side path> && <command>'; the Windows-form repo path above is not valid inside WSL." ;;
        *)
            label="an unrecognised OS"
            note="Verify paths and tooling before running anything." ;;
    esac
    kernel="$(uname -s 2>/dev/null) $(uname -m 2>/dev/null)"
    host=$(uname -n 2>/dev/null)
    user=${USER:-$(id -un 2>/dev/null)}
    root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
    # One line, always: a newline or other control byte smuggled in through
    # a hostname or path would split the context or break the hook JSON.
    # LC_ALL=C because BSD tr in a UTF-8 locale aborts mid-stream on an
    # invalid byte and silently drops everything after it, the note included.
    printf 'Machine: %s (%s) on host %s, user %s, repo %s. %s' \
        "$label" "$kernel" "$host" "$user" "$root" "$note" |
        LC_ALL=C tr '\001-\037\177' '[ *]'
    printf '\n'
}

json_escape() {
    printf '%s' "$1" | LC_ALL=C sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

case "${1:-SessionStart}" in
    --os) machine_os ;;
    --text) machine_text ;;
    -*)
        echo "usage: machine.sh [--os | --text | EVENT]" >&2
        exit 1 ;;
    *)
        ctx=$(machine_text)
        printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}}\n' \
            "$(json_escape "${1:-SessionStart}")" "$(json_escape "$ctx")" ;;
esac
exit 0
