"""``huggingface`` — one Hugging Face hub repository as a WORM acquisition (ADR-0082).

A pretrained model is a set of files whose bytes decide what a run
computes, so it enters dskit the way a data vendor's rows do: acquired
once, snapshotted WORM with a Merkle manifest, re-hashed on ``verify``,
and named downstream by the manifest hash — content, never a hub name a
later push can silently re-point. Nothing in the pipeline ever opens a
socket for a model; this connector does, once, here.

One stream, ``snapshot``. A pull resolves the declared ``revision`` (a
branch, tag or commit) to the commit sha the hub reports, downloads
THAT sha into a private temporary directory, and emits — per file, in
sorted ``relpath`` order — one FILE message (the platform copies the
bytes into ``payload/snapshot/<relpath>``) and one RECORD carrying the
inventory ``{repo_id, repo_type, revision, commit_sha, relpath, size,
sha256}``, dated at the commit's ``last_modified`` (the instant the
weights came to be; a hub commit is always in the past). The pull ends
with STATE ``{commit_sha, revision, repo_type, allow_patterns,
ignore_patterns}`` — the whole SELECTION, because what was acquired is
the commit AS FILTERED, never the sha alone. The next pull compares the
freshly resolved sha, the repo type and both pattern lists to it and,
only when all four agree, emits one LOG and a STATE carrying the CURRENT
selection — "nothing new", an empty pull, never a duplicate snapshot; a
widened ``allow_patterns`` at an unchanged sha downloads again. Two pulls
of the same selection therefore lay out byte-identical payload trees. A
download that matches no file at all refuses, naming the sha and the
patterns, and emits no STATE — the cursor never moves onto nothing. Only
the hub client's own ``.cache/huggingface/`` metadata folder inside a
local download is skipped; anything else under ``.cache/`` is an
ordinary payload file the repository ships.

Cursors are keyed per MODE by the platform (ADR-0014): a ``backfill``
pull followed by a ``live`` pull of the same source re-downloads the same
commit into a second snapshot, because the live cursor starts empty.
Pick one mode for a repository and keep it.

Config knobs (default-deny, per ``spec()``):

- ``repo_id`` (required) — ``owner/name``.
- ``revision`` (required) — branch, tag or commit sha. A commit sha is
  the reproducible spelling; a branch floats, which is fine here because
  the snapshot pins content after the fact and RECORDs carry the sha.
- ``repo_type`` — ``model`` | ``dataset``; default model.
- ``allow_patterns`` / ``ignore_patterns`` — glob lists handed to the
  hub's ``snapshot_download``; default everything.
- ``token_env`` — environment-variable NAME holding a hub token; default
  ``HF_TOKEN``; unset (or empty) means anonymous — the client is handed
  ``token=False``, so its cached login is never used (``None`` would fall
  back to it). The token reaches the hub client and nothing else: no
  record, message, refusal or exception chain ever carries it.
- ``timeout_s`` — the resolve call's socket timeout; default 30.

Import cost: stdlib. ``huggingface_hub`` (the ``huggingface`` extra) is
imported inside the two seams, :meth:`HuggingFaceHubConnector.resolve`
and :meth:`HuggingFaceHubConnector.download`, and nowhere else.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from datetime import datetime

from ..base import AssetError, _raise_if, file_digest, parse_utc
from ..connector import PROTOCOL, Connector

__all__ = [
    "DEFAULT_REPO_TYPE",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOKEN_ENV",
    "HUB_CACHE_DIR",
    "RECORD_FIELDS",
    "REPO_TYPES",
    "SNAPSHOT_STREAM",
    "STATE_KEYS",
    "HuggingFaceHubConnector",
]

#: The one stream this connector offers.
SNAPSHOT_STREAM = "snapshot"

#: Repository kinds the hub serves that hold acquirable file trees.
REPO_TYPES = ("model", "dataset")

#: ONE name per default, read by ``spec()`` and by the code alike.
DEFAULT_REPO_TYPE = "model"
DEFAULT_TOKEN_ENV = "HF_TOKEN"
DEFAULT_TIMEOUT_S = 30.0

#: The folder ``snapshot_download(local_dir=...)`` keeps its own metadata
#: in (POSIX-relative to ``local_dir``); it is the client's bookkeeping,
#: never part of the repository. Exactly this path is skipped — a
#: repository's own ``.cache/<other>`` files are payload.
HUB_CACHE_DIR = ".cache/huggingface"

#: The STATE (cursor) keys — the selection a pull acquired, not the sha alone.
STATE_KEYS = ("commit_sha", "revision", "repo_type", "allow_patterns", "ignore_patterns")

#: The cursor keys that must ALL agree before a pull is "nothing new"
#: (``revision`` is a label: a branch re-pointed at the same sha is the
#: same content, and the same sha under another label is too).
_SELECTION_KEYS = ("commit_sha", "repo_type", "allow_patterns", "ignore_patterns")

#: The inventory RECORD's ``data`` fields, in the order ``discover`` lists them.
RECORD_FIELDS = (
    "repo_id", "repo_type", "revision", "commit_sha", "relpath", "size", "sha256",
)

#: The extra a refusal names when the hub client is absent.
_EXTRA = "pip install 'dskit[huggingface]'"

# \Z, not $ — $ forgives a trailing newline (ADR-0020).
_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*\Z")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}\Z")


def _scrub(text, token):
    """Blank ``token`` wherever it appears in ``text`` (a hub error echoes URLs)."""
    return text.replace(token, "***") if token else text


def _pattern_list(problems, name, value):
    """Append a problem unless ``value`` is None or a list of non-empty strings; return it."""
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(p, str) or not p for p in value):
        problems.append(f"config.{name} must be a list of non-empty glob strings, got {value!r}")
        return None
    return list(value)


def _walk_relpaths(local_dir):
    """Yield every payload file under ``local_dir`` as a sorted POSIX relpath, skipping the hub cache."""
    cache_parent, cache_name = os.path.split(os.path.join(local_dir, *HUB_CACHE_DIR.split("/")))
    out = []
    for parent, dirs, names in os.walk(local_dir):
        if parent == cache_parent and cache_name in dirs:
            dirs.remove(cache_name)
        for name in names:
            rel = os.path.relpath(os.path.join(parent, name), local_dir)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


class HuggingFaceHubConnector(Connector):
    """Acquire one hub repository at a pinned commit as FILE + RECORD messages.

    The four verbs of ADR-0013 over one stream, ``snapshot``. The two
    network seams are methods so a test (or a mirror) can script them:
    :meth:`resolve` turns the declared revision into a commit sha and its
    date, :meth:`download` stages that commit's files locally.

    Parameters
    ----------
    None
        Stateless, like every connector; ``read`` receives config, the
        stream list, the mode-keyed checkpoint and the mode.

    Examples
    --------
    Validate a config and list the stream it offers::

        connector = HuggingFaceHubConnector()
        connector.check({"repo_id": "acme/tiny-bert", "revision": "main"})
        connector.discover({"repo_id": "acme/tiny-bert", "revision": "main"})
        # -> [{"stream": "snapshot", "schema": {...}, "primary_key": [...]}]
    """

    def spec(self):
        """Declare the knobs, default-deny.

        Returns
        -------
        dict
            ``{"params": {...}}`` — one entry per knob named in the module
            docstring; ``token_env`` flagged secret.
        """
        return {"params": {
            "repo_id": {
                "required": True,
                "notes": "Hub repository as owner/name, e.g. 'google-bert/bert-base-uncased'.",
            },
            "revision": {
                "required": True,
                "notes": "Branch, tag or commit sha to acquire. Resolved to a commit "
                         "sha at pull time and downloaded AT that sha; the sha rides "
                         "on every record and in the checkpoint. A commit sha is the "
                         "reproducible spelling.",
            },
            "repo_type": {
                "notes": f"One of {list(REPO_TYPES)}; default {DEFAULT_REPO_TYPE!r}.",
            },
            "allow_patterns": {
                "notes": "Glob patterns of files to fetch (the hub's own semantics); "
                         "default everything. Narrow it — a repo often carries several "
                         "weight formats of the same model.",
            },
            "ignore_patterns": {
                "notes": "Glob patterns of files to skip; default none.",
            },
            "token_env": {
                "secret": True,
                "notes": "Environment-variable NAME holding a hub token, read at pull "
                         f"time; default {DEFAULT_TOKEN_ENV}. Unset means anonymous "
                         "(public repositories) — the client's cached login is never "
                         "used. The token never enters a record, a message, a refusal "
                         "or an exception chain.",
            },
            "timeout_s": {
                "notes": f"Socket timeout of the resolve call in seconds; default "
                         f"{DEFAULT_TIMEOUT_S:g}.",
            },
        }}

    def resolve_knobs(self, config):
        """Validate config values and apply the pack's defaults.

        Parameters
        ----------
        config : dict
            Connector configuration after the platform-reserved keys are
            removed (already default-denied by ``check_config``).

        Returns
        -------
        dict
            Fully resolved knobs: ``repo_id``, ``revision``, ``repo_type``,
            ``allow_patterns``, ``ignore_patterns``, ``token_env``,
            ``timeout_s``.

        Raises
        ------
        AssetError
            Listing every unusable value at once.
        """
        problems = []
        repo_id = config.get("repo_id")
        if not isinstance(repo_id, str) or not _REPO_ID.match(repo_id):
            problems.append(
                f"config.repo_id must be a hub repository as owner/name, got {repo_id!r}"
            )
        revision = config.get("revision")
        if not isinstance(revision, str) or not revision:
            problems.append(
                f"config.revision must be a non-empty branch, tag or commit sha, got {revision!r}"
            )
        repo_type = config.get("repo_type", DEFAULT_REPO_TYPE)
        if repo_type not in REPO_TYPES:
            problems.append(
                f"config.repo_type must be one of {list(REPO_TYPES)}, got {repo_type!r}"
            )
        allow = _pattern_list(problems, "allow_patterns", config.get("allow_patterns"))
        ignore = _pattern_list(problems, "ignore_patterns", config.get("ignore_patterns"))
        token_env = config.get("token_env", DEFAULT_TOKEN_ENV)
        if not isinstance(token_env, str) or not token_env:
            problems.append(
                f"config.token_env must be an environment-variable NAME, got {token_env!r}"
            )
        timeout_s = config.get("timeout_s", DEFAULT_TIMEOUT_S)
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            problems.append(f"config.timeout_s must be a finite number > 0, got {timeout_s!r}")
        _raise_if(problems)
        return {
            "repo_id": repo_id,
            "revision": revision,
            "repo_type": repo_type,
            "allow_patterns": allow,
            "ignore_patterns": ignore,
            "token_env": token_env,
            "timeout_s": timeout_s,
        }

    def check(self, config):
        """Validate the knobs; move no data and open no socket.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Raises
        ------
        AssetError
            Listing every unusable knob value.
        """
        self.resolve_knobs(config)

    def discover(self, config):
        """List the one stream and its inventory schema.

        Parameters
        ----------
        config : dict
            Connector configuration (validated, unused beyond that).

        Returns
        -------
        list of dict
            One entry: stream ``snapshot``, the RECORD fields, and the
            key ``[repo_id, commit_sha, relpath]``.
        """
        self.resolve_knobs(config)
        return [{
            "stream": SNAPSHOT_STREAM,
            "schema": {"fields": list(RECORD_FIELDS)},
            "primary_key": ["repo_id", "commit_sha", "relpath"],
        }]

    # -- the two network seams --------------------------------------------

    def resolve(self, repo_id, revision, repo_type, token, timeout_s):
        """Resolve a revision to the hub's commit sha and its date.

        The default imports ``huggingface_hub`` here; override to inject a
        mirror or a test double.

        Parameters
        ----------
        repo_id : str
            ``owner/name``.
        revision : str
            Branch, tag or commit sha.
        repo_type : str
            One of :data:`REPO_TYPES`.
        token : str or False
            Hub token, or ``False`` for anonymous access (never ``None``:
            the client reads that as "use my cached login").
        timeout_s : float
            Socket timeout in seconds.

        Returns
        -------
        dict
            ``{"sha": <commit sha>, "last_modified": <aware datetime or ISO str>}``.

        Raises
        ------
        AssetError
            When the client is not installed, the repository or revision
            is not found or gated, the hub refuses, or it is unreachable.
            The token is never in the text, and the client's own error is
            never chained (``from None``): the scrubbed text is the record.
        """
        try:
            from huggingface_hub import HfApi
            from huggingface_hub.utils import (
                GatedRepoError,
                HfHubHTTPError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            )
        except ImportError:
            raise AssetError(
                [f"the huggingface connector needs the optional huggingface_hub "
                 f"package ({_EXTRA}); it is imported only inside resolve/download"]
            ) from None
        label = f"{repo_type} {repo_id!r} at revision {revision!r}"
        # ``from None`` throughout: the client's exception echoes request
        # URLs, and a token in a __cause__ is a token in every traceback.
        try:
            info = HfApi().repo_info(
                repo_id, revision=revision, repo_type=repo_type, timeout=timeout_s,
                token=token,
            )
        except GatedRepoError as exc:
            raise AssetError(
                [f"{label}: the repository is gated — accept its terms on the hub and "
                 f"provide a token ({_scrub(str(exc), token)})"]
            ) from None
        except RepositoryNotFoundError as exc:
            raise AssetError(
                [f"{label}: repository not found (or private without a token) — "
                 f"{_scrub(str(exc), token)}"]
            ) from None
        except RevisionNotFoundError as exc:
            raise AssetError(
                [f"{label}: revision not found — {_scrub(str(exc), token)}"]
            ) from None
        except HfHubHTTPError as exc:
            raise AssetError(
                [f"{label}: the hub refused the request — {_scrub(str(exc), token)}"]
            ) from None
        except OSError as exc:
            raise AssetError(
                [f"{label}: the hub is unreachable — {_scrub(str(exc), token)}"]
            ) from None
        return {
            "sha": getattr(info, "sha", None),
            "last_modified": getattr(info, "last_modified", None),
        }

    def download(self, repo_id, revision, repo_type, allow_patterns, ignore_patterns,
                 token, local_dir):
        """Stage one commit's files under ``local_dir``.

        The default imports ``huggingface_hub`` here; override to inject a
        mirror or a test double. ``revision`` is the RESOLVED commit sha —
        the ref is never downloaded by name.

        Parameters
        ----------
        repo_id : str
            ``owner/name``.
        revision : str
            The commit sha :meth:`resolve` answered.
        repo_type : str
            One of :data:`REPO_TYPES`.
        allow_patterns, ignore_patterns : list of str or None
            The hub's glob filters; ``None`` means unfiltered.
        token : str or False
            Hub token, or ``False`` for anonymous access (never ``None``:
            the client reads that as "use my cached login").
        local_dir : str
            A private, empty directory the files land in (the hub client
            keeps its metadata under ``<local_dir>/.cache/huggingface/``).

        Returns
        -------
        None
            The files are the effect.

        Raises
        ------
        AssetError
            When the client is not installed, the hub refuses, or it is
            unreachable — the token never in the text, the client's error
            never chained.
        """
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.utils import (
                GatedRepoError,
                HfHubHTTPError,
                RepositoryNotFoundError,
                RevisionNotFoundError,
            )
        except ImportError:
            raise AssetError(
                [f"the huggingface connector needs the optional huggingface_hub "
                 f"package ({_EXTRA}); it is imported only inside resolve/download"]
            ) from None
        label = f"{repo_type} {repo_id!r} at commit {revision[:12]}"
        try:
            snapshot_download(
                repo_id=repo_id, revision=revision, repo_type=repo_type,
                local_dir=local_dir, token=token,
                allow_patterns=allow_patterns, ignore_patterns=ignore_patterns,
            )
        except GatedRepoError as exc:
            raise AssetError(
                [f"{label}: the repository is gated — accept its terms on the hub and "
                 f"provide a token ({_scrub(str(exc), token)})"]
            ) from None
        except RepositoryNotFoundError as exc:
            raise AssetError(
                [f"{label}: repository not found (or private without a token) — "
                 f"{_scrub(str(exc), token)}"]
            ) from None
        except RevisionNotFoundError as exc:
            raise AssetError(
                [f"{label}: revision not found — {_scrub(str(exc), token)}"]
            ) from None
        except HfHubHTTPError as exc:
            raise AssetError(
                [f"{label}: the hub refused the download — {_scrub(str(exc), token)}"]
            ) from None
        except OSError as exc:
            raise AssetError(
                [f"{label}: the hub is unreachable — {_scrub(str(exc), token)}"]
            ) from None

    # -- read ---------------------------------------------------------------

    def read(self, config, streams, state, mode):
        """Yield FILE + RECORD per file of the resolved commit, then STATE.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list of str
            Must be ``["snapshot"]``; any other name refuses.
        state : dict
            The mode-keyed checkpoint — :data:`STATE_KEYS` from the last
            committed pull, or ``{}`` (a first pull, or a cursor an older
            platform saved without the selection: both download in full).
        mode : str
            ``backfill`` or ``live``; the pull is identical in both — a
            repository has no market session — but the platform keys the
            cursor per mode, so switching modes re-downloads the commit.

        Yields
        ------
        dict
            Envelope messages: per file a FILE then a RECORD, then STATE;
            or LOG + STATE when the commit AND the selection are unchanged.

        Raises
        ------
        AssetError
            On an unknown stream, a hub refusal, an undated commit, a
            malformed sha, or a download that matched no file (no STATE
            is emitted then — the cursor stays where it was).
        """
        knobs = self.resolve_knobs(config)
        for stream in streams:
            if stream != SNAPSHOT_STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r} — this connector offers "
                     f"{SNAPSHOT_STREAM!r} only"]
                )
        # ``False``, never ``None``: to huggingface_hub ``None`` means "fall
        # back to the cached login", and an unset knob means anonymous.
        token = os.environ.get(knobs["token_env"]) or False
        resolved = self.resolve(
            knobs["repo_id"], knobs["revision"], knobs["repo_type"], token,
            knobs["timeout_s"],
        )
        sha = resolved.get("sha")
        if not isinstance(sha, str) or not _COMMIT_SHA.match(sha):
            raise AssetError(
                [f"{knobs['repo_id']!r}@{knobs['revision']!r}: the hub answered "
                 f"{sha!r} where a 40-hex commit sha was expected — refusing to "
                 "acquire an unpinned tree"]
            )
        dated = _effective_date(knobs, resolved.get("last_modified"))
        checkpoint = {**{k: knobs[k] for k in STATE_KEYS if k != "commit_sha"},
                      "commit_sha": sha}
        if all(state.get(k) == checkpoint[k] for k in _SELECTION_KEYS):
            yield {
                "protocol": PROTOCOL, "type": "LOG",
                "message": f"{knobs['repo_id']}@{knobs['revision']} is still commit "
                           f"{sha[:12]} under the same selection — nothing new",
            }
            yield {"protocol": PROTOCOL, "type": "STATE", "state": checkpoint}
            return
        staging = tempfile.mkdtemp(prefix="dskit-hf-")
        try:
            self.download(
                knobs["repo_id"], sha, knobs["repo_type"], knobs["allow_patterns"],
                knobs["ignore_patterns"], token, staging,
            )
            relpaths = _walk_relpaths(staging)
            if not relpaths:
                raise AssetError(
                    [f"{knobs['repo_type']} {knobs['repo_id']!r}: allow_patterns="
                     f"{knobs['allow_patterns']!r} / ignore_patterns="
                     f"{knobs['ignore_patterns']!r} matched no file at commit "
                     f"{sha[:12]} — refusing to cursor past an empty selection"]
                )
            for relpath in relpaths:
                path = os.path.join(staging, *relpath.split("/"))
                yield {
                    "protocol": PROTOCOL, "type": "FILE", "stream": SNAPSHOT_STREAM,
                    "relpath": relpath, "path": path,
                }
                yield {
                    "protocol": PROTOCOL, "type": "RECORD", "stream": SNAPSHOT_STREAM,
                    "effective_date": dated,
                    "data": {
                        "repo_id": knobs["repo_id"],
                        "repo_type": knobs["repo_type"],
                        "revision": knobs["revision"],
                        "commit_sha": sha,
                        "relpath": relpath,
                        "size": os.path.getsize(path),
                        "sha256": file_digest(path),
                    },
                }
            yield {"protocol": PROTOCOL, "type": "STATE", "state": checkpoint}
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _effective_date(knobs, last_modified):
    """Spell the commit's date as the envelope's ISO stamp, or refuse by name."""
    label = f"{knobs['repo_id']!r}@{knobs['revision']!r}"
    if isinstance(last_modified, datetime):
        if last_modified.tzinfo is None:
            raise AssetError(
                [f"{label}: the hub dated the commit without a timezone "
                 f"({last_modified!r}) — refusing to guess one"]
            )
        return last_modified.isoformat()
    if isinstance(last_modified, str) and last_modified:
        try:
            return parse_utc(last_modified).isoformat()
        except AssetError as exc:
            raise AssetError(
                [f"{label}: the hub's commit date {last_modified!r} is not ISO-8601"]
            ) from exc
    raise AssetError(
        [f"{label}: the hub did not date the commit (last_modified={last_modified!r}) "
         "— an observation must carry its instant"]
    )
