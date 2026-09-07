"""分析与评分路由。

对应工程方案：
- 第 19 节 评分系统
- 第 26 节 Personal Reliability Matrix
- 第 30 节 周报
- 第 31 节 月度模型审计
- 第 33 节 Ablation Test
- 第 52 节 Accuracy Lab
- 第 78 节 小样本保护
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.utils import utcnow
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.calibration.scoring import (
    Aggregate,
    ScoreRow,
    aggregate,
)
from app.database import get_session
from app.learning.reliability import ReliabilityMatrix
from app.models.scoring import PredictionScore

router = APIRouter()


# ======================================================================
# 总览（第 52 节 Accuracy Lab）
# ======================================================================
@router.get("/analytics/overall")
def overall(
    user_id: int | None = Query(None, description="不传则统计全部用户"),
    domain: str | None = None,
    time_scale: str | None = None,
    session: Session = Depends(get_session),
):
    """Brier / LogLoss / Calibration / Skill Score / Sharpness / 置信区间。"""
    rows = _load_rows(session, user_id=user_id, domain=domain, time_scale=time_scale)
    agg = aggregate(rows)
    return {"filters": {"user_id": user_id, "domain": domain, "time_scale": time_scale}, **agg.to_dict()}


# ======================================================================
# 校准曲线（第 19.3 节）
# ======================================================================
@router.get("/analytics/calibration")
def calibration(
    user_id: int | None = None,
    session: Session = Depends(get_session),
):
    """Predicted vs Actual 分桶表。前端用于画 ECharts 校准曲线。"""
    rows = _load_rows(session, user_id=user_id)
    agg = aggregate(rows)
    return {
        "bins": [
            {
                "bin": f"{b.bin_lower:.0%}-{b.bin_upper:.0%}",
                "n": b.sample_count,
                "predicted": b.mean_predicted,
                "actual": b.mean_actual,
                "gap": round(b.gap, 4),
            }
            for b in (agg.bins or [])
        ],
        "overconfidence": agg.overconfidence,
        "sample_size": agg.sample_size,
        "reliability": agg.reliability,
    }


# ======================================================================
# Personal Reliability Matrix（第 26 节）
# ======================================================================
@router.get("/analytics/reliability")
def reliability(
    user_id: int | None = None,
    session: Session = Depends(get_session),
):
    """相对 Null Model 的 predictive skill 矩阵。

    注意：这里保存的是 skill，不是命中率。
    """
    return ReliabilityMatrix(session, user_id=user_id).matrix()


# ======================================================================
# Ablation（第 33 节）
# ======================================================================
@router.get("/analytics/ablation")
def ablation(
    user_id: int | None = None,
    session: Session = Depends(get_session),
):
    """第 33 节：判断每个模块价值的消融实验（实时重算 + 落库）。

    系统应允许得到「不好听」的结果，例如：
        Reality：强贡献 / Qimen：强贡献 / Liuyao：弱贡献 /
        Ziwei：很弱贡献 / Bazi：当前无贡献
    """
    from app.services.ablation import run_ablation

    result = run_ablation(session, user_id=user_id)
    if result.get("status") == "ok":
        return {
            "status": "ok",
            "sample_size": result["sample_size"],
            "results": result["results"],
            "note": "相对 Full Model 的 Brier 差异：正值 = 该模块有正贡献",
        }
    return result


# ======================================================================
# 分组对比（按术式 / 领域 / 尺度）
# ======================================================================
@router.get("/analytics/by/{dimension}")
def by_dimension(
    dimension: str,
    user_id: int | None = None,
    session: Session = Depends(get_session),
):
    """第 52 节：按 术式 / 领域 / 时间尺度 / 规则 / Agent / 模型 / Prompt 筛选。"""
    if dimension not in {"domain", "time_scale"}:
        return {"error": f"暂不支持维度：{dimension}（骨架阶段支持 domain / time_scale）"}

    stmt = select(PredictionScore)
    if user_id is not None:
        stmt = stmt.where(PredictionScore.user_id == user_id)
    scores = session.exec(stmt).all()

    buckets: dict[str, list[ScoreRow]] = {}
    for s in scores:
        key = (s.domain if dimension == "domain" else s.time_scale) or "unknown"
        buckets.setdefault(key, []).append(
            ScoreRow(
                probability=s.probability,
                outcome=s.outcome,
                null_probability=s.null_probability,
            )
        )

    return {
        "dimension": dimension,
        "groups": {k: aggregate(v).to_dict() for k, v in buckets.items()},
    }


# ======================================================================
# Future Tree（第 27 节）
# ======================================================================
@router.get("/future-tree")
def future_tree(
    user_id: int = Query(...),
    as_of: date | None = None,
    session: Session = Depends(get_session),
):
    """人生情景树：当前轨迹继续 / 职业变化 / 新项目重心。

    第 27 节：每周按新证据重算 P(Scenario | New Evidence)。
    """
    from app.services.future_tree import FutureTreeBuilder

    return FutureTreeBuilder(session, user_id=user_id).build(as_of=as_of)


# ======================================================================
# Counterfactual（第 28 节）
# ======================================================================
class CounterfactualIn(BaseModel):
    interventions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="[{'label': '每天学习1小时', 'effects': {'study': 0.3}}]",
    )
    horizon_days: int = 365


@router.post("/counterfactual")
def counterfactual(
    user_id: int = Query(...),
    payload: CounterfactualIn | None = None,
    session: Session = Depends(get_session),
):
    """Baseline vs Intervention 对比（Decision Intelligence，第 28 节）。"""
    from app.services.counterfactual import CounterfactualEngine

    payload = payload or CounterfactualIn()
    return CounterfactualEngine(session, user_id=user_id).compare(
        interventions=payload.interventions,
        horizon_days=payload.horizon_days,
    )


# ======================================================================
# 双盲实验（第 34 节）
# ======================================================================
class BlindRunIn(BaseModel):
    limit: int = Field(default=6, ge=1, le=20)
    scale: str = Field(default="day", pattern="^(day|week|month|year)$")
    target_date: date | None = None


@router.post("/experiments/run-blind")
def run_blind_experiment(
    user_id: int = Query(...),
    payload: BlindRunIn | None = None,
    session: Session = Depends(get_session),
):
    """第 34 节：三组盲跑对比。

        A：Reality + Null（无术数）
        B：Metaphysical Only（术数 + Null，无 Reality）
        C：Fusion（全部）
    长期比较三组的概率质量，判断术数是否有增量。
    """
    from app.models.learning import ExperimentRun
    from app.services.pipeline import DailyPipeline

    payload = payload or BlindRunIn()
    import uuid

    arms = [
        ("A_reality_null", "reality_null"),
        ("B_metaphysical_only", "metaphysical_only"),
        ("C_fusion", None),
    ]

    results = {}
    for label, arm in arms:
        pipe = DailyPipeline(session, user_id=user_id)
        pipe.experiment_arm = arm
        r = pipe.run(
            target_date=payload.target_date or (date.today() + timedelta(days=1)),
            scale=payload.scale,
            limit=payload.limit,
        )
        results[label] = {
            "frozen": len(r.frozen),
            "candidates": len(r.candidates),
            "rejected": len(r.rejected),
            "notes": r.notes[:3],
        }
        session.add(
            ExperimentRun(
                run_id=f"BLIND-{uuid.uuid4().hex[:8]}",
                mode="blind_ab",
                arm=label,
                started_at=utcnow(),
                sample_size=len(r.frozen),
                note="三组盲跑（第 34 节）",
            )
        )
    session.commit()

    return {
        "status": "ok",
        "arms": results,
        "note": "三组独立预测，长期比较 Brier/Skill（需验证后统计）",
    }


# ======================================================================
# Obsidian 导出（第 62 节）
# ======================================================================
@router.get("/export/obsidian")
def export_obsidian(
    user_id: int = Query(...),
    base_dir: str = Query("./data/obsidian", description="导出目录"),
    session: Session = Depends(get_session),
):
    """数据库 → Obsidian 目录结构（数据库是权威源，Obsidian 是展示层）。"""
    from app.services.exports import export_obsidian_vault

    return export_obsidian_vault(session, user_id=user_id, base_dir=base_dir)


@router.get("/export/daily")
def export_daily(
    user_id: int = Query(...),
    target_date: date | None = None,
    session: Session = Depends(get_session),
):
    """第 63 节：Markdown Daily Forecast。"""
    from app.services.exports import daily_forecast_markdown

    return {
        "markdown": daily_forecast_markdown(
            session, user_id=user_id, target_date=target_date
        )
    }


# ======================================================================
# 周报 / 月报 / 审计（第 30 / 31 / 32 节）
# ======================================================================
@router.get("/reports/weekly")
def report_weekly(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    from app.services.reports import weekly_report

    return {"markdown": weekly_report(session, user_id=user_id)}


@router.get("/reports/monthly")
def report_monthly(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    from app.services.reports import monthly_report

    return {"markdown": monthly_report(session, user_id=user_id)}


@router.get("/reports/audit")
def report_audit(
    user_id: int = Query(...),
    session: Session = Depends(get_session),
):
    """第 32 节：第一性原理审计（10 问）。"""
    from app.services.reports import audit_report

    return audit_report(session, user_id=user_id)


# ======================================================================
def _load_rows(
    session: Session,
    *,
    user_id: int | None,
    domain: str | None = None,
    time_scale: str | None = None,
) -> list[ScoreRow]:
    stmt = select(PredictionScore)
    if user_id is not None:
        stmt = stmt.where(PredictionScore.user_id == user_id)
    if domain:
        stmt = stmt.where(PredictionScore.domain == domain)
    if time_scale:
        stmt = stmt.where(PredictionScore.time_scale == time_scale)

    return [
        ScoreRow(
            probability=s.probability,
            outcome=s.outcome,
            null_probability=s.null_probability,
        )
        for s in session.exec(stmt).all()
    ]


# ======================================================================
# 公众人物回测（静态产物只读）
# ======================================================================
@router.get("/analytics/backtest")
def public_figure_backtest():
    """tools/backtest_figures.py 最近一次跑出的 74 人 × 165 事件回测结果。

    只读静态产物（docs/回测数据-公众人物.json），不在请求时重算。结果用于发现
    系统性 bug 与校准种子，不构成术式预测力证明（C-006）：数据集正向偏置，
    命中率须联合正向倾向解读。
    """
    import json
    import math
    import sys
    from pathlib import Path

    candidates = []
    repo = Path(__file__).resolve().parents[3] / "docs" / "回测数据-公众人物.json"
    candidates.append(repo)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "docs" / "回测数据-公众人物.json")
        candidates.append(Path(meipass).parent / "docs" / "回测数据-公众人物.json")

    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return {"available": False, "note": "未找到回测产物（PyInstaller 包应含 docs/回测数据-公众人物.json）"}

    data = json.loads(path.read_text(encoding="utf-8"))

    def binom_two_sided_p(k: int, n: int) -> float:
        """精确二项双侧检验 P(X <= k) * 2 截断到 1，H0: p=0.5。"""
        if n == 0:
            return 1.0
        k = min(k, n - k)
        p = sum(math.comb(n, i) for i in range(0, k + 1)) * (0.5 ** n) * 2
        return min(1.0, p)

    per_source = {}
    for src, b in (data.get("per_source") or {}).items():
        hit = int(b.get("hit", 0) or 0)
        miss = int(b.get("miss", 0) or 0)
        abstain = int(b.get("abstain", 0) or 0)
        error = int(b.get("error", 0) or 0)
        n = hit + miss
        per_source[src] = {
            "hit": hit,
            "miss": miss,
            "abstain": abstain,
            "error": error,
            "coverage": (n / max(1, n + abstain + error)),
            "hit_rate": (hit / n) if n else None,
            "p_value": binom_two_sided_p(hit, n),
        }

    pillars = data.get("pillars") or []
    return {
        "available": True,
        "figures": len(pillars),
        "pillar_ok": sum(1 for p in pillars if p.get("ok")),
        "n_events": data.get("n_events", 0),
        "n_positive": data.get("n_positive", 0),
        "per_source": per_source,
        "caveat": "正向事件占多数，命中率须联合正向倾向解读；不构成术式效力证明（C-006）",
    }
