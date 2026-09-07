import { useMemo, useEffect, useState } from 'react';

import { api, DEFAULT_USER_ID } from '../api/client';
import {
  Badge,
  Card,
  EmptyState,
  ErrorBox,
  Loading,
  PageHeader,
  ProbBar,
  ProgressBar,
  inputCls,
} from '../components/ui';
import {
  DOMAIN_LABEL,
  cleanDescription,
  pct,
  shortDateTime,
} from '../lib/format';
import { useAsync } from '../lib/useAsync';

/**
 * 第 50 节 Verification Inbox —— 批复式交互。
 *
 * 用户的心智模型是「批阅」而不是「收件箱」：一条条过、当场裁决、
 * 批完自动换下一条。裁决只用手感明确的四档：命中 / 未中 / 部分 / 无法判定。
 *
 * 第 59 节：到期主动进入 VERIFY_REQUIRED；用户暂不回复则 WAITING_USER，不能自动判成功。
 * 第 60 节：支持自然语言回复；可能对应多条预测时必须要求明确，不能强行命中。
 */

type DueItem = {
  prediction_id: string;
  event_type: string;
  description: string;
  probability: number;
  success_criteria: string[];
  failure_criteria: string[];
  window: [string, string];
  status: string;
};

type Verdict = {
  quick: string;
  label: string;
  /** D=无法判定时不落结果，为 null */
  outcome: number | null;
  confidence: number;
  /** 本条 Brier 误差：(probability - outcome)^2（D 档为 null） */
  brier?: number | null;
  needsConfirmation: boolean;
  at: number;
  reply?: string;
};

const VERDICTS = [
  { key: 'A', label: '命中', stamp: '中', tone: 'jade' },
  { key: 'B', label: '未中', stamp: '失', tone: 'cinnabar' },
  { key: 'C', label: '部分命中', stamp: '半', tone: 'amber' },
  { key: 'D', label: '无法判定', stamp: '疑', tone: 'ink' },
] as const;

const VERDICT_STYLE: Record<string, string> = {
  jade: 'border-jade-500/50 bg-jade-500/[0.07] text-jade-400 hover:bg-jade-500/15 hover:border-jade-400',
  cinnabar:
    'border-cinnabar-500/50 bg-cinnabar-500/[0.07] text-cinnabar-400 hover:bg-cinnabar-500/15 hover:border-cinnabar-400',
  amber:
    'border-amber-500/50 bg-amber-500/[0.07] text-amber-400 hover:bg-amber-500/15 hover:border-amber-400',
  ink: 'border-bd bg-panel text-t3 hover:bg-card hover:text-t1',
};

