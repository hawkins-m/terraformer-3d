# Testing Terraformer on native Windows (beta)

Thanks for trying this. Native Windows covers the **greybox path**: Layout, Greybox (Blender), Shots,
Frames (ComfyUI) and Build city in Unreal. TRELLIS and the vision model are off on native Windows; they
need the WSL install. Nothing here has run on a Windows PC before you, so expect rough edges. The useful
result is a list of what worked and what didn't.

About 1 to 2 hours, mostly downloads. You need:

- Windows 10 21H2 or Windows 11, an NVIDIA GPU (8 GB or more) and a current NVIDIA driver (Game Ready or Studio)
- about 40 GB free disk space
- [Blender](https://www.blender.org/download/) 4.2 or newer (the normal installer)
- Unreal Engine 5.8 from the Epic Games Launcher (only for the last step; you can skip it)
- Optional: Visual Studio 2022 with **Desktop development with C++**. Without it the Unreal city
  gets a static-mesh terrain instead of a Landscape, and the build report says so. That is expected.

Tick each step. When one fails, stop and send what's listed under [If a step fails](#if-a-step-fails).

## 1. Install

- [ ] Download the repo (Code › Download ZIP) and extract it to a short path without spaces, such as
      `C:\terraformer-3d`.
- [ ] Double-click `windows\install.bat`. If Windows warns, click **More info › Run anyway**.
- [ ] When it asks **Which install?**, type `2` (Native Windows) and press Enter.
- [ ] Allow it to install **uv** (a Python installer; no admin rights needed).
- [ ] The setup wizard opens in your browser. On **Components**, leave the defaults. If you have no
      ComfyUI, keep **Install ComfyUI (local)** ticked: it downloads the portable build (about 2 GB).
- [ ] On **Connections**, check that Blender and Unreal were found (click **Test** next to each).
- [ ] Wait for **Finish**. The model downloads take the longest (FLUX and friends, about 30 GB).
- Result: a **Terraformer** shortcut in the Start menu and on the desktop.

## 2. Doctor

- [ ] Open a new Command Prompt and run `ap doctor`.
- Expected: Blender, Unreal and ComfyUI OK; "Native Windows (beta)" as a note; Vision LLM,
  Segmentation and depth, and TRELLIS.2 off with "not supported on native Windows yet". Anything else
  red is worth reporting.

## 3. New project

- [ ] Start **Terraformer** from the Start menu. A console window opens, then the app.
- [ ] File › New project, name it `test-city`.
- Expected: the project opens on the Layout tab.

## 4. Roll a city

- [ ] On the **Layout** tab, click **Roll a city** (any Layout preset is fine).
- [ ] Open **City in 3D** and orbit around with the mouse.
- Expected: a city plan in 2D, and the same city in the 3D viewer.

## 5. Greybox

- [ ] On the **Greybox** tab, click **Build greybox** (Blender runs in the background; a few minutes
      for a big city).
- [ ] Then **Render passes** on the same tab, and look at the **Shots** tab.
- Expected: the greybox preview and the shot passes appear. Blender doesn't open a window.

## 6. Frames

- [ ] In the Status box, click **Start ComfyUI** if it isn't running (the first start takes a few minutes).
- [ ] On the **Frames** tab, click **Generate missing frames** (or generate for one shot).
- Expected: two concept frames for the shot after a minute or two.

## 7. Unreal build

- [ ] Close the Unreal Editor if it's open.
- [ ] On the **Unreal** tab, click **Create UE project**, then **Build city in Unreal**.
- Expected: the build finishes with a short report (placed actors, dressing, placement check). Without
  Visual Studio there is a warning line "static-mesh terrain, no Landscape". Open the project in the
  Unreal Editor (Open in Unreal) and look around the city.

## If a step fails

Send these (a zip is fine):

1. Which step, what you clicked, and what you saw (a screenshot of the message helps).
2. The output of `ap doctor` (copy the Command Prompt text).
3. The log folder: `%LOCALAPPDATA%\terraformer\logs` (all of it; it has the app, installer and
   ComfyUI logs).
4. For a Blender or Unreal step: the newest `*.log` files in
   `%LOCALAPPDATA%\terraformer\projects\test-city\site\build` (Greybox, Shots) or
   `%LOCALAPPDATA%\terraformer\projects\test-city\export\ue` (Unreal).
5. The text in the Terraformer console window (right-click the title bar › Edit › Select All, then Enter
   to copy).
6. Your Windows version (`winver`), GPU and driver version (`nvidia-smi`), Blender and Unreal versions.

The project and logs only hold the test city you made, but look through them before sending if you
used your own names.
