"""How high one cell must score once hundreds of cells were tried.

ADR-0067 says how a single forecast is scored. It says nothing about the
fact that the same data has been searched over models, horizons, row
spacings, price definitions, feature blocks and names — hundreds of
cells, of which the best will look good whether or not anything is
there. This module is the bar that goes ON TOP (ADR-0069): P5 still
decides pass or fail, and P8 only raises the mark P5 must clear.

Three pieces.

**A registry.** :class:`AttemptRegistry` is an append-only ledger of
every cell EVER scored, not tonight's. The adjustment is a function of
how many attempts were made, so a count reconstructed from memory is
the one number a searcher will always get wrong in the flattering
direction.

**A bar.** :func:`max_bar` resamples every cell JOINTLY — one coin per
trading session, shared by every cell — and takes the 95th percentile
of the best-of-all-cells statistic. Sharing the draws is the whole
point: near-identical cells (h=2 beside h=3, close beside vwap) move
together under the resample, so the procedure LEARNS from the data that
they are nearly one attempt instead of being told they are two.
Underneath sits Harvey–Liu–Zhu's floor of :data:`T_FLOOR`, so a grid of
near-duplicates cannot hand back a soft critical value.

**A scramble.** The exchangeable unit is a WHOLE TRADING SESSION, never
a row: a session is self-contained for every horizon tested here, so
moving a session moves every overlapping label with it, and shuffling
rows would destroy both the within-day autocorrelation and the overlap
between labels. :func:`max_bar`'s sign flips are the cheap pass — Shao's
dependent wild bootstrap with session blocks, no refit, 10,000
replicates on stored numbers. The expensive pass, re-running the walk
with the label days reshuffled, is NOT run here; :func:`tier2_plan` and
:func:`tier2_verdict` are its two ends, and the seam between them is
documented at :data:`TIER2_SEAM`.

Import cost: stdlib at module level. :func:`max_bar` names numpy inside
the function — the resample is a matrix product and a Python loop over
``replicates × cells × sessions`` is hours where numpy is seconds.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics

from dskit.pipeline.records import number_ok

__all__ = [
    "ATTEMPTS_FILE",
    "T_FLOOR",
    "TIER2_SEAM",
    "AttemptRegistry",
    "FixedFamilyLedger",
    "bar_verdict",
    "beat_all",
    "bonferroni_t",
    "cell_id",
    "expected_max_null",
    "implied_trials",
    "max_bar",
    "merge_session_totals",
    "session_totals",
    "tier2_plan",
    "tier2_verdict",
    "utc_day",
]

#: Where a project's ledger lives by default, relative to wherever the
#: caller keeps its decision record. One line of JSON per attempt.
ATTEMPTS_FILE = "attempts.jsonl"

#: Harvey–Liu–Zhu's hurdle for a NEW claim on a much-searched dataset.
#: Present as a FLOOR under the resampled critical value, not as a
#: replacement for it: a family of near-identical cells resamples to a
#: soft c*, and this is what stops that softness becoming a pass.
T_FLOOR = 3.0

#: One-sided normal critical value at 5%, named once.
_Z_05 = 1.645

#: Milliseconds in a day — the default session key's only arithmetic.
_DAY_MS = 86_400_000

#: The seam between the cheap scramble and the expensive one, spelled
#: out because a half-built escape hatch is worse than none.
#:
#: :func:`max_bar` scrambles the STORED SCORES: it flips the sign of a
#: whole session's loss gaps and recomputes the statistic. That imposes
#: the null on the numbers a walk already produced, and it costs
#: arithmetic. It CANNOT test the fitting itself — if the label leaks
#: into a feature, the sign flip never sees it, because the leak is
#: already baked into every stored forecast.
#:
#: Tier 2 closes that: reshuffle which SESSION supplies the label, then
#: RE-RUN the walk from the features up, about 100 times. Preserved by
#: construction: within-session autocorrelation, the h-minute label
#: overlap, the time-of-day shape, day-level volatility clustering, and
#: the cross-stock correlation at each minute. Destroyed: only the link
#: between the features at t and the return over [t, t+h].
#:
#: What is built here is both ENDS and neither middle: :func:`tier2_plan`
#: emits the permutations (one per run, seeded, half-sessions dropped,
#: the same permutation for every symbol), and :func:`tier2_verdict`
#: reads the finished runs. The middle — a label node that takes a
#: permutation and a runner that loops it — is deliberately absent, and
#: it is ~100 walks of compute. Run it for a WINNER, never as a sweep.
TIER2_SEAM = "tier1 = sign-flip on stored scores; tier2 = refit with days reshuffled"


def utc_day(stamp_ms):
    """Name the UTC calendar day a millisecond timestamp falls in.

    The default session key. Every regular US equity session lies inside
    one UTC day, so this separates trading days without a timezone
    database; a venue whose session straddles midnight UTC must pass its
    own key instead.

    Parameters
    ----------
    stamp_ms : int
        Milliseconds since the epoch.

    Returns
    -------
    int
        The day index (days since the epoch).

    Raises
    ------
    ValueError
        When ``stamp_ms`` is not a finite number.

    Examples
    --------
    Two stamps six hours apart share a day::

        utc_day(1_700_000_000_000) == utc_day(1_700_021_600_000)  # True
    """
    if not number_ok(stamp_ms):
        raise ValueError(f"stamp_ms must be a finite number, got {stamp_ms!r}")
    return int(stamp_ms) // _DAY_MS


def cell_id(key):
    """Derive a stable id for one searched cell from its knob values.

    Parameters
    ----------
    key : mapping
        The knobs that make this cell what it is — model, horizon, row
        spacing, price field, feature set, outcome unit. Any JSON-able
        mapping; the id is order-independent.

    Returns
    -------
    str
        Sixteen hex characters of sha256 over the canonical JSON.

    Raises
    ------
    ValueError
        When ``key`` is empty or not JSON-serializable.

    Examples
    --------
    Key order never changes the id::

        cell_id({"model": "ridge", "h": 1}) == cell_id({"h": 1, "model": "ridge"})
        # -> True
    """
    if not isinstance(key, dict) or not key:
        raise ValueError(f"key must be a non-empty mapping, got {key!r}")
    try:
        canonical = json.dumps(key, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise ValueError(f"key must be JSON-serializable: {exc}") from exc
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class AttemptRegistry:
    """Every cell ever scored against one dataset, appended not rewritten.

    The count the multiplicity bar consumes. It is a FILE and not a
    memory of tonight's session on purpose: an attempt made last week
    cost the same alpha as one made an hour ago, and a searcher asked to
    recall how many things they tried will under-count every time.

    Re-recording the same knobs is not a new attempt — the id is derived
    from the knobs, so a re-run updates that cell's latest score and
    leaves the count alone.

    Parameters
    ----------
    path : str
        The ledger file. Created on the first :meth:`record`; a missing
        file reads as an empty registry, never as an error.

    Examples
    --------
    Record one cell and count the family::

        reg = AttemptRegistry("/tmp/attempts.jsonl")
        reg.record({"model": "ridge", "horizon": 1, "unit": "JPM"}, t=1.2)
        reg.count(unit="JPM")  # 1
    """

    def __init__(self, path):
        self.path = str(path)

    def record(self, key, **fields):
        """Append one attempt.

        Parameters
        ----------
        key : mapping
            The cell's knob values (see :func:`cell_id`).
        **fields
            Anything worth keeping beside it — the statistic, the walk
            directory, the date. Stored verbatim, JSON-able only.

        Returns
        -------
        str
            The cell id.

        Raises
        ------
        ValueError
            On a bad ``key`` or a non-JSON-able field.
        """
        ident = cell_id(key)
        row = {"cell": ident, "key": dict(key), **fields}
        try:
            line = json.dumps(row, sort_keys=True)
        except TypeError as exc:
            raise ValueError(f"fields must be JSON-serializable: {exc}") from exc
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return ident

    def cells(self, **match):
        """Return every distinct cell, latest record winning.

        Parameters
        ----------
        **match
            Key values every returned cell must carry (``unit="JPM"``).
            A cell missing the key never matches.

        Returns
        -------
        dict
            ``{cell_id: record}``, insertion-ordered by first sighting.
        """
        found = {}
        if not os.path.isfile(self.path):
            return found
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or "cell" not in row:
                    continue
                key = row.get("key") or {}
                if any(key.get(k) != v for k, v in match.items()):
                    continue
                found[row["cell"]] = row
        return found

    def count(self, **match):
        """Count the distinct cells the family holds.

        Parameters
        ----------
        **match
            Same filter as :meth:`cells`.

        Returns
        -------
        int
            The family size ``K`` the bar is built for.
        """
        return len(self.cells(**match))


class FixedFamilyLedger:
    """Append immutable p-values under a fixed Bonferroni family.

    The complete key family and alpha are written before any result. Each
    key keeps its allocation even when it is never tested, so decisions are
    valid under arbitrary dependence and final in any arrival order.

    Parameters
    ----------
    path : str
        JSONL shared with the attempt registry; typed rows do not count as
        searched cells.
    family : str
        Stable family identity.
    keys : sequence of str
        Complete, unique hypothesis keys in frozen order.
    alpha : float
        Family-wise error level in ``(0, 1)``.

    Examples
    --------
    Freeze two hypotheses, then confirm one::

        ledger = FixedFamilyLedger("/tmp/attempts.jsonl", "confirm", ["A", "B"])
        ledger.prepare()
        ledger.record("A", 0.01)["passes"]  # True
    """

    _HEADER = "fixed_family_header"
    _RESULT = "fixed_family_result"

    def __init__(self, path, family, keys, alpha=0.05):
        if not isinstance(path, (str, os.PathLike)) or not str(path):
            raise ValueError("path must be a non-empty path")
        if not isinstance(family, str) or not family:
            raise ValueError("family must be a non-empty string")
        if (
            not isinstance(keys, (list, tuple))
            or not keys
            or any(not isinstance(key, str) or not key for key in keys)
            or len(set(keys)) != len(keys)
        ):
            raise ValueError("keys must be a non-empty sequence of unique strings")
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, (int, float))
            or not math.isfinite(float(alpha))
            or not 0.0 < float(alpha) < 1.0
        ):
            raise ValueError("alpha must be a finite number in (0, 1)")
        self.path = str(path)
        self.family = family
        self.keys = tuple(keys)
        self.alpha = float(alpha)

    @property
    def allocation(self):
        """Return the fixed alpha assigned to every family key."""
        return self.alpha / len(self.keys)

    def _rows(self):
        """Read this family's typed rows, ignoring ordinary attempts."""
        rows = []
        if not os.path.isfile(self.path):
            return rows
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(row, dict)
                    and row.get("record_kind") in {self._HEADER, self._RESULT}
                    and row.get("family") == self.family
                ):
                    rows.append(row)
        return rows

    def _header(self):
        """Build the one canonical family declaration."""
        return {
            "record_kind": self._HEADER,
            "family": self.family,
            "alpha": self.alpha,
            "keys": list(self.keys),
            "allocation": self.allocation,
            "correction": "bonferroni",
            "dependence": "arbitrary",
        }

    def _append(self, row):
        """Append one canonical JSON row while the caller holds the lock."""
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            line = json.dumps(row, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"ledger row must be finite JSON: {exc}") from exc
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _ensure_header(self, rows):
        """Create the header once or refuse any changed declaration."""
        expected = self._header()
        headers = [row for row in rows if row.get("record_kind") == self._HEADER]
        if not headers:
            self._append(expected)
            return expected
        if len(headers) != 1 or headers[0] != expected:
            raise ValueError(f"fixed family {self.family!r} header changed")
        return headers[0]

    def prepare(self):
        """Persist the family before any p-value arrives.

        Returns
        -------
        dict
            The immutable family header.
        """
        from dskit.journal.base import locked

        directory = os.path.dirname(self.path) or "."
        with locked(directory):
            return self._ensure_header(self._rows())

    def record(self, key, p_value, **fields):
        """Append one final hypothesis result.

        Parameters
        ----------
        key : str
            One declared family key.
        p_value : float
            Raw p-value in ``[0, 1]``.
        **fields
            Immutable JSON evidence stored beside the decision.

        Returns
        -------
        dict
            Raw and adjusted p-values, allocation and final decision.

        Raises
        ------
        ValueError
            On an undeclared key, bad p-value, changed family, duplicate or
            changed result, or reserved evidence field.
        """
        reserved = {
            "record_kind", "family", "key", "p_value", "adjusted_p",
            "allocation", "passes",
        }
        if key not in self.keys:
            raise ValueError(f"key {key!r} is not in fixed family {self.family!r}")
        if (
            isinstance(p_value, bool)
            or not isinstance(p_value, (int, float))
            or not math.isfinite(float(p_value))
            or not 0.0 <= float(p_value) <= 1.0
        ):
            raise ValueError("p_value must be a finite number in [0, 1]")
        overlap = sorted(reserved & set(fields))
        if overlap:
            raise ValueError(f"evidence uses reserved field(s): {overlap}")
        p_value = float(p_value)
        row = {
            "record_kind": self._RESULT,
            "family": self.family,
            "key": key,
            "p_value": p_value,
            "adjusted_p": min(len(self.keys) * p_value, 1.0),
            "allocation": self.allocation,
            "passes": p_value <= self.allocation,
            **fields,
        }
        from dskit.journal.base import locked

        directory = os.path.dirname(self.path) or "."
        with locked(directory):
            rows = self._rows()
            self._ensure_header(rows)
            prior = [
                item
                for item in rows
                if item.get("record_kind") == self._RESULT and item.get("key") == key
            ]
            if prior:
                if len(prior) == 1 and prior[0] == row:
                    return prior[0]
                raise ValueError(f"fixed family result for {key!r} changed or duplicated")
            self._append(row)
        return row

    def results(self):
        """Return immutable results by key after locking the header."""
        self.prepare()
        rows = self._rows()
        found = {}
        for row in rows:
            if row.get("record_kind") != self._RESULT:
                continue
            key = row.get("key")
            if key in found:
                raise ValueError(f"fixed family result for {key!r} is duplicated")
            found[key] = row
        return found


