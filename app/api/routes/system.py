"""系统路由：用户档案 / 引擎状态 / 规则 / 本体 / 对抗性测试 / 实验。

对应工程方案：
- 第 6 节 Metaphysical Engine
- 第 25 节 Rule Registry
- 第 34 节 双盲实验模式
- 第 56 节 Event Ontology
- 第 64 节 隐私
- 第 65 节 关键安全边界
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.base import registry as adapter_registry
from app.database import get_session
from app.models.core import BirthProfile, User
from app.models.registry import Rule
from app.prediction.ontology import ONTOLOGY, all_event_types

router = APIRouter()


# ======================================================================
# 引擎状态
# ======================================================================
@router.get("/system/engines")
def engines():
    """七个术式 Adapter 的可用性。不可用时返回 degraded 原因。"""
    return {
        "engines": [
            {
                "source": a.source.value,
                "engine": a.engine_name,
                "version": a.engine_version,
                "available": a.available,
            }
            for a in adapter_registry.all()
        ],
        "available_count": len(adapter_registry.available_sources()),
    }


# ======================================================================
# 用户与出生档案
# ======================================================================
class BirthProfileIn(BaseModel):
    solar_birth_date: date
    solar_birth_time: str = "00:00"
    birth_time_known: bool = False
    gender: str = "unknown"
    birth_place: str = ""
    longitude: float | None = None
    latitude: float | None = None
    use_true_solar_time: bool = True


class UserIn(BaseModel):
    user_key: str
    display_name: str = ""
    timezone: str = "Asia/Shanghai"
    birth_profile: BirthProfileIn | None = None


@router.post("/users")
def create_user(payload: UserIn, session: Session = Depends(get_session)):
    existing = session.exec(select(User).where(User.user_key == payload.user_key)).first()
    if existing:
        raise HTTPException(409, f"用户已存在：{payload.user_key}")

    user = User(
        user_key=payload.user_key,
        display_name=payload.display_name,
        timezone=payload.timezone,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    if payload.birth_profile:
        bp = payload.birth_profile
        session.add(
            BirthProfile(
                user_id=user.id,  # type: ignore[arg-type]
                solar_birth_date=bp.solar_birth_date,
                solar_birth_time=bp.solar_birth_time,
                birth_time_known=bp.birth_time_known,
                gender=bp.gender,
                birth_place=bp.birth_place,
                longitude=bp.longitude,
                latitude=bp.latitude,
                use_true_solar_time=bp.use_true_solar_time,
            )
        )
        session.commit()
        from app.services import cross_engine

        cross_engine._ALMANAC_CACHE.clear()

    return {"user_id": user.id, "user_key": user.user_key}


@router.get("/users")
def list_users(session: Session = Depends(get_session)):
    rows = session.exec(select(User)).all()
    return {"count": len(rows), "items": [{"id": u.id, "user_key": u.user_key} for u in rows]}


@router.get("/users/{user_id}/profile")
def get_profile(user_id: int, session: Session = Depends(get_session)):
    """出生档案属高敏感个人数据（第 64 节）。骨架阶段不做鉴权，部署前必须加。"""
    profile = session.exec(
        select(BirthProfile).where(BirthProfile.user_id == user_id)
    ).first()
    if profile is None:
        raise HTTPException(404, "未找到出生档案")
    return profile


@router.put("/users/{user_id}/profile")
def update_profile(user_id: int, payload: BirthProfileIn, session: Session = Depends(get_session)):
    """更新出生档案（出生时间 / 性别 / 出生地等）。

    用户首次创建时只能填出生日期，后续可在这里补全出生时间（时辰）、
    性别、出生地，用于八字/紫微排盘的时柱与阳男阴女判断。
    """
    profile = session.exec(
        select(BirthProfile).where(BirthProfile.user_id == user_id)
    ).first()
    if profile is None:
        raise HTTPException(404, "未找到出生档案，请先创建用户并填写出生日期")

    profile.solar_birth_date = payload.solar_birth_date
    profile.solar_birth_time = payload.solar_birth_time
    profile.birth_time_known = payload.birth_time_known
    profile.gender = payload.gender
    profile.birth_place = payload.birth_place
    profile.longitude = payload.longitude
    profile.latitude = payload.latitude
    profile.use_true_solar_time = payload.use_true_solar_time
    session.add(profile)
    session.commit()
    session.refresh(profile)
    # 档案变更会使今日锦囊的个人化字段（日主喜忌/桃花引动/冲日支）变化，
    # 必须失效进程内缓存，否则 10 分钟内仍是旧档案口径。
    from app.services import cross_engine

    cross_engine._ALMANAC_CACHE.clear()
    return profile


# ======================================================================
# Event Ontology（第 56 节）
# ======================================================================
@router.get("/ontology")
def ontology(domain: str | None = None, scale: str | None = None):
    items = [
        {
            "event_type": s.event_type,
            "domain": s.domain,
            "label": s.label,
            "success_criteria": list(s.success_criteria),
            "failure_criteria": list(s.failure_criteria),
            "preferred_scales": list(s.preferred_scales),
        }
        for s in ONTOLOGY.values()
        if (domain is None or s.domain == domain)
        and (scale is None or scale in s.preferred_scales)
    ]
    return {"count": len(items), "items": items, "all_types": all_event_types()}


# ======================================================================
# Rule Registry（第 25 节）
# ======================================================================
@router.get("/rules")
def list_rules(
    school: str | None = None,
    status: str | None = "active",
    session: Session = Depends(get_session),
):
    stmt = select(Rule)
    if school:
        stmt = stmt.where(Rule.school == school)
    if status:
        stmt = stmt.where(Rule.status == status)
    rows = session.exec(stmt).all()
    return {
        "count": len(rows),
        "items": [
            {
                "rule_id": r.rule_id,
                "school": r.school,
                "description": r.description,
                "domains": r.domains,
                "supported_windows": r.supported_windows,
                "version": r.version,
                "status": r.status,
            }
            for r in rows
        ],
    }


# ======================================================================
# 对抗性 Gate 手动测试（第 20 / 21 节）
# ======================================================================
class GateTestIn(BaseModel):
    description: str = ""
    event_type: str = ""
    probability: float | None = None
    null_probability: float | None = None
    success_criteria: list[str] = Field(default_factory=list)
    failure_criteria: list[str] = Field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None
    signals: list[dict[str, Any]] = Field(default_factory=list)
    agent_texts: dict[str, str] = Field(default_factory=dict)


@router.post("/adversarial/gate-test")
def gate_test(payload: GateTestIn):
    """手动跑一遍 14 种攻击，用于调试与教学。

    这是核心基础设施，不是附属 Agent（第 20 节）。
    """
    from app.adversarial.attacks.base import AttackContext
    from app.adversarial.gate import AdversarialGate

    groups: dict[str, list[str]] = {}
    for s in payload.signals:
        key = s.get("dependency_group") or f"solo:{s.get('source', 'unknown')}"
        groups.setdefault(key, []).append(str(s.get("source", "unknown")))

    ctx = AttackContext(
        description=payload.description,
        event_type=payload.event_type,
        probability=payload.probability,
        null_probability=payload.null_probability,
        success_criteria=payload.success_criteria,
        failure_criteria=payload.failure_criteria,
        window_start=payload.window_start,
        window_end=payload.window_end,
        signals=payload.signals,
        dependency_groups=groups,
        agent_texts=payload.agent_texts,
    )

    result = AdversarialGate().run(ctx)
    return {
        "decision": result.decision,
        "attacks": [
            {
                "attack": o.attack,
                "verdict": o.verdict.value,
                "severity": o.severity,
                "reason": o.reason,
                "details": o.details,
            }
            for o in result.outcomes
        ],
    }


# ======================================================================
# LLM Provider 配置（第 41 / 42 节）
# ======================================================================
class LLMConfigIn(BaseModel):
    tier: Literal["reasoning", "cheap", "vision"]
    # None = 不动该字段；空字符串 = 清除该字段的覆盖（回退 .env）
    base_url: str | None = None
    model: str | None = None
    # 空字符串/None = 沿用现有 key；提供新值 = 覆盖。
    # 想彻底停用某一层：把 base_url 或 model 清空即可（configured=False）。
    api_key: str | None = None


class LLMConfigTestIn(BaseModel):
    tier: Literal["reasoning", "cheap", "vision"]
    # 可选：用「未保存的草稿」直接测试；不传则用当前生效配置
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


@router.get("/system/llm-config")
def get_llm_config():
    """三层的有效配置视图。API Key 只返回脱敏形式（第 41 节）。"""
    from app.services.llm_config import describe

    return {"tiers": describe()}


@router.put("/system/llm-config")
def put_llm_config(payload: LLMConfigIn):
    """保存某一层的配置到运行时覆盖层（data/llm_config.json）。"""
    from app.services.llm_config import describe, update_tier

    update_tier(
        payload.tier,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key if payload.api_key else None,
    )
    return {"ok": True, "tiers": describe()}


@router.post("/system/llm-config/test")
def test_llm_config(payload: LLMConfigTestIn):
    """用指定（或当前生效）配置发一次最小调用，验证连通性。

    注意：只返回延迟/模型/错误摘要，不回显 key。
    """
    from app.config import ProviderSettings
    from app.providers.base import LLMRequest, OpenAICompatibleProvider
    from app.services.llm_config import effective_provider

    eff = effective_provider(payload.tier)
    ps = ProviderSettings(
        base_url=payload.base_url or eff.base_url,
        model=payload.model or eff.model,
        api_key=payload.api_key or eff.api_key,
    )
    if not ps.configured:
        return {"ok": False, "error": "配置不完整：base_url 与 model 必填", "configured": False}

    provider = OpenAICompatibleProvider(ps, tier=payload.tier)
    resp = provider.complete(
        LLMRequest(
            messages=[{"role": "user", "content": "ping，回复 pong 即可"}],
            # 推理模型（deepseek-v4-flash / glm-5.2）的思考链路也计入 max_tokens，
            # 太小时正文没开始写就被截断，sample 会变成思考链路开头而非回复。
            max_tokens=120,
            temperature=0,
        )
    )
    return {
        "ok": resp.ok,
        "configured": True,
        "model": resp.model,
        "duration_ms": resp.duration_ms,
        # sample 优先取正文；正文为空时展示思考链路开头（推理模型截断场景）
        "sample": (resp.content or resp.reasoning or "")[:40],
        "error": resp.error,
    }


# ======================================================================
# 日历快照（第 6 节 Calendar Core）
# ======================================================================
@router.get("/calendar/snapshot")
def calendar_snapshot(
    user_id: int = Query(...),
    target_date: date | None = None,
    session: Session = Depends(get_session),
):
    """第 6 节：所有术式共享同一个 Calendar Core。"""
    from app.core.calendar.core import CalendarCore

    profile = session.exec(
        select(BirthProfile).where(BirthProfile.user_id == user_id)
    ).first()
    if profile is None:
        raise HTTPException(404, "未找到出生档案，无法计算历法快照")

    target_date = target_date or date.today()
    core = CalendarCore()
    result = core.compute(
        birth_date=profile.solar_birth_date,
        birth_time=profile.solar_birth_time,
        target_date=target_date,
        gender=profile.gender,
        use_true_solar_time=profile.use_true_solar_time,
        longitude=profile.longitude,
    )
    return {
        "target_date": target_date.isoformat(),
        "degraded": result.degraded,
        "degrade_reason": result.degrade_reason,
        "payload": result.payload,
    }


# ======================================================================
# 命理批示（本命盘解读 + 大运 / 流年运势）
# ======================================================================
@router.get("/fortune/reading")
def fortune_reading(
    user_id: int = Query(...),
    refresh: bool = Query(False, description="true 时强制重新生成并覆盖缓存"),
    session: Session = Depends(get_session),
):
    """传统术数命盘解读 + 未来运势批示。

    与预测闭环严格区分：这是纯展示，不进入 Fusion、不参与评分。
    第 6.1 节：程序排盘，LLM 只做解读（禁止自行算盘）。
    结果按 user_id + 出生档案指纹缓存，命中直接返回（cached=True）。
    """
    from app.services.fortune import generate_reading

    return generate_reading(session, user_id=user_id, refresh=refresh)


@router.get("/fortune/reading/{system}")
def fortune_reading_system(
    system: str,
    user_id: int = Query(...),
    refresh: bool = Query(False, description="true 时强制重新生成并覆盖缓存"),
    session: Session = Depends(get_session),
):
    """分术式命理批示（当前支持 ziwei）。

    与 /fortune/reading（八字）并列。独立接口便于前端两条解读并行加载，
    各自缓存互不影响（system_fortune_readings 表，按 system+档案指纹缓存）。
    """
    if system != "ziwei":
        raise HTTPException(404, f"暂不支持术式批示：{system}（当前仅 ziwei）")
    from app.services.fortune import generate_ziwei_reading

    return generate_ziwei_reading(session, user_id=user_id, refresh=refresh)


@router.get("/fortune/daily")
def fortune_daily(
    user_id: int = Query(...),
    date: str | None = Query(None, description="YYYY-MM-DD，缺省为今天"),
    session: Session = Depends(get_session),
):
    """今日锦囊：宜忌 / 吉神方位 / 冲煞 / 吉时 / 幸运色数 / 桃花引动。

    完全由确定性历法（lunar-python 老黄历）+ 民俗规则派生，秒出，
    无 LLM 参与；属传统术数参考展示，不进入预测闭环与评分。
    """
    from app.services.cross_engine import daily_almanac

    from datetime import date as date_cls

    try:
        day = date_cls.fromisoformat(date) if date else date_cls.today()
    except ValueError:
        raise HTTPException(400, "date 格式应为 YYYY-MM-DD") from None
    return daily_almanac(session, user_id, day)
