"""面相几何测量（第 9 节）。

round 17 数量级核验战果：旧实现的「三庭/五眼/眉眼/鼻唇颌」全部是 Haar 框内
等分**写死常数**（t1=t2=t3=h/3、五眼恒 0.2）——不管传谁的照片解读都是
「三庭匀称、五眼匀称」，千人一面假测量。现改为：

1. MediaPipe FaceMesh 468 关键点 → 真三庭（发际10/眉105,334/鼻底2/下巴152）、
   真五眼（234,33,133,362,263,454）、真面宽高比；
2. FaceMesh 不可用时回退 Haar，**只输出真可测的框宽高比**，其余字段留空
   （宁缺毋假——写死常数已删除，勿回退）；
3. 比例计算抽成纯函数 compute_face_ratios，供 canonical 关键点集的数量级
   校准测试（tests/test_palm_face_calibration.py：三庭≈1/3、五眼≈1/5）。

只输出几何比例，绝不输出任何人格/健康/属性推断（第 9 节禁止）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# FaceMesh 关键点索引（标准 468 拓扑）
LM_TOP = 10          # 前额发际
LM_CHIN = 152        # 下巴底
LM_FACE_LEFT = 234   # 左脸颊缘
LM_FACE_RIGHT = 454  # 右脸颊缘
LM_BROW_L = 105      # 左眉
LM_BROW_R = 334      # 右眉
LM_NOSE_BASE = 2     # 鼻底
LM_EYE_L_OUT, LM_EYE_L_IN = 33, 133   # 左眼外/内眦
LM_EYE_R_IN, LM_EYE_R_OUT = 362, 263  # 右眼内/外眦


@dataclass
class FaceFeatures:
    detected: bool = False
    face_width_height_ratio: float = 0.0
    three_halves: dict[str, float] = field(default_factory=dict)  # 上/中/下三庭
    five_eyes: dict[str, float] = field(default_factory=dict)     # 五眼比例
    forehead_ratio: float = 0.0
    eyebrow_eye_position: float = 0.0
    nose_ratio: float = 0.0
    lip_ratio: float = 0.0
    jaw_ratio: float = 0.0
    measure_source: str = ""  # facemesh / haar-frame（诚实标注测量来源）

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "measure_source": self.measure_source,
            "face_width_height_ratio": round(self.face_width_height_ratio, 3),
            "three_halves": {k: round(v, 3) for k, v in self.three_halves.items()},
            "five_eyes": {k: round(v, 3) for k, v in self.five_eyes.items()},
            "forehead_ratio": round(self.forehead_ratio, 3),
            "eyebrow_eye_position": round(self.eyebrow_eye_position, 3),
            "nose_ratio": round(self.nose_ratio, 3),
            "lip_ratio": round(self.lip_ratio, 3),
            "jaw_ratio": round(self.jaw_ratio, 3),
        }


def compute_face_ratios(pts: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """由关键点计算面相几何比例（纯函数，数量级校准的核验对象）。

    pts 键：top/chin/face_left/face_right/brow_l/brow_r/nose_base/
           eye_l_out/eye_l_in/eye_r_in/eye_r_out
    """
    h = abs(pts["chin"][1] - pts["top"][1])
    w = abs(pts["face_right"][0] - pts["face_left"][0])
    if h <= 0 or w <= 0:
        return {"detected": False}

    brow_y = (pts["brow_l"][1] + pts["brow_r"][1]) / 2
    nose_y = pts["nose_base"][1]
    top_y = min(pts["top"][1], pts["chin"][1])

    t1 = max(0.0, brow_y - top_y)
    t2 = max(0.0, nose_y - brow_y)
    t3 = max(0.0, (top_y + h) - nose_y)
    three = {"upper": t1 / h, "middle": t2 / h, "lower": t3 / h}

    eye_l = abs(pts["eye_l_in"][0] - pts["eye_l_out"][0])
    eye_r = abs(pts["eye_r_out"][0] - pts["eye_r_in"][0])
    bridge = abs(pts["eye_r_in"][0] - pts["eye_l_in"][0])
    temple_l = abs(pts["eye_l_out"][0] - pts["face_left"][0])
    temple_r = abs(pts["face_right"][0] - pts["eye_r_out"][0])
    five = {
        "left_temple": temple_l / w,
        "left_eye": eye_l / w,
        "nose_bridge": bridge / w,
        "right_eye": eye_r / w,
        "right_temple": temple_r / w,
    }

    out = {
        "detected": True,
        "face_width_height_ratio": w / h,
        "three_halves": three,
        "five_eyes": five,
        "forehead_ratio": three["upper"],
        # 眉眼位置：眉线到下巴的中点落在中庭下半 → 以 (brow_y 到 chin) / h 计
        "eyebrow_eye_position": (top_y + h - brow_y) / h,
        "nose_ratio": three["middle"],
        "lip_ratio": max(0.0, (top_y + h - nose_y - t3 * 0.45) / h),
        "jaw_ratio": three["lower"],
    }
    return out


def extract_face_features(image_path: str) -> FaceFeatures:
    """从照片提取 FaceFeatures。无法检测时返回 detected=False。"""
    features = FaceFeatures()
    path = Path(image_path)
    if not path.exists():
        return features

    img = cv2.imread(str(path))
    if img is None:
        return features

    # ---- 首选：MediaPipe FaceLandmarker 真关键点测量 ----
    # （mediapipe 1.x 已移除 solutions 旧 API；Tasks API + 内置 face_landmarker.task）
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        h_img, w_img = img.shape[:2]
        model_path = Path(__file__).parent / "assets" / "face_landmarker.task"
        landmarker = mp_vision.FaceLandmarker.create_from_options(
            mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_faces=1,
                min_face_detection_confidence=0.5,
            )
        )
        try:
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB,
                              data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            res = landmarker.detect(mp_img)
        finally:
            landmarker.close()
        if not res.face_landmarks:
            features.measure_source = "facemesh-noface"
            # 落到 Haar 回退再试一次
        if res.face_landmarks:
            lm = res.face_landmarks[0]
            pts = {
                "top": (lm[LM_TOP].x * w_img, lm[LM_TOP].y * h_img),
                "chin": (lm[LM_CHIN].x * w_img, lm[LM_CHIN].y * h_img),
                "face_left": (lm[LM_FACE_LEFT].x * w_img, lm[LM_FACE_LEFT].y * h_img),
                "face_right": (lm[LM_FACE_RIGHT].x * w_img, lm[LM_FACE_RIGHT].y * h_img),
                "brow_l": (lm[LM_BROW_L].x * w_img, lm[LM_BROW_L].y * h_img),
                "brow_r": (lm[LM_BROW_R].x * w_img, lm[LM_BROW_R].y * h_img),
                "nose_base": (lm[LM_NOSE_BASE].x * w_img, lm[LM_NOSE_BASE].y * h_img),
                "eye_l_out": (lm[LM_EYE_L_OUT].x * w_img, lm[LM_EYE_L_OUT].y * h_img),
                "eye_l_in": (lm[LM_EYE_L_IN].x * w_img, lm[LM_EYE_L_IN].y * h_img),
                "eye_r_in": (lm[LM_EYE_R_IN].x * w_img, lm[LM_EYE_R_IN].y * h_img),
                "eye_r_out": (lm[LM_EYE_R_OUT].x * w_img, lm[LM_EYE_R_OUT].y * h_img),
            }
            ratios = compute_face_ratios(pts)
            if ratios.get("detected"):
                features.detected = True
                features.measure_source = "facemesh"
                features.face_width_height_ratio = ratios["face_width_height_ratio"]
                features.three_halves = ratios["three_halves"]
                features.five_eyes = ratios["five_eyes"]
                features.forehead_ratio = ratios["forehead_ratio"]
                features.eyebrow_eye_position = ratios["eyebrow_eye_position"]
                features.nose_ratio = ratios["nose_ratio"]
                features.lip_ratio = ratios["lip_ratio"]
                features.jaw_ratio = ratios["jaw_ratio"]
                return features
    except Exception as exc:  # FaceMesh 不可用/失败 → Haar 回退（来源如实标注）
        features.measure_source = f"facemesh-error:{exc}"

    # ---- 回退：Haar 人脸框 —— 只输出真可测的框宽高比（宁缺毋假）----
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    if not Path(cascade_path).exists():
        cascade_path = str(Path(__file__).parent / "assets" / "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        return features
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    if len(faces) == 0:
        if not features.measure_source:
            features.measure_source = "haar-noface"  # 不覆盖上游来源（回测/诊断需要）
        return features
    x, y, w, h = faces[0]
    features.detected = True
    features.measure_source = "haar-frame"
    features.face_width_height_ratio = w / h if h > 0 else 0.0
    # 三庭/五眼等需要五官关键点，Haar 框给不出真值 —— 留空（宁缺毋假）
    return features