def session_totals(stamps, d, session_of=None):
    """Reduce one cell's rows to per-session sums and counts.

    The reduction that lets the bar hold hundreds of cells at once: a
    walk's rows are tens of thousands per cell, its sessions a few
    hundred, and a session-block resample only ever needs the sums.
    Reduce each fold as it is read and the rows never accumulate.

    Parameters
    ----------
    stamps : list or tuple
        One timestamp per row.
    d : list or tuple of float
        The scale-free loss gaps (ADR-0067's ``d_t / q_f``), aligned
        with ``stamps``.
    session_of : callable or None
        Maps a stamp to its session key. ``None`` takes :func:`utc_day`.

    Returns
    -------
    dict
        ``{session: [sum, count]}``.

    Raises
    ------
    ValueError
        On lengths that disagree or a non-finite gap.

    Examples
    --------
    Two rows in one session::

        session_totals([0, 1000], [0.5, 1.5])  # {0: [2.0, 2]}
    """
    if len(stamps) != len(d):
        raise ValueError(f"{len(stamps)} stamps and {len(d)} gaps — the two must agree")
    key_of = utc_day if session_of is None else session_of
    out = {}
    for stamp, gap in zip(stamps, d):
        if not number_ok(gap):
            raise ValueError(f"gap at stamp {stamp!r} is not finite: {gap!r}")
        cell = out.setdefault(key_of(stamp), [0.0, 0])
        cell[0] += float(gap)
        cell[1] += 1
    return out


