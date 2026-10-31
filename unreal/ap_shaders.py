"""Shared by the full-editor scripts (ap_shot_render, ap_frame_rate): are shaders still compiling?"""
import os
import subprocess


def compiling() -> bool:
    """Any ShaderCompileWorker process running."""
    if os.name == "nt":          # native Windows: tasklist names the workers' image
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ShaderCompileWorker.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "ShaderCompileWorker" in (r.stdout or "")
    r = subprocess.run(["pgrep", "-c", "-f", "ShaderCompileWorker"], capture_output=True, text=True)
    return int((r.stdout or "0").strip() or 0) > 0
