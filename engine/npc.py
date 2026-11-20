import random


def decide_scripted(char, seed, day, slot):
    """Scripted turn: maybe say a canned line. Acts and moves only in an off-duty slot (char.off_duty plan)."""
    rng = random.Random(f"{seed}:{day}:{slot}:{char.id}")
    say = ""
    lines = char.lines.get(slot)
    if lines and rng.random() < char.talk_chance:
        say = rng.choice(lines)
    plan = char.off_duty.get(slot, {})
    return {
        "say": say,
        "action": plan.get("action", "none"),
        "target": None,
        "amount": plan.get("amount"),
        "move_to": plan.get("move_to"),
    }
