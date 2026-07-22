"""Which materials are ACTUALLY on the selected actors, and what do their
parameters resolve to?

    python live/ue_live.py -f examples/effective_materials.py

This is the question a static per-asset dump cannot answer. A material instance
inherits from its parent, the parent may be an instance itself, and only some
parameters are overridden -- so "what is on this actor" and "what value is this
scalar really using" have to be asked of the live editor.
"""
import unreal

sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = sub.get_selected_level_actors()

out = []
for actor in actors:
    for comp in actor.get_components_by_class(unreal.PrimitiveComponent):
        for slot in range(comp.get_num_materials()):
            mat = comp.get_material(slot)
            if mat is None:
                continue
            entry = {
                "actor": actor.get_actor_label(),
                "component": comp.get_name(),
                "slot": slot,
                "material": mat.get_path_name(),
                "class": type(mat).__name__,
            }

            # Walk the instance chain up to the base material.
            chain, node = [], mat
            while isinstance(node, unreal.MaterialInstance):
                chain.append(node.get_path_name())
                node = node.get_editor_property("parent")
            if node is not None:
                chain.append(node.get_path_name())
            entry["parent_chain"] = chain

            # Effective (post-inheritance) parameter values.
            if isinstance(mat, unreal.MaterialInstance):
                mel = unreal.MaterialEditingLibrary
                scalars = {}
                for name in mel.get_scalar_parameter_names(mat.get_base_material()):
                    try:
                        scalars[str(name)] = mel.get_material_instance_scalar_parameter_value(
                            mat, name)
                    except Exception as e:
                        scalars[str(name)] = "<err %s>" % e
                entry["scalars"] = scalars

            out.append(entry)

result = out
