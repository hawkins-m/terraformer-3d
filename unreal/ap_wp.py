"""World Partition levels (a large / metro city's: ap_import, ap_pull_layout, ap_frame_rate)."""
import unreal


def load_all_actors() -> int:
    """Load every actor of the editor world when it's partitioned (a commandlet loads none: the
    import then found no actor of its own and made every slot again). Returns how many; 0 for a
    plain level, whose actors are all loaded with it."""
    guids = [d.guid for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []]   # a plain level: None
    if guids:
        unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    return len(guids)
