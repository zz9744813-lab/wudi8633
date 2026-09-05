"""周易经文层 · 对抗性测试（round 10）。

四道防线：
1. 经文完整性 —— TEXTS 与六爻 HEXAGRAMS 权威表逐卦对照，防手抄漂移；
2. 梅花卦名单一事实源 —— GUA_NAMES 曾被审计出 9 条上下卦颠倒，此处
   固化回归：GUA_NAMES 必须等于由 HEXAGRAMS 程序化推导的权威映射；
3. adapter 经文证据 —— 六爻/梅花信号必须挂得上卦辞/动爻爻辞；
4. 冻结安全 —— 经文只能进读侧叙事（rich_description），绝不进
   冻结描述（Gate 审的文本域）；并反向证明：若经文泄漏进冻结描述，
   DefinitionAttack 会因经文原文含「小人」等不可观测概念而 FAIL。

C-006：经文是文献参读，不是效力宣称。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.base import AdapterQuery
from app.core.liuyao.engine import HEXAGRAMS
from app.core.meihua import engine as me
from app.core.zhouyi import (
    by_name,
    by_pattern,
    cite,
    name_for_pattern,
    yao_positions,
)
from app.core.zhouyi.texts import TEXTS
from app.schemas.signal import (
    Domain,
    Evidence,
    EvidenceSource,
    Signal,
    SourceType,
    TimeScale,
    TimeWindow,
)
from app.services.cross_engine import rich_description

# ----------------------------------------------------------------------
# 1. 经文完整性
# ----------------------------------------------------------------------
def test_texts_complete_64():
    assert len(TEXTS) == 64
    for pat, e in HEXAGRAMS.items():
        t = TEXTS.get(e["name"])
        assert t is not None, f"经文缺卦：{e['name']}"
        assert t["short"], e["name"]
        assert t["gua"].endswith("。"), e["name"]
        assert t["xiang"].endswith("。") or "：" in t["xiang"], e["name"]
        assert len(t["yao"]) == 6, e["name"]
        for y in t["yao"]:
            assert y.endswith("。"), (e["name"], y)
    yongs = [n for n, t in TEXTS.items() if "yong" in t]
    assert sorted(yongs) == ["乾为天", "坤为地"]


def test_yao_positions_rules():
    assert yao_positions("乾为天") == ["初九", "九二", "九三", "九四", "九五", "上九"]
    assert yao_positions("坤为地") == ["初六", "六二", "六三", "六四", "六五", "上六"]
    # 下离(1,0,1) 上巽(0,1,1)：混合阴阳
    assert yao_positions("风火家人") == ["初九", "六二", "九三", "六四", "九五", "上九"]


def test_cite_format_and_lookup():
    assert cite("乾为天") == "《周易·乾》元亨利贞。"
    assert cite("乾为天", 6) == "《周易·乾》上九：亢龙有悔。"
    assert cite("坎为水", 1) == "《周易·坎》初六：习坎，入于坎窞，凶。"
    assert cite("不存在的卦") is None
    assert by_pattern("1,1,1,1,1,1")["short"] == "乾"
    assert by_name("水雷屯")["gua"] == "元亨，利贞。勿用有攸往，利建侯。"
    assert name_for_pattern("9,9,9") is None


# ----------------------------------------------------------------------
# 2. 梅花卦名单一事实源（回归：9 条上下卦颠倒）
# ----------------------------------------------------------------------
def test_meihua_gua_names_match_canonical_table():
    _bits2gua = {tuple(v): k for k, v in me.GUA_BITS.items()}
    canon: dict[tuple[str, str], str] = {}
    for pat, e in HEXAGRAMS.items():
        bits = [int(x) for x in pat.split(",")]
        canon[(_bits2gua[tuple(bits[:3])], _bits2gua[tuple(bits[3:])])] = e["name"]
    assert len(canon) == 64
    assert me.GUA_NAMES == canon, "GUA_NAMES 偏离权威表（曾发生 9 条上下卦颠倒）"


def test_meihua_flip_and_naming_consistency():
    _bits2gua = {tuple(v): k for k, v in me.GUA_BITS.items()}
    checked = 0
    for h in range(0, 24, 2):
        for minute in (0, 17, 43):
            dt = datetime(2026, 9, 5, h, minute)
            m = me.cast_hexagram(
                dt,
                year_branch="午",
                hour_branch="子丑寅卯辰巳午未申酉戌亥"[((h + 1) // 2) % 12],
            )
            bits = list(m["ben_gua"]["lines"])
            bits[m["moving_yao"] - 1] ^= 1
            # 变卦爻位 = 本卦动爻翻转
            assert list(m["bian_gua"]["lines"]) == bits, (h, minute)
            # 变卦名与变卦上下卦一致（曾误用本卦 upper/lower）
            lo = _bits2gua[tuple(bits[:3])]
            hi = _bits2gua[tuple(bits[3:])]
            assert m["bian_gua"]["name"] == me.GUA_NAMES[(lo, hi)]
            assert m["bian_gua"]["lower"] == lo and m["bian_gua"]["upper"] == hi
            checked += 1
    assert checked == 36


# ----------------------------------------------------------------------
# 3. adapter 经文证据
# ----------------------------------------------------------------------
def _mk_query(target_time: str = "10:30") -> AdapterQuery:
    start = datetime(2026, 9, 5)
    return AdapterQuery(
        user_id=1,
        domain=Domain.CAREER,
        target_event="career.unexpected_task",
        time_scale=TimeScale.DAY,
        window=TimeWindow(start=start, end=start + timedelta(hours=24)),
        target_date=date(2026, 9, 5),
        target_time=target_time,
    )


def test_liuyao_adapter_canon_gua_ci():
    from app.core.liuyao.adapter import LiuyaoAdapter

    q = _mk_query()
    chart = LiuyaoAdapter().compute_chart(q)
    sig = LiuyaoAdapter().to_signals(q, chart)[0]
    canon = [e.description for e in sig.evidence if "经文参读：" in e.description]
    assert canon, "六爻信号缺卦辞经文证据"
    assert canon[0].startswith("经文参读：《周易·")


def test_liuyao_adapter_canon_yao_ci_when_yongshen_moving():
    from app.core.liuyao.adapter import LiuyaoAdapter

    adapter = LiuyaoAdapter()
    hit = None
    for h in range(24):
        for minute in (0, 11, 23, 37):
            q = _mk_query(f"{h:02d}:{minute:02d}")
            chart = adapter.compute_chart(q)
            yongshen = "官鬼"
            yao = next(
                (y for y in chart.get("yao_details", []) if y["liuqin"] == yongshen),
                None,
            )
            if yao is not None and yao["moving"]:
                sigs = adapter.to_signals(q, chart)
                if not sigs:
                    continue  # 中性带弃权（round 16 重定心后合法状态）
                sig = sigs[0]
                hit = (chart, sig)
                break
        if hit:
            break
    assert hit, "扫样 96 个时刻未见用神动爻（按 1/6 概率不应发生）"
    chart, sig = hit
    canon_yao = [
        e.description for e in sig.evidence if "经文参读（用神动爻）：" in e.description
    ]
    assert canon_yao, "用神动爻时缺爻辞证据"
    # 爻辞引用里的爻题必须与用神爻阴阳一致（初/上 vs 二至五）
    ben_name = chart["ben_gua"]["name"]
    yao_index = {"初爻": 1, "二爻": 2, "三爻": 3, "四爻": 4, "五爻": 5, "上爻": 6}[
        yao["position"]
    ]
    assert canon_yao[0] == f"经文参读（用神动爻）：{cite(ben_name, yao_index)}"


def test_meihua_adapter_canon_gua_and_yao():
    from app.core.meihua.adapter import MeihuaAdapter

    q = _mk_query()
    adapter = MeihuaAdapter()
    chart = adapter.compute_chart(q)
    sig = adapter.to_signals(q, chart)[0]
    descs = [e.description for e in sig.evidence]
    assert any(d.startswith("经文参读：《周易·") for d in descs), "梅花信号缺卦辞证据"
    assert any(d.startswith("经文参读（动爻）：《周易·") for d in descs), "梅花信号缺动爻爻辞证据"
    # 动爻爻辞必须引用本卦动爻位
    moving = chart["moving_yao"]
    assert any(
        d == f"经文参读（动爻）：{cite(chart['ben_gua']['name'], moving)}"
        for d in descs
    )


# ----------------------------------------------------------------------
# 4a. 富文本：全法盘点 + 经文献录（读侧，不过 Gate）
# ----------------------------------------------------------------------
def _mk_signal(source: SourceType, direction: float, ev_desc: str | None = None) -> Signal:
    start = datetime(2026, 9, 5)
    return Signal(
        signal_id=f"sig-{source.value}",
        source=source,
        domain=Domain.CAREER,
        target_event="career.unexpected_task",
        direction=direction,
        strength=0.5,
        confidence=0.35,
        time_window={"start": start, "end": start + timedelta(hours=24)},
        time_scale=TimeScale.DAY,
        rule_ids=["R-TEST"],
        evidence=[
            Evidence(source=EvidenceSource.TRADITIONAL_RULE, rule_id="R-TEST", description=ev_desc)
        ]
        if ev_desc
        else [],
    )


def _rich(signals, **kw):
    start = datetime(2026, 9, 5)
    return rich_description(
        event_type="career.unexpected_task",
        label="事业或有突发任务",
        scale=TimeScale.DAY,
        window_start=start.date(),
        window_end=(start + timedelta(hours=24)).date(),
        signals=signals,
        almanac=None,
        **kw,
    )


def test_full_tally_lists_all_five_engines():
    out = _rich([])
    assert "全法盘点" in out
    for eng in ("八字", "紫微", "六爻", "梅花", "奇门"):
        assert f"{eng}○ 未表态" in out, eng
    assert "掌纹/面相需拍照参校，未计入" in out


def test_full_tally_marks_directions_and_image_note():
    signals = [
        _mk_signal(SourceType.LIUYAO, 1.0, "用神官鬼旺衰 +1.20"),
        _mk_signal(SourceType.MEIHUA, -1.0, "体生用"),
    ]
    out = _rich(signals)
    assert "六爻✓ 同向" in out
    assert "梅花✗ 反向" in out
    assert "八字○ 未表态" in out
    assert "未计入" in out  # 无掌面相信号 → 注明


def test_canon_citation_line_dedup_and_cap():
    signals = [
        _mk_signal(SourceType.LIUYAO, 1.0, "经文参读：《周易·乾》元亨利贞。"),
        _mk_signal(SourceType.MEIHUA, 1.0, "经文参读：《周易·乾》元亨利贞。"),  # 重复
        _mk_signal(SourceType.QIMEN, 1.0, "经文参读：《周易·坤》元亨，利牝马之贞。安贞吉。"),
        _mk_signal(SourceType.BAZI, 1.0, "经文参读（动爻）：《周易·坎》初六：习坎，入于坎窞，凶。"),
    ]
    out = _rich(signals)
    assert "经文献录（文献参读，非效力宣称）：" in out
    assert out.count("《周易·乾》元亨利贞。") >= 1
    assert "共 3 条" in out and "其余见信号证据" in out
    # 第三条不出现在「经文献录」行（上限 2 条）；多法印证块展示完整证据是另一回事
    canon_line = next(line for line in out.splitlines() if line.startswith("经文献录"))
    assert "《周易·坎》初六" not in canon_line


def test_no_canon_line_without_canon_evidence():
    out = _rich([_mk_signal(SourceType.BAZI, 1.0, "日主得令")])
    assert "经文献录" not in out


# ----------------------------------------------------------------------
# 4b. 冻结安全：经文绝不进冻结描述；泄漏必被 Gate 拦截
# ----------------------------------------------------------------------
def test_freeze_description_never_contains_canon():
    """候选描述只写「何时+何事」，与信号内容无关（pit 16 架构）。"""
    from app.services.pipeline import DailyPipeline, when_text

    canon_signals = [
        _mk_signal(SourceType.LIUYAO, 1.0, "经文参读：《周易·师》大君有命，开国承家，小人勿用。"),
    ]
    start = datetime(2026, 9, 5)
    pipe = object.__new__(DailyPipeline)  # _provisional_candidate 不读实例状态
    cand = pipe._provisional_candidate(
        event_type="career.unexpected_task",
        domain=Domain.CAREER,
        window=TimeWindow(start=start, end=start + timedelta(hours=24)),
        time_scale=TimeScale.DAY,
        probability=0.42,
        signals=canon_signals,
        almanac=None,
    )
    assert cand is not None
    assert "《周易·" not in cand.description
    assert "小人" not in cand.description
    # 而读侧富文本可以引用经文（读侧不进冻结哈希、不过 Gate）
    narrative = _rich(canon_signals)
    assert "《周易·师》" in narrative


def test_canon_leak_into_freeze_would_fail_gate():
    """反向证明安全网有效：若经文进入冻结描述，DefinitionAttack 必 FAIL。"""
    from app.adversarial.attacks.deterministic import DefinitionAttack
    from app.adversarial.attacks.base import AttackContext, Verdict

    leak = (
        "9月5日（周六）事业或有突发任务。经文参读：《周易·师》大君有命，"
        "开国承家，小人勿用。"
    )
    ctx = AttackContext(
        description=leak,
        event_type="career.unexpected_task",
        success_criteria=["窗口内出现一次临时交办任务并留下记录"],
        failure_criteria=["窗口内无任何临时交办任务记录"],
    )
    outcome = DefinitionAttack().run(ctx)
    assert outcome.verdict is Verdict.FAIL
    assert "小人" in outcome.reason
