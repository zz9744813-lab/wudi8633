"""BaziAdapter —— 八字信号（V0.1 主术式）。

对应工程方案：
- 第 6.1 节 八字 / 历法（lunar-python）
- 第 14 节 统一 Signal Schema
- 第 25 节 Rule Registry
- 第 53 节 Adapter 策略

C-006：八字作为 Traditional Metaphysical Signal 进入系统，
      其有效性必须由系统自己的长期验证结果决定，不得预先假定有效。

第 6.1 节硬性规则：
    程序负责排盘，LLM 不允许自己算命盘。
    所有术式必须共享同一个 Calendar Core。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from app.core.base import AdapterQuery, MetaphysicalAdapter, registry
from app.core.calendar.core import CalendarCore
from app.schemas.signal import (
    Domain,
    Evidence,
    EvidenceSource,
    Signal,
    SourceType,
)

ENGINE_VERSION = "bazi-0.3.0"

TIANGAN = "甲乙丙丁戊己庚辛壬癸"
# 五行：木木火火土土金金水水
TG_WUXING = ["木", "木", "火", "火", "土", "土", "金", "金", "水", "水"]

# 地支五行（四柱根气/月令用）
DIZHI = "子丑寅卯辰巳午未申酉戌亥"
DZ_WUXING = ["水", "土", "木", "木", "土", "火", "火", "土", "金", "金", "土", "水"]

# 五行生克：key 生 value / key 克 value
WUXING_SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
WUXING_KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

# 扶抑法喜忌：身强喜克泄耗（官/财/食伤），身弱喜生扶（印/比劫）。
# 中和 → 不判断方向（诚实：不是每张盘都能读出倾向）。
STRONG_FAVORABLE = {"官", "财", "食伤"}
STRONG_UNFAVORABLE = {"印", "比劫"}
WEAK_FAVORABLE = {"印", "比劫"}
WEAK_UNFAVORABLE = {"官", "财", "食伤"}


def wuxing_of(tiangan: str) -> str:
    return TG_WUXING[TIANGAN.index(tiangan)] if tiangan in TIANGAN else ""


def wuxing_of_zhi(dizhi: str) -> str:
    return DZ_WUXING[DIZHI.index(dizhi)] if dizhi in DIZHI else ""


def day_master_strength(bazi: dict[str, str]) -> tuple[float, str, str]:
    """日主强弱简评（扶抑法，确定性）：得令/得地/得势加权求和。

    权重：月令最大（同 +3 / 印 +2 / 泄 -0.5 / 耗 -0.75 / 克 -1.0）；
    其余三支根气（同 +1.2 / 印 +0.8 / 泄 -0.4 / 耗 -0.5 / 克 -0.6）；
    年月时三干（比劫 +1.0 / 印 +0.6 / 食伤 -0.4 / 财 -0.5 / 官 -0.6）。

    返回 (得分, 身强/身弱/中和, 月令判定短句)。|score| ≥ 2 才下强弱结论，
    其余为中和 —— 不强断（对抗性要求：读不出倾向就承认读不出）。
    """
    day_master = bazi.get("day_master", "")
    dm_wx = wuxing_of(day_master)
    if not dm_wx:
        return 0.0, "中和", ""

    month_gz = bazi.get("month", "")
    m_wx = wuxing_of_zhi(month_gz[1:]) if len(month_gz) > 1 else ""
    score = 0.0
    ling = ""
    if m_wx:
        if m_wx == dm_wx:
            score += 3.0
            ling = "得令"
        elif WUXING_SHENG.get(m_wx) == dm_wx:
            score += 2.0
            ling = "得令（月令生身）"
        elif WUXING_SHENG.get(dm_wx) == m_wx:
            score -= 0.5
            ling = "失令（月令泄身）"
        elif WUXING_KE.get(dm_wx) == m_wx:
            score -= 0.75
            ling = "失令（月令耗身）"
        elif WUXING_KE.get(m_wx) == dm_wx:
            score -= 1.0
            ling = "失令（月令克身）"

    # 根气：年/日/时三支（月支已在月令计过，不重复计）
    roots = 0
    for pillar in ("year", "day", "time"):
        gz = bazi.get(pillar, "")
        if len(gz) < 2:
            continue
        z_wx = wuxing_of_zhi(gz[1])
        if not z_wx:
            continue
        if z_wx == dm_wx:
            score += 1.2
            roots += 1
        elif WUXING_SHENG.get(z_wx) == dm_wx:
            score += 0.8
        elif WUXING_SHENG.get(dm_wx) == z_wx:
            score -= 0.4
        elif WUXING_KE.get(dm_wx) == z_wx:
            score -= 0.5
        elif WUXING_KE.get(z_wx) == dm_wx:
            score -= 0.6

    # 得势：年/月/时干（不含日主自身）
    for pillar in ("year", "month", "time"):
        gz = bazi.get(pillar, "")
        if not gz:
            continue
        cat = shishen_category(day_master, gz[0])
        if cat == "比劫":
            score += 1.0
        elif cat == "印":
            score += 0.6
        elif cat == "食伤":
            score -= 0.4
        elif cat == "财":
            score -= 0.5
        elif cat == "官":
            score -= 0.6

    # 阈值 ±1.5（原 ±2 过宽：丁火子月失令这类 -1.5 的清晰盘被误判中和，
    # 与八字批示口径不一致，且导致该用户八字信号永久沉默——审计战果）
    if score >= 1.5:
        return score, "身强", ling
    if score <= -1.5:
        return score, "身弱", ling
    return score, "中和", ling


def shishen_category(day_master: str, other: str) -> str:
    """日主与其他天干的十神类别（简化版：仅按五行生克分类）。

    完整十神需区分阴阳（正/偏），骨架阶段用类别：
        比劫 / 印 / 食伤 / 官 / 财
    """
    dm_wx = wuxing_of(day_master)
    ot_wx = wuxing_of(other)
    if not dm_wx or not ot_wx:
        return ""
    if dm_wx == ot_wx:
        return "比劫"
    if WUXING_SHENG.get(ot_wx) == dm_wx:
        return "印"          # 他生我
    if WUXING_SHENG.get(dm_wx) == ot_wx:
        return "食伤"        # 我生他
    if WUXING_KE.get(ot_wx) == dm_wx:
        return "官"          # 他克我
    if WUXING_KE.get(dm_wx) == ot_wx:
        return "财"          # 我克他
    return ""


class BaziAdapter(MetaphysicalAdapter):
    source = SourceType.BAZI
    engine_name = "lunar-python"
    engine_version = ENGINE_VERSION

    def __init__(self) -> None:
        self._core = CalendarCore()

    @property
    def available(self) -> bool:
        return self._core.available

    # ------------------------------------------------------------------
    def compute_chart(self, query: AdapterQuery) -> dict[str, Any]:
        """确定性排盘。共享 CalendarCore（第 6.1 节禁止各自算日期）。

        需要用户提供 BirthProfile。session 由调用方注入（便于测试隔离），
        未注入时回退到全局 engine。
        """
        from sqlmodel import Session, select

        from app.database import engine as db_engine
        from app.models.core import BirthProfile

        stmt = select(BirthProfile).where(
            BirthProfile.user_id == query.user_id,
            BirthProfile.is_primary.is_(True),  # type: ignore[union-attr]
        )

        if query.session is not None:
            profile = query.session.exec(stmt).first()
        else:
            with Session(db_engine) as session:
                profile = session.exec(stmt).first()

        if profile is None:
            return {}

        result = self._core.compute(
            birth_date=profile.solar_birth_date,
            birth_time=profile.solar_birth_time,
            target_date=query.target_date,
            target_time=query.target_time,
            gender=profile.gender,
            use_true_solar_time=profile.use_true_solar_time,
            longitude=profile.longitude,
        )

        if result.degraded:
            return {}

        return {
            "input_hash": self.input_hash(
                "bazi",
                user_id=query.user_id,
                target_date=query.target_date,
                target_time=query.target_time,
            ),
            **result.payload,
            "birth_time_known": True,
        }

    # ------------------------------------------------------------------
    def to_signals(self, query: AdapterQuery, chart: dict[str, Any]) -> list[Signal]:
        """排盘 → Signal。

        规则（V0.2，扶抑法）：
            1. 先评日主强弱（得令/得地/得势，day_master_strength）；
            2. 身强 → 喜官/财/食伤（克泄耗），忌印/比劫；
               身弱 → 喜印/比劫（生扶），忌官/财/食伤；
               中和 → 不判断方向（direction=0，诚实声明读不出倾向）；
            3. 对照干支按尺度取：日/周=流日，月=流月，年=流年。

        C-006：这只是待验证信号，不代表已证实有效。
        """
        bazi = chart.get("bazi") or {}
        day_master = bazi.get("day_master", "")
        if not day_master:
            return []

        # 按时间尺度选取对照干支（周尺度用流日——一周的吉凶不该用整月干支概括）
        scale_to_ganzhi = {
            "day": chart.get("liuri", ""),
            "week": chart.get("liuri", ""),
            "month": chart.get("liuyue", ""),
            "year": chart.get("liunian", ""),
        }
        pillar_label = {"day": "流日", "week": "流日", "month": "流月", "year": "流年"}
        ganzhi = scale_to_ganzhi.get(query.time_scale.value, chart.get("liuri", ""))
        if not ganzhi:
            return []

        tiangan = ganzhi[0]
        category = shishen_category(day_master, tiangan)
        if not category:
            return []

        score, verdict, ling = day_master_strength(bazi)
        dm_wx = wuxing_of(day_master)

        if verdict == "身强":
            favorable, unfavorable = STRONG_FAVORABLE, STRONG_UNFAVORABLE
        elif verdict == "身弱":
            favorable, unfavorable = WEAK_FAVORABLE, WEAK_UNFAVORABLE
        else:  # 中和：无喜忌主张，方向一律 0
            favorable, unfavorable = set(), set()

        if category in favorable:
            direction = 1.0
        elif category in unfavorable:
            direction = -1.0
        else:
            direction = 0.0  # 中和盘：不判断方向（fusion 中仅拉保守，不虚报支持/反对）

        # strength：方向明确时的固定档位；中和给低强度（有观察、无主张）
        strength = {1.0: 0.6, -1.0: 0.4, 0.0: 0.3}[direction]
        confidence = 0.35  # 弱先验（禁止 6：初始只允许弱先验）

        rule_id = f"BAZI-R-{category}-{verdict}-{query.domain.value}"
        dir_text = {1.0: "喜", -1.0: "忌", 0.0: "中和不判断"}[direction]

        evidence = [
            Evidence(
                source=EvidenceSource.CALENDAR,
                rule_id=rule_id,
                description=(
                    f"日主{day_master}（{dm_wx}）{ling or '月令未知'}，"
                    f"扶抑综合 {score:+.1f} → {verdict}；"
                    f"{pillar_label.get(query.time_scale.value, '流日')}{ganzhi}（{category}）：{dir_text}"
                ),
            )
        ]
        counter = (
            []
            if direction >= 0
            else [
                Evidence(
                    source=EvidenceSource.TRADITIONAL_RULE,
                    rule_id=rule_id,
                    description=f"{category}为{verdict}所忌（扶抑法：{'克泄耗' if verdict == '身强' else '生扶'}为喜）",
                )
            ]
        )

        return [
            Signal(
                **self._base_signal_kwargs(query),
                direction=direction,
                strength=strength,
                confidence=confidence,
                evidence=evidence,
                counter_evidence=counter,
                rule_ids=[rule_id],
                # 第 20.12 节：八字与紫微、黄历共享历法信号，不能当作独立证据
                dependency_group="lunar_calendar",
            )
        ]


# 注册到全局 Adapter 注册表（第 53 节）
registry.register(BaziAdapter())
