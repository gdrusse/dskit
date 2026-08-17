"""Importable component targets for the class-reference tests — this
module plays the role of a user's own research package."""

from dskit.pipeline.records import MarketRecord


def drop_low_mids(records, params):
    """A custom STREAM transform: callable (records, params) -> iterable."""
    floor = params.get("floor", 0.5)
    for rec in records:
        if rec.mid is not None and rec.mid > floor:
            yield rec


class ValidatedTransform:
    """A transform exposing validate_params — strict at construction."""

    @staticmethod
    def validate_params(params):
        return [] if "floor" in params else ["floor is required"]

    def __new__(cls, records, params):  # callable-as-class: returns the stream
        return drop_low_mids(records, params)


class ListSink:
    """A custom Tracker (satisfies the seam without importing it)."""

    instances = []

    def __init__(self, params):
        self.events = []
        ListSink.instances.append(self)

    def log_params(self, mapping):
        self.events.append(("params", dict(mapping)))

    def log_metrics(self, stage, mapping):
        self.events.append(("metrics", stage))

    def close(self):
        self.events.append(("close", None))


class NotASink:
    """Missing the Tracker seam methods on purpose."""


class ConstantModelFamily:
    """A custom model family: train/load both yield a SignalProvider."""

    calls = []

    @staticmethod
    def train(ctx, params):
        ConstantModelFamily.calls.append(("train", dict(params)))
        return _Constant(params.get("q", 0.5))

    @staticmethod
    def load(ctx, params, artifact):
        ConstantModelFamily.calls.append(("load", artifact))
        return _Constant(params.get("q", 0.5))


class _Constant:
    def __init__(self, q):
        self._q = q

    def q_hat(self, contract, p_mid, asof_ms, lead_frac=None):
        return self._q


class BadModelFamily:
    """train returns something that is not a SignalProvider."""

    @staticmethod
    def train(ctx, params):
        return object()


def tally_sizer(ctx, signal, survivors, params):
    """A custom sizing stage replacing backend.optimize."""
    return {"kind": "tally", "survivors": list(survivors), "cap": params.get("cap")}


def report_stage(ctx):
    """A custom stage: reads prior outputs, returns a payload."""
    n = len(ctx.outputs.get("stat_test", {}).get("survivors", []))
    return {"verdict": f"report: {n} survivor(s)", "n_survivors": n}


def broken_stage(ctx):
    return "not a dict"


NOT_CALLABLE = MarketRecord(
    venue="x", instrument="x", contract="x", asof_ms=0, usable=True, reason="ok"
)
