"""Shared test fixtures. Tests that write run on a scratch copy of the project,
never on the real agent/ workspace."""
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


@pytest.fixture
def scratch_root(tmp_path):
    """A copy of everything the services read: params.yaml, the fixture data, agent/,
    and agent_seed/ (the kit's self-check compares PRINCIPAL.md with the seed)."""
    P = yaml.safe_load((ROOT / "params.yaml").read_text())
    shutil.copy(ROOT / "params.yaml", tmp_path / "params.yaml")
    shutil.copytree(ROOT / "data" / "fixture", tmp_path / "data" / "fixture")
    for d in (P["agent"]["workspace"], P["agent"]["seed"]):
        shutil.copytree(ROOT / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path