def merge_session_totals(into, more):
    """Fold one more chunk of session totals into an accumulator.

    Parameters
    ----------
    into : dict
        The accumulator, ``{session: [sum, count]}``; mutated.
    more : mapping
        Another :func:`session_totals` result.

    Returns
    -------
    dict
        ``into``, for chaining.

    Examples
    --------
    Two folds of the same cell::

        merge_session_totals({0: [1.0, 1]}, {0: [2.0, 1]})  # {0: [3.0, 2]}
    """
    for key, (total, count) in more.items():
        cell = into.setdefault(key, [0.0, 0])
        cell[0] += float(total)
        cell[1] += int(count)
    return into


def expected_max_null(n_trials):
    """Say where the best of ``n`` pure-luck attempts sits, on average.

    Bailey–López de Prado's deflated-Sharpe expectation, which is just
    the expected maximum of ``n`` standard normals. Under P5's null the
    Diebold–Mariano statistic IS standard normal, so it transfers with
    the Sharpe ratio replaced by the DM t. Reported as a REFERENCE, not
    a bar: it is the centre of the luck distribution, so half of pure
    noise beats it.

    Parameters
    ----------
    n_trials : int
        Effectively independent attempts, ``>= 2``.

    Returns
    -------
    float
        The expected maximum, in standard-normal units.

    Raises
    ------
    ValueError
        On ``n_trials`` below 2.

    Examples
    --------
    A grid worth about 180 independent tries::

        round(expected_max_null(180), 2)  # 2.73
    """
    if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 2:
        raise ValueError(f"n_trials must be an int >= 2, got {n_trials!r}")
    gamma = 0.5772156649015329
    normal = statistics.NormalDist()
    return (1.0 - gamma) * normal.inv_cdf(1.0 - 1.0 / n_trials) + gamma * (
        normal.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )


