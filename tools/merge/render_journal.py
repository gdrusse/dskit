"""Take-either merge driver for the generated child decisioning README.

Git invokes this as ``render_journal.py %O %A %B`` (ADR-0107). The CSVs
are the store and this file is only their projection, so a conflict here
is noise: the driver takes the current side untouched and exits clean.
The regeneration is deliberately POST-merge — the ledger union driver's
output only reaches the working tree after all drivers ran — so
``render_all.py`` (installed as the post-merge hook by ``install.sh``)
re-renders every child from the merged CSVs, and any later journal write
re-renders again. The projection self-heals; it is never hand-resolved.
"""

from __future__ import annotations

import sys


def main(argv):
    """Take the current side (``%A``) as-is; nothing to merge.

    Parameters
    ----------
    argv : list of str
        ``[script, %O, %A, %B]`` — the paths git supplies. Unused beyond
        arity: ``%A`` already holds the side being kept.

    Returns
    -------
    int
        Always ``0`` — a generated file never conflicts the merge.
    """
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
