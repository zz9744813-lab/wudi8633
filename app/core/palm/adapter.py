"""PalmAdapter —— 掌纹信号。

对应工程方案：
- 第 8 节 掌纹系统（原图 → 关键点 → 结构化 PalmFeatures → Rule Engine → PalmSignal）
- 第 14 节 统一 Signal Schema
- 第 57 节 时间尺度约束（掌纹：不使用 日/周，弱 月/年）

CV 实现见 cv.py（OpenCV 传统方法，不依赖 mediapipe 模型文件）。

隐私（第 64 节）：原始照片仅本地，不入库、不上传。
"""

from __future__ import annotations

from typing import Any

from app.core.base import AdapterQuery, MetaphysicalAdapter, registry
from app.schemas.signal import (
    Evidence,
    EvidenceSource,
    Signal,
    SourceType,
)

from .cv import extract_palm_features

ENGINE_VERSION = "palm-0.1.0"


class PalmAdapter(MetaphysicalAdapter):
    source = SourceType.PALM
    engine_name = "palm-cv"
    engine_version = ENGINE_VERSION

    @property
    def available(self) -> bool:
        try:
            import cv2  # noqa: F401

            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    def compute_chart(self, query: AdapterQuery) -> dict[str, Any]:
        """掌纹特征：优先现传照片；无照片时回退最近一次存档特征（round 17）。

        存档特征让掌纹信号无需每次传图即可持续参与预测闭环、积累验证样本。
        """
        if query.image_path:
            try:
                return extract_palm_features(query.image_path).to_dict()
            except Exception:
                pass
        # 存档回退：最近一次 PalmFeature（原图从未入库，仅特征数值）
        if query.session is not None:
            try:
                from sqlmodel import select

                from app.models.metaphysical import PalmFeature

                row = query.session.exec(
                    select(PalmFeature)
                    .where(
                        PalmFeature.user_id == query.user_id,
                        PalmFeature.degraded == False,  # noqa: E712
                    )
                    .order_by(PalmFeature.captured_at.desc())
                ).first()
                if row and row.features:
                    out = dict(row.features)
                    out["from_store"] = True
                    out["store_captured_at"] = row.captured_at.isoformat()
                    return out
            except Exception:
                pass
        return {}

    # ------------------------------------------------------------------
    def to_signals(self, query: AdapterQuery, chart: dict[str, Any]) -> list[Signal]:
        """PalmFeatures → Signal。

        规则（骨架，待验证）：
            手掌轮廓比例作为基础强度，掌纹长度作为方向。
            月/年尺度才有意义（第 57 节）。
        """
        if not chart.get("detected"):
            return []

        life = chart.get("life_line", {})
        continuity = life.get("continuity", 0.5)
        length = life.get("length_ratio", 0.5)

        # 生命线长而连续 → 正向基础；短弱 → 负向
        base = (continuity + length) / 2.0
        direction = 1.0 if base >= 0.5 else -1.0

        rule_id = f"PALM-R-life-{query.domain.value}"
        return [
            Signal(
                **self._base_signal_kwargs(query),
                direction=direction,
                strength=round(0.2 + abs(base - 0.5), 3),  # 掌纹是弱信号（第 57 节）
                confidence=0.28,
                evidence=[
                    Evidence(
                        source=EvidenceSource.TRADITIONAL_RULE,
                        rule_id=rule_id,
                        description=(
                            f"生命线长度比 {length:.2f}、连续性 {continuity:.2f}"
                            f"、曲率 {life.get('curvature', 0):.2f}"
                        ),
                    )
                ],
                rule_ids=[rule_id],
                dependency_group=None,
            )
        ]


registry.register(PalmAdapter())
