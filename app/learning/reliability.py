"""Personal Reliability Matrix —— 层级可靠度。

对应工程方案：
- 第 26 节 Personal Reliability Matrix
- 第 77 节 模型学习逻辑（层级可靠度）
- 第 23 节 失败归因
- 第 78 节 小样本保护
- 第 84 节 North Star Metric

第 26 节：这里保存的是「相对于 Null Model 的 predictive skill」，
         而不是命中率。

     System  | 日    | 周    | 月    | 年    | Career | Money | Social
     Ziwei   | 0.02  | 0.11  | 0.24  | 0.30  | 0.27   | 0.08  | 0.14
     Reality | 0.30  | 0.32  | 0.37  | 0.25  | 0.39   | 0.31  | 0.29
     Null    | baseline ...

第 77 节：
    Reliability(user, system, domain, time_scale, event_type, rule)
    形成独立可靠度。

第 78 节：需要最小样本量 / 贝叶斯先验 / 可信区间 / 收缩估计。
    3/3 必须显示为 Observed 100% / Reliability Low / Sample 3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlmodel import Session, select

from app.calibration.scoring import (
    Aggregate,
    ScoreRow,
    aggregate,
    reliability_label,
    skill_score,
)
from app.models.scoring import PredictionScore

# 第 78 节：低于此样本量不给出可靠度数值，只标注样本不足
MIN_SAMPLE = 20


@dataclass
class ReliabilityCell:
    """矩阵中的一个格子。"""

    key: str
    dimensions: dict[str, str] = field(default_factory=dict)
    sample_size: int = 0
    skill: float | None = None      # 相对 Null 的 skill（第 26 节）
    brier: float | None = None
    null_brier: float | None = None
    reliability: str = "low"
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            **self.dimensions,
            "sample_size": self.sample_size,
            "skill": None if self.skill is None else round(self.skill, 4),
            "brier": None if self.brier is None else round(self.brier, 4),
            "null_brier": None if self.null_brier is None else round(self.null_brier, 4),
            "reliability": self.reliability,
            "note": self.note,
        }


@dataclass
class AblationRow:
    """第 33 节 Ablation Test：摘除某系统后的表现。"""

    variant: str
    sample_size: int
    brier: float | None
    skill: float | None = None


class ReliabilityMatrix:
    """按 (system, domain, time_scale) 维度统计相对 Null 的 skill。"""

    def __init__(self, session: Session, user_id: int | None = None) -> None:
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------
    def _load(self) -> list[PredictionScore]:
        stmt = select(PredictionScore)
        if self.user_id is not None:
            stmt = stmt.where(PredictionScore.user_id == self.user_id)
        return list(self.session.exec(stmt).all())

    def _rows(self, scores: Iterable[PredictionScore]) -> list[ScoreRow]:
        return [
            ScoreRow(
                probability=s.probability,
                outcome=s.outcome,
                null_probability=s.null_probability,
            )
            for s in scores
        ]

    # ------------------------------------------------------------------
    def overall(self) -> Aggregate:
        return aggregate(self._rows(self._load()))

    def by_time_scale(self) -> list[ReliabilityCell]:
        scores = self._load()
        out: list[ReliabilityCell] = []
        for scale in sorted({s.time_scale for s in scores if s.time_scale}):
            subset = [s for s in scores if s.time_scale == scale]
            out.append(self._cell(f"scale:{scale}", {"time_scale": scale}, subset))
        return out

    def by_domain(self) -> list[ReliabilityCell]:
        scores = self._load()
        out: list[ReliabilityCell] = []
        for domain in sorted({s.domain for s in scores if s.domain}):
            subset = [s for s in scores if s.domain == domain]
            out.append(self._cell(f"domain:{domain}", {"domain": domain}, subset))
        return out

    def by_source(self) -> list[ReliabilityCell]:
        """按术式系统维度。一条预测可能含多个 source_types。"""
        scores = self._load()
        buckets: dict[str, list[PredictionScore]] = {}
        for s in scores:
            for src in s.source_types or []:
                buckets.setdefault(src, []).append(s)

        out: list[ReliabilityCell] = []
        for src in sorted(buckets):
            out.append(self._cell(f"system:{src}", {"system": src}, buckets[src]))
        return out

    # ------------------------------------------------------------------
    def _cell(
        self, key: str, dimensions: dict[str, str], subset: list[PredictionScore]
    ) -> ReliabilityCell:
        rows = self._rows(subset)
        agg = aggregate(rows)
        comparable = [r for r in rows if r.null_probability is not None]

        if agg.sample_size < MIN_SAMPLE:
            return ReliabilityCell(
                key=key,
                dimensions=dimensions,
                sample_size=agg.sample_size,
                skill=None,
                brier=None if agg.sample_size == 0 else agg.brier,
                reliability="low",
                note=f"样本不足（{agg.sample_size}/{MIN_SAMPLE}），不给出可靠度结论（第 78 节）",
            )

        if not comparable:
            return ReliabilityCell(
                key=key,
                dimensions=dimensions,
                sample_size=agg.sample_size,
                skill=None,
                brier=agg.brier,
                reliability=reliability_label(agg.sample_size),
                note="缺少 Null 基线，无法计算相对 skill（第 11 节要求必须提供）",
            )

        null_loss = sum(
            (r.null_probability - r.outcome) ** 2 for r in comparable
        ) / len(comparable)

        return ReliabilityCell(
            key=key,
            dimensions=dimensions,
            sample_size=agg.sample_size,
            skill=skill_score(agg.brier, null_loss),
            brier=agg.brier,
            null_brier=null_loss,
            reliability=reliability_label(agg.sample_size),
        )

    # ------------------------------------------------------------------
    def fusion_weights(self) -> dict[str, float]:
        """把 skill 转成 Fusion 权重（第 26 / 77 节）。

        规则：
            skill > 0  → 权重 > 1（放大）
            skill <= 0 → 权重 < 1（压制），但不为负
            skill 未知 / 无数据 → 0.5（弱先验下限）

        禁止 6「初始只允许弱先验」的落实：未实证的信号源不能按「全可信」进融合
        （旧实现 skill=None → 1.0 曾导致「噪声偶然偏离 Null」的假 edge 穿透
        质量门槛）。因此本方法为所有已知信号源都给出权重：
        有实证 cell 的按 skill 映射；没有的落在允许带 [0.5, 2.0] 的下限，
        直到验证数据把 skill 学出来。
        """
        from app.schemas.signal import SourceType

        cell_by_source: dict[str, ReliabilityCell] = {}
        for cell in self.by_source():
            system = cell.dimensions.get("system")
            if system:
                cell_by_source[system] = cell

        # 回测先验（45 人 105 事件，round 15）：方向信息量差异的弱先验地板，
        # 幅度 ≤±0.1；用户自己的已验证 skill 一旦存在即完全覆盖（本方法上面的映射）。
        BACKTEST_PRIOR = {"ziwei": 0.55, "liuyao": 0.44, "meihua": 0.47, "zhouyi": 0.42}

        weights: dict[str, float] = {}
        for src in SourceType:
            cell = cell_by_source.get(src.value)
            if cell is None or cell.skill is None:
                weights[src.value] = BACKTEST_PRIOR.get(src.value, 0.5)
                continue
            # 线性映射：skill ∈ [-0.5, 0.5] → weight ∈ [0.5, 1.5]
            weights[src.value] = min(2.0, max(0.5, 1.0 + cell.skill * 2.0))
        return weights

    def matrix(self) -> dict:
        """完整矩阵，供前端 Accuracy Lab 使用（第 52 节）。"""
        return {
            "overall": self.overall().to_dict(),
            "by_system": [c.to_dict() for c in self.by_source()],
            "by_domain": [c.to_dict() for c in self.by_domain()],
            "by_time_scale": [c.to_dict() for c in self.by_time_scale()],
            "fusion_weights": self.fusion_weights(),
        }
