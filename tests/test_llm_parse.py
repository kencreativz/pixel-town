from engine.llm import NONE_TURN, parse_turn

FULL = '{"say": "Hi", "action": "none", "target": null, "amount": null, "move_to": null}'


def test_plain_json():
    assert parse_turn(FULL) == {"say": "Hi", "action": "none", "target": None, "amount": None, "move_to": None}


def test_fenced_json():
    assert parse_turn("```json\n" + FULL + "\n```")["say"] == "Hi"


def test_json_in_prose():
    assert parse_turn("Sure! Here you go: " + FULL + " Hope that helps.")["say"] == "Hi"


def test_garbage():
    assert parse_turn("not json at all") == NONE_TURN
    assert parse_turn("") == NONE_TURN


def test_say_capped():
    assert len(parse_turn('{"say": "' + "x" * 500 + '"}')["say"]) == 280


def test_action_lowercased():
    assert parse_turn('{"say": "", "action": "  LEND_Money "}')["action"] == "lend_money"


def test_amount_string_coerced():
    assert parse_turn('{"say": "", "action": "lend_money", "amount": "10"}')["amount"] == 10
