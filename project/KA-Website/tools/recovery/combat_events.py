"""Synchronous special-battle event ordering recovered from native subscribers."""


def send_to_listeners(listeners, payload):
    # Delegate.Combine creates a new invocation array. An ongoing invocation
    # retains its captured array while nested sends use their current binding.
    for listener in tuple(listeners):
        listener(payload)


def dispatch_attack_event(results, process_effect, apply_fighter_results):
    # BattleSystem subscribes first and processes the ENTIRE result array.
    for result in results:
        process_effect(result)
    apply_fighter_results(results)
