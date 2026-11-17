"""Joint scenario generators for the weekly Kelly program: sampled joint settlements over the forecast marginals.

The weekly fractional-Kelly method (ADR-0299, climbed by :mod:`index_options.kelly_local`) scores a book on joint
wealth scenarios. By default those are the 17 basket moves of ``WeeklyOptionPortfolioStudy._centred_scenarios``.
A kelly block that declares ``joint_scenarios`` replaces them with ``S`` SAMPLED joint settlements built here. Every
group's marginal stays its own forecast grid, and the dependence between names comes from a generator.

What this module owns:

* :class:`JointScenarioGenerator`, the abstract family. :meth:`~JointScenarioGenerator.draw` is the template every
  member shares, and it may not be overridden. It validates the request, asks the member for one uniform sample per
  ``(name, horizon)``, maps each group's uniforms to atoms of its own grid by the step inverse of its masses
  (:func:`index_options.kelly_sizer.step_atoms`, the sizer's owner), and returns the compact form the toolkit's
  utility block takes (``TransportPortfolioSolve.UTILITY_ATOM_KEY``). A member supplies one hook, :meth:`uniforms`.
* :class:`EmpiricalCopula`, the first member. It block-bootstraps whole historical days, so every name reads the
  same dates. Each name's horizon-matched return is summed along the bootstrapped path, and its midrank among the
  ``S`` draws is the uniform: an empirical copula whose marginals the forecast replaces.

Determinism: the generator is ``numpy.random.default_rng(scenario_seed(seed, day))``, ranks are midranks of a stable
sort, and nothing reads the clock. The same request and spec give the same atoms on every host.

Not here: payoffs. A scenario is an atom index per group. The toolkit derives each group's profit there from the
group's own loss rows, centred on the sampled mean, so the expectation stays the forecast's (see
``TransportPortfolioSolve._atom_tables``).
"""

import math
from abc import ABC, abstractmethod

from .kelly_sizer import scenario_seed, step_atoms

__all__ = ["GENERATORS", "EmpiricalCopula", "InsufficientHistory", "JointScenarioGenerator", "generator"]


class InsufficientHistory(ValueError):
    """The request's history is too short for the generator's spec: the week does not trade (it is not a defect)."""


