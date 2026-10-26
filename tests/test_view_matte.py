"""Stage 3's floor and shadow matte (s3_views.matte_ground): a soft grey shadow beside an object's base
goes to white; the object, a textured part SAM's mask missed, and anything higher up stay; a mask that
misses much of its band changes nothing."""
import numpy as np
from PIL import Image

from asset_pipeline import segment
from asset_pipeline.stages import s3_views


def _sheet():
    px = np.full((200, 200, 3), 255, np.uint8)
    px[150:170, 20:180] = 200                               # a soft grey shadow strip under it
    px[40:160, 60:140] = (180, 150, 120)                    # the object (brown), base at y=159
    px[150:160, 145:155] = np.where(np.indices((10, 10)).sum(0)[..., None] % 2, 60, 230)   # textured missed part
    px[20:30, 160:170] = 200                                # grey, but high up: not ground
    mask = np.zeros((200, 200), bool)
    mask[40:160, 60:140] = True
    return px, mask


def test_the_shadow_goes_and_the_object_stays():
    px, mask = _sheet()
    n = s3_views.matte_ground(px, mask, 0, 200)
    assert n > 0
    assert (px[165, 30] == 255).all() and (px[165, 170] == 255).all()       # shadow beside and under it
    assert (px[100, 100] == (180, 150, 120)).all()                            # the object
    assert (px[150:160, 145:155].min(axis=2) < 100).any()                     # the textured part SAM missed
    assert (px[25, 165] == 200).all()                                         # high up: untouched


def test_a_mask_that_misses_the_view_changes_nothing():
    px, _ = _sheet()
    part = np.zeros((200, 200), bool)
    part[40:160, 60:80] = True                                                # SAM found a sliver of it
    before = px.copy()
    assert s3_views.matte_ground(px, part, 0, 200) == -1 and (px == before).all()


def test_band_crops_matte_only_when_asked():
    px, mask = _sheet()
    det = segment.Detection(mask=mask, bbox=(60, 40, 140, 160), area_frac=float(mask.mean()), touches_border=False)
    sheet = Image.fromarray(px)
    plain = s3_views.band_crops(sheet, [det])[0]
    counts = []
    matted = s3_views.band_crops(sheet, [det], matte=True, matted=counts)[0]
    assert counts and counts[0] > 0
    assert plain.size != matted.size                     # the shadow strip no longer widens the trimmed view
