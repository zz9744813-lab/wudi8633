"""每日预测闭环编排。

对应工程方案：
- 第 2.1 节 每日闭环
- 第 3 节 系统主动预测（系统不等待用户提问）
- 第 12 节 Blind Multi-Agent
- 第 21 节 对抗性 Gate
- 第 58 节 Scheduler

    23:30 更新 Reality State
    23:40 Future Scanner
    23:45 术数计算
    23:50 多 Agent + 对抗审查
    23:55 Freeze tomorrow predictions
    次日晚 Outcome verification

第 12 节核心约束：
    各术式 Agent 独立计算，提交 Fusion 前互不知晓彼此结论。
    本管线在结构上保证这点：每个 Adapter/Agent 只接收自己的输入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.utils import utcnow
from typing import Any

from sqlmodel import Session, select

from app.agents.base import AgentContext
from app.agents.fusion import fuse, FusionInput
from app.agents.pipeline_agents import (
    CandidateAgent,
    FreezeAgent,
    FutureScannerAgent,
)
from app.agents.registry import BaziAgent, NullAgent, RealityAgent, ZiweiAgent
from app.adversarial.attacks.base import AttackContext
from app.adversarial.gate import AdversarialGate
from app.config import get_settings
from app.core.base import AdapterQuery
from app.core.calendar.core import CalendarCore
from app.models.prediction import ForecastCandidate, PredictionFreeze, PredictionRecord
from app.models.reality import DailyState
from app.services.cross_engine import daily_almanac, summarize_signals, when_text
from app.prediction.budget import apply_budget, default_slots
from app.prediction.ontology import ONTOLOGY, by_scale
from app.reality.null_model import NullModel
from app.reality.state import build_reality_state, persist_daily_state
from app.schemas.prediction import Prediction, PredictionCandidate, PredictionStatus
from app.schemas.signal import Domain, Signal, TimeScale, TimeWindow

logger = logging.getLogger(__name__)

SCALE_BY_NAME = {
    "day": TimeScale.DAY,
    "week": TimeScale.WEEK,
    "month": TimeScale.MONTH,
    "year": TimeScale.YEAR,
}


@dataclass
class PipelineResult:
    """一次管线运行的产物。"""

    target_date: date
    scanned: int = 0
    candidates: list[PredictionCandidate] = field(default_factory=list)
    frozen: list[Prediction] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    budget_usage: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": self.target_date.isoformat(),
            "scanned": self.scanned,
            "candidate_count": len(self.candidates),
            "frozen": [
                {
                    "prediction_id": p.prediction_id,
                    "event_type": p.event_type,
                    "probability": p.probability,
                    "null_probability": p.null_probability,
                    "sha256": (p.prediction_hash or "")[:16],
                    "visibility": p.visibility_mode.value,
                }
                for p in self.frozen
            ],
            "rejected": self.rejected,
            "budget_usage": self.budget_usage,
            "notes": self.notes,
        }


class DailyPipeline:
    """每日预测闭环。"""

    def __init__(self, session: Session, user_id: int) -> None:
        self.session = session
        self.user_id = user_id
        self.settings = get_settings()
        # 第 34 节双盲实验：None=正常融合；reality_null / metaphysical_only / fusion
        self.experiment_arm: str | None = None

    # ==================================================================
    # 23:30 更新 Reality State
    # ==================================================================
    def update_reality_state(self, target_date: date | None = None) -> DailyState:
        return persist_daily_state(
            self.session, user_id=self.user_id, target_date=target_date
        )

    # ==================================================================
    # 23:40 Future Scanner
    # ==================================================================
    def scan(self, target_date: date, scale: str = "day", limit: int = 50) -> list[dict[str, Any]]:
        state = build_reality_state(self.session, user_id=self.user_id, target_date=target_date)
        ctx = AgentContext(
            user_id=self.user_id,
            session=self.session,
            payload={"time_scale": scale, "target_date": target_date.isoformat(), "reality_state": state},
        )
        return FutureScannerAgent().scan(ctx, limit=limit)

    # ==================================================================
    # 23:45 术数计算 + 23:50 多 Agent + 对抗审查 → 23:55 Freeze
    # ==================================================================
    def run(
        self,
        target_date: date | None = None,
        scale: str = "day",
        limit: int = 20,
    ) -> PipelineResult:
        target_date = target_date or (date.today() + timedelta(days=1))
        time_scale = SCALE_BY_NAME.get(scale, TimeScale.DAY)
        result = PipelineResult(target_date=target_date)

        # ---------- 1. Reality State ----------
        self.update_reality_state(target_date)
        reality_state = build_reality_state(
            self.session, user_id=self.user_id, target_date=target_date
        )

        # ---------- 2. 校准阶段门槛（C-006 + 禁止 6）----------
        # 已验证样本不足时，术式信号的预测力未经实证，其 strength 是未校准噪声。
        # 三个阶段：
        #   cold    (<MIN_CALIBRATION_SAMPLES)：术式不参与融合，产出 p=Null 的研究样本；
        #   explore (<MIN_FORMAL_SAMPLES)    ：术式以弱先验参与融合，产出仍标记 RESEARCH
        #             （不代表预测力），但完整记录各源信号，验证后为每术式积累实证样本；
        #   formal  (≥MIN_FORMAL_SAMPLES)    ：edge 门槛 + 对抗 Gate + 学习到的融合权重。
        calibrated = self._calibration_sample_count()
        phase = self._calibration_phase(calibrated)
        if phase == "cold":
            result.notes.append(
                f"冷启动：已验证样本 {calibrated}/{self.settings.MIN_CALIBRATION_SAMPLES}，"
                f"术式信号用于选题/叙事并留痕，概率仍取 Null 基线"
                f"（术式预测力未经实证，不宣称有效）"
            )
        elif phase == "explore":
            result.notes.append(
                f"实证期：已验证样本 {calibrated}/{self.settings.MIN_FORMAL_SAMPLES}，"
                f"术式信号以弱先验参与融合并完整留痕，产出仍为研究样本（不代表预测力）"
            )

        # ---------- 3. 研究期：多尺度批量采样（全程零 LLM，秒级）----------
        if phase != "formal":
            return self._run_research(
                result=result,
                target_date=target_date,
                reality_state=reality_state,
                phase=phase,
            )

        # ---------- 4. 正式期 ---------- 
        scanned = self.scan(target_date, scale=scale, limit=limit)
        result.scanned = len(scanned)
        if not scanned:
            result.notes.append("Future Scanner 未产出候选")
            return result

        window = FreezeAgent.default_window(
            datetime(target_date.year, target_date.month, target_date.day), time_scale
        )

        try:
            almanac = daily_almanac(self.session, self.user_id, window.start.date())
        except Exception:  # 锦囊缺失不阻断正式期
            almanac = None

        # ---------- 4. 确定性通道（无 LLM）：信号 → 融合 → 门槛 → 临时候选 → Gate ----------
        # 核心原则（C-005 + 禁止 6）：概率永远来自确定性融合/Null 基线；
        # LLM 只在正式期对入选候选做「措辞增强」，永远不许自己报数。
        provisional: list[tuple[PredictionCandidate, float, Any]] = []
        for item in scanned:
            event_type = item.get("event_type", "")
            spec = ONTOLOGY.get(event_type)
            domain = _domain(item.get("domain", "") or (spec.domain if spec else ""))

            try:
                signals, null_p = self._collect_signals(
                    event_type=event_type,
                    domain=domain,
                    window=window,
                    time_scale=time_scale,
                    target_date=target_date,
                    reality_state=reality_state,
                    experiment_arm=self.experiment_arm,
                    include_metaphysical=(phase != "cold"),
                )
            except Exception as exc:
                result.notes.append(f"{event_type} 信号收集失败：{exc}")
                continue

            # ---------- 4.1 Fusion（第 12 节：只消费结构化 Signal）----------
            fusion = fuse(
                FusionInput(
                    signals=signals,
                    null_probability=null_p,
                    time_scale=time_scale,
                    reliability=self._reliability_weights(),
                )
            )

            # 概率权威判定：cold 强制 Null 基线；explore / formal 为融合概率
            p_final = null_p if phase == "cold" else fusion.probability

            # ---------- 4.2 预测质量门槛（C-006 诚实原则，仅正式期）----------
            if phase == "formal":
                edge = fusion.probability - null_p
                if abs(edge) < self.settings.MIN_PREDICTION_EDGE:
                    result.rejected.append(
                        {
                            "event_type": event_type,
                            "decision": "NO_EDGE",
                            "failed": ["BaselineAttack"],
                            "reasons": [
                                f"融合概率 {fusion.probability:.2%} 与 Null 基线 {null_p:.2%} "
                                f"差距仅 {edge:+.2%}，未超过最小预测力门槛 "
                                f"{self.settings.MIN_PREDICTION_EDGE:.0%}，诚实放弃（C-006）"
                            ],
                        }
                    )
                    continue

            # ---------- 4.3 临时候选（Ontology 确定性构造，保证 C-001 可证伪）----------
            cand = self._provisional_candidate(
                event_type=event_type,
                domain=domain,
                window=window,
                time_scale=time_scale,
                probability=p_final,
                signals=signals,
                almanac=almanac,
            )
            if cand is None:
                result.notes.append(f"{event_type} 无法构造可证伪候选（C-001）")
                continue

            # ---------- 4.4 Adversarial Gate（第 21 节）----------
            gate_result = AdversarialGate().run(
                self._attack_context(cand, null_p, fusion, len(scanned))
            )
            if gate_result.decision in {"REJECT", "REWRITE"}:
                result.rejected.append(
                    {
                        "event_type": event_type,
                        "decision": gate_result.decision,
                        "failed": [o.attack for o in gate_result.failed],
                        "reasons": [o.reason for o in gate_result.failed][:3],
                    }
                )
                continue

            provisional.append((cand, null_p, fusion))

        result.candidates = [c for c, _, _ in provisional]

        # ---------- 5. Prediction Budget（第 4 节）----------
        candidates_only = [c for c, _, _ in provisional]
        selected, usage = apply_budget(candidates_only, default_slots(self.settings))
        by_id = {c.candidate_id: entry for entry in provisional for c in [entry[0]]}
        shortlisted = [by_id[c.candidate_id] for c in selected]
        result.budget_usage = usage
        result.notes.append(
            f"预算竞争：{len(provisional)} 候选 → {len(shortlisted)} 条获得额度"
        )

        # ---------- 6. LLM 措辞增强（并发执行）----------
        # C-005：LLM 只润色描述与成败标准，probability 一律丢弃（权威在融合）。
        # 中转站慢：并发 4 路，LLM 失败/放弃时保留确定性版本。
        final_entries = shortlisted
        if shortlisted:
            final_entries, n_refined = self._refine_with_llm(
                shortlisted,
                window=window,
                time_scale=time_scale,
                reality_state=reality_state,
                pool_size=len(scanned),
            )
            result.notes.append(
                f"LLM 措辞增强：{n_refined}/{len(shortlisted)} 条（概率由融合决定，不受 LLM 影响）"
            )

        # ---------- 7. Freeze（第 16 节）----------
        # 正式期同样按 (event_type, time_scale, 窗口日) 去重（坑 19 的同类防护：
        # 研究期有、正式期此前没有，重复点「生成」会冻结重复候选）。
        existing_keys = self._existing_sample_keys()
        formal_dupe_skipped = 0
        for cand, _, _ in final_entries:
            dup_key = (cand.event_type, cand.time_scale.value, cand.window_start.date())
            if dup_key in existing_keys:
                formal_dupe_skipped += 1
                continue
            existing_keys.add(dup_key)
            pred = self._freeze(cand, fusion=None)
            if pred:
                result.frozen.append(pred)
        if formal_dupe_skipped:
            result.notes.append(
                f"去重：跳过 {formal_dupe_skipped} 条与在库样本重复的正式候选"
            )

        return result

    # ==================================================================
    # 研究期多尺度采样（cold / explore 共用，全程零 LLM）
    # ==================================================================

    # 日 / 周 / 月的研究样本配额：让用户同时看到「明天 / 本周 / 本月」的样本
    RESEARCH_SCALE_PLAN: tuple[tuple[str, int], ...] = (
        ("day", 3),
        ("week", 2),
        ("month", 1),
    )

    def _run_research(
        self,
        *,
        result: PipelineResult,
        target_date: date,
        reality_state: dict[str, Any],
        phase: str,
    ) -> PipelineResult:
        """研究期主流程：多尺度多法交叉扫描 → 收敛优选 → 富文本冻结。

        全程零 LLM：研究样本的使命是启动验证闭环，必须秒级完成。
        术式信号的角色（第一性拆解）：
          - 「选什么事」：各引擎对每个本体事件独立给信号，正向同向数多的
            事件优先入选（多方法交叉）；纯 Null 事件兜底补齐配额；
          - 「怎么说」：描述由确定性叙事注册表 + 真实证据句渲染；
          - 「概率多少」：权威不变 —— cold 强制 Null 基线，explore 弱先验融合
            （C-005/C-006；信号照常落库，为后续验证积累 per-source 实证样本）。
        """
        all_provisional: list[PredictionCandidate] = []
        chosen: list[tuple[PredictionCandidate, float, Any]] = []
        by_scale_count: dict[str, int] = {}

        # 每个尺度窗口的锦囊（幸运元素/宜忌，窗口起始日确定论派生；一次一日期）
        almanac_cache: dict[Any, dict[str, Any]] = {}

        for scale_name, quota in self.RESEARCH_SCALE_PLAN:
            scale = SCALE_BY_NAME.get(scale_name, TimeScale.DAY)
            window = FreezeAgent.default_window(
                datetime(target_date.year, target_date.month, target_date.day), scale
            )
            if window.start.date() not in almanac_cache:
                try:
                    almanac_cache[window.start.date()] = daily_almanac(
                        self.session, self.user_id, window.start.date()
                    )
                except Exception as exc:  # 锦囊缺失不阻断生成
                    logger.warning("今日锦囊生成失败：%s", exc)
                    almanac_cache[window.start.date()] = {}
            almanac = almanac_cache[window.start.date()]

            scanned = self._ontology_scan(
                scale=scale_name, limit=max(quota * 4, self.settings.RESEARCH_SAMPLE_LIMIT)
            )
            result.scanned += len(scanned)

            per_scale: list[tuple[PredictionCandidate, float, Any]] = []
            for item in scanned:
                event_type = item.get("event_type", "")
                spec = ONTOLOGY.get(event_type)
                domain = _domain(item.get("domain", "") or (spec.domain if spec else ""))
                try:
                    # 研究期同样全量收集术式信号：选事件 + 叙事 + 留痕三层用途。
                    # （概率权威不受此影响：cold 仍取 null_p，见下。）
                    signals, null_p = self._collect_signals(
                        event_type=event_type,
                        domain=domain,
                        window=window,
                        time_scale=scale,
                        target_date=target_date,
                        reality_state=reality_state,
                        experiment_arm=self.experiment_arm,
                        include_metaphysical=True,
                    )
                except Exception as exc:
                    result.notes.append(f"{event_type} 信号收集失败：{exc}")
                    continue

                fusion = fuse(
                    FusionInput(
                        signals=signals,
                        null_probability=null_p,
                        time_scale=scale,
                        reliability=self._reliability_weights(),
                    )
                )
                cand = self._provisional_candidate(
                    event_type=event_type,
                    domain=domain,
                    window=window,
                    time_scale=scale,
                    probability=null_p if phase == "cold" else fusion.probability,
                    signals=signals,
                    almanac=almanac,
                )
                if cand is None:
                    continue
                all_provisional.append(cand)
                per_scale.append((cand, null_p, fusion))

            # ---------- 交叉印证优先 + 领域多样性选择 ----------
            # 排序键：术式正向源数（降序）→ 概率（降序）；同分保证领域多样性 ——
            # 已选的 domain 靠后，让每轮样本覆盖不同生活面。
            def _rank(entry: tuple[PredictionCandidate, float, Any]) -> tuple[int, float]:
                cs = summarize_signals(entry[0].signals)
                return (cs.metaphysical_support, entry[0].probability)

            per_scale.sort(key=_rank, reverse=True)
            picked: list[tuple[PredictionCandidate, float, Any]] = []
            seen_domains: set[str] = set()
            deferred: list[tuple[PredictionCandidate, float, Any]] = []
            for entry in per_scale:
                if entry[0].domain.value in seen_domains:
                    deferred.append(entry)
                else:
                    picked.append(entry)
                    seen_domains.add(entry[0].domain.value)
                if len(picked) >= quota:
                    break
            if len(picked) < quota:
                picked.extend(deferred[: quota - len(picked)])
            chosen.extend(picked)
            by_scale_count[scale_name] = len(picked)

        if not chosen:
            result.notes.append("研究期扫描未产出可证伪候选")
            return result

        result.candidates = all_provisional
        result.budget_usage = {"research": len(chosen), **by_scale_count}
        crossed = sum(
            1 for cand, _, _ in chosen if summarize_signals(cand.signals).crossed
        )
        result.notes.append(
            f"研究期多尺度：{' / '.join(f'{k} {v}' for k, v in by_scale_count.items())}，"
            f"共 {len(chosen)} 条研究样本（零 LLM，秒级；{crossed} 条达成 ≥2 法交叉印证）"
        )

        # 去重：同事件 + 同尺度 + 同窗口起点且尚未定论（冻结/研究/待验证）的
        # 样本不重复入库，防止同一天重复点「生成」刷出一模一样的行。
        # 尺度必须进键：同一事件在日/周/月是三个不同的可证伪断言。
        existing_keys = self._existing_sample_keys()
        dup_skipped = 0

        for cand, _, _ in chosen:
            dup_key = (cand.event_type, cand.time_scale.value, cand.window_start.date())
            if dup_key in existing_keys:
                dup_skipped += 1
                continue
            existing_keys.add(dup_key)
            pred = self._freeze(cand, fusion=None, status=PredictionStatus.RESEARCH.value)
            if pred:
                result.frozen.append(pred)

        if dup_skipped:
            result.notes.append(
                f"去重：跳过 {dup_skipped} 条与在库样本同事件同窗口的重复选题"
            )

        return result

    # ------------------------------------------------------------------
    def _existing_sample_keys(self) -> set[tuple[str, str, Any]]:
        """在库未定论样本的去重键集合：(event_type, time_scale, 窗口起点日)。

        研究期与正式期共用（坑 19 / 审查 P2-④）。
        """
        rows = self.session.exec(
            select(
                PredictionRecord.event_type,
                PredictionRecord.time_scale,
                PredictionRecord.window_start,
            ).where(
                PredictionRecord.user_id == self.user_id,  # type: ignore[arg-type]
                PredictionRecord.status.in_(  # type: ignore[attr-defined]
                    [
                        PredictionStatus.FROZEN.value,
                        PredictionStatus.RESEARCH.value,
                        PredictionStatus.VERIFY_REQUIRED.value,
                    ]
                )
            )
        ).all()
        return {(et, ts, ws.date()) for et, ts, ws in rows}

    # ==================================================================
    def _collect_signals(
        self,
        *,
        event_type: str,
        domain: Domain,
        window: TimeWindow,
        time_scale: TimeScale,
        target_date: date,
        reality_state: dict[str, Any],
        experiment_arm: str | None = None,
        include_metaphysical: bool = True,
    ) -> tuple[list[Signal], float]:
        """Blind 收集各源信号。

        第 12 节：每个 Adapter/Agent 只拿到自己的输入，
        不存在任何 agent 读取他人结论的路径。

        第 34 节双盲实验（arm）：
            reality_null      → 只用 Reality + Null（排除术数）
            metaphysical_only → 只用术式 + Null（排除 Reality）
            fusion / None     → 全部信号

        include_metaphysical=False → 冷启动：术式信号未经实证不参与融合。
        """
        signals: list[Signal] = []

        # --- Null Model（第 11 节，所有 arm 都必须提供基线）---
        null_model = NullModel(self.session)
        null_signal = null_model.signal(
            user_id=self.user_id,
            event_type=event_type,
            domain=domain,
            window=window,
            time_scale=time_scale,
        )
        null_p = null_signal.strength
        signals.append(null_signal)

        # --- Reality（第 10 节）---
        # 无现实事件时跳过 LLM 调用（纯 Null 基线即可），
        # 减少免费模型池的调用量与延迟。
        if experiment_arm != "metaphysical_only":
            try:
                total_events = reality_state.get("_meta", {}).get("total_events", 0)
                if total_events > 0:
                    reality_ctx = AgentContext(
                        user_id=self.user_id,
                        session=self.session,
                        target_event=event_type,
                        domain=domain.value,
                        payload={
                            "window": window,
                            "time_scale": time_scale,
                            "reality_state": reality_state,
                            "engine_version": "reality-0.1.0",
                        },
                    )
                    reality_agent = RealityAgent()
                    r = reality_agent.run(reality_ctx)
                    if r.ok:
                        sig = reality_agent.to_signal(reality_ctx, r)
                        if not sig.degraded:
                            signals.append(sig)
            except Exception as exc:
                logger.warning("RealityAgent 失败：%s", exc)

        # --- 术式 Adapter（deterministic 部分，第 6.1 节）---
        # 时间起卦用生成时刻的真实时辰：固定 00:00 会让梅花等时间起卦术
        # 锁死在同一时辰上下卦组合上，产生系统方向偏斜（回测+审计战果）。
        # 同一查询 → 同一排盘的确定性语义不变（时辰是查询的一部分）。
        query = AdapterQuery(
            user_id=self.user_id,
            domain=domain,
            target_event=event_type,
            time_scale=time_scale,
            window=window,
            target_date=target_date,
            target_time=datetime.now().strftime("%H:%M"),
            session=self.session,
        )
        from app.core.base import registry as adapter_registry

        for adapter in adapter_registry.all():
            if not adapter.available:
                continue
            # 第 34 节双盲 A 组：只用 Reality + Null，排除全部术式
            if experiment_arm == "reality_null":
                continue
            # 冷启动：术式信号未经实证，不参与融合（避免未校准噪声产生假 edge）
            if not include_metaphysical:
                continue
            try:
                signals.extend(adapter.signals(query))
            except Exception as exc:
                logger.warning("Adapter %s 失败：%s", adapter.source.value, exc)

        return signals, null_p

    # ------------------------------------------------------------------
    def _ontology_scan(self, *, scale: str, limit: int) -> list[dict[str, Any]]:
        """研究期扫描：直接从 Event Ontology 取候选（确定性，不调 LLM）。

        研究样本的目的是启动验证闭环，候选来源必须是确定性、秒级的——
        不能被中转站延迟绑架。正式期才使用 FutureScannerAgent 的 LLM 扫描。
        """
        return [
            {
                "event_type": spec.event_type,
                "domain": spec.domain,
                "description": spec.label,
                "why_falsifiable": "；".join(spec.success_criteria),
                "source": "ontology",
            }
            for spec in by_scale(scale)[:limit]
        ]

    # ------------------------------------------------------------------
    def _provisional_candidate(
        self,
        *,
        event_type: str,
        domain: Domain,
        window: TimeWindow,
        time_scale: TimeScale,
        probability: float,
        signals: list[Signal],
        almanac: dict[str, Any] | None = None,  # 保留形参：叙事已改在读取端重建（见 cross_engine.rich_description）
    ) -> PredictionCandidate | None:
        """由 Event Ontology 构造确定性候选（第 56 节）。

        预测的「最小可证伪骨架」必须能独立于 LLM 存在（C-001）。
        description 只写「何时 + 何事」的事实断言 —— 对抗 Gate 审的就是它；
        富文本叙事（情景/印证/建议/幸运）是展示层，由读取端用同一套确定性
        函数从「事件 + 信号 + 锦囊」重建，不进冻结哈希、不过 Gate（C-003 语义不变）。
        """
        spec = ONTOLOGY.get(event_type)
        if spec is None:
            return None
        return PredictionCandidate(
            domain=domain,
            event_type=event_type,
            description=f"{when_text(time_scale, window.start, window.end)}{spec.label}。",
            probability=probability,
            time_scale=time_scale,
            window_start=window.start,
            window_end=window.end,
            success_criteria=list(spec.success_criteria),
            failure_criteria=list(spec.failure_criteria),
            grading_rule=spec.grading_rule,
            signals=signals,
        )

    # ------------------------------------------------------------------
    def _refine_with_llm(
        self,
        entries: list[tuple[PredictionCandidate, float, Any]],
        *,
        window: TimeWindow,
        time_scale: TimeScale,
        reality_state: dict[str, Any],
        pool_size: int,
    ) -> tuple[list[tuple[PredictionCandidate, float, Any]], int]:
        """正式期措辞增强：并发调用 CandidateAgent（仅对已获额度的候选）。

        原则（C-005）：
        - 概率权威在融合侧，LLM 自报的 probability 一律丢弃；
        - 只采纳 description / criteria / grading_rule，且必须重过 Gate；
        - LLM 失败/放弃时保留确定性版本（管线不被中转站绑架）。

        并发安全：SQLModel Session 非线程安全，每个 worker 用独立 Session
        （绑定同一 engine；测试的 StaticPool 下共享同一内存库连接）。
        """
        from concurrent.futures import ThreadPoolExecutor

        def work(
            entry: tuple[PredictionCandidate, float, Any],
        ) -> tuple[PredictionCandidate, float, Any]:
            cand, null_p, fusion = entry
            try:
                refined = self._refine_one(
                    cand,
                    null_p=null_p,
                    fusion=fusion,
                    window=window,
                    time_scale=time_scale,
                    reality_state=reality_state,
                    pool_size=pool_size,
                )
                if refined is not None:
                    return (refined, null_p, fusion)
            except Exception as exc:
                logger.warning("措辞增强失败（%s）：%s", cand.event_type, exc)
            return entry

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="xm-refine") as pool:
            out = list(pool.map(work, entries))
        n_refined = sum(
            1 for (c, _, _), (orig, _, _) in zip(out, entries) if c is not orig
        )
        return out, n_refined

    def _refine_one(
        self,
        cand: PredictionCandidate,
        *,
        null_p: float,
        fusion: Any,
        window: TimeWindow,
        time_scale: TimeScale,
        reality_state: dict[str, Any],
        pool_size: int,
    ) -> PredictionCandidate | None:
        with Session(self.session.get_bind()) as tsession:
            ctx = AgentContext(
                user_id=self.user_id,
                session=tsession,
                target_event=cand.event_type,
                domain=cand.domain.value,
                payload={
                    "window": window,
                    "window_text": f"{window.start.isoformat()} ~ {window.end.isoformat()}",
                    "time_scale": time_scale,
                    "null_probability": null_p,
                    "reality_state": reality_state,
                },
            )
            res = CandidateAgent().run(ctx)

        if not res.ok or res.output.get("abstain"):
            return None

        desc = str(res.output.get("description") or "").strip()
        succ = [str(x) for x in (res.output.get("success_criteria") or []) if str(x).strip()]
        fail = [str(x) for x in (res.output.get("failure_criteria") or []) if str(x).strip()]
        if not desc or not succ or not fail:
            return None

        refined = cand.model_copy(
            update={
                "description": desc,
                "success_criteria": succ,
                "failure_criteria": fail,
                "grading_rule": str(res.output.get("grading_rule") or cand.grading_rule),
            }
        )

        # 措辞变了必须重过对抗 Gate（第 21 节）；不过则保留确定性版本
        gate_result = AdversarialGate().run(
            self._attack_context(refined, null_p, fusion, pool_size)
        )
        if gate_result.decision in {"REJECT", "REWRITE"}:
            return None
        return refined

    # ------------------------------------------------------------------
    def _attack_context(
        self,
        cand: PredictionCandidate,
        null_p: float,
        fusion: Any,
        pool_size: int,
    ) -> AttackContext:
        """构造 Gate 输入。

        第 20.12 节：把 dependency_group 传给 CorrelatedEvidenceAttack。
        """
        groups: dict[str, list[str]] = {}
        for s in cand.signals:
            key = s.dependency_group or f"solo:{s.source.value}"
            groups.setdefault(key, []).append(s.source.value)

        return AttackContext(
            description=cand.description,
            event_type=cand.event_type,
            probability=cand.probability,
            null_probability=null_p,
            success_criteria=cand.success_criteria,
            failure_criteria=cand.failure_criteria,
            window_start=cand.window_start,
            window_end=cand.window_end,
            grading_rule=cand.grading_rule,
            signals=[s.model_dump(mode="json") for s in cand.signals],
            dependency_groups=groups,
            candidate_pool_size=pool_size,
        )

    # ------------------------------------------------------------------
    def _reliability_weights(self) -> dict[str, float]:
        """第 26 节：从历史 skill 学习到的融合权重。样本不足时为 {}（即 1.0）。"""
        try:
            from app.learning.reliability import ReliabilityMatrix

            return ReliabilityMatrix(self.session, user_id=self.user_id).fusion_weights()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    def _calibration_phase(self, calibrated: int) -> str:
        """校准阶段判定：cold（基线校准）/ explore（信号实证）/ formal（正式预测）。

        第 78 节：样本不足时不得宣布模型有预测力。
        只有达到 MIN_FORMAL_SAMPLES 后，系统才进入正式预测
        （edge 门槛 + 对抗 Gate + 学习到的融合权重）。
        """
        if calibrated < self.settings.MIN_CALIBRATION_SAMPLES:
            return "cold"
        if calibrated < self.settings.MIN_FORMAL_SAMPLES:
            return "explore"
        return "formal"

    # ------------------------------------------------------------------
    def _calibration_sample_count(self) -> int:
        """已验证样本数（prediction_scores 行数），用于冷启动校准门槛。

        第 78 节：样本不足时不得宣布模型有预测力。
        只有达到 MIN_CALIBRATION_SAMPLES 后，术式信号才被允许参与融合。
        """
        from sqlmodel import func, select

        from app.models.scoring import PredictionScore

        return int(
            self.session.exec(
                select(func.count(PredictionScore.id)).where(
                    PredictionScore.user_id == self.user_id
                )
            ).one()
            or 0
        )

    # ------------------------------------------------------------------
    def _freeze(
        self,
        cand: PredictionCandidate,
        fusion: Any = None,
        status: str | None = None,
    ) -> Prediction | None:
        """第 16 节：预注册 + 冻结 + 落库。

        status=None → 正常冻结（FROZEN）；status="RESEARCH" → 冷启动研究样本。
        研究样本保持 VISIBLE（用户需看到并验证），仅用 status 标记，不计入正式预测。
        """
        visibility = "HIDDEN" if self.settings.EXPERIMENT_MODE == "hidden" else "VISIBLE"

        pred = Prediction(
            user_id=str(self.user_id),
            domain=cand.domain,
            event_type=cand.event_type,
            description=cand.description,
            probability=cand.probability,
            window_start=cand.window_start,
            window_end=cand.window_end,
            time_scale=cand.time_scale,
            success_criteria=cand.success_criteria,
            failure_criteria=cand.failure_criteria,
            grading_rule=cand.grading_rule,
            null_probability=next(
                (s.strength for s in cand.signals if s.source.value == "null"), None
            ),
            visibility_mode=visibility,  # type: ignore[arg-type]
            candidate_id=cand.candidate_id,
            signals=cand.signals,
            input_snapshot={
                "signal_count": len(cand.signals),
                # 必须是 list：set 无法 JSON 序列化，落库会报 StatementError
                "dependency_groups": sorted(
                    {s.dependency_group or f"solo:{s.source.value}" for s in cand.signals}
                    - {""}
                ),
                "signal_sources": [s.source.value for s in cand.signals],
            },
        )

        ctx = AgentContext(
            user_id=self.user_id,
            session=self.session,
            payload={"prediction": pred},
        )
        out = FreezeAgent().run(ctx)
        if not out.ok:
            return None

        # 冷启动研究样本：freeze() 默认置 FROZEN，这里覆盖为 RESEARCH。
        # status 不参与冻结哈希（freeze_payload 不含 status），故不影响完整性校验。
        if status is not None:
            pred.status = PredictionStatus(status)

        # 落库：predictions
        record = PredictionRecord(
            prediction_id=pred.prediction_id,
            user_id=self.user_id,
            domain=pred.domain.value,
            event_type=pred.event_type,
            description=pred.description,
            probability=pred.probability,
            null_probability=pred.null_probability,
            time_scale=pred.time_scale.value,
            window_start=pred.window_start,
            window_end=pred.window_end,
            success_criteria=pred.success_criteria,
            failure_criteria=pred.failure_criteria,
            grading_rule=pred.grading_rule,
            status=pred.status.value,
            visibility_mode=pred.visibility_mode.value,
            created_at=pred.created_at,
            frozen_at=pred.frozen_at,
            verification_due_at=pred.verification_due_at,
            sha256=pred.prediction_hash,
            model_version=pred.model_version,
            fusion_version=pred.fusion_version,
            prompt_version=pred.prompt_version,
            rule_version=pred.rule_version,
            engine_version=pred.engine_version,
            candidate_id=pred.candidate_id,
            version=pred.version,
        )
        self.session.add(record)

        # 落库：signals
        from app.models.prediction import SignalRecord

        for s in pred.signals:
            self.session.add(
                SignalRecord(
                    signal_id=s.signal_id,
                    prediction_id=pred.prediction_id,
                    prediction_candidate_id=cand.candidate_id,
                    source_type=s.source.value,
                    source_engine=s.engine_version,
                    domain=s.domain.value,
                    target_event=s.target_event,
                    direction=s.direction,
                    strength=s.strength,
                    confidence=s.confidence,
                    time_scale=s.time_scale.value,
                    window_start=s.time_window.start,
                    window_end=s.time_window.end,
                    evidence=[e.model_dump(mode="json") for e in s.evidence],
                    counter_evidence=[e.model_dump(mode="json") for e in s.counter_evidence],
                    rule_ids=s.rule_ids,
                    dependency_group=s.dependency_group,
                    engine_version=s.engine_version,
                    prompt_version=s.prompt_version,
                    degraded=s.degraded,
                    degrade_reason=s.degrade_reason,
                )
            )

        # 落库：freeze 快照（第 16 节）
        self.session.add(
            PredictionFreeze(
                prediction_id=pred.prediction_id,
                freeze_payload=pred.freeze_payload(),
                agent_outputs=[],
                input_snapshot=pred.input_snapshot or {},
                sha256=pred.prediction_hash or "",
                frozen_at=pred.frozen_at or utcnow(),
            )
        )
        self.session.commit()
        return pred


# ----------------------------------------------------------------------
def _domain(value: str) -> Domain:
    try:
        return Domain(value)
    except ValueError:
        return Domain.UNEXPECTED_EVENT
