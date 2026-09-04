"""``pmquant`` — prediction-market ladders as a dskit child (ADR-0021).

Import = registration: importing this package registers every
``pmquant-*`` node kind with the toolkit's default registry, which is
exactly what ``--adapter pmquant`` does for the CLI. The kinds are split
by what they know: :mod:`.nodes_data` reads the acquired ladder and
settlement streams (the observations seam), :mod:`.nodes_model` owns the
transformer q-hat path over event panels, :mod:`.nodes_capital` sizes
the survivors through the fractional-Kelly MIO. The pure engines they
call — :mod:`.books`, :mod:`.fees`, :mod:`.mio`, :mod:`.models`,
:mod:`.ladder` — never import a node.

Nothing here imports a heavy library at module top: a document naming
these kinds plans on a machine with only ``dskit`` installed.
:mod:`.testing` (the synthetic ladder world) stays out of the import
surface so plans stay cheap.
"""

from . import nodes_capital, nodes_data, nodes_model
from .ladder import protocols as ladder_protocols

__all__ = ["NODE_KINDS", "ladder_protocols", "nodes_capital", "nodes_data", "nodes_model"]

#: Every kind this child registers, keyed by document name.
NODE_KINDS = {
    **nodes_data.NODE_KINDS,
    **nodes_model.NODE_KINDS,
    **nodes_capital.NODE_KINDS,
}
