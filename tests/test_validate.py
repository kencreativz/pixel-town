import json
import shutil

import pytest

from conftest import WORLD_DIR
from engine.validate import validate_world


def edit_json(path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_seed_world_passes():
    errors, warnings, summary = validate_world(WORLD_DIR)
    assert errors == []
    assert summary == "OK: pixel-town (5 characters, 5 locations). Estimated Gemini calls/day: 9-12"


def duplicate_id(d):
    shutil.copy(d / "characters" / "bob.json", d / "characters" / "bob2.json")


def unknown_location(d):
    edit_json(d / "characters" / "greg.json", lambda c: c["schedule"].update({"Night": "nowhere"}))


def overlapping_rect(d):
    edit_json(d / "world.json", lambda w: w["locations"]["charlie_home"]["rect"].update({"x": 1, "y": 1}))


def unknown_action(d):
    edit_json(d / "characters" / "bob.json", lambda c: c["allowed_actions"].append("fly"))


@pytest.mark.parametrize("breakage", [duplicate_id, unknown_location, overlapping_rect, unknown_action])
def test_broken_copy_fails(tmp_path, breakage):
    d = tmp_path / "pixel-town"
    shutil.copytree(WORLD_DIR, d)
    breakage(d)
    errors, _, summary = validate_world(d)
    assert errors
    assert summary is None
