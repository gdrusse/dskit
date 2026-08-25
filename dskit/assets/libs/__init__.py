"""Tier-2 store packs — alternative backends behind the Store ABC (ADR-0018).

One module per backend (``sqlite.py``, ``parquet.py``; postgres when
requirements arrive, per ADR-0011's sequencing). Each pack answers to
the same purity gate as the core (module level = stdlib + this
package) and imports its backend library only inside methods — even
stdlib ``sqlite3``, because the pack is the TEMPLATE for drivers that
must stay lazy.

Packs are reached by declaration, never by import: ``store.json``
names the backend, ``open_store``/``create_store`` resolve it
(built-in name or ``pkg.module:Class``) and import the pack on demand.

This ``__init__`` deliberately imports NO pack: importing the toolkit
must never pay for a backend the root does not declare.

Import cost: stdlib only.
"""
