"""预测相关路由。

对应工程方案：
- 第 29 节 每日用户体验（首页 / 晚间验证）
- 第 49 节 Prediction Detail（完全可解释）
- 第 50 节 Verification Inbox
- 第 51 节 Prediction History（强制同时展示成败）
- 第 80 节 Prediction Lineage
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from app.utils import utcnow
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

logger = logging.getLogger("xuanmirror.api.predictions")

from app.agents.verification_agents import OutcomeCollectorAgent, OutcomeJudgeAgent
from app.database import get_session
from app.models.prediction import PredictionFreeze, PredictionRecord, SignalRecord
from app.models.scoring import OutcomeRecord, OutcomeRequestRecord, PredictionScore
from app.schemas.prediction import Prediction, PredictionStatus
from app.schemas.signal import Evidence, TimeScale
from app.services.pipeline import DailyPipeline

router = APIRouter()


# ======================================================================
# 生成预测（第 58 节手动触发）
# ======================================================================
@router.post("/predictions/generate")
def generate_predictions(
    user_id: int = Query(..., description="用户 ID"),
    scale: str = Query("day", pattern="^(day|week|month|year)$"),
    limit: int = Query(20, ge=1, le=100),
    target_date: date | None = None,
    session: Session = Depends(get_session),
):
    """跑一次完整预测闭环：扫描 → 盲审 → 融合 → Gate → 预算 → 冻结。"""
    pipeline = DailyPipeline(session, user_id=user_id)
    result = pipeline.run(target_date=target_date, scale=scale, limit=limit)
    return result.to_dict()


# ======================================================================
# 列表（第 29.1 节首页）
# ======================================================================
@router.get("/predictions")
def list_predictions(
    user_id: int = Query(...),
    status: str | None = Query(None, description="如 FROZEN / VERIFIED"),
    domain: str | None = None,
    include_hidden: bool = Query(
        False, description="第 35 节：默认不返回 HIDDEN 预测，防止自我实现"
    ),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    stmt = select(PredictionRecord).where(PredictionRecord.user_id == user_id)
    if status:
        stmt = stmt.where(PredictionRecord.status == status)
    if domain:
        stmt = stmt.where(PredictionRecord.domain == domain)
    if not include_hidden:
        stmt = stmt.where(PredictionRecord.visibility_mode == "VISIBLE")

    rows = session.exec(stmt.order_by(PredictionRecord.created_at.desc()).limit(limit)).all()

    # 一次查询取回所有展示的预测的信号来源与交叉印证数（卡片徽标用）。
    # N+1 规避：按 prediction_id 分组到内存。
    signal_map: dict[str, dict[str, Any]] = {}
    if rows:
        from collections import defaultdict

        sigs = session.exec(
            select(SignalRecord).where(
                SignalRecord.prediction_id.in_([r.prediction_id for r in rows])  # type: ignore[union-attr]
            )
        ).all()
        grouped: dict[str, list[SignalRecord]] = defaultdict(list)
        for s in sigs:
            grouped[s.prediction_id].append(s)
        for pid, slist in grouped.items():
            supporting = {
                s.source_type
                for s in slist
                if not s.degraded
                and s.direction > 0
                and s.source_type not in ("null", "reality")
            }
            opposing = {
                s.source_type
                for s in slist
                if not s.degraded
                and s.direction < 0
                and s.source_type not in ("null", "reality")
            }
            signal_map[pid] = {
                "supporting_sources": sorted(supporting),
                "opposing_sources": sorted(opposing),
                "crossed": len(supporting) >= 2,
            }

    items = []
    for r in rows:
        brief = _brief(r)
        brief.update(signal_map.get(r.prediction_id, {}))
        # 富文本详批（展示层，deterministic 重建，不进冻结哈希）
        try:
            from app.services.cross_engine import narrative_for_record

            brief["narrative"] = narrative_for_record(session, r, grouped.get(r.prediction_id, []))
        except Exception:
            brief["narrative"] = ""
        items.append(brief)
    return {"count": len(rows), "items": items}


# ======================================================================
# 待验证收件箱（第 50 节）
# ======================================================================
@router.get("/predictions/due")
def due_predictions(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    """第 59 节：到期主动进入 VERIFY_REQUIRED，等待用户验证。"""
    now = utcnow()
    rows = session.exec(
        select(PredictionRecord)
        .where(PredictionRecord.user_id == user_id)
        .where(PredictionRecord.status.in_([  # type: ignore[union-attr]
            PredictionStatus.FROZEN.value,
            PredictionStatus.RESEARCH.value,  # 冷启动研究样本同样可验证，用于积累校准数据
            PredictionStatus.VERIFY_REQUIRED.value,
            PredictionStatus.WAITING_USER.value,
        ]))
        .where(PredictionRecord.verification_due_at <= now)  # type: ignore[union-attr]
    ).all()
    return {
        "count": len(rows),
        "items": [
            {
                "prediction_id": r.prediction_id,
                "event_type": r.event_type,
                "description": r.description,
                "probability": r.probability,
                "success_criteria": r.success_criteria,
                "failure_criteria": r.failure_criteria,
                "window": [r.window_start.isoformat(), r.window_end.isoformat()],
                "status": r.status,
            }
            for r in rows
        ],
    }


# ======================================================================
# 历史（第 51 节：强制同时展示成功与失败）
# ======================================================================
@router.get("/predictions/history")
def prediction_history(
    user_id: int = Query(...),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    """第 51 节：默认必须同时展示成功、失败、部分、无法判断。
    禁止产品设计诱导只看「神预测」。
    """
    rows = session.exec(
        select(PredictionRecord, OutcomeRecord)
        .join(OutcomeRecord, OutcomeRecord.prediction_id == PredictionRecord.prediction_id)  # type: ignore[arg-type]
        .where(PredictionRecord.user_id == user_id)
        .order_by(PredictionRecord.created_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    ).all()

    from app.prediction.ontology import ONTOLOGY

    items = []
    for pred, out in rows:
        _spec = ONTOLOGY.get(pred.event_type)
        items.append(
            {
                "prediction_id": pred.prediction_id,
                "event_type": pred.event_type,
                "label": _spec.label if _spec else pred.event_type,
                "description": pred.description,
                "probability": pred.probability,
                "outcome": out.outcome,
                "null_probability": pred.null_probability,
                "brier": round((pred.probability - out.outcome) ** 2, 4),
                "judged_at": out.judged_at.isoformat(),
            }
        )
    return {"count": len(items), "items": items}


# ======================================================================
# 详情（第 49 节：完全可解释）
# ======================================================================
@router.get("/predictions/{prediction_id}")
def get_prediction(
    prediction_id: str,
    session: Session = Depends(get_session),
):
    """第 49 节要求展开：预测 / 概率 / 窗口 / 成功失败定义 /
    Metaphysical Signals / Reality Signal / Null Probability /
    Agent Disagreement / Evidence Dependency / Adversarial Checks /
    Freeze Hash / Prompt Version / Model Version / Rule Version
    """
    row = session.exec(
        select(PredictionRecord).where(PredictionRecord.prediction_id == prediction_id)
    ).first()
    if row is None:
        raise HTTPException(404, f"未找到预测：{prediction_id}")

    signals = session.exec(
        select(SignalRecord).where(SignalRecord.prediction_id == prediction_id)
    ).all()

    freeze = session.exec(
        select(PredictionFreeze).where(PredictionFreeze.prediction_id == prediction_id)
    ).first()

    outcome = session.exec(
        select(OutcomeRecord).where(OutcomeRecord.prediction_id == prediction_id)
    ).first()

    # 第 20.7 节：事后校验完整性。
    # 主校验直接对冻结快照重算哈希 —— 从其他表重建会因 signal_id 等
    # 随机字段产生假阳性。
    integrity = None
    if row.sha256 and freeze:
        recomputed = Prediction.hash_payload(freeze.freeze_payload)
        integrity = {
            "stored_hash": row.sha256,
            "recomputed_hash": recomputed,
            "ok": recomputed == row.sha256,
        }
        # 辅助校验：主表字段是否也与冻结快照一致
        rebuilt = _rebuild(row, signals)
        if rebuilt is not None and rebuilt.prediction_id == row.prediction_id:
            integrity["rebuild_matches_payload"] = (
                rebuilt.hash_payload(rebuilt.freeze_payload())
                == Prediction.hash_payload(freeze.freeze_payload)
            )

    # Agent 分歧（第 49 节）
    values = [s.direction * s.strength for s in signals if not s.degraded]
    disagreement = (max(values) - min(values)) if len(values) >= 2 else 0.0

    # 证据依赖（第 20.12 节）
    groups: dict[str, list[str]] = {}
    for s in signals:
        key = s.dependency_group or f"solo:{s.source_type}"
        groups.setdefault(key, []).append(s.source_type)

    # 展示层详批（deterministic 重建，不进冻结哈希）
    narrative = ""
    try:
        from app.services.cross_engine import narrative_for_record

        narrative = narrative_for_record(session, row, list(signals))
    except Exception:
        narrative = ""

    return {
        "prediction_id": row.prediction_id,
        "domain": row.domain,
        "event_type": row.event_type,
        "description": row.description,
        "narrative": narrative,
        "probability": row.probability,
        "null_probability": row.null_probability,
        "window": [row.window_start.isoformat(), row.window_end.isoformat()],
        "time_scale": row.time_scale,
        "sha256_head": (row.sha256 or "")[:16],
        "verification_due_at": (
            row.verification_due_at.isoformat() if row.verification_due_at else None
        ),
        "success_criteria": row.success_criteria,
        "failure_criteria": row.failure_criteria,
        "grading_rule": row.grading_rule,
        "status": row.status,
        "visibility_mode": row.visibility_mode,
        "frozen_at": row.frozen_at.isoformat() if row.frozen_at else None,
        "signals": [
            {
                "signal_id": s.signal_id,
                "source": s.source_type,
                "direction": s.direction,
                "strength": s.strength,
                "confidence": s.confidence,
                "dependency_group": s.dependency_group,
                "rule_ids": s.rule_ids,
                "degraded": s.degraded,
                "degrade_reason": s.degrade_reason,
                "evidence": s.evidence,
                "counter_evidence": s.counter_evidence,
            }
            for s in signals
        ],
        "agent_disagreement": round(disagreement, 4),
        "evidence_dependency": groups,
        "integrity": integrity,
        "versions": {
            "model": row.model_version,
            "fusion": row.fusion_version,
            "prompt": row.prompt_version,
            "rule": row.rule_version,
            "engine": row.engine_version,
        },
        "lineage": {"candidate_id": row.candidate_id, "version": row.version},
        "outcome": (
            {
                "outcome": outcome.outcome,
                "confidence": outcome.confidence,
                "needs_confirmation": outcome.needs_confirmation,
                "disagreement": outcome.disagreement,
            }
            if outcome
            else None
        ),
    }


# ======================================================================
# 提交验证（第 17 / 60 节）
# ======================================================================
# 桌面端 FastAPI 同步端点跑在线程池；双击 verify 会并发进本函数。
# request_id 唯一键冲突会冒成 500，用进程内锁收窄「查重→插入」窗口。
_REQUEST_LOCK = threading.Lock()


def _record_request(
    session: Session,
    prediction_id: str,
    *,
    user_reply: str | None = None,
    quick_answer: str | None = None,
    ambiguous: bool = False,
    ambiguity_note: str = "",
    answered: bool = False,
) -> OutcomeRequestRecord:
    """验证留痕统一入口。

    - 该预测已有未应答请求（D / 歧义先占的坑）→ 复用并补全，避免同预测一行行 D 堆积；
      复用时**保留首次 D 的轨迹**：旧裁定/旧附言被新裁决替换前追记进 ambiguity_note，
      非空字段不被空值覆盖——否则「无法判定」一事连同附言会从审计视角消失；
    - 否则新建，request_id 追加序号保唯一（R-{尾12}、-1、-2…）。
    """
    with _REQUEST_LOCK:
        reqs = session.exec(
            select(OutcomeRequestRecord).where(
                OutcomeRequestRecord.prediction_id == prediction_id
            )
        ).all()
        pending = [r for r in reqs if r.answered_at is None]
        if pending:
            req = sorted(pending, key=lambda r: r.asked_at)[-1]
            if req.quick_answer and quick_answer and req.quick_answer != quick_answer:
                trail = f"先前裁定{req.quick_answer}"
                if req.user_reply:
                    trail += f"（{req.user_reply[:40]}）"
                req.ambiguity_note = (
                    f"{req.ambiguity_note}；{trail}" if req.ambiguity_note else trail
                )
            req.quick_answer = quick_answer or req.quick_answer
            req.user_reply = user_reply or req.user_reply
            req.ambiguous = req.ambiguous or ambiguous
            if ambiguity_note:
                req.ambiguity_note = (
                    f"{req.ambiguity_note}；{ambiguity_note}"
                    if req.ambiguity_note
                    else ambiguity_note
                )
            if answered and req.answered_at is None:
                req.answered_at = utcnow()
        else:
            suffix = "" if not reqs else f"-{len(reqs)}"
            req = OutcomeRequestRecord(
                request_id=f"R-{prediction_id[-12:]}{suffix}",
                prediction_id=prediction_id,
                user_reply=user_reply,
                quick_answer=quick_answer,
                ambiguous=ambiguous,
                ambiguity_note=ambiguity_note,
                answered_at=utcnow() if answered else None,
            )
        session.add(req)
        session.flush()  # 让唯一键冲突在锁内暴露，而非等到端点提交时 500
    return req


@router.post("/predictions/{prediction_id}/verify")
def verify_prediction(
    prediction_id: str,
    user_reply: str | None = None,
    quick_answer: str | None = Query(None, pattern="^[ABCD]$"),
    session: Session = Depends(get_session),
):
    """用户自然语言回复 → OutcomeCollector → 三方 Judge → 结构化结果。

    第 60 节：若可能对应多个预测，必须要求明确对应关系，不能强行命中。

    判定权威分层（C-003 + 批复式 UI 语义）：
        - 快捷裁决 A/B/C：用户是判定权威，直接落 outcome（不经 LLM）；
        - 快捷裁决 D：无法判定 → WAITING_USER，不落 outcome、不评分；
        - 自然语言：三方 Judge 审读用户描述（分歧 / Judge 失败 → 转人工）。
    """
    row = session.exec(
        select(PredictionRecord).where(PredictionRecord.prediction_id == prediction_id)
    ).first()
    if row is None:
        raise HTTPException(404, f"未找到预测：{prediction_id}")

    # ---------- 0. C-003：已批复的预测不可事后改口 ----------
    existing_outcome = session.exec(
        select(OutcomeRecord).where(OutcomeRecord.prediction_id == prediction_id)
    ).first()
    if existing_outcome is not None:
        raise HTTPException(409, "该预测已批复归档，不可改口（C-003）。")

    from app.agents.base import AgentContext
    from app.schemas.outcome import Outcome

    # ---------- 1. Collector：解析用户回复 ----------
    collector_ctx = AgentContext(
        user_id=row.user_id,
        session=session,
        target_event=row.event_type,
        domain=row.domain,
        payload={
            "user_reply": user_reply or "",
            "quick_answer": quick_answer,
            "candidate_prediction_ids": [prediction_id],
        },
        prediction_id=prediction_id,
    )
    collected = OutcomeCollectorAgent().run(collector_ctx)

    # 歧义 → 不强行判定（第 60 节）
    # 注：当前 payload 恒为单候选，collector 只在多候选时置 ambiguous——
    # 此分支现不可达，留作将来多候选解析的防御。
    if collected.output.get("ambiguous"):
        row.status = PredictionStatus.WAITING_USER.value
        session.add(row)
        _record_request(
            session,
            prediction_id,
            user_reply=user_reply,
            quick_answer=quick_answer,
            ambiguous=True,
            ambiguity_note=collected.output.get("ambiguity_note") or "",
        )
        session.commit()
        return {
            "status": "WAITING_USER",
            "message": collected.output.get("ambiguity_note"),
        }

    quick = (quick_answer or "").strip().upper()

    # ---------- 2a. 快捷裁决 D：无法判定 → 转人工补充描述 ----------
    if quick == "D":
        row.status = PredictionStatus.WAITING_USER.value
        session.add(row)
        # D 不落 outcome，但要留痕：否则「无法判定」从审计视角不可见（P3 审查项）
        _record_request(
            session,
            prediction_id,
            user_reply=user_reply,
            quick_answer="D",
        )
        session.commit()
        return {
            "prediction_id": prediction_id,
            "outcome": None,
            "confidence": 0.0,
            "needs_confirmation": True,
            "disagreement": 0.0,
            "judges": [],
            "status": PredictionStatus.WAITING_USER.value,
            "message": "已标记无法判定：补充一句实际情况后再批复即可（不落结果、不计分）。",
        }

    # ---------- 2b. 快捷裁决 A/B/C：用户直判（权威，不经 LLM）----------
    if quick in ("A", "B", "C"):
        quick_value = {"A": 1.0, "B": 0.0, "C": 0.5}[quick]
        quick_label = {"A": "命中", "B": "未中", "C": "部分命中"}[quick]
        outcome = Outcome(
            prediction_id=prediction_id,
            outcome=quick_value,
            confidence=1.0,
            evidence=(
                f"用户快捷裁决：{quick_label}"
                + (f"（附言：{user_reply}）" if user_reply else "")
            ),
            needs_confirmation=False,
            disagreement=0.0,
            verdicts=[],
            judged_at=utcnow(),
        )
    else:
        # ---------- 2c. 自然语言：三方 Judge（第 20.13 节）----------
        judge_ctx = AgentContext(
            user_id=row.user_id,
            session=session,
            target_event=row.event_type,
            domain=row.domain,
            payload={
                "prediction": {
                    "description": row.description,
                    "success_criteria": row.success_criteria,
                    "failure_criteria": row.failure_criteria,
                    "grading_rule": row.grading_rule,
                    "probability": row.probability,
                },
                "user_reply": user_reply or "",
            },
            prediction_id=prediction_id,
        )
        outcome = OutcomeJudgeAgent().judge(judge_ctx, prediction_id)

    # ---------- 3. 落库 ----------
    # 注：歧义已在上方提前 return（当前 collector 单候选恒不歧义），此处不再传 ambiguous 死值。
    req = _record_request(
        session,
        prediction_id,
        user_reply=user_reply,
        quick_answer=quick_answer,
        answered=True,
    )

    rec = OutcomeRecord(
        outcome_id=outcome.outcome_id,
        prediction_id=prediction_id,
        request_id=req.request_id,
        outcome=outcome.outcome,
        confidence=outcome.confidence,
        evidence=outcome.evidence,
        needs_confirmation=outcome.needs_confirmation,
        disagreement=outcome.disagreement,
        judged_at=outcome.judged_at,
    )
    session.add(rec)

    row.status = (
        PredictionStatus.WAITING_USER.value
        if outcome.needs_confirmation
        else PredictionStatus.VERIFIED.value
    )
    session.add(row)
    session.commit()

    # ---------- 4. 评分（第 19 节）----------
    if not outcome.needs_confirmation:
        _score(session, row, outcome.outcome)

        # 第 22-26 节：预测错误驱动学习 —— 归因 → 假设 → Shadow → 规则统计 → 可靠度回喂
        try:
            from app.services.learning import run_learning_after_verify

            learning = run_learning_after_verify(
                session, prediction_id=prediction_id, user_id=row.user_id
            )
        except Exception as exc:
            learning = {"status": "error", "reason": str(exc)}
            logger.error("学习闭环失败：%s", exc)

    return {
        "prediction_id": prediction_id,
        "outcome": outcome.outcome,
        "confidence": outcome.confidence,
        "needs_confirmation": outcome.needs_confirmation,
        "disagreement": round(outcome.disagreement, 3),
        "judges": [
            {"role": v.role.value, "outcome": v.outcome, "confidence": v.confidence}
            for v in outcome.verdicts
        ],
        "status": row.status,
    }


# ======================================================================
# 辅助函数
# ======================================================================


# ======================================================================
def _brief(r: PredictionRecord) -> dict[str, Any]:
    return {
        "prediction_id": r.prediction_id,
        "domain": r.domain,
        "event_type": r.event_type,
        "description": r.description,
        "probability": r.probability,
        "null_probability": r.null_probability,
        "time_scale": r.time_scale,
        "status": r.status,
        "visibility_mode": r.visibility_mode,
        "window": [r.window_start.isoformat(), r.window_end.isoformat()],
        "verification_due_at": (
            r.verification_due_at.isoformat() if r.verification_due_at else None
        ),
        "sha256_head": (r.sha256 or "")[:16],
    }


def _rebuild(
    r: PredictionRecord, signal_rows: list[SignalRecord] | None = None
) -> Prediction | None:
    """从 DB 行重建 Prediction 以校验哈希（第 20.7 节）。

    必须传 signal_rows：冻结哈希包含 signals，重建时缺了信号
    就会算出不同的哈希，导致完整性校验误报「被篡改」。
    """
    try:
        signals = [
            Signal(
                # signal_id 参与冻结哈希，重建时必须从库里原样恢复，
                # 否则会重新随机生成，导致哈希不匹配、误报「被篡改」。
                signal_id=s.signal_id,
                source=s.source_type,
                domain=s.domain,
                target_event=s.target_event,
                direction=s.direction,
                strength=s.strength,
                confidence=s.confidence,
                time_window={"start": s.window_start, "end": s.window_end},
                time_scale=TimeScale(s.time_scale),
                rule_ids=s.rule_ids,
                dependency_group=s.dependency_group,
                engine_version=s.engine_version,
                prompt_version=s.prompt_version,
                degraded=s.degraded,
                degrade_reason=s.degrade_reason,
                # 冻结哈希包含 evidence，重建时缺了就会误报「被篡改」
                evidence=[Evidence(**e) for e in (s.evidence or []) if isinstance(e, dict)],
                counter_evidence=[
                    Evidence(**e) for e in (s.counter_evidence or []) if isinstance(e, dict)
                ],
            )
            for s in (signal_rows or [])
        ]
        return Prediction(
            prediction_id=r.prediction_id,
            user_id=str(r.user_id),
            domain=r.domain,  # type: ignore[arg-type]
            event_type=r.event_type,
            description=r.description,
            probability=r.probability,
            window_start=r.window_start,
            window_end=r.window_end,
            time_scale=TimeScale(r.time_scale),
            success_criteria=r.success_criteria,
            failure_criteria=r.failure_criteria,
            grading_rule=r.grading_rule,
            null_probability=r.null_probability,
            model_version=r.model_version,
            fusion_version=r.fusion_version,
            prompt_version=r.prompt_version,
            rule_version=r.rule_version,
            engine_version=r.engine_version,
            version=r.version,
            signals=signals,
        )
    except Exception:
        return None


def _score(session: Session, row: PredictionRecord, outcome: float) -> None:
    """第 19 节：写入 Brier / LogLoss / Skill 贡献。"""
    from app.calibration.scoring import brier, log_loss, skill_score

    existing = session.exec(
        select(PredictionScore).where(PredictionScore.prediction_id == row.prediction_id)
    ).first()
    if existing:
        return

    null_brier = (
        (row.null_probability - outcome) ** 2 if row.null_probability is not None else None
    )
    contribution = (
        skill_score(brier(row.probability, outcome), null_brier)
        if null_brier and null_brier > 0
        else None
    )

    # 第 26 节：可靠度矩阵按 (source, domain, scale) 学习，必须记录本条预测
    # 实际参与融合的信号来源与规则；否则 by_source() 永远为空，skill 回喂环断掉。
    sig_rows = session.exec(
        select(SignalRecord).where(SignalRecord.prediction_id == row.prediction_id)
    ).all()
    source_types = sorted({s.source_type for s in sig_rows})
    rule_ids = sorted({rid for s in sig_rows for rid in (s.rule_ids or [])})

    session.add(
        PredictionScore(
            prediction_id=row.prediction_id,
            user_id=row.user_id,
            probability=row.probability,
            outcome=outcome,
            brier=brier(row.probability, outcome),
            log_loss=log_loss(row.probability, outcome),
            null_probability=row.null_probability,
            null_brier=null_brier,
            skill_contribution=contribution,
            domain=row.domain,
            time_scale=row.time_scale,
            source_types=source_types,
            rule_ids=rule_ids,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        # 并发双击 verify：另一线程已写入同预测评分，数据一致，静默采信已有行
        session.rollback()
