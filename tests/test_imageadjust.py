"""Cover-editor colour adjustments (pure numpy)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from spindle.imageadjust import adjust


def img(b, g, r, h=8, w=8):
    a = np.zeros((h, w, 4), np.uint8)
    a[..., 0], a[..., 1], a[..., 2], a[..., 3] = b, g, r, 255
    return a


def test_all():
    base = img(60, 120, 200)
    assert (adjust(base) == base).all()
    assert adjust(base, brightness=50)[0, 0, 2] > 200 and adjust(base, brightness=-50)[0, 0, 0] < 60
    hi, lo = adjust(base, contrast=60)[0, 0], adjust(base, contrast=-100)[0, 0]
    assert hi[2] > 200 and hi[0] < 60, hi                      # further from mid-grey
    assert abs(int(lo[0]) - 128) <= 1 and abs(int(lo[2]) - 128) <= 1, lo   # -100 contrast = flat grey
    gray = adjust(base, saturation=-100)[0, 0]
    assert abs(int(gray[0]) - int(gray[2])) <= 1, gray        # -100 saturation = black & white
    vivid = adjust(base, saturation=80)[0, 0]
    assert int(vivid[2]) - int(vivid[0]) > 200 - 60, vivid     # channels pushed further apart
    assert (adjust(base, brightness=100)[..., 3] == 255).all()  # alpha untouched
    # auto levels: a dull 80..170 gradient becomes full range
    g = np.zeros((10, 64, 4), np.uint8); v = np.linspace(80, 170, 64).astype(np.uint8)
    g[..., 0] = g[..., 1] = g[..., 2] = v; g[..., 3] = 255
    a = adjust(g, auto=True)
    assert a[..., 0].min() <= 2 and a[..., 0].max() >= 253, (a[..., 0].min(), a[..., 0].max())
    # sharpen raises edge contrast and leaves flat areas alone
    e = img(100, 100, 100, 8, 8); e[:, 4:, :3] = 160
    s = adjust(e, sharpen=100)
    assert s[3, 3, 0] < 100 and s[3, 4, 0] > 160, (s[3, 3, 0], s[3, 4, 0])
    assert s[3, 0, 0] == 100
    print("ok image adjustments")


if __name__ == "__main__":
    test_all()