class JointScenarioGenerator(ABC):
    """The family: ``S`` joint settlements as one atom per group per scenario, with equal weights.

    Parameters
    ----------
    spec : dict
        The kelly block's ``joint_scenarios`` declaration. ``method`` names the member (:data:`GENERATORS`), and
        ``scenarios`` (int in ``[2, SCENARIO_CEILING]``) and ``seed`` (int >= 0) are common to every member. The rest
        are the member's own :attr:`KEYS`.
    """

    #: Keys every member's spec declares, beside its own :attr:`KEYS`.
    COMMON_KEYS = ("method", "scenarios", "seed")
    #: The member's own keys (each member names them).
    KEYS = ()
    #: The most scenarios a spec may declare: a cap on the climb's ``items x scenarios`` matrix, not a tractability
    #: claim (the sizer's ceiling, ``WeeklyOptionPortfolioStudy._SIZER_SCENARIO_CEILING``).
    SCENARIO_CEILING = 100000
    METHOD = None

    def __init_subclass__(cls, **kwargs):
        """Keep :meth:`draw` the family's one template (a member supplies :meth:`uniforms` only)."""
        super().__init_subclass__(**kwargs)
        if "draw" in cls.__dict__:
            raise TypeError(f"{cls.__name__} may not override draw(); supply uniforms()")

    @classmethod
    def defects(cls, spec):
        """Return the spec's configuration defects (empty when well formed)."""
        if not isinstance(spec, dict) or set(spec) != {*cls.COMMON_KEYS, *cls.KEYS}:
            return [f"joint_scenarios needs exactly the keys {sorted({*cls.COMMON_KEYS, *cls.KEYS})}"]
        problems = []
        if spec["method"] != cls.METHOD:
            problems.append(f"joint_scenarios method must be {cls.METHOD!r}")
        if type(spec["scenarios"]) is not int or not 2 <= spec["scenarios"] <= cls.SCENARIO_CEILING:
            problems.append(f"joint_scenarios scenarios must be an int in [2, {cls.SCENARIO_CEILING}]")
        if type(spec["seed"]) is not int or spec["seed"] < 0:
            problems.append("joint_scenarios seed must be an int >= 0")
        return problems+cls.own_defects(spec)

    @classmethod
    def own_defects(cls, spec):
        """Return the defects of the member's own keys (none by default)."""
        return []

    def __init__(self, spec):
        problems = self.defects(spec)
        if problems:
            raise ValueError("; ".join(problems))
        self.spec = dict(spec)

    def draw(self, request):
        """Return the week's joint scenarios: ``{"weights", "atoms", "record"}``.

        Parameters
        ----------
        request : dict
            ``day`` (ISO date of the decision), ``groups`` (list of ``{"id", "name", "horizon", "masses"}``: the
            group's name, its horizon in sessions (int >= 1), and its forecast masses on its grid), ``names`` (the
            names whose history the generator may read, in the column order of ``returns``), ``returns`` (an
            ``(M, len(names))`` array of daily log returns, oldest first, every row strictly before ``day``) and
            ``min_returns`` (int, the fewest returns a draw may use).

        Returns
        -------
        dict
            ``weights``: ``S`` equal floats. ``atoms``: ``{group id: [S ints]}``, each an index into that group's
            masses (plain ints, the toolkit's compact form). ``record``: the member's record plus ``method``,
            ``scenarios`` and ``seed``.

        Raises
        ------
        InsufficientHistory
            The history is too short for the spec.
        ValueError
            A malformed request: a group without its name's history, a horizon below one, or masses that are not a
            finite nonnegative vector summing to one.
        """
        import numpy as np

        groups, names = request["groups"], list(request["names"])
        column = {name: j for j, name in enumerate(names)}
        returns = np.asarray(request["returns"], dtype=float)
        if returns.ndim != 2 or returns.shape[1] != len(names) or not np.all(np.isfinite(returns)):
            raise ValueError("returns must be a finite (sessions, names) matrix")
        for g in groups:
            masses = np.asarray(g["masses"], dtype=float)
            if (g["name"] not in column or type(g["horizon"]) is not int or g["horizon"] < 1 or masses.ndim != 1
                    or not len(masses) or not np.all(np.isfinite(masses)) or np.any(masses < 0)
                    or abs(float(masses.sum())-1.) > 1e-6):
                raise ValueError("invalid joint-scenario group "+str(g.get("id")))
        needed = sorted({(g["name"], g["horizon"]) for g in groups})
        uniforms, record = self.uniforms(request, returns, column, needed)
        n = self.spec["scenarios"]
        atoms = {g["id"]: step_atoms(g["masses"], uniforms[g["name"], g["horizon"]]).tolist() for g in groups}
        return {"weights": [1./n]*n, "atoms": atoms,
                "record": {"method": self.METHOD, "scenarios": n, "seed": self.spec["seed"], **record}}

    @abstractmethod
    def uniforms(self, request, returns, column, needed):
        """Return ``({(name, horizon): (S,) uniforms in (0, 1)}, record)`` for every pair in ``needed``.

        ``returns`` is the request's validated matrix and ``column`` maps a name to its column. Scenario ``s`` must
        be ONE joint state across every pair (the property the program sizes on).
        """