def bonferroni_t(n_trials, alpha=0.05):
    """Compute the one-sided Bonferroni critical value for ``n`` attempts.

    Reported beside the resampled bar as the harsh end of the range: it
    treats every neighbouring horizon as a separate try, which is why it
    is a reported floor and never the rule.

    Parameters
    ----------
    n_trials : int
        Attempts in the family, ``>= 1``.
    alpha : float
        Family-wise level, in ``(0, 1)``.

    Returns
    -------
    float
        The critical value in standard-normal units.

    Raises
    ------
    ValueError
        On a bad ``n_trials`` or ``alpha``.

    Examples
    --------
    A family of 180 cells::

        round(bonferroni_t(180), 2)  # 3.45
    """
    if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 1:
        raise ValueError(f"n_trials must be an int >= 1, got {n_trials!r}")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError(f"alpha must be a number in (0, 1), got {alpha!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1), got {alpha!r}")
    return statistics.NormalDist().inv_cdf(1.0 - alpha / n_trials)


def implied_trials(c_star, alpha=0.05):
    """Say how many independent attempts the resampled bar is WORTH.

    ``alpha / (1 - Phi(c*))`` — the self-consistent trial count, and the
    one a write-up should quote: it is read off the data's own
    dependence rather than counted off a config list that treats
    duplicates as distinct.

    Parameters
    ----------
    c_star : float
        The resampled critical value.
    alpha : float
        The level it was taken at, in ``(0, 1)``.

    Returns
    -------
    float
        The implied number of independent trials, at least 1.

    Raises
    ------
    ValueError
        On a non-finite ``c_star`` or a bad ``alpha``.

    Examples
    --------
    A bar at the 5% normal critical value is worth one try::

        round(implied_trials(1.6448536269514722))  # 1
    """
    if not number_ok(c_star):
        raise ValueError(f"c_star must be a finite number, got {c_star!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1), got {alpha!r}")
    tail = 1.0 - statistics.NormalDist().cdf(c_star)
    return max(alpha / tail, 1.0) if tail > 0.0 else float("inf")


