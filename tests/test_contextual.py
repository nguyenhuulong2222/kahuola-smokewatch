"""Contextual test (MODIS parameters, off by default) on synthetic scenes."""
import numpy as np
import pytest

from smokewatch import fire
from smokewatch.goes import Frame
from tests.test_fire import T0, refs, scene


@pytest.fixture
def ctx():
    return {**fire.FP, "contextual": True}


def _warm_patch(r, c, half=6, dk=8.0):
    """Broad uniform warming (sun on dry ground): every pixel +dk K in C07."""
    f = scene(T0)
    b = {k: v.copy() for k, v in f.bands.items()}
    b["C07"][r - half:r + half + 1, c - half:c + half + 1] += dk
    b["C14"][r - half:r + half + 1, c - half:c + half + 1] += 1.0
    return Frame(time=f.time, key=f.key, bands=b, lats=f.lats, lons=f.lons)


def test_contextual_is_off_by_default():
    assert fire.FP["contextual"] is False


def test_broad_warming_rejected_only_with_contextual(ctx):
    bg = fire.build_background(refs())
    land = fire.land_mask(scene(T0))
    col, row = fire._px(scene(T0), 19.6, -155.5)          # interior of Hawaiʻi Island
    r, c = int(round(row)), int(round(col))
    assert land[r - 6:r + 7, c - 6:c + 7].all()
    f = _warm_patch(r, c)
    assert fire.thermal_anomaly(f, bg)[r, c] == 1          # absolute test fires on the patch
    assert fire.thermal_anomaly(f, bg, ctx)[r, c] == 0     # neighbours warmed too: not a fire


def test_compact_hotspot_kept_with_contextual(ctx):
    bg = fire.build_background(refs())
    cands, anom, _ = fire.detect_fires(scene(T0, fire_px=(110, 95)), bg, ctx)
    assert len(cands) == 1 and anom[110:112, 95:97].all()
    assert fire.detect_fires(scene(T0), bg, ctx)[0] == []
