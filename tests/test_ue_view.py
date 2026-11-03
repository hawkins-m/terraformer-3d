"""What the Unreal editor shows on open: the World Partition region saved as loaded, and the camera."""
from types import SimpleNamespace

from asset_pipeline import ue, ue_view

WORLD = "/Game/AP/Maps/CityLarge.CityLarge"


def _layout(size, bounds=(-4000.0, -3000.0, 4000.0, 2000.0), radius=None):
    return SimpleNamespace(city=SimpleNamespace(size=size, bounds=list(bounds), detail_radius_m=radius, enclosure=None, nodes=[]))


def test_a_standard_city_has_no_region_a_large_one_the_bounds_a_metro_one_the_detail_radius():
    assert ue_view.region_box(_layout("standard")) is None
    big = ue_view.region_box(_layout("large"))
    assert big == {"min": [-420000.0, -220000.0, -1e7], "max": [420000.0, 320000.0, 1e7]}      # Blender Y is UE -Y, 200 m margin
    metro = ue_view.region_box(_layout("metro", (-7000.0, -5000.0, 7000.0, 5000.0), radius=2500.0))
    assert metro == {"min": [-250000.0, -250000.0, -1e7], "max": [250000.0, 250000.0, 1e7]}   # centred on the bounds' middle


def test_merging_a_region_keeps_other_worlds_and_settings_and_replaces_its_own():
    box = {"min": [1, 2, 3], "max": [4, 5, 6]}
    old = ('(("/Game/Other.Other",(LoadedEditorRegions=((Min=(X=9.0,Y=9.0,Z=9.0),Max=(X=9.0,Y=9.0,Z=9.0),IsValid=True)),'
           'bForceDisableDynamicLoadingRangeScaling=True)))')
    once = ue_view.merge_world(old, WORLD, box)
    assert once.count("LoadedEditorRegions") == 2 and "bForceDisableDynamicLoadingRangeScaling=True" in once
    twice = ue_view.merge_world(once, WORLD, {"min": [7, 7, 7], "max": [8, 8, 8]})
    assert twice.count("LoadedEditorRegions") == 2 and "X=7.0" in twice and "X=1.0" not in twice and "X=9.0" in twice


def test_the_settings_file_gets_its_section_and_key_added_or_updated(tmp_path):
    ini = ue_view.user_settings(tmp_path)
    assert ini.parts[-3:] == ("Config", "LinuxEditor", "EditorPerProjectUserSettings.ini") or ue_view.oscompat.IS_WINDOWS
    box = {"min": [1, 2, 3], "max": [4, 5, 6]}
    assert ue_view.set_loaded_region(ini, WORLD, box)
    assert not ue_view.set_loaded_region(ini, WORLD, box)                     # unchanged
    ini.write_text("[Other]\nA=1\n" + ini.read_text())
    assert not ue_view.set_loaded_region(ini, WORLD, {"min": [0, 0, 0], "max": [9, 9, 9]}, keep_existing=True)
    assert ue_view.set_loaded_region(ini, WORLD, {"min": [0, 0, 0], "max": [9, 9, 9]})
    text = ini.read_text()
    assert text.startswith("[Other]\nA=1\n") and text.count(ue_view.SECTION) == 1 and "X=9.0" in text
    assert ue_view.user_settings(tmp_path, windows=True).parts[-2] == "WindowsEditor"
