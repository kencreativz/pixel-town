def _pairs(d):
    return ", ".join(f"{k}: {v}" for k, v in d.items())


def build_prompt(char, state, world, rules, characters):
    slot = world.clock.slots[state["clock"]["slot_index"]]
    char_state = state["characters"][char.id]
    loc_id = char_state["location"]

    stats = char_state["stats"]
    stat_lines = "\n".join(f"- {k}: {v}" for k, v in stats.items()) if stats else "- none"

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
        )
    else:
        walking = "You cannot walk anywhere; set move_to to null."

    recent = state["log"][-world.history_lines:] if world.history_lines > 0 else []
    history = "\n".join(f"[{e['name']}]: {e['text']}" for e in recent) if recent else "(nothing yet)"

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
        f"Respond ONLY with a JSON object with these keys:\n"
        f"\"say\" (string, in character, under 2 sentences), \"action\" (string), \"target\" (string or null, a character id),\n"
        f"\"amount\" (integer or null), \"move_to\" (string or null, a location id).\n"
    )
