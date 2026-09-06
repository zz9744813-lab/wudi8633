"""影像相学分析服务（面相 / 掌纹）。

对应工程方案：
- 第 8 节 掌纹系统 / 第 9 节 面相系统 / 第 64 节 隐私

隐私边界（铁律，勿改）：
1. 原图只在内存 + 临时文件存活一次分析周期，**分析结束立即删除**；
2. 特征数值结果只回传给调用者，**不写入数据库**；
3. 云端视觉模型只有在用户**当次显式勾选**时才收到原图
   （且服务器端 `ENABLE_CLOUD_VISION` 必须为 true）；
4. 禁止从面相推断健康、寿命、智力、人格定性等敏感结论（第 9 节）。
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings

UPLOAD_DIR = Path("data/uploads")

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_BYTES = 8 * 1024 * 1024  # 8MB


class ImagingError(Exception):
    """可回给前端的业务错误（400 级）。"""


def _write_temp(data: bytes, ext: str) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"scan-{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    return path


# ----------------------------------------------------------------------
# 确定性解读（本地特征 → 文案，不过 LLM，不上云）
# ----------------------------------------------------------------------
def _palm_lines(f: dict[str, Any]) -> list[str]:
    if not f.get("detected"):
        return [
            "未识别到清晰手部轮廓。建议：掌心朝上摊平、五指自然分开、"
            "光线均匀、镜头垂直对准手掌后重拍。"
        ]
    lines: list[str] = []
    life, head, heart = (
        f.get("life_line") or {},
        f.get("head_line") or {},
        f.get("heart_line") or {},
    )

    c, length = life.get("continuity", 0.0), life.get("length_ratio", 0.0)
    if c >= 0.55 and length >= 0.55:
        lines.append("生命线：纹路连贯绵长，传统相学视为元气稳、耐力足的征象。")
    elif c < 0.4:
        lines.append("生命线：偏断续浅淡，传统相学视为元气起伏的提醒——规律作息比纹路本身更要紧。")
    else:
        lines.append("生命线：深浅适中、走势平稳，传统相学视为元气平顺。")

    c, length = head.get("continuity", 0.0), head.get("length_ratio", 0.0)
    curve = head.get("curvature", 0.0)
    if length >= 0.55:
        style = "偏感性、能沉进去想事" if curve >= 0.15 else "偏直、务实讲道理"
        lines.append(f"智慧线：纵深够长，思维{style}，传统相学视为谋定后动的征象。")
    else:
        lines.append("智慧线：偏短促，传统相学视为反应快、决断型；重要决定不妨多想一层。")

    c = heart.get("continuity", 0.0)
    length = heart.get("length_ratio", 0.0)
    if c >= 0.5 and length >= 0.5:
        lines.append("感情线：清晰而长，传统相学视为情感表达稳定、重诺的征象。")
    else:
        lines.append("感情线：浅淡或断续，传统相学视为情感内敛、慢热——不是没有，是不外露。")

    ratio = f.get("palm_width_ratio", 0.0)
    # 掌宽/掌长语义（mediapipe 真测量，典型 0.6~0.8；旧「掌宽/图宽」阈值已废）
    if ratio >= 0.78:
        lines.append("掌型：宽厚方正，传统相学称「土形掌」，重执行、靠得住。")
    elif ratio and ratio <= 0.66:
        lines.append("掌型：窄长，传统相学称「木形掌」，善思考、重条理。")
    return lines


def _face_lines(f: dict[str, Any]) -> list[str]:
    if not f.get("detected"):
        return [
            "未检测到正脸。建议：正面免冠、五官无遮挡、光线均匀、"
            "镜头与面部平齐后重拍。"
        ]
    lines: list[str] = []
    halves = f.get("three_halves") or {}
    if halves:
        upper = halves.get("upper", 0.0)
        middle = halves.get("middle", 0.0)
        lower = halves.get("lower", 0.0)
        best = max((("上庭", upper), ("中庭", middle), ("下庭", lower)), key=lambda x: x[1])
        worst = min((("上庭", upper), ("中庭", middle), ("下庭", lower)), key=lambda x: x[1])
        if best[1] - worst[1] < 0.08:
            lines.append("三庭：上中下三停匀称，传统相学视为一生节奏平稳、起伏不剧的征象。")
        else:
            focus = {"上庭": "早年学习运", "中庭": "中年行动力", "下庭": "晚年根基"}[best[0]]
            lines.append(
                f"三庭：{best[0]}偏饱满，传统相学视为{focus}较顺的征象；"
                f"{worst[0]}偏窄，对应阶段的功课补在准备与耐心上。"
            )
    # 五眼（FaceLandmarker 真测量：五段各/脸宽；传统口径眼距≈一只眼宽）
    five = f.get("five_eyes") or {}
    eye_l, eye_r = five.get("left_eye", 0.0), five.get("right_eye", 0.0)
    bridge = five.get("nose_bridge", 0.0)
    eye_avg = (eye_l + eye_r) / 2 if (eye_l + eye_r) else 0.0
    if eye_avg and bridge:
        if abs(eye_l - eye_r) / eye_avg <= 0.12:
            lines.append("五眼：双眼左右对称，传统相学视为心性平和、行事有度的征象。")
        else:
            lines.append("五眼：双眼宽度略有不对称，传统相学视为性情有侧重的提醒。")
        if 0.9 <= bridge / eye_avg <= 1.15:
            lines.append("眼距：约一只眼宽，传统相学视为中和之相、审势公允。")
        elif bridge / eye_avg > 1.15:
            lines.append("眼距：偏宽，传统相学视为视野宽、慢热而大条的征象。")
        else:
            lines.append("眼距：偏窄，传统相学视为专注敏锐、先做后想的征象。")
    forehead = f.get("forehead_ratio", 0.0)
    if forehead >= 0.34:
        lines.append("额部：宽阔饱满，传统相学视为思维开阔、早慧的征象。")
    elif forehead and forehead <= 0.26:
        lines.append("额部：偏窄，传统相学视为实干先行、边做边学的征象。")
    ratio = f.get("face_width_height_ratio", 0.0)
    if ratio >= 0.78:
        lines.append("轮廓：面型偏方阔，传统相学视为行事稳、抗压的征象。")
    elif ratio and ratio <= 0.62:
        lines.append("轮廓：面型偏长秀，传统相学视为心思细、感受力强的征象。")
    return lines


def analyze_local(data: bytes, ext: str, kind: str) -> dict[str, Any]:
    """本地 CV 分析：写临时文件 → 提取特征 → 立即删除原图。

    返回 {detected, features, reading[]}。reading 为确定性文案（不过 LLM）。
    """
    from app.core.face.cv import extract_face_features
    from app.core.palm.cv import extract_palm_features

    path = _write_temp(data, ext)
    try:
        if kind == "palm":
            features = extract_palm_features(str(path)).to_dict()
            lines = _palm_lines(features)
        else:
            features = extract_face_features(str(path)).to_dict()
            lines = _face_lines(features)
    finally:
        # 隐私第 64 节：原图分析后即焚，无论成败
        path.unlink(missing_ok=True)

    return {
        "detected": bool(features.get("detected")),
        "features": features,
        "reading": lines,
    }


# ----------------------------------------------------------------------
# 云端视觉详批（仅当：用户当次勾选 use_cloud 且服务器 ENABLE_CLOUD_VISION=true）
# ----------------------------------------------------------------------
_VISION_RULES = (
    "你是传统相学的参考解说员。规则：只描述与民间传统相学口径一致的观察；"
    "不得推断健康、寿命、疾病、智力、人格定性或任何敏感属性；"
    "不作吉凶断言，负面观察必须转化为可执行的生活建议；"
    "结尾注明「传统相学参考，仅供对照」。120 字以内。"
)


def cloud_reading(data: bytes, mime: str, kind: str) -> dict[str, Any]:
    """把原图发给视觉模型做传统相学口径的详批。调用方保证已获用户当次勾选。"""
    if not get_settings().ENABLE_CLOUD_VISION:
        return {"used": False, "reason": "服务器未开启云端视觉（ENABLE_CLOUD_VISION=false）"}

    from app.providers.base import LLMRequest, get_provider

    provider = get_provider("vision")
    if hasattr(provider, "name") and provider.name == "mock":
        return {"used": False, "reason": "视觉模型未配置（VISION_* 为空）"}

    target = (
        "这张照片是手掌特写。请按民间掌纹学口径观察三大主线（生命/智慧/感情线）"
        "的清晰度、长短、是否断续，并给出克制的参考解读。"
        if kind == "palm"
        else "这张照片是人脸正面照。请按民间面相学口径观察三庭五眼的比例与气色，"
        "并给出克制的参考解读。"
    )
    req = LLMRequest(
        messages=[
            {"role": "system", "content": _VISION_RULES},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": target},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"
                        },
                    },
                ],
            }
        ],
        temperature=0.3,
        max_tokens=600,
    )
    resp = provider.complete(req)
    if not resp.ok or not resp.content.strip():
        return {"used": False, "reason": f"视觉模型调用失败：{resp.error or '空响应'}"}
    return {
        "used": True,
        "text": resp.content.strip(),
        "model": resp.model,
        "duration_ms": resp.duration_ms,
        "system_prompt": _VISION_RULES,
    }


# ----------------------------------------------------------------------
# 特征存档（round 17）：原图即焚不变，派生特征经用户确认后入库供长期参照
# 与信号闭环复用。表中 local_image_path 恒为 None（绝不存原图路径）。
# ----------------------------------------------------------------------
def save_record(
    session, user_id: int, kind: str, features: dict, detected: bool, hand: str = "right"
) -> int:
    from app.models.metaphysical import FaceFeature, PalmFeature

    detected = bool(features.get("detected", detected))
    kwargs = dict(
        user_id=user_id,
        features=features,
        degraded=not detected,
        degrade_reason=None if detected else "未检出",
        local_image_path=None,  # 隐私铁律：原图即焚，路径不入库
    )
    if kind == "palm":
        rec = PalmFeature(engine_version="palm-cv-0.2.0", hand=hand, **kwargs)
    else:
        rec = FaceFeature(engine_version="face-cv-0.2.0", **kwargs)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return rec.id


def list_records(session, user_id: int, kind: str, limit: int = 12) -> list[dict]:
    """历史特征列表；解读文案由特征确定性重生成（_palm_lines/_face_lines）。"""
    from sqlalchemy import desc

    from app.models.metaphysical import FaceFeature, PalmFeature
    from sqlmodel import select

    model = PalmFeature if kind == "palm" else FaceFeature
    rows = session.exec(
        select(model)
        .where(model.user_id == user_id)
        .order_by(desc(model.captured_at))
        .limit(limit)
    ).all()
    out = []
    for r in rows:
        feats = r.features or {}
        lines = _palm_lines(feats) if kind == "palm" else _face_lines(feats)
        out.append(
            {
                "id": r.id,
                "kind": kind,
                "captured_at": r.captured_at.isoformat(),
                "detected": not r.degraded,
                "hand": getattr(r, "hand", "") or "",
                "features": feats,
                "reading": lines,
            }
        )
    return out


def purge_records(session, user_id: int, kind: str | None = None) -> int:
    from app.models.metaphysical import FaceFeature, PalmFeature
    from sqlmodel import select

    n = 0
    for model, k in ((PalmFeature, "palm"), (FaceFeature, "face")):
        if kind and k != kind:
            continue
        rows = session.exec(
            select(model).where(model.user_id == user_id)
        ).all()
        for r in rows:
            session.delete(r)
            n += 1
    session.commit()
    return n
