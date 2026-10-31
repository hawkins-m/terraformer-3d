"""Measure the editor's frame rate looking over a point of the city level (editor Python, UE 5.8,
full editor with -RenderOffscreen; started by `ap ue fps`, ue.frame_rate).

Args come from the AP_UE_ARGS environment variable (a JSON file path):
    {"map": "/Game/AP/Maps/<Name>", "report": ".../ap_frame_rate_report.json",
     "camera": {"location": [x, y, z] (cm), "rotation": [pitch, yaw, roll] (deg)},
     "warmup_s": 20, "settle_s": 15, "measure_s": 20, "timeout_s": 3600}

As ap_shot_render: the level loads, shaders compile until the ShaderCompileWorkers have been idle
for `settle_s`. Then the level viewport goes to the camera, realtime, the editor unthrottled (an
offscreen editor counts as in the background, which caps it at a few frames a second), no frame
cap and no vsync; after a 3 s settle every frame's time is recorded for `measure_s`. A World
Partition level has all its actors loaded first (the whole city, not only the cells by the camera).
"""
import json
import os
import sys
import time
import traceback

import unreal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # its sibling scripts
import ap_wp  # noqa: E402
from ap_shaders import compiling  # noqa: E402

args = json.load(open(os.environ["AP_UE_ARGS"]))
state = {"t0": time.time(), "quiet_since": None, "phase": "load", "dts": [], "busy": False}
report = {"ok": False}


def log(msg):
    unreal.log(f"AP_FPS {msg}")


def finish(error=None):
    unreal.unregister_slate_post_tick_callback(handle)
    dts = sorted(state["dts"])
    if dts:
        mean = sum(dts) / len(dts)
        report.update(frames=len(dts), fps_mean=round(1 / mean, 1), frame_ms_mean=round(1000 * mean, 2),
                      frame_ms_p95=round(1000 * dts[int(0.95 * (len(dts) - 1))], 2),
                      fps_p5=round(1 / dts[int(0.95 * (len(dts) - 1))], 1))
    report["ok"] = error is None and bool(dts)
    report["seconds"] = round(time.time() - state["t0"], 1)
    if error:
        report["error"] = error
    with open(args["report"], "w") as f:
        json.dump(report, f, indent=1)
    log(f"done ok={report['ok']} fps={report.get('fps_mean')}")
    unreal.SystemLibrary.quit_editor()


def unthrottle():
    """Realtime viewport, no background throttling, no frame cap, no vsync: what worked goes in the report."""
    done = []
    perf = unreal.get_default_object(unreal.EditorPerformanceSettings)
    for prop in ("throttle_cpu_when_not_foreground", "use_less_cpu_when_in_background"):
        try:
            perf.set_editor_property(prop, False)
            done.append(prop)
        except Exception:  # noqa: BLE001 (a name this engine doesn't have)
            pass
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    for name in ("editor_set_viewport_realtime", "set_viewport_realtime"):
        fn = getattr(les, name, None)
        if fn is not None:
            try:
                fn(True)
                done.append(name)
                break
            except Exception:  # noqa: BLE001
                pass
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for cmd in ("t.MaxFPS 0", "r.VSyncEditor 0", "r.VSync 0", "Editor.ThrottleCPUWhenNotForeground 0"):
        unreal.SystemLibrary.execute_console_command(world, cmd)
        done.append(cmd)
    report["unthrottled"] = done


def tick(dt):
    if state["busy"]:
        return
    state["busy"] = True
    try:
        step(dt)
    except Exception:
        finish(traceback.format_exc())
    finally:
        state["busy"] = False


def step(dt):
    now = time.time()
    if now - state["t0"] > args.get("timeout_s", 3600):
        return finish(f"timed out in phase {state['phase']}")
    if state["phase"] == "load":
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if not les.load_level(args["map"]):
            return finish(f"can't load {args['map']}")
        report["actors_loaded"] = ap_wp.load_all_actors()   # a World Partition level: the whole city, as after Load All
        cam = args["camera"]
        unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
            unreal.Vector(*cam["location"]), unreal.Rotator(*cam["rotation"]))
        unthrottle()
        state["phase"], state["loaded"] = "warmup", now
        log("level loaded")
        return
    if state["phase"] == "warmup":
        if now - state["loaded"] < args.get("warmup_s", 20):
            return
        if compiling():
            state["quiet_since"] = None
            return
        state["quiet_since"] = state["quiet_since"] or now
        if now - state["quiet_since"] < args.get("settle_s", 15):
            return
        report["shader_wait_s"] = round(now - state["loaded"], 1)
        state["phase"], state["measure_t0"] = "measure", now
        log(f"shaders settled after {report['shader_wait_s']} s; measuring")
        return
    if now - state["measure_t0"] < 3:
        return
    state["dts"].append(dt)
    if now - state["measure_t0"] >= 3 + args.get("measure_s", 20):
        finish()


handle = unreal.register_slate_post_tick_callback(tick)
