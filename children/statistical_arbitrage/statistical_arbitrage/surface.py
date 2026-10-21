"""Child identity for DSKit's manifest-fed option surface processor."""

from dskit.pipeline.libs.chain_features import ChainDayFeatures


class TechOptionSurface(ChainDayFeatures):
    """Phase-one surface node.

    All option and rate conventions are declared in the run JSON and
    validated by ChainDayFeatures. This subclass adds no pricing logic.
    """
