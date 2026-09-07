"""全面审查修复的回归测试（round 11）。

覆盖审查报告的 P1/P2/P3 项：
1. 历法常量完整性：DIZHI_WUXING 12 字（戌土/亥水），任意时刻不降级；
2. 六爻降级分支可用（hour_branch 曾未定义 → UnboundLocalError）；
3. 六爻婚恋用神依性别（男/默认 → 妻财，女 → 官鬼）；
4. 八字扶抑法：强弱判定、中和盘不发信号、周尺度用流日；
5. 验证闭环：快捷裁决 A/B/C 直通、D 转待确认、C-003 不可改口；
6. Judge 失败感知：≥2 方 confidence=0 → needs_confirmation；
7. 正式期与研究期共用去重键；
8. DefinitionAttack 扫 grading_rule。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine


# ======================================================================
# 1/2. 历法常量 + 六爻降级分支
# ======================================================================
def test_calendar_dizhi_wuxing_complete():
    from app.core.calendar.core import DIZHI, DIZHI_WUXING, TIANGAN, TIANGAN_WUXING

    assert len(DIZHI_WUXING) == len(DIZHI) == 12, "地支五行表长度必须为 12（曾漏戌）"
    assert len(TIANGAN_WUXING) == len(TIANGAN) == 10
    assert DIZHI_WUXING[DIZHI.index("戌")] == "土"
    assert DIZHI_WUXING[DIZHI.index("亥")] == "水"


def test_calendar_no_degrade_across_all_hours():
    """亥时曾因常量越界整段降级（每天 21:00-22:59 排盘全废）。"""
    from app.core.calendar.core import CalendarCore

    core = CalendarCore()
    for h in range(24):
        r = core.compute(
            birth_date=date(2026, 9, 5),
            birth_time=f"{h:02d}:30",
            target_date=date(2026, 9, 5),
            target_time=f"{h:02d}:30",
            gender="unknown",
        )
        assert not r.degraded, (h, r.degrade_reason)


def test_liuyao_cast_chart_all_hours_no_crash():
    """六爻降级分支缺 hour_branch 曾导致 UnboundLocalError。"""
    from app.core.liuyao.engine import cast_chart

    for h in (0, 6, 12, 18, 21, 22, 23):
        chart = cast_chart(datetime(2026, 9, 5, h, 30))
        assert "error" not in chart or chart.get("lines"), h


# ======================================================================
# 3. 六爻性别用神
# ======================================================================
def _mk_query(domain, target_time="10:30"):
    """构造 AdapterQuery 并注入临时库 session + 固定档案作为确定性预言机。

    不注入时 adapter 会回退到全局引擎读开发者真实档案（测试即依赖环境）。
    """
    from app.core.base import AdapterQuery
    from app.database import create_db_and_tables, engine as db_engine
    from app.models.core import BirthProfile
    from app.schemas.signal import TimeScale, TimeWindow

    create_db_and_tables()  # 本文件不走 client 夹具，临时库表需自建
    session = Session(db_engine)
    from sqlmodel import select as _sel

    if session.exec(_sel(BirthProfile).where(BirthProfile.user_id == 1)).first() is None:
        session.add(
            BirthProfile(
                user_id=1,
                solar_birth_date=date(1990, 5, 15),
                solar_birth_time="14:30",
                birth_time_known=True,
                gender="male",
                birth_place="北京",
                longitude=116.4,
                latitude=39.9,
            )
        )
        session.commit()
    start = datetime(2026, 9, 5)
    return AdapterQuery(
        user_id=1,
        domain=domain,
        target_event="relationship.candidate",
        time_scale=TimeScale.DAY,
        window=TimeWindow(start=start, end=start + timedelta(hours=24)),
        target_date=date(2026, 9, 5),
        target_time=target_time,
        session=session,
    )


def test_liuyao_relationship_yongshen_by_gender():
    from app.core.liuyao.adapter import LiuyaoAdapter
    from app.schemas.signal import Domain

    la = LiuyaoAdapter()
    q = _mk_query(Domain.RELATIONSHIP)
    checked_m = checked_f = 0
    for h in range(24):
        chart = la.compute_chart(_mk_query(Domain.RELATIONSHIP, f"{h:02d}:30"))
        sig = la.to_signals(q, chart)
        if sig:
            assert "妻财" in sig[0].evidence[0].description  # 默认（无档案）
            checked_m += 1
        chart["_querent_gender"] = "female"
        sig_f = la.to_signals(q, chart)
        if sig_f:
            assert "官鬼" in sig_f[0].evidence[0].description
            checked_f += 1
    assert checked_m > 0 and checked_f > 0, "24 小时扫样应有用神可取的盘"


# ======================================================================
# 4. 八字扶抑法
# ======================================================================
def test_bazi_strength_verdicts():
    from app.core.bazi.adapter import day_master_strength

    # 金比劫成势 → 身强
    strong = {"year": "庚申", "month": "甲申", "day": "庚辰", "time": "庚辰", "day_master": "庚"}
    score, verdict, _ = day_master_strength(strong)
    assert verdict == "身强" and score >= 2
    # 判定必须落在三档之一
    mixed = {"year": "庚午", "month": "辛巳", "day": "庚辰", "time": "癸未", "day_master": "庚"}
    _, v2, _ = day_master_strength(mixed)
    assert v2 in ("身强", "身弱", "中和")


def test_bazi_neutral_chart_zero_direction():
    """中和盘 direction=0（曾把一切非喜用硬记成反向 -1）。"""
    from app.core.bazi.adapter import BaziAdapter, day_master_strength
    from app.schemas.signal import Domain

    a = BaziAdapter()
    q = _mk_query(Domain.CAREER)
    chart = a.compute_chart(q)
    _, verdict, _ = day_master_strength(chart["bazi"])
    sigs = a.to_signals(q, chart)
    assert sigs, "八字 adapter 必须产出信号（中和也是观察）"
    sig = sigs[0]
    assert not sig.degraded
    if verdict == "中和":
        assert sig.direction == 0.0, "中和盘不得给方向"
    else:
        assert sig.direction in (1.0, -1.0)
    assert "扶抑" in sig.evidence[0].description


def test_bazi_week_scale_uses_day_pillar():
    from app.core.bazi.adapter import BaziAdapter
    from app.schemas.signal import Domain, TimeScale

    a = BaziAdapter()
    q = _mk_query(Domain.CAREER, "10:30")
    # 复制为周尺度
    qw = q.model_copy(update={"time_scale": TimeScale.WEEK})
    chart = a.compute_chart(q)
    s1 = a.to_signals(q, chart)
    s2 = a.to_signals(qw, chart)
    if s1 and s2:
        # 日与周都对照流日干支 → 证据中的干支一致
        g1 = s1[0].evidence[0].description.split("流日")[-1]
        g2 = s2[0].evidence[0].description.split("流日")[-1]
        assert g1.split("（")[0] == g2.split("（")[0]


# ======================================================================
# 5/6. 验证闭环（TestClient 级）
# ======================================================================
@pytest.fixture(name="env")
def _env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlmodel", fromlist=["StaticPool"]).StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    from app.database import get_session
    from app.main import app

    with Session(engine) as s:
        def _override():
            yield s

        app.dependency_overrides[get_session] = _override
        yield TestClient(app), engine
        app.dependency_overrides.clear()


def _seed_user(engine, uid: int = 1):
    from sqlmodel import Session

    from app.models.core import BirthProfile

    with Session(engine) as s:
        s.add(
            BirthProfile(
                user_id=uid,
                solar_birth_date=date(1990, 5, 15),
                solar_birth_time="14:30",
                birth_time_known=True,
                gender="male",
            )
        )
        s.commit()


def test_verify_quick_answer_is_authoritative(env):
    """P1①：快捷 A/B/C 必须直接决定 outcome，不经 LLM。"""
    client, engine = env
    _seed_user(engine)
    client.post("/api/predictions/generate?user_id=1&scale=day&limit=15")
    items = client.get("/api/predictions?user_id=1").json()["items"]
    if not items:
        pytest.skip("无预测可验证")
    pid = items[0]["prediction_id"]

    r = client.post(f"/api/predictions/{pid}/verify", params={"quick_answer": "A"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["outcome"] == 1.0, "点「命中」必须记录 1.0"
    assert data["confidence"] == 1.0
    assert data["status"] == "VERIFIED"


def test_verify_d_goes_waiting_without_outcome(env):
    """P1①：快捷 D=无法判定 → WAITING_USER，不落 OutcomeRecord、不评分。"""
    from sqlmodel import Session

    from app.models.scoring import OutcomeRecord

    client, engine = env
    _seed_user(engine)
    client.post("/api/predictions/generate?user_id=1&scale=day&limit=15")
    items = client.get("/api/predictions?user_id=1").json()["items"]
    if not items:
        pytest.skip("无预测可验证")
    pid = items[0]["prediction_id"]

    r = client.post(f"/api/predictions/{pid}/verify", params={"quick_answer": "D"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "WAITING_USER"
    assert data["outcome"] is None
    with Session(engine) as s:
        from sqlmodel import select

        assert (
            s.exec(select(OutcomeRecord).where(OutcomeRecord.prediction_id == pid)).first()
            is None
        ), "无法判定不得落结果"


def test_verify_no_re_adjudication_c003(env):
    """P1③：已批复的预测不可改口。"""
    client, engine = env
    _seed_user(engine)
    client.post("/api/predictions/generate?user_id=1&scale=day&limit=15")
    items = client.get("/api/predictions?user_id=1").json()["items"]
    if not items:
        pytest.skip("无预测可验证")
    pid = items[0]["prediction_id"]

    r1 = client.post(f"/api/predictions/{pid}/verify", params={"quick_answer": "A"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/predictions/{pid}/verify", params={"quick_answer": "B"})
    assert r2.status_code == 409, "第二次批复必须被拒（C-003）"


def test_from_verdicts_all_failed_goes_confirmation():
    """P1②：三方 Judge 全挂（confidence=0）→ 转人工，不得静默记未中。"""
    from app.agents.verification_agents import OutcomeJudgeAgent
    from app.schemas.outcome import JudgeRole, JudgeVerdict, Outcome

    pid = "P-test"
    verdicts = [
        JudgeVerdict(
            prediction_id=pid,
            role=role,
            outcome=0.0,
            confidence=0.0,
            reasoning="Judge 不可用",
        )
        for role in (JudgeRole.PROSECUTION, JudgeRole.DEFENSE, JudgeRole.NEUTRAL)
    ]
    o = Outcome.from_verdicts(pid, verdicts)
    assert o.needs_confirmation is True, "全部失败必须转人工"
    assert o.outcome == 0.0  # 均值仍为 0，但已标记需人工

    # 两方失败一方正常：也转人工
    verdicts[2] = JudgeVerdict(
        prediction_id=pid,
        role=JudgeRole.NEUTRAL,
        outcome=1.0,
        confidence=0.8,
        reasoning="ok",
    )
    o2 = Outcome.from_verdicts(pid, verdicts)
    assert o2.needs_confirmation is True

    # 全部正常：不转人工
    verdicts_ok = [
        JudgeVerdict(prediction_id=pid, role=role, outcome=1.0, confidence=0.8, reasoning="ok")
        for role in (JudgeRole.PROSECUTION, JudgeRole.DEFENSE, JudgeRole.NEUTRAL)
    ]
    o3 = Outcome.from_verdicts(pid, verdicts_ok)
    assert o3.needs_confirmation is False


# ======================================================================
# 7. 正式期/研究期共用去重键
# ======================================================================
def test_existing_sample_keys_dedup(env):
    from app.services.pipeline import DailyPipeline
    from sqlmodel import Session

    client, engine = env
    _seed_user(engine)
    client.post("/api/predictions/generate?user_id=1&scale=day&limit=15")
    with Session(engine) as s:
        keys = DailyPipeline(s, 1)._existing_sample_keys()
        # 生成的都是 RESEARCH 样本，键集非空且为三元组
        assert keys
        for et, ts, ws in keys:
            assert isinstance(et, str) and isinstance(ws, date)


# ======================================================================
# 8. DefinitionAttack 扫 grading_rule
# ======================================================================
def test_definition_attack_scans_grading_rule():
    from app.adversarial.attacks.base import AttackContext, Verdict
    from app.adversarial.attacks.deterministic import DefinitionAttack

    ctx = AttackContext(
        description="9月5日（周六）临时工作安排。",
        event_type="career.unexpected_task",
        success_criteria=["窗口内出现一次临时交办任务并留下记录"],
        failure_criteria=["窗口内无任何临时交办任务记录"],
        grading_rule="出现小人即记为发生",
    )
    outcome = DefinitionAttack().run(ctx)
    assert outcome.verdict is Verdict.FAIL
    assert "小人" in outcome.reason


def test_history_items_carry_label_and_description(env):
    """时间线中文化：history 必须带中文 label 与冻结断言原文。"""
    client, engine = env
    _seed_user(engine)
    client.post("/api/predictions/generate?user_id=1&scale=day&limit=15")
    items = client.get("/api/predictions?user_id=1").json()["items"]
    if not items:
        pytest.skip("无预测")
    pid = items[0]["prediction_id"]
    client.post(f"/api/predictions/{pid}/verify", params={"quick_answer": "A"})
    hist = client.get("/api/predictions/history?user_id=1").json()["items"]
    assert hist
    top = hist[0]
    assert top.get("label") and top["label"] != top["event_type"], "label 应为本体中文名"
    assert top.get("description"), "应带冻结断言原文"


# ======================================================================
# 9/10. round-22 审查 P3 遗留：D 留痕 + 日卦缓存
# ======================================================================
def test_verify_d_leaves_request_trail(env):
    """P3：快捷 D（无法判定）不落结果，但必须落 OutcomeRequestRecord 留痕；
    随后补批复 A 时复用同一请求行（answered_at 补齐），不撞唯一键。"""
    from sqlmodel import Session, select

    from app.models.scoring import OutcomeRequestRecord

    client, engine = env
    _seed_user(engine)
    client.post("/api/predictions/generate?user_id=1&scale=day&limit=15")
    items = client.get("/api/predictions?user_id=1").json()["items"]
    if not items:
        pytest.skip("无预测可验证")
    pid = items[0]["prediction_id"]

    # 先按 D（附言：审计轨迹的一部分）
    r1 = client.post(
        f"/api/predictions/{pid}/verify",
        params={"quick_answer": "D", "user_reply": "现场情况复杂，无法核对"},
    )
    assert r1.status_code == 200 and r1.json()["status"] == "WAITING_USER"
    with Session(engine) as s:
        reqs = s.exec(
            select(OutcomeRequestRecord).where(
                OutcomeRequestRecord.prediction_id == pid
            )
        ).all()
        assert len(reqs) == 1, "按 D 必须留一条验证请求记录"
        assert reqs[0].quick_answer == "D"
        assert reqs[0].user_reply == "现场情况复杂，无法核对"
        assert reqs[0].answered_at is None

    # 再补 A：复用 D 占的请求行（唯一键不炸），answered_at 补齐；
    # 且 D 的裁定与附言不得被抹掉（要追记进轨迹）
    r2 = client.post(f"/api/predictions/{pid}/verify", params={"quick_answer": "A"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["outcome"] == 1.0
    with Session(engine) as s:
        reqs = s.exec(
            select(OutcomeRequestRecord).where(
                OutcomeRequestRecord.prediction_id == pid
            )
        ).all()
        assert len(reqs) == 1, "补批复应复用 D 占的请求行而不是多插一行"
        assert reqs[0].quick_answer == "A"
        assert reqs[0].answered_at is not None
        assert reqs[0].user_reply == "现场情况复杂，无法核对", "D 的附言不得被覆盖抹掉"
        assert "先前裁定D" in reqs[0].ambiguity_note, "D 裁定轨迹必须保留在注记里"


def test_daily_almanac_short_cache(env):
    """P3：同日同用户的今日锦囊第二次命中进程内缓存（不重算），跨日期不吃旧。"""
    from datetime import date as _date

    from sqlmodel import Session

    from app.services import cross_engine

    client, engine = env
    _seed_user(engine)
    cross_engine._ALMANAC_CACHE.clear()

    with Session(engine) as s:
        a = cross_engine.daily_almanac(s, 1, _date(2026, 9, 7))
        n_after_first = len(cross_engine._ALMANAC_CACHE)
        b = cross_engine.daily_almanac(s, 1, _date(2026, 9, 7))
        n_after_second = len(cross_engine._ALMANAC_CACHE)
        assert n_after_first == n_after_second == 1, "同日重复请求不得独写新条目"
        assert a is b, "缓存命中应返回同一对象"
        # 换日即重算（且单用户桌面语义下全清后仅一条）
        c = cross_engine.daily_almanac(s, 1, _date(2026, 9, 8))
        assert c["day_ganzhi"] != a["day_ganzhi"]
    cross_engine._ALMANAC_CACHE.clear()