def _require_numpy():
    """Import numpy, or raise with the extra that supplies it."""
    try:
        import numpy
    except Exception as exc:  # noqa: BLE001 — reported, not swallowed
        raise ImportError(
            f"the multiplicity bar needs numpy (pip install dskit[numpy]): {exc}"
        ) from exc
    return numpy


def _cell_columns(cells, sessions):
    """Build recentred session sums, observed t, mean and cluster SE."""
    columns = {}
    for name, totals in cells.items():
        gross = sum(t for t, _n in totals.values())
        rows = sum(n for _t, n in totals.values())
        if rows < 2:
            continue
        mean = gross / rows
        centred = [
            totals.get(s, (0.0, 0))[0] - totals.get(s, (0.0, 0))[1] * mean
            for s in sessions
        ]
        scale = math.sqrt(sum(v * v for v in centred))
        columns[name] = {
            "centred": centred,
            "mean": mean,
            "se": scale / rows if rows else 0.0,
            "t": gross / scale if scale > 0.0 else None,
            "n_rows": rows,
            "n_sessions": sum(1 for _t, n in totals.values() if n),
        }
    return columns


def _replicate_matrix(np, columns, order, n_sessions, n_boot, seed, chunk):
    """Draw bootstrap t for every cell under shared per-session flips."""
    design = np.array([columns[name]["centred"] for name in order], dtype=np.float64).T
    scale = np.array(
        [math.sqrt(sum(v * v for v in columns[name]["centred"])) for name in order]
    )
    out = np.empty((n_boot, len(order)), dtype=np.float32)
    rng = np.random.default_rng(seed)
    done = 0
    while done < n_boot:
        take = min(chunk, n_boot - done)
        coins = rng.integers(0, 2, size=(take, n_sessions)).astype(np.float64)
        coins *= 2.0
        coins -= 1.0
        out[done : done + take] = (coins @ design) / scale
        done += take
    return out


def _stepdown(np, replicates, order, observed, alpha, n_boot):
    """Step down Romano–Wolf adjusted p-values over shared replicates."""
    adjusted = {}
    active = list(range(len(order)))
    floor = 0.0
    while active:
        maxima = replicates[:, active].max(axis=1)
        ps = {
            i: max(
                float((1 + int((maxima >= observed[i]).sum())) / (1 + n_boot)),
                floor,
            )
            for i in active
        }
        for i, p in ps.items():
            adjusted[order[i]] = p
        rejected = [i for i in active if ps[i] <= alpha]
        if not rejected:
            break
        floor = max(ps[i] for i in rejected)
        active = [i for i in active if i not in set(rejected)]
    return adjusted


