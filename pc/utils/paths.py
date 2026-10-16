"""Folders the tools read from and write to, all relative to the repository root."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "runs"
BASELINES_DIR = REPO_ROOT / "baselines"
FIRMWARE_BUILD_DIR = REPO_ROOT / "firmware" / "build"
