"""dskit — a data-science toolkit.

Subpackages:

* :mod:`dskit.pipeline` — the venue-agnostic pipeline toolkit. One JSON
  document declares a whole process (data -> ... -> optimize -> report);
  one command (``python -m dskit.pipeline run <doc>``) runs any such
  document. The tier-1 core is stdlib-only; heavy libraries live in
  optional tier-2 packs (:mod:`dskit.pipeline.libs`) and are imported only
  inside a node's ``run()``.

More subpackages are added alongside it as the toolkit grows.
"""

__version__ = "0.0.1"