def max_bar(
    cells,
    n_boot=10000,
    seed=0,
    alpha=0.05,
    floor_t=T_FLOOR,
    chunk=500,
    k_declared=None,
):
    """Set the many-attempts bar: resample every cell jointly, take the best.

    Shao's dependent wild bootstrap with SESSION blocks. Each cell's
    session sums are recentred (which imposes the null exactly), one
    ``±1`` coin is drawn per session and SHARED by every cell, and each
    cell's studentised statistic is recomputed. Because the coins are
    shared, the correlation between near-identical cells is carried
    exactly and they cost the family barely more than one attempt; the
    denominator is a session-cluster standard error, which is invariant
    under the flip and at least as conservative as a Bartlett band
    truncated at the label's overlap depth (a session is longer than
    every horizon tested here).

    ``c_star`` is the 95th percentile of the best-of-all-cells statistic
    — the pass mark. :data:`T_FLOOR` sits under it.

    Nothing is reordered: within-session autocorrelation and the overlap
    between h-step labels are untouched, which plain row shuffling would
    destroy twice over.

    Parameters
    ----------
    cells : mapping
        ``{cell_id: {session: (sum, count)}}`` — one
        :func:`session_totals` accumulator per cell, all on the same
        scale-free gaps. At least one cell, each with at least two rows.
    n_boot : int
        Replicates, ``>= 100``. 10,000 is the default; it is arithmetic
        on stored numbers.
    seed : int
        Seeds the coin draws, so a bar is reproducible.
    alpha : float
        Family-wise level, in ``(0, 1)``.
    floor_t : float
        The floor under ``c_star``.
    chunk : int
        Replicates drawn per matrix product. The peak is
        ``chunk × sessions`` floats for the coins plus
        ``n_boot × cells`` float32 for the kept statistics — 16 MB at
        10,000 × 400.
    k_declared : int or None
        How many cells the REGISTRY knows about. When it exceeds the
        cells resampled here — an older attempt whose rows were never
        saved — ``c_star`` is a LOWER bound on the true critical value
        and the result says so in ``notes``. ``None`` means the
        resampled family is the whole family.

    Returns
    -------
    dict
        ``c_star``, ``pass_mark`` (``max(c_star, floor_t)``), ``k`` (the
        cells scored), ``k_declared``, ``k_implied``
        (:func:`implied_trials`), ``bonferroni``, ``expected_max`` (both
        reference numbers, on ``k_declared``), ``n_boot``,
        ``n_sessions``, ``alpha``, ``floor_t``, ``notes`` and ``rows``
        — one per cell with ``cell``, ``t``, ``mean``, ``se``,
        ``lower`` (the one-sided 5% band on the mean gap), ``adj_p``,
        ``n_rows`` and ``n_sessions``. Cells whose statistic is
        undefined (no variance across sessions) appear with ``t`` of
        ``None`` and take no part in the maximum.

    Raises
    ------
    ValueError
        On an empty family, a malformed accumulator, a bad ``n_boot``,
        ``alpha``, ``floor_t`` or ``chunk``, or no cell with a defined
        statistic.
    ImportError
        When numpy is not installed.

    Examples
    --------
    Two cells over the same sessions::

        bar = max_bar({"a": {0: (1.0, 5)}, "b": {0: (0.5, 5)}}, n_boot=200)
        bar["pass_mark"] >= 3.0  # True
    """
    np = _require_numpy()
    if not isinstance(cells, dict) or not cells:
        raise ValueError("cells must be a non-empty mapping of cell -> totals")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 100:
        raise ValueError(f"n_boot must be an int >= 100, got {n_boot!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie in (0, 1), got {alpha!r}")
    if not number_ok(floor_t):
        raise ValueError(f"floor_t must be a finite number, got {floor_t!r}")
    if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk < 1:
        raise ValueError(f"chunk must be an int >= 1, got {chunk!r}")
    sessions = sorted({s for totals in cells.values() for s in totals})
    if not sessions:
        raise ValueError("no cell carried a single session — nothing to resample")
    columns = _cell_columns(cells, sessions)
    order = [name for name in sorted(columns) if columns[name]["t"] is not None]
    if not order:
        raise ValueError(
            "every cell's gaps are constant across sessions — no studentised "
            "statistic exists, so no bar can be built"
        )
    observed = [columns[name]["t"] for name in order]
    replicates = _replicate_matrix(
        np, columns, order, len(sessions), n_boot, seed, chunk
    )
    c_star = float(np.quantile(replicates.max(axis=1), 1.0 - alpha))
    adjusted = _stepdown(np, replicates, order, observed, alpha, n_boot)
    rows = [
        {
            "cell": name,
            "t": columns[name]["t"],
            "mean": columns[name]["mean"],
            "se": columns[name]["se"],
            "lower": columns[name]["mean"] - _Z_05 * columns[name]["se"],
            "adj_p": adjusted.get(name),
            "n_rows": columns[name]["n_rows"],
            "n_sessions": columns[name]["n_sessions"],
        }
        for name in sorted(columns)
    ]
    rows.sort(key=lambda r: (r["t"] is None, -(r["t"] or 0.0)))
    k = len(columns)
    declared = k if k_declared is None else max(int(k_declared), k)
    notes = []
    if declared > k:
        notes.append(
            f"the registry knows {declared} cells but only {k} kept the rows "
            "the resample needs — c* is a LOWER bound on the true critical "
            "value, and the Bonferroni column beside it is the honest ceiling "
            "until the missing cells are re-run"
        )
    return {
        "c_star": c_star,
        "pass_mark": max(c_star, float(floor_t)),
        "k": k,
        "k_declared": declared,
        "k_implied": implied_trials(c_star, alpha=alpha),
        "bonferroni": bonferroni_t(declared, alpha=alpha),
        "expected_max": expected_max_null(declared) if declared >= 2 else None,
        "n_boot": n_boot,
        "n_sessions": len(sessions),
        "alpha": alpha,
        "floor_t": float(floor_t),
        "notes": notes,
        "rows": rows,
    }


