"""A scriptable connector for tests — emits exactly the messages told to.

Importable by class reference (``tests.onboarding.fake_connector:
FakeConnector``), so acquisition tests exercise the REAL resolve path.
The message script is a class attribute because ``run_acquisition``
instantiates the class itself — tests set ``FakeConnector.script``
(and restore it) rather than passing constructor args.
"""

from dskit.onboarding import PROTOCOL, Connector


def record(stream, eff, data=None, kind=None):
    msg = {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
           "effective_date": eff, "data": data or {"v": 1}}
    if kind is not None:
        msg["kind"] = kind
    return msg


def state(obj):
    return {"protocol": PROTOCOL, "type": "STATE", "state": obj}


class FakeConnector(Connector):
    """Yields ``script`` verbatim; records every call for assertions."""

    script = []
    calls = []

    def spec(self):
        return {"params": {
            "token": {"secret": True, "notes": "env var NAME"},
            "flavor": {"notes": "free knob for config tests"},
        }}

    def check(self, config):
        FakeConnector.calls.append(("check", config))

    def discover(self, config):
        return [{"stream": "s", "schema": {"fields": []}, "primary_key": []}]

    def read(self, config, streams, state, mode):
        FakeConnector.calls.append(("read", streams, state, mode))
        yield from FakeConnector.script


class NotAConnector:
    """Resolvable attribute that fails the subclass check."""
