"""dskit — a data-science toolkit. Four packages, one doctrine: code is
generic, configuration is the interface, and your project is a thin child.

Subpackages:

* :mod:`dskit.pipeline` — the execution engine. One JSON document
  declares a whole process as a node DAG; one command
  (``python -m dskit.pipeline run <doc>``) runs any such document.
* :mod:`dskit.assets` — the config-driven asset registry/catalog:
  content-addressed records, lifecycles, lineage; the asset model
  itself is a JSON document.
* :mod:`dskit.onboarding` — acquisition & onboarding: connectors pull
  into WORM snapshots, declarative suites validate, certification and
  publication hand evidence to the assets catalog.
* :mod:`dskit.journal` — the child action ledger (ADR-0056): every
  acquire / research / execute / production row, CSV store, generated
  markdown, owner path-to-production.

Every tier-1 core is stdlib-only; heavy libraries live in optional
tier-2 packs (``<pkg>/libs/``), imported only inside ``run()``/``read()``.
Domain-specific code lives outside dskit entirely — see
``children/README.md``.
"""

__version__ = "0.0.1"
