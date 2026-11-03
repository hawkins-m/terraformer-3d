# Terraformer v0.1.1

Everything since v0.1.0: cities you can describe and view in 3D, bigger cities, more building types, and a
one-click path from a new project to a dressed Unreal level.

**Demo video:** [watch on YouTube](https://www.youtube.com/watch?v=R8xaOrSxGe4)

## Describe the terrain
- Type a landscape and Terraformer builds it: a terrain spec plus a composer.
- The local vision model writes the spec; without it, a keyword parser does.
- It keeps the picked terrain's base and places landforms around the city.
- Edit shows the spec as JSON.

## Extended building types
- Castle (the centrepiece of walled-castle and star-fortress cities), church, medical center, airport,
  power grid, factory, warehouse, depot, refinery, houses, cottages, condos, penthouses and chateaux.
- On for new projects and rolls; off for existing projects.

## Roll a city
- Inputs stay visible while a roll is applying, and Random seed ticks.
- Roll and Apply use exactly what the dropdowns show.
- Each project has its own clean state; new projects start on classic.
- Layouts are listed classic first, then A to Z.
- The classic preset is reworked into a Chicago-style coastal metro.

## See the city
- An in-app 3D city viewer (offline) with a top-down plan mode.
- City sizes Standard, Large and Metro (Metro is experimental).
- A background region with farmland, hedgerows, farms, forest and villages.

## Unreal
- One click takes a new project to a dressed Unreal level.
- Unreal opens with the city loaded and the camera above downtown (large and metro levels are World
  Partition levels: the build saves the region the editor loads).
- The build summary shows library assets placed and stand-ins separately.

## Editing and projects
- Blender greybox edits for every building: move, resize, remove and duplicate.
- A project manager: File > Manage projects…, or `ap project`.

## Platforms and repo
- Native Windows beta (greybox path). CI runs on Ubuntu and Windows.
- Repo tidy: licenses, credits and a "How ComfyUI is used" section.

## Known issues
- A large or metro level's region can be loaded by hand if the editor opens empty (see the README).
- The Water plugin's ocean can't run in the headless build; the Simple sea is used.
- The generated-terrain Landscape export builds on Linux engines only.
- Native Windows is a beta.

Full changes: [v0.1.0...v0.1.1](https://github.com/hawkins-m/terraformer-3d/compare/v0.1.0...v0.1.1).