def bar_verdict(cell, bar, skill=None, alpha=0.05):
    """Compose P5's decision with P8's raised mark for ONE cell.

    P5 decides; P8 raises. A cell passes only when its own skill test
    passed (unchanged, and a missing skill result is a refusal, not a
    pass), its statistic clears ``pass_mark``, its stepdown-adjusted
    p-value clears ``alpha``, and the WIN ITSELF is positive with its
    one-sided band above zero — a significant sign with a zero-touching
    size is not a result.

    Parameters
    ----------
    cell : str
        The cell id to judge.
    bar : mapping
        A :func:`max_bar` result covering that cell.
    skill : mapping or None
        The cell's :func:`~dskit.pipeline.stats.skill_vs_mean` result.
        ``None`` fails the first condition and says so.
    alpha : float
        Level for the adjusted p-value, in ``(0, 1)``.

    Returns
    -------
    dict
        ``cell``, ``passes``, ``reasons`` (every condition that failed),
        ``t``, ``pass_mark``, ``adj_p``, ``r2oos`` and ``r2oos_lower``.

    Raises
    ------
    ValueError
        When ``bar`` holds no row for ``cell``.

    Examples
    --------
    A cell that beats the bar but not P5::

        bar_verdict("a", bar, skill={"passes": False})["passes"]  # False
    """
    row = next((r for r in bar.get("rows", ()) if r["cell"] == cell), None)
    if row is None:
        raise ValueError(f"the bar holds no row for cell {cell!r}")
    reasons = []
    if not (isinstance(skill, dict) and skill.get("passes")):
        reasons.append(
            "P5's skill test did not pass (or was not supplied) — P8 raises "
            "the mark, it never replaces the decision"
        )
    if row["t"] is None or row["t"] < bar["pass_mark"]:
        reasons.append(
            f"statistic {row['t']} is below the pass mark {bar['pass_mark']} "
            f"(resampled c* {bar['c_star']}, floor {bar['floor_t']})"
        )
    if row["adj_p"] is None or row["adj_p"] > alpha:
        reasons.append(f"stepdown adjusted p {row['adj_p']} is above {alpha}")
    if row["mean"] <= 0.0 or row["lower"] <= 0.0:
        reasons.append(
            f"the win itself is not positive: mean gap {row['mean']}, "
            f"one-sided lower band {row['lower']}"
        )
    return {
        "cell": cell,
        "passes": not reasons,
        "reasons": reasons,
        "t": row["t"],
        "pass_mark": bar["pass_mark"],
        "adj_p": row["adj_p"],
        "r2oos": row["mean"],
        "r2oos_lower": row["lower"],
    }


def tier2_plan(sessions, n_runs=100, seed=0, drop=()):
    """Emit the day reshuffles the EXPENSIVE scramble would re-run.

    One end of the seam at :data:`TIER2_SEAM`. Each plan says, for one
    scrambled walk, which session donates the label to which — whole
    sessions only, the same map for every symbol, so the h-minute label
    overlap and the within-day shape move intact. Nothing is fitted
    here and no walk is run: this is the specification a future runner
    consumes, ~100 walks of compute, for a WINNER only.

    Parameters
    ----------
    sessions : list or tuple
        The session keys eligible to be permuted, in time order. Short
        sessions must be excluded (see ``drop``): permuting a half-day
        against a full one changes the row count, not just the labels.
    n_runs : int
        Scrambled walks to plan, ``>= 2``.
    seed : int
        Base seed; run ``b`` draws from ``seed + b`` so a plan is
        reproducible and each run is independent.
    drop : sequence
        Session keys to exclude (half-days, holidays).

    Returns
    -------
    list of dict
        One per run: ``run`` (its index), ``seed``, and ``donor`` —
        ``{session: donor_session}`` over the kept sessions.

    Raises
    ------
    ValueError
        On fewer than two usable sessions or a bad ``n_runs``.

    Examples
    --------
    Two runs over three sessions::

        len(tier2_plan([1, 2, 3], n_runs=2))  # 2
    """
    import random

    kept = [s for s in sessions if s not in set(drop)]
    if len(kept) < 2:
        raise ValueError(
            f"tier2_plan needs at least 2 usable sessions, got {len(kept)}"
        )
    if isinstance(n_runs, bool) or not isinstance(n_runs, int) or n_runs < 2:
        raise ValueError(f"n_runs must be an int >= 2, got {n_runs!r}")
    plans = []
    for b in range(n_runs):
        rng = random.Random(seed + b)
        donors = list(kept)
        rng.shuffle(donors)
        plans.append(
            {
                "run": b,
                "seed": seed + b,
                "donor": dict(zip(kept, donors)),
            }
        )
    return plans


