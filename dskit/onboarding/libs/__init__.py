"""Tier-2 connector packs — generic wrappers, one file per source family.

Same rule as ``dskit/pipeline/libs``: a pack wraps a KIND of source
generically (local files, an HTTP API family, a database driver), never
one project's use of it. Heavy imports live inside ``read()``;
``localfiles`` is pure stdlib and exists as the conformance reference.
"""
