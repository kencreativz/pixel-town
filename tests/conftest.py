import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # no pytest.ini in the spec's file tree, so make `engine` importable here

from engine.hub import Hub  # noqa: E402
from engine.llm import MockClient  # noqa: E402
from engine.loader import load_world  # noqa: E402
from engine.simulation import Settings, Simulation  # noqa: E402

WORLD_DIR = ROOT / "worlds" / "pixel-town"


def make_sim(world, rules, characters, runtime_dir, max_calls=0):
    settings = Settings(time_scale=0, always_run=True, max_calls_per_hour=max_calls)
    return Simulation(world, rules, characters, MockClient(), runtime_dir, settings, Hub())


@pytest.fixture
def world():
    return load_world(WORLD_DIR)


@pytest.fixture
def sim(world, tmp_path):
    return make_sim(*world, tmp_path)


@pytest.fixture
def sim_no_plans(world, tmp_path):
    w, rules, characters = world
    cleared = {cid: c.model_copy(update={"mock_plan": {}}) for cid, c in characters.items()}
    return make_sim(w, rules, cleared, tmp_path)
