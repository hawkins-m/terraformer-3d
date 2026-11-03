"""Put the editor's level viewport over downtown once the city level has loaded (editor Python, UE 5.8;
started by `ap ue` Open in Unreal through -ExecCmds=py, and by ue.check_open).

Args come from the AP_UE_ARGS environment variable (a JSON file path):
    {"map": "/Game/AP/Maps/<Name>", "camera": {"location": [x, y, z] (cm), "rotation": [pitch, yaw, roll] (deg)},
     "report": ".../ap_open_report.json" (optional: check_open's: the script then reports what it found and quits the editor),
     "timeout_s": 600}

The editor opens the city level as its startup map; this waits for it, then sets the camera (a
retry until the viewport exists). A World Partition level's saved region (ue_view) loads with it:
with a report, the actor counts are taken once they stop growing.
"""
import json
import os
import time
import traceback

import unreal

args = json.load(open(os.environ["AP_UE_ARGS"]))
state = {"t0": time.time(), "phase": "wait", "counts": [], "last": None, "stable_since": None, "busy": False}
report = {"ok": False}


def log(msg):
    unreal.log(f"AP_OPEN {msg}")


def finish(error=None):
    unreal.unregister_slate_post_tick_callback(handle)
    if args.get("report"):
        report["ok"] = error is None and report.get("camera_ok", False)
        report["seconds"] = round(time.time() - state["t0"], 1)
        if error:
            report["error"] = error
        with open(args["report"], "w") as f:
            json.dump(report, f, indent=1)
        log(f"done ok={report['ok']}")
        unreal.SystemLibrary.quit_editor()
    elif error:
        unreal.log_error(f"AP_OPEN {error}")


def on_map():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return world is not None and world.get_path_name().split(".")[0] == args["map"]


def loaded_actors():
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = eas.get_all_level_actors()
    mine = [a for a in actors if any(str(t).startswith("ap:id=") for t in a.tags)]
    return len(actors), len(mine)


def tick(dt):
    if state["busy"]:
        return
    state["busy"] = True
    try:
        step()
    except Exception:
        finish(traceback.format_exc())
    finally:
        state["busy"] = False


def step():
    now = time.time()
    if now - state["t0"] > args.get("timeout_s", 600):
        return finish(f"timed out in phase {state['phase']}")
    if state["phase"] == "wait":
        if not on_map():
            return
        descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
        report["partitioned"] = bool(descs)
        report["actor_descs"] = len(descs or [])
        state["phase"], state["loaded"] = "camera", now
        log("level open")
        return
    if state["phase"] == "camera":
        cam = args["camera"]
        ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        try:
            ues.set_level_viewport_camera_info(unreal.Vector(*cam["location"]), unreal.Rotator(roll=cam["rotation"][2], pitch=cam["rotation"][0], yaw=cam["rotation"][1]))
            info = ues.get_level_viewport_camera_info()          # (location, rotation); 5.8 drops the success flag
            ok, loc, rot = (True, *info) if len(info) == 2 else info
        except Exception as e:  # noqa: BLE001
            state["tries"] = state.get("tries", 0) + 1
            if state["tries"] % 100 == 1:
                log(f"camera not set yet: {e}")
            return
        state["tries"] = state.get("tries", 0) + 1
        if state["tries"] % 100 == 1:
            log(f"camera read back ok={ok} loc={loc}")
        if not ok or abs(loc.z - cam["location"][2]) > 1:
            return
        report.update(camera_ok=True, camera={"location": [loc.x, loc.y, loc.z], "rotation": [rot.pitch, rot.yaw, rot.roll]})
        log("camera set")
        if not args.get("report"):
            return finish()
        state["phase"] = "count"
        return
    # count: the regions load a little after the level opens; done when the number has held for 20 s
    n = loaded_actors()
    if n != state["last"]:
        state["last"], state["stable_since"] = n, now
        return
    if now - state["stable_since"] >= 20:
        report["level_actors"], report["pipeline_actors"] = n
        return finish()


handle = unreal.register_slate_post_tick_callback(tick)
