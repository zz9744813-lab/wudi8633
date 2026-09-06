/**
 * 预测详情抽屉（round 19 UI 升级 P0-2）。
 *
 * 方案第 49 节「完全可解释」的呈现层：
 * - 全法盘点六格板：同一时间点各术式 ✓/✗/○ 一目了然；
 * - 逐术式信号：方向箭头 + 强度条 + 可展开的证据/反证列表；
 * - 冻结与完整性：哈希、读侧重算一致性、版本血缘；
 * - 数据全部来自 /api/predictions/{id}，不含任何重建内容。
 */

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

import { api, type PredictionDetail, type Signal } from '../api/client';
import { Badge, ErrorBox, Loading, ProbBar } from './ui';
import { DOMAIN_LABEL, SCALE_LABEL, pct } from '../lib/format';

const SOURCE_ZH: Record<string, string> = {
  ziwei: '紫微',
  bazi: '八字',
  qimen: '奇门',
  liuyao: '六爻',
  meihua: '梅花',
  zhouyi: '周易',
  palm: '掌纹',
  face: '面相',
  reality: '现实',
  null: '基线',
};

/** 全法盘点：源 → ✓同向 / ✗反向 / ○未表态（与后端 rich_description 同口径） */
function tally(signals: Signal[]): { src: string; mark: string; tone: 'good' | 'bad' | 'default' }[] {
  const sources = ['bazi', 'ziwei', 'liuyao', 'meihua', 'zhouyi', 'qimen'];
  const extra = [...new Set(signals.map((s) => s.source))].filter(
    (s) => !sources.includes(s) && s !== 'null' && s !== 'reality',
  );
  return [...sources, ...extra].map((src) => {
    const live = signals.filter((s) => s.source === src && !s.degraded);
    const up = live.some((s) => s.direction > 0);
    const down = live.some((s) => s.direction < 0);
    if (up && down) return { src, mark: '✓✗ 分歧', tone: 'default' as const };
    if (up) return { src, mark: '✓ 同向', tone: 'good' as const };
    if (down) return { src, mark: '✗ 反向', tone: 'bad' as const };
    return { src, mark: '○ 未表态', tone: 'default' as const };
  });
}

function SignalBlock({ sig }: { sig: Signal }) {
  const [open, setOpen] = useState(false);
  const zh = SOURCE_ZH[sig.source] ?? sig.source;
  const arrow = sig.direction > 0 ? '▲' : sig.direction < 0 ? '▼' : '─';
  const arrowCls =
    sig.direction > 0 ? 'text-jade-400' : sig.direction < 0 ? 'text-cinnabar-400' : 'text-t5';
  return (
    <div className="rounded-lg border border-line bg-card/60 p-2.5">
      <button className="flex w-full items-center gap-2 text-left" onClick={() => setOpen((v) => !v)}>
        <span className={`font-serif text-sm ${arrowCls}`}>{arrow}</span>
        <span className="w-12 shrink-0 text-xs font-medium text-t1">{zh}</span>
        <ProbBar p={sig.strength} className="w-24" />
        <span className="text-[11px] tabular text-t3">{pct(sig.strength, 0)}</span>
        {sig.degraded && <Badge tone="warn">降级</Badge>}
        <span className="ml-auto text-[10px] text-t5">{open ? '收起 ▲' : '证据 ▼'}</span>
      </button>
      {sig.degrade_reason && (
        <p className="mt-1 text-[11px] text-t5">降级原因：{sig.degrade_reason}</p>
      )}
      {open && (
        <div className="mt-2 space-y-1 border-t border-line pt-1.5">
          {((sig.evidence ?? []) as { description: string }[]).map((e, i) => (
            <p key={i} className="text-[11px] leading-relaxed text-t3">
              <span className="text-gt">·</span> {e.description}
            </p>
          ))}
          {((sig.counter_evidence ?? []) as { description: string }[]).map((e, i) => (
            <p key={i} className="text-[11px] leading-relaxed text-cinnabar-400/80">
              <span>✗</span> {e.description}
            </p>
          ))}
          {!(sig.evidence ?? []).length && !(sig.counter_evidence ?? []).length && (
            <p className="text-[11px] text-t5">无证据条目</p>
          )}
          {sig.dependency_group && (
            <p className="text-[10px] text-t5">依赖组：{sig.dependency_group}（同组信号融合时不叠加计权）</p>
          )}
        </div>
      )}
    </div>
  );
}

