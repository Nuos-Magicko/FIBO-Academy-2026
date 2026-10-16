"""Lets the tests import the pc/ packages the same way main.py does (from connection import protocol, ...)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
