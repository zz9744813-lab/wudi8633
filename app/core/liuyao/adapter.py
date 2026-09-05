"""LiuyaoAdapter —— 六爻信号。

对应工程方案：
- 第 6.1 节 六爻（程序确定性排盘，LLM 负责断卦）
- 第 14 节 统一 Signal Schema
- 第 25 节 Rule Registry
- 第 53 节 Adapter 策略

引擎：自研 deterministic 排盘（移植 xiongdun8/liuyao 核心，
      时间起卦 + CalendarCore 四柱），见 engine.py。

C-006：六爻作为 Traditional Metaphysical Signal 进入系统，
       其有效性必须由系统自己的长期验证结果决定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.base import AdapterQuery, MetaphysicalAdapter, registry
from app.models.core import BirthProfile
from app.schemas.signal import (
    Domain,
    Evidence,
    EvidenceSource,
    Signal,
    SourceType,
    TimeScale,
)

from .engine import ENGINE_VERSION, cast_chart
from app.core.zhouyi import cite as zhouyi_cite

# 用神映射：domain → 六亲（传统用神取用）
DOMAIN_YONGSHEN: dict[Domain, str] = {
    Domain.CAREER: "官鬼",
    Domain.MONEY: "妻财",
    Domain.STUDY: "父母",
    Domain.SOCIAL: "兄弟",
    Domain.RELATIONSHIP: "妻财",
    Domain.PROJECT: "官鬼",
    Domain.TRAVEL: "子孙",
    Domain.COMMUNICATION: "子孙",
    Domain.HABIT: "兄弟",
    Domain.PURCHASE: "妻财",
    Domain.SCHEDULE: "官鬼",
    Domain.UNEXPECTED_EVENT: "官鬼",
}

# 旺衰打分零点漂移（随机 400 日 × 3 域实测均值，见 to_signals 注释）
SCORE_BASELINE_OFFSET = 0.4
# 中性弃权带：|调整后得分| 低于此值不判方向（宁弃权不硬猜）
NEUTRAL_BAND = 0.5

# 爻位名 → 1-6 爻序（初爻=1 … 上爻=6），供经文爻辞取位
_POSITION_INDEX: dict[str, int] = {
    "初爻": 1, "二爻": 2, "三爻": 3, "四爻": 4, "五爻": 5, "上爻": 6,
}


class LiuyaoAdapter(MetaphysicalAdapter):
    source = SourceType.LIUYAO
    engine_name = "liuyao-engine"
    engine_version = ENGINE_VERSION

    @property
    def available(self) -> bool:
        # 纯 Python 实现，无外部依赖
        return True

    # ------------------------------------------------------------------
    def compute_chart(self, query: AdapterQuery) -> dict[str, Any]:
        """确定性排盘（第 54 节）。"""
        profile = None
        if query.session is not None:
            stmt = select(BirthProfile).where(BirthProfile.user_id == query.user_id)
            profile = query.session.exec(stmt).first()

        dt = datetime.combine(query.target_date, datetime.min.time())
        try:
            h, m = (query.target_time or "00:00").split(":")
            dt = dt.replace(hour=int(h), minute=int(m))
        except (ValueError, AttributeError):
            pass

        chart = cast_chart(dt, birth_date=(profile.solar_birth_date if profile else None))
        # 婚恋用神依求测人性别：男用妻财、女用官鬼（compute_chart 传入 to_signals）
        chart["_querent_gender"] = profile.gender if profile else "unknown"

        if "error" in chart:
            return {}
        return chart

    # ------------------------------------------------------------------
    def to_signals(self, query: AdapterQuery, chart: dict[str, Any]) -> list[Signal]:
        """卦盘 → Signal。

        规则（骨架，待验证）：
            以「用神」爻的旺衰为核心信号：
                旺（score > 0）→ 该领域方向正向
                衰（score < 0）→ 负向
            strength 由 |score| 归一化，confidence 固定弱先验（禁止 6）。
        """
        # 婚恋用神依求测人性别：男测婚用财（妻星）、女测婚用官（夫星）；
        # 无档案/未知 → 妻财（历史默认）。其余域仍按 DOMAIN_YONGSHEN。
        if query.domain == Domain.RELATIONSHIP:
            querent = chart.get("_querent_gender", "unknown")
            yong_shen = "官鬼" if querent == "female" else "妻财"
        else:
            yong_shen = DOMAIN_YONGSHEN.get(query.domain, "官鬼")
        yao = next((y for y in chart.get("yao_details", []) if y["liuqin"] == yong_shen), None)
        if yao is None:
            # 用神伏藏/缺失：返回降级信号（不可用 ≠ 反对）
            return []

        score = float(yao["score"])
        # 打分零点漂移校准（round 16 回测战果）：旺衰打分在随机 400 日 × 3 域上
        # 均值 -0.4、负分占 59%（月克/日克权重天然略偏负），未校准前命中率 41%
        # 纯属漂移而非事件相关。重定心 + 中性弃权带，使方向基线对称。勿回退。
        adjusted = score + SCORE_BASELINE_OFFSET
        if abs(adjusted) < NEUTRAL_BAND:
            return []  # 中性带：方向不确定，弃权
        direction = 1.0 if adjusted > 0 else -1.0
        # |score| ∈ [0, 6] → strength ∈ [0.15, 0.85]
        strength = min(0.85, 0.15 + abs(score) * 0.12)
        confidence = 0.35

        rule_id = f"LIUYAO-R-{yong_shen}-{query.domain.value}"
        moving = yao["moving"]

        evidence = [
            Evidence(
                source=EvidenceSource.TRADITIONAL_RULE,
                rule_id=rule_id,
                description=(
                    f"用神{yong_shen}位于{yao['position']}"
                    f"（{yao['branch']}{yao['wuxing']}），旺衰 {score:+.2f}："
                    f"{'、'.join(yao['status']) or '平'}"
                ),
            )
        ]
        # 周易经文参读（C-006：文献出处，非效力宣称；静卦读卦辞，动爻加读爻辞）
        ben_name = chart.get("ben_gua", {}).get("name")
        if ben_name:
            canon_gua = zhouyi_cite(ben_name)
            if canon_gua:
                evidence.append(
                    Evidence(
                        source=EvidenceSource.TRADITIONAL_RULE,
                        rule_id=f"{rule_id}-CANON",
                        description=f"经文参读：{canon_gua}",
                    )
                )
        if moving:
            evidence.append(
                Evidence(
                    source=EvidenceSource.TRADITIONAL_RULE,
                    rule_id=rule_id,
                    description=f"{yao['position']}动，变爻 {yao['changed_branch']}（动则事态有变）",
                )
            )
            yao_index = _POSITION_INDEX.get(yao["position"])
            canon_yao = zhouyi_cite(ben_name, yao_index) if (ben_name and yao_index) else None
            if canon_yao:
                evidence.append(
                    Evidence(
                        source=EvidenceSource.TRADITIONAL_RULE,
                        rule_id=f"{rule_id}-CANON",
                        description=f"经文参读（用神动爻）：{canon_yao}",
                    )
                )

        return [
            Signal(
                **self._base_signal_kwargs(query),
                direction=direction,
                strength=round(strength, 3),
                confidence=confidence,
                evidence=evidence,
                counter_evidence=(
                    [
                        Evidence(
                            source=EvidenceSource.TRADITIONAL_RULE,
                            rule_id=rule_id,
                            description=f"用神{yong_shen}处{'、'.join(yao['status']) or '平'}，力量不足",
                        )
                    ]
                    if score < 0
                    else []
                ),
                rule_ids=[rule_id],
                dependency_group="yi_jing",  # 第 20.12 节：六爻与梅花同属易卦体系
            )
        ]


registry.register(LiuyaoAdapter())
