import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { api, DEFAULT_USER_ID } from '../api/client';
import { AlmanacDial, ColorSwatches, DOMAIN_ACCENT, Sparkles } from '../components/almanac';
import { DivinationStage } from '../components/rituals';
import { PredictionDrawer } from '../components/prediction-drawer';
import {
  Badge,
  Card,
  EmptyState,
  ErrorBox,
  Loading,
  PageHeader,
  PrimaryButton,
  ProbBar,
  ProgressBar,
  Segmented,
  Stat,
} from '../components/ui';
import {
  DOMAIN_LABEL,
  SCALE_LABEL,
  STATUS_LABEL,
  cleanDescription,
  edgeClass,
  edgeText,
  num,
  pct,
  shortDate,
  shortDateTime,
} from '../lib/format';
import { useAsync } from '../lib/useAsync';

const SCALES = [
  { key: 'day', label: '今日' },
  { key: 'week', label: '7 天' },
  { key: 'month', label: '30 天' },
  { key: 'year', label: '90 天' },
] as const;

/* ---------- 按日分组：预测的组织主线是「哪一天 → 发生什么事」 ---------- */
const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'] as const;

/** 术式来源中文名（交叉印证徽标用） */
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

/* ---------- 经文小标签（卦辞/动爻/大象） ---------- */
function CanonTag({ label }: { label: string }) {
  return (
    <span className="mr-2 inline-block rounded border border-gilt-500/30 bg-gilt-500/10 px-1.5 py-px align-middle text-[10px] font-sans text-gt">
      {label}
    </span>
  );
}

function localDayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

