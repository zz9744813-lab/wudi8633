"""ZhouyiAdapter —— 周易经文义理信号（义理派断法）。

与六爻（纳甲断法：六亲旺衰）、梅花（体用断法：五行生克）并列的
第三条易学路径：直接以卦辞 + 动爻爻辞的吉凶断辞定方向。

- 起卦复用六爻时间起卦（同一历法内核，确定性）；
- 断辞词表按通行本吉凶用语，长词优先、命中区间互斥（「无咎」不得
  再计内层「咎」）；
- dependency_group 仍为 yi_jing：三条易学路径在融合里组内平均，
  不当独立证据叠加（第 20.12 节去相关）；
- C-006：断辞是传统文献口径，效力由系统验证闭环实证，不预设有效。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.base import AdapterQuery, MetaphysicalAdapter, registry
from app.schemas.signal import (
    Evidence,
    EvidenceSource,
    Signal,
    SourceType,
)

ENGINE_VERSION = "zhouyi-yili-0.3.0"

# 通行本吉凶断辞 → 权重（正=同向，负=反向）。扫描按词长降序、命中区间互斥。
GLOSS_TERMS: list[tuple[str, float]] = sorted(
    [
        # 吉类
        ("元吉", 1.0),
        ("大吉", 0.95),
        ("吉", 0.7),
        ("无不利", 0.55),
        ("利涉大川", 0.5),
        ("利有攸往", 0.45),
        # 亨/利/无咎/无悔 是高频套话（多数卦辞都有），降权到阈下作中性，
        # 否则 direction 恒为正向、零信息量（回测教训：94% 命中全是灌水）
        ("亨", 0.3),
        ("无咎", 0.2),
        ("无悔", 0.2),
        ("利", 0.15),
        # 凶类
        ("凶", -1.0),
        ("无攸利", -0.6),
        ("不利", -0.5),
        ("厉", -0.5),
        ("吝", -0.45),
        ("勿用", -0.45),
        ("有悔", -0.4),
        ("咎", -0.5),
        ("悔", -0.25),
    ],
    key=lambda x: -len(x[0]),
)


def gloss_score(text: str) -> tuple[float, list[str]]:
    """对经文文本做吉凶断辞扫描。返回（加权和, 命中词列表）。长词优先、区间互斥。"""
    total = 0.0
    hits: list[str] = []
    consumed: list[tuple[int, int]] = []

    for term, weight in GLOSS_TERMS:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            span = (idx, idx + len(term))
            if any(not (span[1] <= c0 or span[0] >= c1) for c0, c1 in consumed):
                start = idx + 1
                continue
            consumed.append(span)
            total += weight
            hits.append(term)
            start = idx + len(term)
    return total, hits


class ZhouyiAdapter(MetaphysicalAdapter):
    source = SourceType.ZHOUYI
    engine_name = "zhouyi-yili"
    engine_version = ENGINE_VERSION

    @property
    def available(self) -> bool:
        return True  # 纯 Python，经文库内置

    # ------------------------------------------------------------------
    def compute_chart(self, query: AdapterQuery) -> dict[str, Any]:
        """时间起卦（复用六爻 cast_chart 的确定性排盘），只取卦象层。"""
        from app.core.liuyao.engine import cast_chart

        dt = datetime.combine(query.target_date, datetime.min.time())
        try:
            h, m = (query.target_time or "00:00").split(":")
            dt = dt.replace(hour=int(h), minute=int(m))
        except (ValueError, AttributeError):
            pass

        chart = cast_chart(dt)
        if "error" in chart:
            return {}
        return {
            "ben_gua": chart.get("ben_gua"),
            "bian_gua": chart.get("bian_gua"),
            "moving_yao": [y["position"] for y in chart.get("yao_details", []) if y["moving"]],
        }

    # ------------------------------------------------------------------
    def to_signals(self, query: AdapterQuery, chart: dict[str, Any]) -> list[Signal]:
        """卦辞 + 动爻爻辞吉凶断辞 → Signal（义理派）。"""
        from app.core.zhouyi import cite  # 延迟导入：__init__ 尾部才挂 adapter

        ben = chart.get("ben_gua") or {}
        ben_name = ben.get("name", "")
        if not ben_name:
            return []

        moving = chart.get("moving_yao") or []
        moving_index = None
        if moving:
            order = {"初爻": 1, "二爻": 2, "三爻": 3, "四爻": 4, "五爻": 5, "上爻": 6}
            moving_index = order.get(moving[0])

        # 义理断语：静卦读卦辞，动卦加读动爻爻辞（传统口径）
        canon = cite(ben_name) or ""
        canon_yao = cite(ben_name, moving_index) if moving_index else None
        text = canon + (canon_yao or "")

        score, hits = gloss_score(text)
        # 回测结论（45 人 105 事件）：文献吉凶词频天然吉多凶少，词频法定方向
        # 恒为正向灌水（93% 命中 ≈ 正向事件占比，方向信息量为零）。
        # C-006 下选择不投票：direction 恒 0，经文文本继续作为参读证据；
        # 待回测语料 ≥200 且完成方向校准后再评估恢复。勿回退。
        direction = 0.0
        strength = 0.3
        confidence = 0.05  # 仅文本参读权重，方向弃权故对融合几乎无影响

        rule_id = f"ZHOUYI-R-{ben_name}-{query.domain.value}"
        dir_text = (
            f"断辞扫描 {score:+.2f}（{'、'.join(hits) if hits else '无断语'}）—— "
            "词频基线吉多凶少，方向不投票（回测结论）"
        )

        evidence = [
            Evidence(
                source=EvidenceSource.TRADITIONAL_RULE,
                rule_id=rule_id,
                description=f"本卦{ben_name}（{'、'.join(moving) + '动' if moving else '静卦'}）",
            ),
            Evidence(
                source=EvidenceSource.TRADITIONAL_RULE,
                rule_id=rule_id,
                description=f"经文参读：{canon}" + (f" {canon_yao}" if canon_yao else ""),
            ),
            Evidence(
                source=EvidenceSource.TRADITIONAL_RULE,
                rule_id=rule_id,
                description=(
                    f"义理断辞扫描：{'、'.join(hits) if hits else '无吉凶断语'}"
                    f"（{score:+.2f}）→ {dir_text}"
                ),
            ),
        ]

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
                            description=f"卦辞/爻辞含凶悔吝断语（{ '、'.join(hits) }）",
                        )
                    ]
                    if direction < 0
                    else []
                ),
                rule_ids=[rule_id],
                dependency_group="yi_jing",  # 第 20.12 节：与六爻/梅花同属易卦体系
            )
        ]


registry.register(ZhouyiAdapter())
