"""A neutral synthetic 'adapter' — the fixture behind the --adapter tests.

Importing this module registers a node kind, exactly as a real adapter
package does at import time ("import = registration"). It names no venue;
it exists only to prove the ``--adapter`` flag reaches a registered kind
from the one universal command line.

A real adapter would register its own wrapper Nodes here; the fixture
simply aliases a toolkit synthetic node under a registered kind name, so
a one-node document can plan and run to completion with zero data.
"""

from dskit.pipeline.node import DEFAULT_NODE_KINDS, register_node_kind
from dskit.pipeline.synthetic_nodes import SynthEvents

#: The registered kind this fixture claims. Unreachable from the CLI until
#: this module is imported — which is what ``--adapter`` does.
KIND = "synth-source"

if KIND not in DEFAULT_NODE_KINDS:
    register_node_kind(KIND, SynthEvents)
