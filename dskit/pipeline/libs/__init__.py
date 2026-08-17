"""Tier-2 library packs — generic wrappers for common DS libraries (D-146).

One module per library (``pyomo.py``, ``sklearn.py``, ``torch.py``,
``transformers.py``, ``optuna.py``, ``numpy.py``). Each may NAME its
library, but imports it only inside ``run()`` — enforced twice by
``tests/pipeline/test_purity.py`` (statically, and by importing every
pack in a fresh interpreter with the library blocked from
``sys.modules``). This is the ONLY subdirectory the toolkit sanctions.

Packs are consumed by IMPORT PATH from documents
(``"uses": "dskit.pipeline.libs.sklearn:SklearnFit"`` or a project's
own subclass); nothing here auto-registers into ``DEFAULT_NODE_KINDS``
at toolkit import — a pack's ``NODE_KINDS`` table plus its optional
``register()`` is the deliberate, explicit path, exactly like an
adapter's.

This ``__init__`` deliberately imports NO pack: importing the toolkit
must never pay for a pack the document does not use.

Import cost: stdlib only.
"""
