"""Recovered persistent skill command 29; independent of Fighter.state."""
from combat_initial_state import i32, trunc_div


def enqueue_skill_command(commands, target, skill, *, target_exists, first=False):
    """AddCommandSkill stores a target ID and a fresh independent command clock.

    Counter uses first=False. No deduplication or gameplay queue cap is present
    in the inspected constructor/AddCommandLast path.
    """
    command = dict(opcode=29, target=target if target_exists else -1,
                   skill=skill['id'], tick=0,
                   duration=19 if skill['count']==1 else i32(19+i32(5*skill['count'])),
                   use_index=0)
    if first:
        commands.insert(0,command)
    else:
        commands.append(command)
    return command


def update_skill_command(command, skill, animation, change_animation, use_skill,
                         remove_first_command):
    """Execute ScrSkill0x148a950 with an already resolved skill and target.

    command stores tick, duration and use_index; animation stores rate/frame.
    use_skill takes the current use index. A failed use still consumes an index.
    No fighter-state gate: interruptions do not suspend this command.
    """
    count = skill['count']
    if command['tick'] == 0:
        change_animation(skill['motion'])
        if count >= 2:
            animation['rate'] = 4
    if count == 1:
        if command['tick'] == 11:
            use_skill(command['use_index'])
            command['use_index'] = i32(command['use_index'] + 1)
    else:
        if command['use_index'] < count:
            remainder = command['tick'] - trunc_div(command['tick'], 5) * 5
            if remainder == 1:
                use_skill(command['use_index'])
                command['use_index'] = i32(command['use_index'] + 1)
            elif remainder == 0:
                change_animation(skill['motion'])
        if command['tick'] == i32(command['duration'] - 19):
            animation['rate'] = 1
            change_animation(3)
    command['tick'] = i32(command['tick'] + 1)
    if command['use_index'] >= count and command['tick'] > command['duration']:
        animation['frame'] = 0
        animation['rate'] = 1
        remove_first_command()
        return True
    return False


def execute_skill_queue(commands, resolve_skill, animation, change_animation,
                        use_skill, on_release=None):
    """Recovered AISystem command loop restricted explicitly to command 29.

    A completed head is removed and the next command begins in the same call.
    The callback receives the command, preserving its stored target identity.
    `on_release` (optional) is a read-only observer notified with the completed command
    exactly as its removal happens - never on a queue clear, which is not a release.
    """
    while commands:
        command = commands[0]
        if command['opcode'] != 29:
            raise NotImplementedError('Only recovered skill commands are supported')
        def remove_first_command():
            commands.pop(0)
            if on_release is not None:
                on_release(command)
        completed = update_skill_command(
            command, resolve_skill(command), animation, change_animation,
            lambda index: use_skill(command, index), remove_first_command)
        if not completed:
            break
