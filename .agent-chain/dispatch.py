"""Cross-platform headless dispatch for /chain stages.

Given a target CLI tool, a logical model tier, and a prompt, builds the
correct subprocess invocation from tools.json's adapter table, runs it
headlessly, and returns the captured output. Deciding WHICH tool, model,
and prompt a stage needs is the driving agent's judgment call, made before
any of this runs; this module only knows how to execute one already-decided
stage.
"""

import json
import subprocess
from pathlib import Path

TOOLS_CONFIG_PATH = Path(__file__).parent / "tools.json"


def load_tools_config(path=TOOLS_CONFIG_PATH):
    """
    Load and parse the per-platform adapter table.

    Parameters
    ----------
    path : Path
        Location of tools.json.

    Returns
    -------
    dict
        Parsed adapter table, keyed by platform name.

    Raises
    ------
    FileNotFoundError
        If `path` does not exist.
    ValueError
        If the file is not valid JSON.
    """
    if not path.exists():
        raise FileNotFoundError(f"tools.json not found at {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"tools.json is not valid JSON: {exc}") from exc


def build_command(tool, model_tier, prompt, config, cwd=None):
    """
    Build the subprocess argv for one dispatched chain stage.

    Parameters
    ----------
    tool : str
        A platform key present in `config` (e.g. "claude", "codex",
        "cursor", "opencode").
    model_tier : str or None
        Logical model tier ("opus", "sonnet", "haiku"), or None to leave
        the tool on its own default model.
    prompt : str
        The stage instruction, already including any prior-stage output.
    config : dict
        The loaded adapter table, see `load_tools_config`.
    cwd : str or None
        Working directory override; only applied when the adapter entry
        declares a non-null `cwdFlag`.

    Returns
    -------
    list of str
        The argv to pass to `subprocess.run`.

    Raises
    ------
    KeyError
        If `tool` is not a key in `config`.
    """
    if tool not in config:
        raise KeyError(f"Unknown tool {tool!r}; tools.json defines {sorted(config)}")
    adapter = config[tool]
    argv = [adapter["binary"]]
    if "subcommand" in adapter:
        argv.append(adapter["subcommand"])
    argv.extend(adapter.get("autoApproveFlags", []))
    if model_tier is not None:
        concrete_model = adapter.get("models", {}).get(model_tier)
        if concrete_model is not None:
            argv.extend([adapter["modelFlag"], concrete_model])
        # else: no confirmed mapping for this tier on this tool -- fall
        # through to the tool's own default rather than guess a model id.
    if cwd is not None and adapter.get("cwdFlag"):
        argv.extend([adapter["cwdFlag"], cwd])
    if adapter.get("promptArg") == "positional":
        argv.append(prompt)
    else:
        argv.extend([adapter["promptArg"], prompt])
    return argv


def dispatch(tool, model_tier, prompt, config=None, cwd=None, timeout=1800):
    """
    Run one chain stage headlessly and return its captured output.

    Parameters
    ----------
    tool : str
        Target platform key, see `build_command`.
    model_tier : str or None
        Logical model tier, see `build_command`.
    prompt : str
        The stage instruction.
    config : dict or None
        Pre-loaded adapter table; loaded from `tools.json` when None.
    cwd : str or None
        Working directory for the subprocess.
    timeout : int
        Seconds to wait before killing the stage. Default 1800 (30 min).

    Returns
    -------
    str
        The stage's captured stdout, stripped of trailing whitespace.

    Raises
    ------
    RuntimeError
        If the subprocess exits non-zero; message includes the tool,
        exit code, and captured stderr.
    subprocess.TimeoutExpired
        If the stage does not finish within `timeout` seconds.
    """
    if config is None:
        config = load_tools_config()
    argv = build_command(tool, model_tier, prompt, config, cwd=cwd)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"{tool} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--cwd", default=None)
    args = parser.parse_args()
    print(dispatch(args.tool, args.model, args.prompt, cwd=args.cwd))


if __name__ == "__main__":
    _main()
