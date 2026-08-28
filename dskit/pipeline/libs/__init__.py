"""Tier-2 library packs — generic wrappers for common DS libraries (D-146).

One module per library (``pyomo.py``, ``sklearn.py``, ``torch.py``,
``transformers.py``, ``optuna.py``, ``numpy.py``, ``sb3.py``,
``matplotlib.py``, ``mlflow.py``). Each may NAME its
library, but imports it only inside a method (``run()`` for a node pack)
— enforced twice by
``tests/pipeline/test_purity.py`` (statically, and by importing every
pack in a fresh interpreter with the library blocked from
``sys.modules``). This is the ONLY subdirectory the toolkit sanctions.

Packs are consumed by IMPORT PATH from documents
(``"uses": "dskit.pipeline.libs.sklearn:SklearnFit"`` or a project's
own subclass); nothing here auto-registers into ``DEFAULT_NODE_KINDS``
at toolkit import — a pack's ``NODE_KINDS`` table plus its optional
``register()`` is the deliberate, explicit path, exactly like an
adapter's.

Not every pack fills the node registry. ``mlflow.py`` is a TRACKING SINK
pack: its ``NODE_KINDS`` is empty and its ``register()`` claims a kind in
``base.SINK_KINDS`` instead, the same seam
``dskit.pipeline.testing.register_synthetic`` uses for the test
``"memory"`` sink. Same doctrine, different registry — explicit,
idempotent, application-side.

This ``__init__`` deliberately imports NO pack: importing the toolkit
must never pay for a pack the document does not use.

Import cost: stdlib only.
"""