class EmpiricalCopula(JointScenarioGenerator):
    """Block-bootstrapped historical days, ranked per name and horizon: an empirical copula.

    Own keys: ``window`` (int >= 2, the most trailing daily returns read) and ``block`` (int >= 1, the bootstrap's
    block length in sessions).

    Scenario ``s`` is one index path ``pi_s`` of ``H`` sessions (``H`` the longest horizon). It concatenates
    ``ceil(H / block)`` blocks of consecutive sessions, each started uniformly among the ``M - block + 1`` starts of
    the last ``M = min(window, available)`` returns, and is truncated to ``H``. Every name and every horizon reads
    the same path, so the joint state is real historical days. With ``block >= H`` a scenario is one real
    ``H``-session window. A shorter block stitches windows when the horizons are long against the history. A
    name's horizon-``h`` return is ``R = sum_{k <= h} r[pi_s(k)]``. Its uniform is ``(midrank of R among the S draws
    - 1/2) / S``: the draws' empirical copula, its historical marginals discarded by the ranks.

    Fewer than ``max(2 * block, min_returns)`` returns raises :class:`InsufficientHistory`.
    """

    METHOD = "empirical_copula"
    KEYS = ("window", "block")

    @classmethod
    def own_defects(cls, spec):
        """``window`` an int >= 2, ``block`` an int >= 1 and no longer than the window."""
        problems = []
        if type(spec["window"]) is not int or spec["window"] < 2:
            problems.append("joint_scenarios window must be an int >= 2")
        if type(spec["block"]) is not int or spec["block"] < 1:
            problems.append("joint_scenarios block must be an int >= 1")
        elif type(spec["window"]) is int and spec["block"] > spec["window"]:
            problems.append("joint_scenarios block must not exceed the window")
        return problems

    def minimum(self, min_returns):
        """Return the fewest daily returns a draw needs under this spec."""
        return max(2*self.spec["block"], int(min_returns))

    def uniforms(self, request, returns, column, needed):
        """See :class:`EmpiricalCopula`."""
        import numpy as np
        from scipy.stats import rankdata

        window, block, n = self.spec["window"], self.spec["block"], self.spec["scenarios"]
        history = returns[-window:]
        m = history.shape[0]
        if m < self.minimum(request["min_returns"]):
            raise InsufficientHistory(f"{m} daily returns, {self.minimum(request['min_returns'])} needed")
        record = {"window": window, "block": block, "returns": int(m), "block_starts": int(m-block+1),
                  "horizon_max": max((h for _name, h in needed), default=0), "pairs": len(needed)}
        if not needed:
            return {}, record
        horizon = record["horizon_max"]
        rng = np.random.default_rng(scenario_seed(self.spec["seed"], request["day"]))
        blocks = math.ceil(horizon/block)
        starts = rng.integers(0, m-block+1, size=(n, blocks))
        path = (starts[:, :, None]+np.arange(block)[None, None, :]).reshape(n, blocks*block)[:, :horizon]
        out = {}
        by_name = {}
        for name, h in needed:
            by_name.setdefault(name, []).append(h)
        for name, horizons in by_name.items():
            # The name's daily returns along every path, summed: one cumulative row per scenario.
            cumulative = np.cumsum(history[path, column[name]], axis=1)
            for h in horizons:
                out[name, h] = (rankdata(cumulative[:, h-1], method="average")-.5)/n
        return out, record


#: The registered members, by their ``method`` key.
GENERATORS = {EmpiricalCopula.METHOD: EmpiricalCopula}


def generator(spec):
    """Return the generator a ``joint_scenarios`` spec declares (``ValueError`` for an unknown method or a defect)."""
    if not isinstance(spec, dict) or spec.get("method") not in GENERATORS:
        raise ValueError(f"joint_scenarios method must be one of {sorted(GENERATORS)}")
    return GENERATORS[spec["method"]](spec)


def defects(spec):
    """Return a ``joint_scenarios`` spec's configuration defects (empty when well formed)."""
    if not isinstance(spec, dict) or spec.get("method") not in GENERATORS:
        return [f"joint_scenarios method must be one of {sorted(GENERATORS)}"]
    return GENERATORS[spec["method"]].defects(spec)
