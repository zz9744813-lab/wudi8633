"""周易义理引擎 + 日卦×命数×未来事件 回归测试（round 12）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import app.models.core  # noqa: F401
from app.models.core import BirthProfile


# ======================================================================
# 1. 注册与枚举
# ======================================================================
def test_zhouyi_registered_as_eighth_engine():
    from app.core.base import registry
    from app.schemas.signal import SourceType

    sources = {a.source for a in registry.all()}
    assert SourceType.ZHOUYI in sources
    assert len(sources) == 8
    zy = registry.get("zhouyi")
    assert zy is not None and zy.available


def test_zhouyi_in_scale_support_and_reliability_floor(sesh):
    from app.learning.reliability import ReliabilityMatrix
    from app.schemas.signal import SourceType, TimeScale

    weights = ReliabilityMatrix(sesh, user_id=1).fusion_weights()
    assert weights.get("zhouyi") == 0.42, "未实证源必须落在回测先验地板（zhouyi 方向弃权）"
    sup = SourceType.ZHOUYI


# ======================================================================
# 2. 断辞扫描
# ======================================================================
def test_gloss_score_overlap_masking():
    from app.core.zhouyi.adapter import gloss_score

    # 「无咎」命中后内层「咎」不得再计（降权后 0.2）
    score, hits = gloss_score("无咎。")
    assert score == 0.2
    assert hits == ["无咎"]
    # 「大吉」优先于「吉」
    score2, hits2 = gloss_score("元吉，大吉。")
    assert score2 == pytest.approx(1.0 + 0.95)
    assert hits2 == ["元吉", "大吉"]
    # 反向
    score3, _ = gloss_score("凶，无攸利。")
    assert score3 == pytest.approx(-1.0 - 0.6)
    # 无断辞
    score4, hits4 = gloss_score("姤其角。")
    assert score4 == 0.0 and hits4 == []


# ======================================================================
# 3. adapter 端到端
# ======================================================================
def _mk_query(target_time: str = "10:30", domain="career"):
    from app.core.base import AdapterQuery
    from app.schemas.signal import Domain, TimeScale, TimeWindow

    start = datetime(2026, 9, 5)
    return AdapterQuery(
        user_id=1,
        domain=Domain(domain),
        target_event="career.unexpected_task",
        time_scale=TimeScale.DAY,
        window=TimeWindow(start=start, end=start + timedelta(hours=24)),
        target_date=date(2026, 9, 5),
        target_time=target_time,
    )


def test_zhouyi_adapter_deterministic_and_evidence():
    from app.core.zhouyi.adapter import ZhouyiAdapter

    a = ZhouyiAdapter()
    q = _mk_query()
    chart = a.compute_chart(q)
    assert chart.get("ben_gua", {}).get("name")
    s1 = a.to_signals(q, chart)
    s2 = a.to_signals(q, a.compute_chart(q))
    assert s1 and s2, "周易 adapter 必须产出信号"
    assert s1[0].direction == s2[0].direction
    assert s1[0].direction in (1.0, -1.0, 0.0)
    assert s1[0].dependency_group == "yi_jing"
    descs = [e.description for e in s1[0].evidence]
    assert any(d.startswith("经文参读：《周易·") for d in descs)
    assert any("义理断辞扫描" in d for d in descs)
    assert s1[0].rule_ids[0].startswith("ZHOUYI-R-")


def test_zhouyi_signals_across_24h_no_crash():
    from app.core.zhouyi.adapter import ZhouyiAdapter

    a = ZhouyiAdapter()
    for h in range(24):
        q = _mk_query(f"{h:02d}:30")
        sigs = a.to_signals(q, a.compute_chart(q))
        assert sigs, h


# ======================================================================
# 4. 全法盘点六术
# ======================================================================
def test_full_tally_now_six_engines():
    from app.schemas.signal import TimeScale
    from app.services.cross_engine import rich_description

    start = datetime(2026, 9, 5)
    out = rich_description(
        event_type="career.unexpected_task",
        label="事业或有突发任务",
        scale=TimeScale.DAY,
        window_start=start.date(),
        window_end=(start + timedelta(hours=24)).date(),
        signals=[],
        almanac=None,
    )
    for eng in ("八字", "紫微", "六爻", "梅花", "周易", "奇门"):
        assert f"{eng}○ 未表态" in out, eng
    assert "六术同参" in out


# ======================================================================
# 5. 日卦 × 命数 × 未来事件
# ======================================================================
@pytest.fixture(name="sesh")
def _sesh():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_daily_gua_trigram_wuxing_generic(sesh):
    from app.services.cross_engine import daily_almanac

    out = daily_almanac(sesh, 1, date(2026, 9, 5))
    g = out["daily_gua"]
    assert g["upper_gua"] and g["upper_wuxing"]
    assert g["lower_gua"] and g["lower_wuxing"]
    # 通用版（无档案）无命数结合
    assert "natal_notes" not in g
    assert "related_predictions" not in out or out["related_predictions"] == []


def test_daily_gua_natal_notes_with_profile(sesh):
    sesh.add(
        BirthProfile(
            user_id=7,
            solar_birth_date=date(1986, 8, 12),
            solar_birth_time="10:30",
            birth_time_known=True,
            gender="male",
        )
    )
    sesh.commit()
    from app.services.cross_engine import daily_almanac

    out = daily_almanac(sesh, 7, date(2026, 9, 5))
    g = out["daily_gua"]
    assert g.get("natal_verdict") in ("身强", "身弱", "中和")
    notes = g.get("natal_notes") or []
    assert notes, "个人版应有命数结合句"
    assert "日主" in notes[0], "卦气句应结合日主"
    assert any("动在第" in n for n in notes), "应有动爻爻位句"


def test_daily_related_predictions_join(sesh):
    from app.models.prediction import PredictionRecord
    from app.schemas.prediction import PredictionStatus
    from app.schemas.signal import Domain, TimeScale
    from app.services.cross_engine import daily_almanac

    sesh.add(
        PredictionRecord(
            prediction_id="P-rp1",
            user_id=7,
            domain=Domain.CAREER.value,
            event_type="career.task",
            description="9月5日（周六）临时工作安排。",
            probability=0.42,
            null_probability=0.4,
            time_scale=TimeScale.DAY.value,
            window_start=datetime(2026, 9, 5),
            window_end=datetime(2026, 9, 5, 23, 59, 59),
            success_criteria=["发生"],
            failure_criteria=["未发生"],
            grading_rule="二值",
            status=PredictionStatus.RESEARCH.value,
            visibility_mode="VISIBLE",
        )
    )
    # 不覆盖 9/6 的窗口不应出现在 9/5 的参读里
    sesh.add(
        PredictionRecord(
            prediction_id="P-rp2",
            user_id=7,
            domain=Domain.MONEY.value,
            event_type="money.expense",
            description="9月8日（周二）计划外支出。",
            probability=0.3,
            null_probability=0.3,
            time_scale=TimeScale.DAY.value,
            window_start=datetime(2026, 9, 8),
            window_end=datetime(2026, 9, 8, 23, 59, 59),
            success_criteria=["发生"],
            failure_criteria=["未发生"],
            grading_rule="二值",
            status=PredictionStatus.RESEARCH.value,
            visibility_mode="VISIBLE",
        )
    )
    sesh.commit()

    out = daily_almanac(sesh, 7, date(2026, 9, 5))
    rp = out.get("related_predictions", [])
    ids = {x["prediction_id"] for x in rp}
    assert "P-rp1" in ids
    assert "P-rp2" not in ids, "窗口不覆盖当日的事件不得入选"


def test_natal_reading_differs_by_verdict(sesh):
    """车轱辘话回归：不同命数的人，同一卦的批示必须不同。"""
    from app.services.cross_engine import daily_almanac

    sesh.add(
        BirthProfile(user_id=101, solar_birth_date=date(1975, 11, 3), solar_birth_time="11:00", birth_time_known=True, gender="male")
    )
    sesh.add(
        BirthProfile(user_id=102, solar_birth_date=date(1986, 8, 12), solar_birth_time="10:30", birth_time_known=True, gender="male")
    )
    sesh.commit()
    n1 = daily_almanac(sesh, 101, date(2026, 9, 5))["daily_gua"].get("natal_notes") or []
    n2 = daily_almanac(sesh, 102, date(2026, 9, 5))["daily_gua"].get("natal_notes") or []
    assert n1 and n2
    assert n1 != n2, "不同命盘的日卦批示不得是同一句模板"


def test_qimen_renshi_main_signal():
    """回测战果回归：奇门主断必须来自日干(人)×时干(事)宫生克，非恒负的门/格局。"""
    from app.core.base import AdapterQuery, registry
    from app.schemas.signal import Domain, TimeScale, TimeWindow

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(BirthProfile(user_id=1, solar_birth_date=date(1961, 8, 4),
                           solar_birth_time="19:24", birth_time_known=True, gender="male"))
        s.commit()
        q = AdapterQuery(user_id=1, domain=Domain.CAREER, target_event="b.c",
                         time_scale=TimeScale.DAY,
                         window=TimeWindow(start=datetime(2008, 11, 4),
                                           end=datetime(2008, 11, 4, 23, 59)),
                         target_date=date(2008, 11, 4), target_time="12:00", session=s)
        qm = registry.get("qimen")
        sigs = qm.signals(q)
        assert sigs and not sigs[0].degraded
        descs = [e.description for e in sigs[0].evidence]
        assert any("人）" in d and "事）" in d for d in descs), "应有人事宫关系主断"


def test_zhouyi_boilerplate_gloss_neutral():
    """回测战果回归：「元亨利贞」这类高频套话不得再产生正向方向。"""
    from app.core.zhouyi.adapter import gloss_score

    score, hits = gloss_score("元亨利贞。")
    assert abs(score) < 0.5, f"套话应落在阈下，实际 {score}（{hits}）"