/* ---------- 日卦卦象：六爻自上而下（上爻最上）；阳爻整条，阴爻两段，动爻鎏金 ---------- */
function HexGlyph({ lines, moving }: { lines: number[]; moving?: number }) {
  const rows = lines.map((v, i) => ({ v, pos: i + 1 })).reverse();
  const bar = (isMoving: boolean) => ({
    background: isMoving ? '#d9b96a' : 'var(--t2)',
    boxShadow: isMoving ? '0 0 8px rgba(201,162,39,0.55)' : 'none',
  });
  return (
    <div className="flex w-full shrink-0 flex-col gap-[7px]" aria-label="本日卦象">
      {rows.map(({ v, pos }) => (
        <div
          key={pos}
          className="relative flex h-2.5 items-center"
          title={`第${pos}爻${pos === moving ? '（动爻）' : ''}`}
        >
          {v === 1 ? (
            <div className="h-[3px] w-full rounded-full" style={bar(pos === moving)} />
          ) : (
            <div className="flex w-full justify-between">
              <div className="h-[3px] w-[44%] rounded-full" style={bar(pos === moving)} />
              <div className="h-[3px] w-[44%] rounded-full" style={bar(pos === moving)} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function groupByDueDay<
  T extends { verification_due_at?: string | null; window: [string, string] },
>(items: T[]): [string, T[]][] {
  // 按「预测指向的那一天」（应验窗口起点）分组 —— 用户心智是「某天会发生什么事」
  const map = new Map<string, T[]>();
  for (const it of items) {
    const key = (it.window[0] ?? '').slice(0, 10) || '未知日期';
    map.set(key, [...(map.get(key) ?? []), it]);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

function DayHeader({ dateKey }: { dateKey: string }) {
  const d = new Date(`${dateKey}T00:00:00`);
  const invalid = Number.isNaN(d.getTime());
  const today = localDayKey(new Date());
  const tomorrow = localDayKey(new Date(Date.now() + 86400_000));
  const rel = dateKey === today ? '今天' : dateKey === tomorrow ? '明天' : null;
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-9 w-9 shrink-0 flex-col items-center justify-center rounded-lg border border-gilt-500/25 bg-gilt-500/[0.07]">
        <span className="tabular text-[13px] font-bold leading-none text-gt">
          {invalid ? '—' : d.getDate()}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium text-t1">
          {invalid ? dateKey : `${d.getMonth() + 1}月${d.getDate()}日`}
        </span>
        {!invalid && <span className="text-xs text-t4">周{WEEKDAYS[d.getDay()]}</span>}
        {rel && (
          <span className="rounded-full border border-gilt-500/30 bg-gilt-500/10 px-1.5 py-px text-[10px] font-medium text-gt">
            {rel}
          </span>
        )}
      </div>
      <div className="h-px flex-1 bg-gradient-to-r from-line to-transparent" />
    </div>
  );
}

/** 预测闭环七步（对应系统流水线） */
const PIPELINE = [
  { key: 'scan', label: '扫描', desc: '候选事件' },
  { key: 'blind', label: '盲审', desc: '去标识评分' },
  { key: 'fuse', label: '融合', desc: '多引擎加权' },
  { key: 'gate', label: '审查', desc: '对抗性 Gate' },
  { key: 'budget', label: '预算', desc: '额度竞争' },
  { key: 'freeze', label: '冻结', desc: 'SHA-256 封账' },
  { key: 'verify', label: '验证', desc: '现实检验' },
];

/**
 * 闭环流水线可视化 —— 页面的视觉锚点。
 * 生成中：逐步点亮的进行态；空闲：静态展示流程。
 */
function PipelineSteps({ active, done }: { active: boolean; done: boolean }) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (!active) return;
    setStep(0);
    const t = setInterval(() => setStep((s) => (s + 1) % PIPELINE.length), 900);
    return () => clearInterval(t);
  }, [active]);

  return (
    <div className="flex items-stretch gap-0 overflow-x-auto pb-1">
      {PIPELINE.map((p, i) => {
        const lit = active ? i <= step : done;
        const current = active && i === step;
        return (
          <div key={p.key} className="flex min-w-0 flex-1 items-center">
            <div className="flex min-w-[64px] flex-1 flex-col items-center gap-1.5 text-center">
              <span
                className={`flex h-8 w-8 items-center justify-center rounded-full border text-[11px] font-semibold transition-all duration-500 ${
                  current
                    ? 'status-dot border-gilt-400 bg-gilt-500/20 text-gt'
                    : lit
                      ? 'border-gilt-500/50 bg-gilt-500/10 text-gt'
                      : 'border-line bg-panel text-t5'
                }`}
              >
                {i + 1}
              </span>
              <div>
                <div
                  className={`text-xs font-medium transition-colors duration-500 ${
                    lit ? 'text-t1' : 'text-t4'
                  }`}
                >
                  {p.label}
                </div>
                <div className="mt-0.5 text-[10px] text-t5">{p.desc}</div>
              </div>
            </div>
            {i < PIPELINE.length - 1 && (
              <div
                className={`mx-1 h-px w-4 shrink-0 transition-colors duration-500 md:w-6 ${
                  lit && i < step ? 'bg-gilt-500/50' : 'bg-panel'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * 校准旅程：三阶段（基线校准 → 信号实证 → 正式预测）。
 * 冷启动校准分层方案的对外呈现 —— 系统在哪个阶段、为什么、还差多少，全部透明。
 */
type Phase = 'cold' | 'explore' | 'formal';

function CalibrationJourney({
  phase,
  calibrated,
  minCalibration,
  minFormal,
}: {
  phase: Phase;
  calibrated: number;
  minCalibration: number;
  minFormal: number;
}) {
  const steps: { key: Phase; label: string; desc: string; target: number | null }[] = [
    { key: 'cold', label: '基线校准', desc: '建立真实频率基线', target: minCalibration },
    { key: 'explore', label: '信号实证', desc: '术式弱先验参与留痕', target: minFormal },
    { key: 'formal', label: '正式预测', desc: '可靠度权重已实证', target: null },
  ];
  const idx = phase === 'cold' ? 0 : phase === 'explore' ? 1 : 2;
  const target = steps[idx].target;

  return (
    <div>
      <div className="flex items-stretch">
        {steps.map((s, i) => {
          const done = i < idx;
          const current = i === idx;
          return (
            <div key={s.key} className="flex min-w-0 flex-1 items-center">
              <div className="flex min-w-[86px] flex-1 flex-col items-center gap-1.5 text-center">
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full border text-[10px] font-semibold transition-all duration-500 ${
                    done
                      ? 'border-gilt-400 bg-gilt-500/15 text-gt'
                      : current
                        ? 'status-dot border-gilt-400 bg-gilt-500/20 text-gt shadow-[0_0_12px_-2px_rgba(217,185,106,0.6)]'
                        : 'border-line bg-panel text-t5'
                  }`}
                >
                  {done ? (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                      <path d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    i + 1
                  )}
                </span>
                <div>
                  <div
                    className={`text-xs font-medium transition-colors duration-300 ${
                      current ? 'text-t1' : done ? 'text-t2' : 'text-t5'
                    }`}
                  >
                    {s.label}
                  </div>
                  <div className="mt-0.5 hidden text-[10px] text-t5 md:block">{s.desc}</div>
                </div>
              </div>
              {i < steps.length - 1 && (
                <div className="relative mx-1 h-px flex-1 bg-line">
                  <div
                    className={`absolute inset-y-0 left-0 bg-gilt-500/60 transition-all duration-700 ${
                      done ? 'w-full' : 'w-0'
                    }`}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {target != null && (
        <div className="mt-4">
          <div className="mb-1.5 flex items-baseline justify-between text-xs">
            <span className="text-t3">
              {phase === 'cold'
                ? '已验证样本（Null 基线校准中，术式尚未参与）'
                : '已验证样本（术式弱先验参与，积累实证中）'}
            </span>
            <span className="tabular font-semibold text-t1">
              {calibrated}
              <span className="text-t4">/{target}</span>
            </span>
          </div>
          <ProgressBar value={calibrated} max={target} />
        </div>
      )}
    </div>
  );
}

export default function Future() {
  const [scale, setScale] = useState<(typeof SCALES)[number]['key']>('day');
  const [generating, setGenerating] = useState(false);
  const [notes, setNotes] = useState<string[] | null>(null);
  const [notesOpen, setNotesOpen] = useState(true);
  const [runDone, setRunDone] = useState(false);

  const preds = useAsync(() => api.listPredictions(DEFAULT_USER_ID), []);
  const daily = useAsync(() => api.fortuneDaily(DEFAULT_USER_ID), []);
  const overall = useAsync(() => api.overall(DEFAULT_USER_ID), []);
  const due = useAsync(() => api.duePredictions(DEFAULT_USER_ID), []);
  const health = useAsync(() => api.health(), []);
  const tree = useAsync(() => api.futureTree(DEFAULT_USER_ID), []);
  const meta = useAsync(() => api.meta(), []);
  const [drawerPid, setDrawerPid] = useState<string | null>(null);

  // 校准阶段门槛：三阶段（cold 基线校准 → explore 信号实证 → formal 正式预测）
  const calibration = (
    meta.data as
      | {
          calibration?: {
            min_calibration_samples?: number;
            min_formal_samples?: number;
          };
        }
      | undefined
  )?.calibration;
  const minCalibration = calibration?.min_calibration_samples ?? 5;
  const minFormal = calibration?.min_formal_samples ?? 20;
  const calibratedCount = overall.data?.sample_size ?? 0;
  const phase: Phase =
    calibratedCount < minCalibration
      ? 'cold'
      : calibratedCount < minFormal
        ? 'explore'
        : 'formal';
  const phaseTarget = phase === 'cold' ? minCalibration : minFormal;
  const researching = phase !== 'formal';

  const visible = (preds.data?.items ?? []).filter(
    (p) => p.time_scale === scale || scale === 'day',
  );
  const research = visible.filter((p) => p.status === 'RESEARCH');
  const formal = visible.filter((p) => p.status !== 'RESEARCH');

  const generate = async () => {
    setGenerating(true);
    setRunDone(false);
    setNotes(null);
    setNotesOpen(true);
    try {
      const r = await api.generate(DEFAULT_USER_ID, scale, 20);
      setNotes([
        ...r.notes,
        ...r.rejected.map((x) => `拦截 ${x.event_type}（${x.decision}）：${x.reasons[0] ?? ''}`),
      ]);
      preds.reload();
      overall.reload();
      due.reload();
      setRunDone(true);
    } catch (e) {
      setNotes([e instanceof Error ? e.message : String(e)]);
    } finally {
      setGenerating(false);
    }
  };

  const engineOk = health.data
    ? Object.values(health.data.engines).filter((e) => e.available).length
    : 0;
  const engineTotal = health.data ? Object.keys(health.data.engines).length : 8;

  const renderRow = (p: (typeof visible)[number], isResearch = false) => {
    const accent = DOMAIN_ACCENT[p.domain];
    return (
    <li
      key={p.prediction_id}
      className="row-hover relative cursor-pointer rounded-lg border border-line p-3 pl-4 transition-colors hover:border-gilt-500/40"
      onClick={() => setDrawerPid(p.prediction_id)}
      title="点击查看信号与冻结详情"
    >
      {/* 域色渐变边条 + 域字小印（罗盘一脉相承的章感） */}
      <i
        aria-hidden
        className="pointer-events-none absolute inset-y-2 left-0 w-[3px] rounded-full"
        style={{
          background: accent
            ? `linear-gradient(180deg, ${accent.from}, ${accent.to})`
            : 'linear-gradient(180deg, #cbd5e1, #94a3b8)',
        }}
      />
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {accent && (
              <span
                aria-hidden
                className="flex h-5 w-5 items-center justify-center rounded-[5px] text-[10px] font-bold text-white/95 shadow-sm"
                style={{ background: `linear-gradient(160deg, ${accent.from}, ${accent.to})` }}
                title={DOMAIN_LABEL[p.domain] ?? p.domain}
              >
                {accent.seal}
              </span>
            )}
            <span className="text-sm text-t1">{cleanDescription(p.description, p.event_type)}</span>
            <Badge>{DOMAIN_LABEL[p.domain] ?? p.domain}</Badge>
            <Badge tone="info">{SCALE_LABEL[p.time_scale]}</Badge>
            {p.visibility_mode === 'HIDDEN' && <Badge tone="warn">隐藏模式</Badge>}
          </div>
          <div className="mt-1 text-xs text-t4">
            {p.event_type} · 窗口 {shortDate(p.window[0])} ~ {shortDate(p.window[1])}
            {p.verification_due_at && ` · 验证截止 ${shortDateTime(p.verification_due_at)}`}
          </div>
          <div className="mt-2 flex items-center gap-3">
            <ProbBar p={p.probability} className="w-40" />
            <span className="text-sm font-semibold tabular text-t1">{pct(p.probability)}</span>
            {p.null_probability != null && (
              <>
                <span className="text-xs text-t4">Null 基线 {pct(p.null_probability)}</span>
                <span
                  className={`text-xs font-medium ${edgeClass(p.probability, p.null_probability)}`}
                  title="预测概率相对 Null 基线的差值：正值=比随机强，负值=比随机还差"
                >
                  {edgeText(p.probability, p.null_probability)}
                </span>
              </>
            )}
          </div>
          {/* 多法交叉印证：≥2 术式同向才是真正的「多方法交叉」 */}
          {((p.supporting_sources?.length ?? 0) > 0 || (p.opposing_sources?.length ?? 0) > 0) && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {p.crossed && (
                <Badge tone="good" className="badge-glow-sweep">
                  ◆ {p.supporting_sources!.length} 法交叉印证
                </Badge>
              )}
              {(p.supporting_sources ?? []).map((s) => (
                <Badge key={s} tone="gilt">
                  ✓ {SOURCE_ZH[s] ?? s}
                </Badge>
              ))}
              {(p.opposing_sources ?? []).map((s) => (
                <Badge key={s} tone="warn">
                  ✗ {SOURCE_ZH[s] ?? s}
                </Badge>
              ))}
            </div>
          )}
          {p.narrative && (
            <div className="mt-2 whitespace-pre-line rounded-lg border border-bd/60 bg-white/[0.03] px-3 py-2 text-xs leading-relaxed text-t2">
              {p.narrative}
            </div>
          )}
          {isResearch && (
            <div className="mt-2 flex items-start gap-1.5 text-xs text-amber-400/90">
              <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-amber-400" />
              <span>
                {phase === 'explore'
                  ? '研究样本（实证期）：术式信号以弱先验参与并完整留痕，验证后将转化为各术式的可靠度实证。尚不代表预测力。'
                  : '研究样本（冷启动）：多术式交叉只决定「选题与详批」，概率仍是 Null 基线 —— 术式效力未经你的现实验证前，系统不替它背书。验证样本攒够后即自动升级。'}
              </span>
            </div>
          )}
        </div>
        <div className="shrink-0 text-right">
          <Badge
            tone={
              p.status === 'VERIFIED'
                ? 'good'
                : p.status === 'RESEARCH'
                  ? 'warn'
                  : p.status === 'REJECTED' || p.status === 'LEAKED'
                    ? 'bad'
                    : 'default'
            }
          >
            {STATUS_LABEL[p.status] ?? p.status}
          </Badge>
          <div className="mt-1 font-mono text-[10px] text-t5"
            title="冻结哈希前缀（防篡改）"
          >
            {p.sha256_head}
          </div>
        </div>
      </div>
    </li>
    );
  };

  return (
    <div className="space-y-5">
      {/* 头部：方案第 48 节 Future Dashboard */}
      <PageHeader
        title="未来"
        desc="系统主动生成预测并冻结，等待现实检验。候选不等于正式预测。"
        right={
          <>
            <Badge tone={engineOk > 0 ? 'good' : 'warn'}>
              引擎 {engineOk}/{engineTotal} 可用
            </Badge>
            <PrimaryButton className="btn-aura" onClick={generate} busy={generating}>
              {generating
                ? phase === 'formal'
                  ? '生成中，LLM 正在评审候选…'
                  : '生成中，秒级完成…'
                : '生成预测'}
            </PrimaryButton>
          </>
        }
      />

      {/* 待批引导前置：有到期预测时首屏即可进入批复流 */}
      {(due.data?.count ?? 0) > 0 && (
        <div className="animate-fade-up flex items-center justify-between gap-3 rounded-xl border border-gilt-500/40 bg-gilt-500/[0.05] px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <Badge tone="gilt">待批 {due.data!.count}</Badge>
            <span className="truncate text-sm text-t1">
              有 {due.data!.count} 条预测已到期，等你批复裁决 —— 每条批复都在为可靠度矩阵积累实证
            </span>
          </div>
          <Link to="/verify" className="shrink-0">
            <PrimaryButton className="text-xs">去验证</PrimaryButton>
          </Link>
        </div>
      )}

      {/* 今日卡：锦囊 + 日卦 + 本日参读 一卡聚合（round 19 UI 升级） */}
      {daily.data && (
        <Card
          className="frame-flow overflow-hidden"
          title={`今日 · ${daily.data.day_ganzhi}日 · ${daily.data.lunar_date}`}
          subtitle={`值神 ${daily.data.day_god} · 冲${daily.data.chong} 煞${daily.data.sha_direction}`}
          right={
            daily.data.peach_activated && daily.data.peach_activated.length > 0 ? (
              <Badge tone="good">❀ {daily.data.peach_activated.join('、')}</Badge>
            ) : undefined
          }
        >
          <div aria-hidden className="aura-gilt pointer-events-none absolute inset-0" />
          <Sparkles count={9} seed={11} />
          <div className="grid gap-4 lg:grid-cols-[1fr_auto]">
            {/* 左：宜忌 + 吉神 + 幸运元素 */}
            <div className="min-w-0 space-y-2.5 text-xs text-t2">
              <div className="grid grid-cols-[auto_1fr] items-start gap-x-3 gap-y-1.5">
                <span className="pt-0.5 font-medium text-t3">宜</span>
                <div className="flex flex-wrap gap-1">
                  {daily.data.yi.map((y) => (
                    <Badge key={y} tone="good">
                      {y}
                    </Badge>
                  ))}
                </div>
                <span className="pt-0.5 font-medium text-t3">忌</span>
                <div className="flex flex-wrap gap-1">
                  {daily.data.ji.map((j) => (
                    <Badge key={j} tone="bad">
                      {j}
                    </Badge>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span>
                  喜神 <b className="text-t1">{daily.data.xi_dir}</b>
                </span>
                <span>
                  财神 <b className="text-t1">{daily.data.cai_dir}</b>
                </span>
                <span>
                  福神 <b className="text-t1">{daily.data.fu_dir}</b>
                </span>
                <span>
                  幸运数 <b className="text-gt">{daily.data.lucky_numbers.join('、')}</b>
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span>幸运色</span>
                <ColorSwatches text={daily.data.lucky_color} />
                <span className="text-t4">（辅</span>
                <ColorSwatches text={daily.data.lucky_color_aux} size={10} />
                <span className="text-t4">）</span>
                {daily.data.day_master && (
                  <span className="text-t3">
                    日主{daily.data.day_master}（{daily.data.day_master_wuxing}）x 今日
                    {daily.data.day_master_relation}
                  </span>
                )}
              </div>
              <div>
                吉时{' '}
                {daily.data.lucky_hours.map((h) => (
                  <span key={h} className="mr-2">
                    {h}
                  </span>
                ))}
              </div>
            </div>
            {/* 右：罗盘（视觉锚） */}
            <div className="mx-auto lg:mx-0">
              <AlmanacDial
                xi={daily.data.xi_dir}
                cai={daily.data.cai_dir}
                fu={daily.data.fu_dir}
              />
            </div>
          </div>

          {/* 日卦（压缩行）+ 命数：折叠保留全文 */}
          {daily.data.daily_gua && (
            <div className="mt-3 rounded-xl border border-line bg-panel p-3">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2.5">
                  <div className="w-10">
                    <HexGlyph
                      lines={daily.data.daily_gua.lines}
                      moving={daily.data.daily_gua.moving_yao}
                    />
                  </div>
                  <div>
                    <div className="font-serif text-lg font-semibold text-gt">
                      {daily.data.daily_gua.short}
                    </div>
                    <Badge tone="gilt">动爻·第{daily.data.daily_gua.moving_yao}爻</Badge>
                  </div>
                </div>
                <div className="min-w-0 flex-1 space-y-0.5 font-serif text-xs leading-relaxed text-t2">
                  {daily.data.daily_gua.gua_ci && (
                    <p className="line-clamp-1" title={daily.data.daily_gua.gua_ci}>
                      <CanonTag label="卦辞" />
                      {daily.data.daily_gua.gua_ci}
                    </p>
                  )}
                  {daily.data.daily_gua.yao_ci && (
                    <p className="line-clamp-1" title={daily.data.daily_gua.yao_ci}>
                      <CanonTag label="动爻" />
                      {daily.data.daily_gua.yao_ci}
                    </p>
                  )}
                </div>
              </div>
              {(daily.data.daily_gua.natal_notes?.length ?? 0) > 0 && (
                <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-t3">
                  <CanonTag label="命数" />
                  {daily.data.daily_gua.natal_notes![0]}
                  {(daily.data.daily_gua.natal_notes?.length ?? 0) > 1 && ' ……（展开见全部）'}
                </p>
              )}
              <details className="mt-1.5 text-[11px] text-t4">
                <summary className="cursor-pointer select-none hover:text-t2">
                  命数全部批示 · 大象传
                </summary>
                <ul className="mt-1.5 space-y-1 text-t2">
                  {(daily.data.daily_gua.natal_notes ?? []).map((n, i) => (
                    <li key={i}>· {n}</li>
                  ))}
                </ul>
                {daily.data.daily_gua.xiang && (
                  <p className="mt-1.5 font-serif text-t3">大象：{daily.data.daily_gua.xiang}</p>
                )}
                <p className="mt-1 text-t5">同日并列参读，非因果，非效力宣称。</p>
              </details>
            </div>
          )}

          {/* 本日参读：窗口覆盖本日的在库预测 */}
          {daily.data.related_predictions &&
            daily.data.related_predictions.length > 0 && (
              <div className="mt-2.5 rounded-xl border border-line bg-panel p-3">
                <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-t3">
                  <CanonTag label="本日参读" />
                  窗口覆盖今天的在库预测（{daily.data.related_predictions.length} 条 · 与卦同日并列，非因果）
                </div>
                <div className="flex flex-wrap gap-2">
                  {daily.data.related_predictions.map((p) => (
                    <span
                      key={p.prediction_id}
                      className="rounded-lg border border-bd bg-card px-2.5 py-1.5 text-xs text-t2"
                      title={`窗口 ${p.window[0]} ~ ${p.window[1]} · 状态 ${STATUS_LABEL[p.status] ?? p.status}`}
                    >
                      {p.description}
                      <b className="ml-2 text-gt">{pct(p.probability)}</b>
                    </span>
                  ))}
                </div>
              </div>
            )}
        </Card>
      )}

      {/* 闭环流水线：页面视觉锚点 */}
      <Card
        className="frame-flow"
        title="预测闭环"
        subtitle={
          generating
            ? phase === 'formal'
              ? '正在逐站推进，LLM 评审入选候选（已并发，约一两分钟）…'
              : '研究期全程确定性计算，不依赖 LLM，秒级完成…'
            : runDone
              ? '本轮闭环已跑完，以下为运行记录'
              : '每条正式预测都必须走完这七站'
        }
      >
        <PipelineSteps active={generating} done={runDone} />
        {/* 七术式推演仪式：生成期间轮转各术式的传统仪程动效 */}
        {(generating || runDone) && <DivinationStage active={generating} done={runDone} />}
      </Card>

      <PredictionDrawer pid={drawerPid} onClose={() => setDrawerPid(null)} />

      {notes && (
        <Card
          title={`本轮运行记录 · ${notes.length} 条`}
          subtitle="含被对抗性 Gate 拦截的候选"
          right={
            <button
              onClick={() => setNotesOpen((v) => !v)}
              className="btn-press text-xs text-t3 hover:text-t1"
            >
              {notesOpen ? '收起 ▲' : '展开 ▼'}
            </button>
          }
        >
          {notesOpen && (
            <ul className="stagger space-y-1 text-xs text-t2">
              {notes.map((n, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-gt">·</span>
                  <span>{n}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {/* 校准旅程（研究期显示）：三阶段可感知，诚实且可预期 */}
      {researching && (
        <Card
          title="校准旅程"
          subtitle="系统在当前阶段的每次克制，都是为了让「正式预测」四个字有实证支撑"
          right={<Badge tone="warn">{phase === 'cold' ? '冷启动' : '信号实证'}</Badge>}
        >
          <CalibrationJourney
            phase={phase}
            calibrated={calibratedCount}
            minCalibration={minCalibration}
            minFormal={minFormal}
          />
        </Card>
      )}

      {/* 尺度切换：分段控件 */}
      <Segmented options={SCALES} value={scale} onChange={setScale} />

      {/* 概览指标 */}
      <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="待验证" value={due.data?.count ?? '—'} hint="到期需用户确认" />
        <Stat
          label={researching ? '校准进度' : '校准样本'}
          value={`${calibratedCount}/${phaseTarget}`}
          hint={
            phase === 'cold'
              ? '基线校准中 · 达标后进入信号实证'
              : phase === 'explore'
                ? '信号实证中 · 达标后解锁正式预测'
                : '已解锁正式预测'
          }
          tone={phase === 'formal' ? 'good' : 'warn'}
        />
        <Stat
          label="Skill Score"
          value={
            overall.data?.skill_score != null ? pct(overall.data.skill_score, 1) : '—'
          }
          hint="相对 Null Model"
          tone={(overall.data?.skill_score ?? 0) > 0 ? 'good' : 'bad'}
        />
        <Stat
          label="Brier"
          value={overall.data ? num(overall.data.brier) : '—'}
          hint="越低越好"
          tone={(overall.data?.brier ?? 1) < 0.25 ? 'good' : 'warn'}
        />
      </div>

      {/* 研究期研究样本 */}
      {researching && (
        <Card
          title="研究样本"
          subtitle={
            phase === 'cold'
              ? `校准进度 ${calibratedCount}/${minCalibration}：尚未积累足够验证数据，术式信号未参与预测。以下样本用于启动校准闭环，验证后系统才会学会真正预测。`
              : `实证进度 ${calibratedCount}/${minFormal}：术式信号以弱先验权重参与融合并完整留痕，每条验证都在为对应术式积累实证。`
          }
          right={
            <Badge tone="warn">{phase === 'cold' ? '冷启动模式' : '实证研究模式'}</Badge>
          }
        >
          {preds.loading && <Loading />}
          {preds.error && <ErrorBox message={preds.error} />}
          {!preds.loading && !preds.error && research.length === 0 && (
            <EmptyState>
              暂无研究样本。
              <br />
              点击右上角「生成预测」，系统会从候选事件里挑出最值得观察的几件，
              作为研究样本冻结，供你在「验证」页填结果。
            </EmptyState>
          )}
          <div className="stagger space-y-5">
            {groupByDueDay(research).map(([day, list]) => (
              <div key={day} className="space-y-2.5">
                <DayHeader dateKey={day} />
                <ul className="space-y-2.5">{list.map((p) => renderRow(p, true))}</ul>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* 正式冻结预测 */}
      <Card
        title="正式冻结预测"
        subtitle="只有通过对抗性 Gate 并获得预算额度的预测才会出现在这里"
      >
        {preds.loading && <Loading />}
        {preds.error && <ErrorBox message={preds.error} />}
        {!preds.loading && !preds.error && formal.length === 0 && (
          <EmptyState>
            {researching ? (
              <>
                还没有正式预测。
                <br />
                {phase === 'cold'
                  ? '系统处于基线校准期：术式信号未经实证、不参与融合，只产出「研究样本」（见上方）。'
                  : '系统处于信号实证期：术式以弱先验参与融合并留痕，但暂不产出正式预测，只产出「研究样本」。'}
                <br />
                <span className="text-t4">
                  （C-006 诚实原则：术数不比随机强时，系统必须承认它没有预测力，
                  而不是硬造噪声预测。累计 {minFormal} 条验证样本后自动解锁正式预测。）
                </span>
              </>
            ) : (
              <>
                当前没有正式预测。
                <br />
                系统只在「术数/现实信号显著超过随机基线」时才冻结预测——
                如果今天没找到有预测力的事件，会诚实放弃，而不是硬造噪声预测。
                <br />
                <span className="text-t4">
                  （这是 C-006 诚实原则：若术数不比随机强，系统必须承认它没有贡献。）
                </span>
              </>
            )}
          </EmptyState>
        )}
        <div className="stagger space-y-5">
          {groupByDueDay(formal).map(([day, list]) => (
            <div key={day} className="space-y-2.5">
              <DayHeader dateKey={day} />
              <ul className="space-y-2.5">{list.map((p) => renderRow(p))}</ul>
            </div>
          ))}
        </div>
      </Card>

      {/* 第 27 节 Future Tree：人生情景树（每周按新证据重算） */}
      <Card
        title="人生情景树"
        subtitle="第 27 节：Future Tree 每周按新证据重算 P(Scenario | New Evidence)"
      >
        {tree.loading && <Loading />}
        {tree.error && <ErrorBox message={tree.error} />}
        {!tree.loading && !tree.error && tree.data && (
          <ul className="stagger space-y-3">
            {tree.data.scenarios.map((s) => (
              <li
                key={s.key}
                className="row-hover rounded-lg border border-line p-3"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-gilt-500/30 bg-gilt-500/10 text-sm font-semibold text-gt">
                    {s.key}
                  </span>
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-t1">{s.label}</span>
                      <span className="text-sm font-semibold tabular text-t1">
                        {pct(s.probability, 0)}
                      </span>
                    </div>
                    <ProbBar p={s.probability} className="mt-1.5 w-full" />
                    <div className="mt-1.5 text-xs text-t4">{s.description}</div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