export default function Verify() {
  const due = useAsync(() => api.duePredictions(DEFAULT_USER_ID), []);
  const [done, setDone] = useState<Record<string, Verdict>>({});
  const [reply, setReply] = useState('');
  const [busy, setBusy] = useState(false);

  const items: DueItem[] = useMemo(() => due.data?.items ?? [], [due.data]);
  // 待批 = 服务端返回的到期项里，本轮还没批过的
  const pending = items.filter((it) => !done[it.prediction_id]);
  const current: DueItem | undefined = pending[0];
  const doneList = useMemo(
    () => Object.values(done).sort((a, b) => b.at - a.at),
    [done],
  );
  const totalToday = doneList.length + pending.length;

  // 键盘连批：1命中 / 2未中 / 3部分 / 4无法判定（输入框聚焦时不劫持）
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName ?? '';
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
      if (busy) return;
      const cur = pending[0];
      if (!cur) return;
      const idx = ['1', '2', '3', '4'].indexOf(e.key);
      if (idx < 0) return;
      e.preventDefault();
      const v = VERDICTS[idx];
      void stamp(cur.prediction_id, v.key, v.label);
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  });

  const stamp = async (pid: string, quick: string, label: string) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api.verify(pid, reply.trim() || undefined, quick);
      const cur = pending.find((p) => p.prediction_id === pid);
      const brier =
        r.outcome == null || cur == null ? null : (cur.probability - r.outcome) ** 2;
      setDone((s) => ({
        ...s,
        [pid]: {
          quick,
          label,
          outcome: r.outcome,
          confidence: r.confidence,
          brier,
          needsConfirmation: r.needs_confirmation,
          at: Date.now(),
          reply: reply.trim() || undefined,
          desc: pending.find((p) => p.prediction_id === pid)?.description ?? '',
        } as Verdict,
      }));
      setReply('');
      due.reload();
    } catch (e) {
      // 失败不清空，允许重试；错误展示在当前卡片上
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="验证"
        desc="到期的预测一条条过：当场裁决，批完即走，成败一视同仁。"
        right={<Badge tone="gilt">待批 {pending.length}</Badge>}
      />

      {/* 批阅进度 */}
      {totalToday > 0 && (
        <div className="flex items-center gap-4 rounded-2xl border border-line bg-panel px-4 py-3">
          <span className="text-xs text-t3">今日批阅</span>
          <ProgressBar value={doneList.length} max={totalToday} className="flex-1" />
          <span className="tabular text-xs font-semibold text-t1">
            {doneList.length}/{totalToday}
          </span>
        </div>
      )}

      {due.loading && (
        <Card>
          <Loading />
        </Card>
      )}
      {due.error && <ErrorBox message={due.error} />}

      {!due.loading && !due.error && !current && (
        <Card>
          <EmptyState
            title={doneList.length > 0 ? '今日已全部批完' : '当前没有到期的预测'}
          >
            {doneList.length > 0
              ? `已批复 ${doneList.length} 条。每一次批复都在喂养校准闭环（第 22 节：预测失败不是异常，是训练数据）。`
              : '到期的预测会自动出现在这里。明晚记得来给今天的预测做批复。'}
          </EmptyState>
        </Card>
      )}

      {/* 当前待批卡片 */}
      {current && (
        <Card
          key={current.prediction_id}
          className="animate-fade-up border-bd shadow-lift"
          right={
            <div className="flex items-center gap-2">
              <Badge>{DOMAIN_LABEL[current.event_type.split('.')[0]] ?? ''}</Badge>
              {current.status === 'RESEARCH' && <Badge tone="warn">研究样本</Badge>}
              <Badge tone="gilt">{pct(current.probability)}</Badge>
            </div>
          }
        >
          <div className="space-y-4">
            {/* 预测本体：大字号呈现，这是在「批」的东西 */}
            <div>
              <div className="text-lg font-medium leading-relaxed text-t1">
                {cleanDescription(current.description, current.event_type)}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-t4">
                <span>{current.event_type}</span>
                <span>
                  窗口 {shortDateTime(current.window[0])} ~ {shortDateTime(current.window[1])}
                </span>
              </div>
              <div className="mt-3 flex items-center gap-3">
                <ProbBar p={current.probability} className="w-44" />
                <span className="text-sm font-semibold tabular text-t1">
                  {pct(current.probability)}
                </span>
              </div>
            </div>

            {/* 裁定标准：批之前先看判据 */}
            <div className="grid gap-2 text-xs md:grid-cols-2">
              <div className="rounded-xl border border-jade-500/15 bg-jade-500/5 p-3">
                <div className="mb-1.5 font-medium text-jade-400">怎样算中</div>
                <ul className="space-y-1 text-t2">
                  {current.success_criteria.map((c, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="text-jade-500/60">✓</span>
                      {c}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rounded-xl border border-cinnabar-500/15 bg-cinnabar-500/5 p-3">
                <div className="mb-1.5 font-medium text-cinnabar-400">怎样算不中</div>
                <ul className="space-y-1 text-t2">
                  {current.failure_criteria.map((c, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="text-cinnabar-500/60">✗</span>
                      {c}
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            {/* 批复按钮：四档裁决，大目标、强手感；键盘 1-4 连批 */}
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {VERDICTS.map((v) => (
                <button
                  key={v.key}
                  disabled={busy}
                  data-kbd={v.key}
                  onClick={() => void stamp(current.prediction_id, v.key, v.label)}
                  className={`btn-press flex flex-col items-center gap-1 rounded-xl border px-3 py-3 disabled:opacity-50 ${VERDICT_STYLE[v.tone]}`}
                >
                  <span className="text-base font-semibold tracking-wide">{v.label}</span>
                  <span className="text-[10px] opacity-70">
                    快捷裁定 · 按{' '}
                    <kbd className="rounded border border-current/30 px-1 font-mono">
                      {VERDICTS.findIndex((x) => x.key === v.key) + 1}
                    </kbd>
                  </span>
                </button>
              ))}
            </div>

            {/* 补充说明（可选）：随批复一起提交；回车不直接裁决，避免误按成「命中」 */}
            <div className="flex gap-2">
              <input
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="补充一句今天实际发生了什么（可选，点上方裁决时随批复一起提交）…"
                className={`flex-1 ${inputCls}`}
              />
            </div>

            <div className="text-center text-[11px] text-t5">
              还有 {pending.length - 1} 条待批 · 批复即冻结结果，不可事后改口（C-003）
            </div>
          </div>
        </Card>
      )}

      {/* 本轮已批 —— 收拢展示，盖章式徽章 */}
      {doneList.length > 0 && (
        <Card title="本轮已批复" subtitle="最新在前" className="opacity-90">
          <ul className="space-y-2">
            {doneList.map((d, i) => {
              const pid = Object.keys(done).find((k) => done[k].at === d.at);
              const orig = items.find((it) => it.prediction_id === pid);
              const v = VERDICTS.find((v) => v.key === d.quick);
              return (
                <li
                  key={i}
                  className="animate-fade-up flex items-center gap-3 rounded-xl border border-line px-3.5 py-2.5"
                >
                  {/* 印章式裁定标 */}
                  <span
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border text-sm font-bold ${
                      v ? VERDICT_STYLE[v.tone].split(' ').slice(0, 3).join(' ') : ''
                    }`}
                  >
                    {v?.stamp}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-t1">
                      {orig ? cleanDescription(orig.description, orig.event_type) : ''}
                    </div>
                    <div className="mt-0.5 text-[11px] text-t4">
                      {d.label} ·{' '}
                      {d.outcome == null
                        ? '未计结果'
                        : `判定值 ${(d.outcome * 100).toFixed(0)}%`}{' '}
                      · 置信 {((d.confidence ?? 0) * 100).toFixed(0)}%
                      {d.brier != null && ` · 误差 ${d.brier.toFixed(3)}`}
                      {d.needsConfirmation && ' · 转待人工确认'}
                      {d.reply && ` · 附言：${d.reply}`}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      )}
    </div>
  );
}
