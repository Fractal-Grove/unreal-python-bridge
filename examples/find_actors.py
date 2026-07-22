"""Count and locate every actor of a given class in the open level.

    python live/ue_live.py -f examples/find_actors.py

Edit WANT below, or send the whole thing inline:

    python live/ue_live.py -c "len(unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors())"

Because the bridge namespace persists between calls, you can also set something
here and keep poking at it in later one-liners.
"""
import unreal

WANT = "StaticMeshActor"   # class name substring to match

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
matches = [a for a in sub.get_all_level_actors() if WANT.lower() in type(a).__name__.lower()]

# Kept in the persistent namespace, so a follow-up call can reuse `matches`.
result = {
    "want": WANT,
    "count": len(matches),
    "first_20": [
        {"label": a.get_actor_label(), "location": str(a.get_actor_location())}
        for a in matches[:20]
    ],
}
