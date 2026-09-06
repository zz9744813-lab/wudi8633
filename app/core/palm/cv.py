"""掌纹特征提取（OpenCV 实现）。

对应工程方案：
- 第 8 节 掌纹系统

流程（第 8 节要求）：
    原图 → 关键点检测 → 透视/角度校正 → 掌纹检测 → 分类 →
    长度/曲率/断点/深度特征 → 结构化 PalmFeatures

而不是：照片 → Vision LLM → 直接算命

实现说明：
    使用 OpenCV 传统 CV（肤色分割 + 手部轮廓 + 掌纹线边缘检测），
    不依赖 mediapipe 模型文件。输出第 8.1 节格式的结构化特征。

隐私（第 64 节）：
    原始照片本地保存，不入库、不上传。本模块只输出数值特征。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass
class PalmFeatures:
    """第 8.1 节 PalmFeatures 结构化输出。"""

    life_line: dict[str, float] = field(default_factory=dict)
    head_line: dict[str, float] = field(default_factory=dict)
    heart_line: dict[str, float] = field(default_factory=dict)
    hand_ratio: float = 0.0
    palm_width_ratio: float = 0.0
    detected: bool = False
    measure_source: str = ""  # hands / skin（诚实标注测量来源）

    def to_dict(self) -> dict[str, Any]:
        return {
            "life_line": self.life_line,
            "head_line": self.head_line,
            "heart_line": self.heart_line,
            "hand_ratio": round(self.hand_ratio, 3),
            "palm_width_ratio": round(self.palm_width_ratio, 3),
            "measure_source": self.measure_source,
            "detected": self.detected,
        }


def extract_palm_features(image_path: str) -> PalmFeatures:
    """从掌纹照片提取 PalmFeatures。

    无法检测时返回 detected=False 的空特征（调用方应降级，不硬猜）。
    """
    features = PalmFeatures()
    path = Path(image_path)
    if not path.exists():
        return features

    img = cv2.imread(str(path))
    if img is None:
        return features

    # 0. 首选：MediaPipe Hands 真掌宽比（掌宽/掌长，解剖学 0.6~0.8，
    #    与拍摄距离无关——旧实现 palm_width_ratio=掌宽/图宽 随取景漂移，数量级核验战果）
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        h_img, w_img = img.shape[:2]
        model_path = Path(__file__).parent.parent / "face" / "assets" / "hand_landmarker.task"
        landmarker = mp_vision.HandLandmarker.create_from_options(
            mp_vision.HandLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_hands=1,
                min_hand_detection_confidence=0.5,
            )
        )
        try:
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            res = landmarker.detect(mp_img)
        finally:
            landmarker.close()
        if not res.hand_landmarks:
            features.measure_source = "hands-noface"
        if res.hand_landmarks:
            lm = res.hand_landmarks[0]

            def _px(i):
                return (lm[i].x * w_img, lm[i].y * h_img)

            wrist, idx_mcp, pinky_mcp, mid_mcp = _px(0), _px(5), _px(17), _px(9)
            palm_w = ((idx_mcp[0] - pinky_mcp[0]) ** 2 + (idx_mcp[1] - pinky_mcp[1]) ** 2) ** 0.5
            palm_l = ((mid_mcp[0] - wrist[0]) ** 2 + (mid_mcp[1] - wrist[1]) ** 2) ** 0.5
            if palm_l > 0:
                features.hand_ratio = palm_w / palm_l  # 真掌宽/掌长
                features.palm_width_ratio = palm_w / palm_l
                features.measure_source = "hands"
                features.detected = True
                xs = [lm[i].x * w_img for i in range(21)]
                ys = [lm[i].y * h_img for i in range(21)]
                x0, x1 = max(0, int(min(xs)) - 10), min(w_img, int(max(xs)) + 10)
                y0, y1 = max(0, int(min(ys)) - 10), min(h_img, int(max(ys)) + 10)
                edges = _edges_of(img[y0:y1, x0:x1])
                h_roi, w_roi = edges.shape
                features.life_line = _measure_line_group(edges, vertical=True, img_h=h_roi)
                features.head_line = _measure_line_group(edges, vertical=False, img_h=w_roi)
                features.heart_line = _measure_line_group(
                    edges, vertical=False, img_h=w_roi, upper=True
                )
                return features
    except Exception as exc:
        features.measure_source = f"hands-error:{exc}"  # Hands 不可用 → 回退

    # 1. 肤色分割（YCrCb 空间）提取手部（回退路径）
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    skin = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

    contours, _ = cv2.findContours(skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return features

    hand = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(hand)
    img_area = img.shape[0] * img.shape[1]
    if area / img_area < 0.03:
        return features  # 肤色区域太小，不是手掌

    # 2. 手型比例（回退：仅框比例；掌宽/图宽随取景漂移，宁缺毋假）
    x, y, w, h = cv2.boundingRect(hand)
    features.hand_ratio = w / h if h > 0 else 0.0
    if not features.measure_source:
        features.measure_source = "skin"

    # 3. 掌纹线（ROI 内 Canny 边缘 → 最长线段的长度/曲率/连续性近似）
    roi = img[y : y + h, x : x + w]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    edges = cv2.Canny(blurred, 40, 120)

    # 用形态学连接掌纹线
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    # 按角度分三组线（生命线/智慧线/感情线的近似方向）
    features.life_line = _measure_line_group(edges, vertical=True, img_h=h)
    features.head_line = _measure_line_group(edges, vertical=False, img_h=w)
    features.heart_line = _measure_line_group(edges, vertical=False, img_h=w, upper=True)

    features.detected = True
    return features


def _edges_of(roi_img):
    """ROI 灰度 → Canny 边缘（掌纹线测量的公共入口）。"""
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 0)
    return cv2.dilate(cv2.Canny(blurred, 40, 120), np.ones((3, 3), np.uint8), iterations=1)


def _measure_line_group(
    edges: np.ndarray, *, vertical: bool, img_h: int, upper: bool = False
) -> dict[str, float]:
    """测量一组掌纹线的长度比/曲率/连续性（近似）。"""
    h, w = edges.shape
    roi = edges[: h // 2, :] if upper else edges

    # 投影统计线密度：竖直组按行投影（nz=行号 → span=纵向线长），
    # 水平组按列投影（nz=列号 → span=横向线长）。
    # 旧实现两轴写反，单条线的 length_ratio 恒为 0（数量级核验战果，勿回退）。
    if vertical:
        density = roi.sum(axis=1)
    else:
        density = roi.sum(axis=0)

    nz = [i for i, v in enumerate(density) if v > 0]
    if not nz:
        return {}

    span = nz[-1] - nz[0]
    length_ratio = min(1.0, span / max(1, img_h if vertical else w))

    # 曲率：线密度的标准差（波动大 → 曲率大）
    vals = [density[i] for i in nz]
    mean_v = sum(vals) / len(vals) if vals else 0
    std = (sum((v - mean_v) ** 2 for v in vals) / len(vals)) ** 0.5 if vals else 0
    curvature = min(1.0, std / 50.0)

    # 连续性：非零区间的占比
    continuity = min(1.0, len(nz) / max(1, span + 1))

    return {
        "length_ratio": round(length_ratio, 3),
        "continuity": round(continuity, 3),
        "curvature": round(curvature, 3),
    }
