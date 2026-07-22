"""What is selected in the level right now?

    python live/ue_live.py -f examples/selected_actors.py

Set `result` to return structured data -- the client prints it, and --json gives
you a machine-readable round-trip.
"""
import unreal

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_selected_level_actors()

result = [
    {
        "label": a.get_actor_label(),
        "class": type(a).__name__,
        "path": a.get_path_name(),
        "location": str(a.get_actor_location()),
    }
    for a in actors
]
