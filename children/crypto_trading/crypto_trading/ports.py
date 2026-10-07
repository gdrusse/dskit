"""The one owner of "this port must carry a list of rows": the base of the row-in, row-out nodes.

Every stage B transform takes record streams on named ports and refuses, at run time, a port that is not a
list (a one-shot iterable would be consumed by the check and reach ``run`` empty). The check is the same
everywhere, so it lives here once and a node only names its ports in :attr:`ListPortsNode.LIST_PORTS`.

Import cost: stdlib + dskit.
"""

from dskit.pipeline.node import Node

__all__ = ["ListPortsNode"]


class ListPortsNode(Node):
    """A node whose named input ports must each carry a list of rows (abstract: a subclass supplies ``run``).

    Examples
    --------
    A transform with two row ports::

        class Joiner(ListPortsNode):
            role = "transform"
            LIST_PORTS = ("records", "extras")

            def run(self, ctx, inputs):
                return {"records": inputs["records"] + inputs["extras"]}
    """

    #: The input ports that must be lists of rows.
    LIST_PORTS = ("records",)

    def validate_inputs(self, inputs):
        """Refuse a port in :attr:`LIST_PORTS` that is not a list.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per port that is not a list; empty when all are.
        """
        return [f"{port} must be a list of rows, got {type(inputs.get(port)).__name__}"
                for port in self.LIST_PORTS if not isinstance(inputs.get(port), list)]
