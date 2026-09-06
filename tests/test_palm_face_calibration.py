"""相法测量数量级校准（round 17 用户指令：面相核验校准、掌纹尽量做）。

核验对象是**测量数学层**（不依赖照片检测）：
1. 面相：canonical 关键点集（构造三庭相等/五眼相等的标准人脸）→
   三庭必须 ≈1/3、五眼 ≈1/5、宽高比精确——旧实现这里全是写死常数（假测量）；
2. 掌纹线测量：已知长度/断续的合成纹图 →
   length_ratio/continuity 必须与构造值同数量级（旧实现投影轴写反，恒为 0）；
3. 解剖学边界：随机扰动关键点，比例仍应落在人体合理范围（0.2~0.5 三庭、
   0.1~0.35 五眼段）——测量不应产生生物学不可能的输出。
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from app.core.face.cv import compute_face_ratios
from app.core.palm.cv import _measure_line_group


def _canonical_pts():
    H, W = 300.0, 216.0  # 脸高:脸宽 = 1.39（成人典型）
    return {
        "top": (W / 2, 0), "chin": (W / 2, H),
        "face_left": (0, H * 0.55), "face_right": (W, H * 0.55),
        "brow_l": (W * 0.4, H / 3), "brow_r": (W * 0.6, H / 3),
        "nose_base": (W / 2, H * 2 / 3),
        "eye_l_out": (W * 0.2, H * 0.38), "eye_l_in": (W * 0.4, H * 0.38),
        "eye_r_in": (W * 0.6, H * 0.38), "eye_r_out": (W * 0.8, H * 0.38),
    }


def test_face_ratios_canonical_thirds_and_fifths():
    r = compute_face_ratios(_canonical_pts())
    assert r["detected"]
    for k in ("upper", "middle", "lower"):
        assert abs(r["three_halves"][k] - 1 / 3) < 0.01, (k, r["three_halves"])
    for k, v in r["five_eyes"].items():
        assert abs(v - 0.2) < 0.01, (k, r["five_eyes"])
    assert abs(r["face_width_height_ratio"] - 0.72) < 0.01


def test_face_ratios_stay_in_anatomical_range_under_noise():
    """随机扰动关键点：输出必须仍在人体合理范围（数量级防线）。"""
    rng = random.Random(2026)
    for _ in range(200):
        pts = {}
        for k, (x, y) in _canonical_pts().items():
            pts[k] = (x + rng.uniform(-8, 8), y + rng.uniform(-6, 6))
        r = compute_face_ratios(pts)
        assert r["detected"]
        for v in r["three_halves"].values():
            assert 0.2 <= v <= 0.5, r["three_halves"]
        for v in r["five_eyes"].values():
            assert 0.1 <= v <= 0.35, r["five_eyes"]
        assert 0.5 <= r["face_width_height_ratio"] <= 1.1


def test_palm_line_measurement_horizontal_continuous():
    img = np.zeros((100, 200), np.uint8)
    img[50, 10:110] = 255  # 连续横线 x∈[10,110)
    m = _measure_line_group(img, vertical=False, img_h=200)
    assert 0.4 <= m["length_ratio"] <= 0.6, m  # 100/200
    assert m["continuity"] > 0.95, m


def test_palm_line_measurement_broken_line():
    img = np.zeros((100, 200), np.uint8)
    img[50, 10:110:4] = 255  # 每 4 像素 1 点 → 连续性 ≈ 0.25
    m = _measure_line_group(img, vertical=False, img_h=200)
    assert 0.15 <= m["continuity"] <= 0.4, m


def test_palm_line_measurement_vertical():
    img = np.zeros((100, 200), np.uint8)
    img[20:80, 100] = 255  # 连续竖线 y∈[20,80)
    m = _measure_line_group(img, vertical=True, img_h=100)
    assert 0.5 <= m["length_ratio"] <= 0.7, m  # 60/100
    assert m["continuity"] > 0.95, m


def test_palm_line_measurement_empty():
    img = np.zeros((100, 200), np.uint8)
    m = _measure_line_group(img, vertical=False, img_h=200)
    assert m == {}


@pytest.mark.skipif(
    not __import__("importlib.util", fromlist=["util"]).find_spec("mediapipe"),
    reason="mediapipe 未安装",
)
def test_facemesh_rejects_blank_image_honestly():
    """FaceMesh 集成冒烟：无脸图必须诚实 detected=False，绝不编造比例。"""
    import cv2
    import numpy as np

    from app.core.face.cv import extract_face_features

    img = np.full((240, 240, 3), 128, np.uint8)
    cv2.imwrite(str(__import__("pathlib").Path("_tmp_blank.png")), img)
    try:
        f = extract_face_features("_tmp_blank.png")
        assert not f.detected
        # 无脸时不得有「写死常数比例」
        assert not f.three_halves and not f.five_eyes
    finally:
        __import__("pathlib").Path("_tmp_blank.png").unlink(missing_ok=True)
