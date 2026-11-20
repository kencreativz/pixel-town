def _pairs(d):
    return ", ".join(f"{k}: {v}" for k, v in d.items())


def build_prompt(char, state, world, rules, characters):
    slot = world.clock.slots[state["clock"]["slot_index"]]
    char_state = state["characters"][char.id]
    loc_id = char_state["location"]

    stats = char_state["stats"]
    stat_lines = "\n".join(f"- {k}: {v}" for k, v in stats.items()) if stats else "- none"

    notes = char_state.get("memory") or []
    memories = "\n".join(f"- {n}" for n in notes) if notes else "(nothing yet)"

    other_lines = []
    for cid, other in characters.items():
        if cid == char.id:
            continue
        other_state = state["characters"][cid]
        line = f"- {other.name} ({other.kind}) at {world.locations[other_state['location']].name}"
        public = {k: other_state["stats"][k] for k in other.public_stats if k in other_state["stats"]}
        if public:
            line += f" — {_pairs(public)}"
        other_lines.append(line)

    action_lines = "\n".join(f"- {a}: {rules.actions[a].description}" for a in char.allowed_actions if a in rules.actions)

    if char.can_move:
        walking = "You may walk to one of these places by setting move_to (or use null to stay put):\n" + "\n".join(
            f"- {lid}: {loc.name}" for lid, loc in world.locations.items()
        ) + "\n" + ", ".join(
            ["Other people's homes"]
            + [loc.name for loc in world.locations.values() if loc.owner and loc.owner != char.id]
        ) + ": you must knock, and the owner decides if you come in."
    else:
        walking = "You cannot walk anywhere; set move_to to null."

    recent = state["log"][-world.history_lines:] if world.history_lines > 0 else []
    history = "\n".join(
        f"[{e['where']}] {e['name']}: {e['text']}" if e.get("where") else f"[{e['name']}]: {e['text']}"
        for e in recent
    ) if recent else "(nothing yet)"

    return (
        f"{char.persona}\n"
        f"\n"
        f"WORLD\n"
        f"Day {state['clock']['day']}, {slot}. {_pairs(state['globals'])}.\n"
        f"You are at: {world.locations[loc_id].name} ({loc_id}).\n"
        f"\n"
        f"YOUR STATS\n"
        f"{stat_lines}\n"
        f"\n"
        f"YOUR MEMORIES\n"
        f"{memories}\n"
        f"\n"
        f"OTHERS\n"
        f"{chr(10).join(other_lines)}\n"
        f"\n"
        f"AVAILABLE ACTIONS (choose exactly one; use \"none\" if nothing fits)\n"
        f"{action_lines}\n"
        f"\n"
        f"WALKING\n"
        f"{walking}\n"
        f"\n"
        f"RECENT HISTORY\n"
        f"{history}\n"
        f"\n"
        f"To talk to someone, set target to their id.\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (string), \"target\" (string or null, a character id),\n"
        f"\"amount\" (integer or null), \"move_to\" (string or null, a location id).\n"
    )


def build_confront_prompt(guard, name, place, problems):
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        f"{guard.persona}\n"
        f"\n"
        f"You walk up to {name} at {place}. They are breaking these rules:\n"
        f"{listed}\n"
        f"\n"
        f"Politely but firmly explain exactly these, face to face. If any of these is violence, be strict and stern instead.\n"
        f"\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (\"none\"),\n"
        f"\"target\" (null), \"amount\" (null), \"move_to\" (null).\n"
    )


def build_aid_prompt(guard, helper_name, patient_name, place):
    return (
        f"{guard.persona}\n"
        f"\n"
        f"{patient_name} is badly hurt at {place}. You walk up to {helper_name} and ask them to go and help.\n"
        f"Say it face to face.\n"
        f"\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (\"none\"),\n"
        f"\"target\" (null), \"amount\" (null), \"move_to\" (null).\n"
    )


def build_rounds_prompt(landlord, name, place, stat, value):
    return (
        f"{landlord.persona}\n"
        f"\n"
        f"You walk up to {name}, your tenant, at {place}. Their {stat} is {value}.\n"
        f"Talk to them about it, face to face.\n"
        f"\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (\"none\"),\n"
        f"\"target\" (null), \"amount\" (null), \"move_to\" (null).\n"
    )


def build_reply_prompt(char, name, place, talk, stats):
    stat_lines = "\n".join(f"- {k}: {v}" for k, v in stats.items()) if stats else "- none"
    lines = "\n".join(f"{who}: {text}" for who, text in talk)
    return (
        f"{char.persona}\n"
        f"\n"
        f"YOUR STATS\n"
        f"{stat_lines}\n"
        f"\n"
        f"{name} is talking with you at {place}. The talk so far:\n"
        f"{lines}\n"
        f"\n"
        f"Answer them.\n"
        f"\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (\"continue\" to keep talking, or \"end\" to end the talk),\n"
        f"\"target\" (null), \"amount\" (null), \"move_to\" (null).\n"
    )


def build_verdict_prompt(guard, name, crime, talk, stats):
    stat_lines = "\n".join(f"- {k}: {v}" for k, v in stats.items()) if stats else "- none"
    lines = "\n".join(f"{who}: {text}" for who, text in talk)
    return (
        f"{guard.persona}\n"
        f"\n"
        f"{name} {crime}. You asked them about it. The talk:\n"
        f"{lines}\n"
        f"\n"
        f"THEIR STATS\n"
        f"{stat_lines}\n"
        f"\n"
        f"Decide: lock them in the jail cell, or let them off with a warning. Tell them your decision and why.\n"
        f"\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (\"jail\" or \"warn\"),\n"
        f"\"target\" (null), \"amount\" (null), \"move_to\" (null).\n"
    )


def build_knock_prompt(owner, visitor_name, place):
    return (
        f"{owner.persona}\n"
        f"\n"
        f"{visitor_name} knocks on your door at {place} and asks to come in.\n"
        f"\n"
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (\"let_in\" to let them in, or \"none\" to say no),\n"
        f"\"target\" (null), \"amount\" (null), \"move_to\" (null).\n"
    )