def beat_all(observed_r2, scrambled_r2):
    """Whether the real walk beats EVERY scrambled one, strictly.

    The one owner of the rank predicate (ADR-0092). :func:`tier2_verdict`
    takes its beat-all limb from here, and a fail-fast Gate 3 asks the
    same question after every single draw: one null at or above the real
    result already decides the family, and the remaining draws cannot
    change it. ``>=`` on that null is the exact negation of the strict
    ``>`` here, and ``max`` is monotone, so stopping at the first
    exceedance cannot diverge from the full family, ties included.

    Parameters
    ----------
    observed_r2 : float
        The real walk's pooled out-of-sample R².
    scrambled_r2 : list or tuple of float
        One per scrambled walk; a single value is allowed, so the
        per-draw stop can ask after each one.

    Returns
    -------
    bool
        ``True`` only when ``observed_r2`` exceeds every scrambled value.

    Raises
    ------
    ValueError
        When ``observed_r2`` is not finite, when ``scrambled_r2`` is
        empty, or when any scrambled value is not finite.

    Examples
    --------
    A tie is a loss, and one null is enough to ask::

        beat_all(0.01, [0.001, -0.002])  # True
        beat_all(0.01, [0.01])  # False
    """
    if not number_ok(observed_r2):
        raise ValueError(f"observed_r2 must be finite, got {observed_r2!r}")
    nulls = list(scrambled_r2)
    if not nulls or not all(number_ok(v) for v in nulls):
        raise ValueError("scrambled_r2 needs at least 1 finite value")
    return observed_r2 > max(float(v) for v in nulls)


def tier2_verdict(observed_r2, scrambled_r2, scrambled_t=()):
    """Read a finished expensive scramble — both of its checks.

    The other end of the seam. Two questions, and the second is worth
    the compute on its own: does the real walk beat EVERY scrambled one,
    and do the scrambled statistics sit where a correct variance
    estimator says they should? If the scrambled ``t`` values are not
    near mean 0 and sd 1, the variance estimator is wrong and every
    p-value in the project is wrong with it.

    Parameters
    ----------
    observed_r2 : float
        The real walk's pooled out-of-sample R².
    scrambled_r2 : list or tuple of float
        One per scrambled walk, at least two.
    scrambled_t : list or tuple of float
        The scrambled walks' DM statistics; empty skips the calibration
        check and says so.

    Returns
    -------
    dict
        ``passes``, ``reasons``, ``n_runs``, ``beat_all``, ``best_null``,
        ``null_mean``, ``null_sd`` and ``calibrated``.

    Raises
    ------
    ValueError
        On fewer than two scrambled runs or a non-finite value.

    Examples
    --------
    A real walk that clears every scramble::

        tier2_verdict(0.01, [0.001, -0.002, 0.0])["beat_all"]  # True
    """
    if not number_ok(observed_r2):
        raise ValueError(f"observed_r2 must be finite, got {observed_r2!r}")
    nulls = [float(v) for v in scrambled_r2]
    if len(nulls) < 2 or not all(number_ok(v) for v in nulls):
        raise ValueError("scrambled_r2 needs at least 2 finite values")
    beat = beat_all(observed_r2, nulls)
    ts = [float(v) for v in scrambled_t if number_ok(v)]
    mean = statistics.fmean(ts) if len(ts) >= 2 else None
    sd = statistics.stdev(ts) if len(ts) >= 2 else None
    calibrated = None if mean is None else bool(mean < 0.3 and 0.7 < sd < 1.4)
    reasons = []
    if not beat:
        reasons.append(
            f"{sum(1 for v in nulls if v >= observed_r2)}/{len(nulls)} scrambled "
            "walks matched or beat the real one"
        )
    if calibrated is None:
        reasons.append(
            "no scrambled statistics were supplied, so the variance estimator "
            "was not checked — the expensive run's second answer is missing"
        )
    elif not calibrated:
        reasons.append(
            f"scrambled statistics sit at mean {mean:.3f}, sd {sd:.3f}; "
            "calibration needs sd in (0.7, 1.4) and mean below +0.3 "
            "(negative fitting cost is conservative) — every p-value in "
            "the project is suspect until that is fixed"
        )
    return {
        "passes": not reasons,
        "reasons": reasons,
        "n_runs": len(nulls),
        "beat_all": beat,
        "best_null": max(nulls),
        "null_mean": mean,
        "null_sd": sd,
        "calibrated": calibrated,
    }
