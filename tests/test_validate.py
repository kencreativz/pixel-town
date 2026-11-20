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
    assert summary == "OK: pixel-town (6 characters, 14 locations). Estimated Gemini calls/day: 9-12"


def duplicate_id(d):
    shutil.copy(d / "characters" / "bob.json", d / "characters" / "bob2.json")


def unknown_location(d):
    edit_json(d / "characters" / "greg.json", lambda c: c["schedule"].update({"Night": "nowhere"}))


def overlapping_rect(d):
    edit_json(d / "world.json", lambda w: w["locations"]["charlie_home"]["rect"].update({"x": 1, "y": 1}))


def unknown_action(d):
    edit_json(d / "characters" / "bob.json", lambda c: c["allowed_actions"].append("fly"))


def unknown_shelter(d):
    edit_json(d / "world.json", lambda w: w.update({"shelter": "nowhere"}))


def unknown_jail(d):
    edit_json(d / "world.json", lambda w: w.update({"jail": "nowhere"}))


def bad_meet(d):
    edit_json(d / "rules.json", lambda r: r["actions"]["punch"].update({"meet": "nobody"}))


def road_over_room(d):
    edit_json(d / "world.json", lambda w: w["map"]["roads"].append({"x": 1, "y": 1, "width": 2, "height": 2}))


@pytest.mark.parametrize("breakage", [duplicate_id, unknown_location, overlapping_rect, unknown_action, unknown_shelter,
                                      unknown_jail, bad_meet, road_over_room])
def test_broken_copy_fails(tmp_path, breakage):
    d = tmp_path / "pixel-town"
    shutil.copytree(WORLD_DIR, d)
    breakage(d)
    errors, _, summary = validate_world(d)
    assert errors
    assert summary is None


def test_roads_reach_every_door():
    # Same door rule as renderer.js: a bottom door mid-bottom, a right door at the bottom of the right wall.
    world = json.loads((WORLD_DIR / "world.json").read_text())
    road = set()
    for r in world["map"]["roads"]:
        road |= {(x, y) for x in range(r["x"], r["x"] + r["width"]) for y in range(r["y"], r["y"] + r["height"])}
    for lid, loc in world["locations"].items():
        r = loc["rect"]
        bottom, right = r["y"] + r["height"] - 1, r["x"] + r["width"] - 1
        bx = r["x"] + (r["width"] - 2) // 2
        for exit_tile in [(bx, bottom + 1), (bx + 1, bottom + 1), (right + 1, bottom - 1), (right + 1, bottom)]:
            assert exit_tile in road, f"{lid} door exit {exit_tile} is off the road"
    # one connected network: no gaps
    seen, todo = set(), [next(iter(road))]
    while todo:
        x, y = todo.pop()
        if (x, y) in seen:
            continue
        seen.add((x, y))
        todo += [n for n in [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)] if n in road]
    assert seen == road


def test_unknown_office_owner_fails(tmp_path):
    d = tmp_path / "pixel-town"
    shutil.copytree(WORLD_DIR, d)
    edit_json(d / "world.json", lambda w: w["locations"]["landlord_office"].update({"owner": "nobody"}))
    errors, _, _ = validate_world(d)
    assert any("owner 'nobody'" in e for e in errors)
