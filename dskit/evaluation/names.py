"""The one rule for a bare file name, shared by every writer in this package.

A report and a node that writes beside it both accept a file name only if
it names a file directly in the output directory. :func:`is_bare_file_name`
is the single owner of that rule, so :mod:`~dskit.evaluation.report` (a
section's extra files) and :mod:`~dskit.evaluation.links` (``csv_name`` and
axis values) cannot drift apart.

Import cost: stdlib only.
"""

from __future__ import annotations

import os

__all__ = ["is_bare_file_name"]


def is_bare_file_name(value):
    """Report whether ``value`` is a non-empty file name with no directory part.

    Parameters
    ----------
    value : object
        Anything; a non-string is never a file name.

    Returns
    -------
    bool
        False for ``''``, ``'.'``, ``'..'``, a name with a path separator
        (slash or backslash) and an absolute path.

    Examples
    --------
    ::

        is_bare_file_name("by-ticker.csv")  # True
        is_bare_file_name("../by-ticker.csv")  # False
    """
    return (isinstance(value, str) and value not in ("", ".", "..")
            and "\\" not in value and os.path.basename(value) == value)
