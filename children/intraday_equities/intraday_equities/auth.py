"""Manual Schwab authorization over ADR-0046.

Prints the provider URL or exchanges the browser callback. Token
material never enters config; the source file only names environment
variables.
"""

from __future__ import annotations

import argparse
import json
import sys

from dskit.onboarding import AssetError, check_config

from .connectors import SchwabBars

__all__ = ["authorize", "main"]


def _load_config(path):
    """Load one source-config object."""
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssetError([f"cannot read source config {path!r}: {exc}"]) from exc
    if not isinstance(config, dict):
        raise AssetError([f"source config {path!r} must be a JSON object"])
    return config


def authorize(source_config, code=None):
    """Print the authorization URL or persist an exchanged token.

    Parameters
    ----------
    source_config : str
        Path to ``source-schwab-live.json``.
    code : str or None
        Raw authorization code or full callback URL.

    Returns
    -------
    str
        ``authorized`` after exchange, otherwise the URL to open.

    Raises
    ------
    AssetError
        If the config or OAuth exchange is invalid.
    """
    config = _load_config(source_config)
    connector = SchwabBars()
    check_config(connector, config)
    service = connector.oauth_service(
        {key: value for key, value in config.items() if key != "storage"}
    )
    if code:
        service.exchange(code)
        return "authorized"
    return service.authorization_url()


def main(argv=None):
    """CLI entry for ``python -m intraday_equities.auth``.

    Parameters
    ----------
    argv : list of str or None
        Arguments after the module name.

    Returns
    -------
    int
        Process exit code.
    """
    parser = argparse.ArgumentParser(prog="intraday_equities.auth")
    sub = parser.add_subparsers(dest="command", required=True)
    authorize_p = sub.add_parser("authorize")
    authorize_p.add_argument("--source-config", required=True)
    authorize_p.add_argument("--code")
    args = parser.parse_args(argv)
    try:
        print(authorize(args.source_config, args.code))
    except AssetError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
