import random


def decide_scripted(char, seed, day, slot):
    """Scripted turn: maybe say a canned line, never act or move."""
    rng = random.Random(f"{seed}:{day}:{slot}:{char.id}")
    say = ""
    lines = char.lines.get(slot)
    if lines and rng.random() < char.talk_chance:
        say = rng.choice(lines)
    return {"say": say, "action": "none", "target": None, "amount": None, "move_to": None}
