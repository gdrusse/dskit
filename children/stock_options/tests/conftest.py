"""Run the child tests from any working directory."""

import os
import sys

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if CHILD_ROOT not in sys.path:
    sys.path.insert(0, CHILD_ROOT)