export function PredictionDrawer({ pid, onClose }: { pid: string | null; onClose: () => void }) {
  const [detail, setDetail] = useState<PredictionDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!pid) return;
    setDetail(null);
    setErr(null);
    api
      .prediction(pid)
      .then(setDetail)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [pid]);

  useEffect(() => {
    if (!pid) return;
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [pid, onClose]);

  if (!pid) return null;

  // Portal 挂载到 body：页面滚动容器 MAIN 里若存在动画 transform 祖先，
  // position:fixed 会被约束为该祖先的包含块（fixed 元素随滚动偏移出错位）。
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose} role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/55" />
      <aside
        className="animate-fade-up relative h-full w-full max-w-xl overflow-y-auto border-l border-gilt-500/30 bg-page shadow-[-8px_0_32px_rgba(0,0,0,0.45)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-line bg-page/95 px-4 py-3 backdrop-blur">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Badge tone={detail?.status === 'VERIFIED' ? 'good' : detail?.status === 'RESEARCH' ? 'warn' : 'default'}>
                {detail ? (SOURCE_LABEL_STATUS[detail.status] ?? detail.status) : '…'}
              </Badge>
              {detail && <Badge tone="info">{DOMAIN_LABEL[detail.domain] ?? detail.domain}</Badge>}
              {detail && <Badge tone="info">{SCALE_LABEL[detail.time_scale] ?? detail.time_scale}</Badge>}
            </div>
            <h3 className="mt-1 text-sm font-medium text-t1">
              {detail?.description ?? '加载中…'}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-md border border-line px-2 py-1 text-xs text-t3 hover:text-t1"
            aria-label="关闭详情"
          >
            ✕
          </button>
        </div>

        <div className="space-y-4 px-4 py-4">
          {err && <ErrorBox message={err} />}
          {!err && !detail && <Loading label="加载预测详情…" />}
          {detail && (
            <>
              {/* 概率口径 */}
              <div className="rounded-xl border border-line bg-panel p-3">
                <div className="flex items-center gap-3">
                  <ProbBar p={detail.probability} className="flex-1" />
                  <span className="text-lg font-semibold tabular text-t1">{pct(detail.probability)}</span>
                </div>
                {detail.null_probability != null && (
                  <p className="mt-1 text-[11px] text-t4">
                    Null 基线 {pct(detail.null_probability)} · 相对差{' '}
                    {(detail.probability - detail.null_probability >= 0 ? '+' : '') +
                      `${Math.round((detail.probability - detail.null_probability) * 100)} 个百分点`}
                  </p>
                )}
                <p className="mt-1 text-[11px] text-t4">
                  窗口 {detail.window[0]} ~ {detail.window[1]} · 验证截止{' '}
                  {detail.verification_due_at ?? '—'}
                </p>
              </div>

              {/* 全法盘点六格板 */}
              <div>
                <div className="mb-1.5 text-xs font-medium text-t3">全法盘点（同一时间点·六术同参）</div>
                <div className="grid grid-cols-3 gap-1.5">
                  {tally(detail.signals).map(({ src, mark, tone }) => (
                    <div
                      key={src}
                      className={`rounded-lg border px-2 py-1.5 text-center text-[11px] ${
                        tone === 'good'
                          ? 'border-jade-500/40 bg-jade-500/[0.06] text-jade-400'
                          : tone === 'bad'
                            ? 'border-cinnabar-500/40 bg-cinnabar-500/[0.06] text-cinnabar-400'
                            : 'border-line bg-panel text-t4'
                      }`}
                    >
                      <div className="text-t2">{SOURCE_ZH[src] ?? src}</div>
                      <div className="mt-0.5">{mark}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* 逐术式信号 */}
              <div className="space-y-1.5">
                <div className="text-xs font-medium text-t3">术式信号与证据（点击展开）</div>
                {detail.signals
                  .filter((s) => s.source !== 'null' && s.source !== 'reality')
                  .map((s) => (
                    <SignalBlock key={s.signal_id} sig={s} />
                  ))}
              </div>

              {/* 判定标准 */}
              <div className="rounded-xl border border-line bg-panel p-3 text-xs">
                <div className="mb-1 font-medium text-t2">判定标准（冻结时定义，不得事后放宽）</div>
                <ul className="space-y-0.5">
                  {detail.success_criteria.map((c, i) => (
                    <li key={i} className="text-jade-400/90">✓ {c}</li>
                  ))}
                  {detail.failure_criteria.map((c, i) => (
                    <li key={i} className="text-cinnabar-400/90">✗ {c}</li>
                  ))}
                </ul>
                <p className="mt-1 text-[11px] text-t5">评分规则：{detail.grading_rule}</p>
              </div>

              {/* 冻结与完整性 */}
              <div className="rounded-xl border border-line bg-panel p-3 text-[11px] leading-relaxed text-t3">
                <div className="mb-1 font-medium text-t2">冻结与完整性</div>
                <p>
                  冻结哈希 <span className="font-mono">{detail.sha256_head}</span>
                  {detail.integrity && (
                    <Badge tone={detail.integrity.ok ? 'good' : 'bad'}>
                      {detail.integrity.ok ? '完整性一致' : '完整性异常'}
                    </Badge>
                  )}
                </p>
                {detail.frozen_at && <p>冻结于 {detail.frozen_at.slice(0, 19).replace('T', ' ')}</p>}
                <p>
                  版本：模型 {detail.versions.model} · 融合 {detail.versions.fusion} · 规则{' '}
                  {detail.versions.rule}
                </p>
                {detail.lineage.candidate_id && (
                  <p className="font-mono text-t5">候选 {detail.lineage.candidate_id} · v{detail.lineage.version}</p>
                )}
                <p>
                  Agent 分歧度 {detail.agent_disagreement.toFixed(3)} · 证据依赖组{' '}
                  {Object.keys(detail.evidence_dependency).length} 个
                </p>
              </div>

              {/* 已有结果 */}
              {detail.outcome && (
                <div className="rounded-xl border border-line bg-panel p-3 text-xs">
                  <div className="mb-1 font-medium text-t2">判定结果</div>
                  <p className="text-t2">
                    结果 {detail.outcome.outcome} · 置信 {pct(detail.outcome.confidence, 0)}
                    {detail.outcome.needs_confirmation && ' · 待人工确认'}
                  </p>
                </div>
              )}

              <p className="text-[10px] leading-relaxed text-t5">
                本页为冻结事实与确定性重建内容的呈现；术式信号属传统口径参读，效力由验证闭环实证（C-006）。
              </p>
            </>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}

const SOURCE_LABEL_STATUS: Record<string, string> = {
  RESEARCH: '研究样本',
  FROZEN: '已冻结',
  VERIFIED: '已验证',
  VERIFY_REQUIRED: '待验证',
  WAITING_USER: '待补充',
  REJECTED: '已拦截',
  LEAKED: '已泄漏',
};
