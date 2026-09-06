import { useState } from 'react';

import { api, DEFAULT_USER_ID, type FortuneReading, type ZiweiReading } from '../api/client';
import {
  BRIGHTNESS_CLS,
  DAYUN_NOTE,
  MiniTaiji,
  MUTAGEN_CLS,
  Sparkles,
  WUXING_COLOR,
  tenGod,
  tenGodOfZhi,
  wuxingOfGan,
  wuxingOfZhi,
} from '../components/almanac';
import { RitualLoading } from '../components/rituals';
import { ImagingPanel } from '../components/imaging-panel';
import { Badge, Card, EmptyState, ErrorBox, GhostButton, Loading, PageHeader, PrimaryButton, inputCls } from '../components/ui';
import { SOURCE_LABEL, cleanDescription, pct } from '../lib/format';
import { useAsync } from '../lib/useAsync';

/**
 * 第 6 节 Metaphysical Engine
 * 第 49 节 Prediction Detail（完全可解释）
 * 第 80 节 Prediction Lineage
 */

const ENGINE_LABEL: Record<string, string> = {
  ziwei: '紫微斗数',
  bazi: '八字',
  qimen: '奇门遁甲',
  liuyao: '六爻',
  meihua: '梅花易数',
  zhouyi: '周易（义理）',
  palm: '掌纹',
  face: '面相',
};

const ENGINE_REF: Record<string, string> = {
  ziwei: 'SylarLong/iztro',
  bazi: '6tail/lunar-python',
  qimen: 'Maximilian-Winter/Qimen-Dunjia',
  liuyao: 'Johnson-Jia/liuyao-divination',
  meihua: 'handsomejustin/meihua-yi',
  zhouyi: '通行本《周易》内置抄本 · 卦辞爻辞断辞（本地）',
  palm: 'OpenCV 肤色分割 + 线纹测量（本地）',
  face: 'OpenCV Haar 级联 + 三庭五眼几何（本地）',
};

/** 小徽标色调（传统色系）：批示维度/十神按类别分色，不再清一色鎏金 */
const TONE = {
  gilt: '#C9A227', // 鎏金
  azure: '#4A7EBB', // 黛蓝
  amber: '#D98E2B', // 琥珀
  rouge: '#D4587A', // 茜红
  jade: '#3E9E7A', // 竹青
  violet: '#8A63B8', // 紫檀
  celadon: '#4FA3A5', // 青瓷
} as const;

/** 十神 → 关系类别色调：官杀朱 / 财帛琥珀 / 印绶黛蓝 / 食伤竹青 / 比劫紫檀 */
const SHISHEN_TONE: Record<string, string> = {
  正官: '#D45D5D', 七杀: '#D45D5D',
  正财: TONE.amber, 偏财: TONE.amber,
  正印: TONE.azure, 偏印: TONE.azure,
  食神: TONE.jade, 伤官: TONE.jade,
  比肩: TONE.violet, 劫财: TONE.violet,
};

/** 批示维度 → 图标 + 色调 */
const READING_META: { key: string; label: string; icon: string; tone: string }[] = [
  { key: '命格总论', label: '命格总论', icon: '命', tone: TONE.gilt },
  { key: '事业', label: '事业', icon: '业', tone: TONE.azure },
  { key: '财运', label: '财运', icon: '财', tone: TONE.amber },
  { key: '婚恋', label: '婚恋', icon: '姻', tone: TONE.rouge },
  { key: '健康', label: '健康', icon: '健', tone: TONE.jade },
  { key: '未来5年', label: '未来 5 年', icon: '5', tone: TONE.violet },
  { key: '未来10年', label: '未来 10 年', icon: '10', tone: TONE.celadon },
];

/** 命理批示区块（大运时间轴 + 流年 + 批示卡片） */
function FortuneSection({ data }: { data: FortuneReading }) {  const chart = data.chart;
  if (!chart) {
    return <ErrorBox message={data.error ?? '命盘排盘失败'} />;
  }

  const bazi = chart.bazi ?? {};
  const PILLAR_KEYS = ['year', 'month', 'day', 'time'] as const;
  const pillarLabel: Record<(typeof PILLAR_KEYS)[number], string> = {
    year: '年柱',
    month: '月柱',
    day: '日柱',
    time: '时柱',
  };

  // 五行分布：四柱八个字的五行计数，画成一条彩色能量条
  const elementCount: Record<string, number> = {};
  for (const k of PILLAR_KEYS) {
    const gz: string = bazi[k] ?? '';
    const g = wuxingOfGan(gz.slice(0, 1));
    const z = wuxingOfZhi(gz.slice(1, 2));
    if (g) elementCount[g] = (elementCount[g] ?? 0) + 1;
    if (z) elementCount[z] = (elementCount[z] ?? 0) + 1;
  }
  const elementTotal = Object.values(elementCount).reduce((a, b) => a + b, 0);

  const dayun = chart.dayun ?? [];
  // 当前精确周岁（后端已算好，前端直接用）；为 null 时回退到年份差近似
  const currentAgeExact = chart.current_age_exact;
  const nowYear = new Date().getFullYear();
  const fallbackAge = nowYear - (chart.liunian?.[0]?.age ?? 0);
  const effectiveAge = currentAgeExact ?? fallbackAge;
  const dayMaster: string = chart.day_master ?? '';
  // 选中的大运（点击运柱联动流年高亮）
  const [selDy, setSelDy] = useState<number | null>(null);
  const selDayun = selDy !== null ? dayun[selDy] : null;
  const selWindow =
    selDayun && selDy !== null
      ? (() => {
          const next = dayun[selDy + 1];
          return {
            startYear: selDayun.start_year,
            endYear: (next ? next.start_year : selDayun.start_year + 10) - 1,
            startAge: selDayun.start_age,
            endAge: (next ? next.start_age : selDayun.start_age + 10) - 1,
          };
        })()
      : null;

  return (
    <div className="space-y-5">
      {/* 四柱 + 五行 + 十神 */}
      <Card
        className="frame-flow"
        title="本命八字"
        subtitle={`日主 ${chart.day_master} · 命宫 ${chart.ming_gong || '—'} · ${
          chart.birth_time_known ? '时辰已确认' : '时辰未知（时柱存疑）'
        }`}
      >
        {/* 四柱卷轴：天干地支按五行着色，十神在上、纳音在下 */}
        <div className="grid grid-cols-4 gap-2 text-center">
          {PILLAR_KEYS.map((k) => {
            const gz: string = bazi[k] ?? '';
            const gan = gz.slice(0, 1);
            const zhi = gz.slice(1, 2);
            const wg = wuxingOfGan(gan);
            const wz = wuxingOfZhi(zhi);
            const isDay = k === 'day';
            return (
              <div
                key={k}
                className={`card-hover rounded-xl border py-3 ${
                  isDay ? 'border-gilt-500/40 bg-gilt-500/[0.06]' : 'border-line bg-panel'
                }`}
              >
                <div className={`text-[11px] ${isDay ? 'font-medium text-gt' : 'text-t4'}`}>
                  {pillarLabel[k]}
                  {isDay && ' · 日主'}
                </div>
                <div className="mt-0.5 min-h-4 text-[10px] text-t3">
                  {chart.shishen?.[k] ?? ''}
                </div>
                <div className="mt-1 font-serif text-2xl font-bold leading-9 tracking-wider">
                  <div style={{ color: WUXING_COLOR[wg] }} title={`天干${gan} · 五行属${wg}`}>
                    {gan || '—'}
                  </div>
                  <div style={{ color: WUXING_COLOR[wz] }} title={`地支${zhi} · 五行属${wz}`}>
                    {zhi || '—'}
                  </div>
                </div>
                <div className="mt-1 min-h-4 text-[10px] text-t5">
                  {chart.nayin?.[k] ?? ''}
                </div>
              </div>
            );
          })}
        </div>

        {/* 五行分布能量条 */}
        {elementTotal > 0 && (
          <div className="mt-3">
            <div className="mb-1.5 flex items-baseline justify-between text-[11px]">
              <span className="font-medium text-t3">五行分布</span>
              <span className="text-t5">
                {(['木', '火', '土', '金', '水'] as const)
                  .map((w) => (elementCount[w] ? `${w}${elementCount[w]}` : null))
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-panel">
              {(['木', '火', '土', '金', '水'] as const)
                .filter((w) => elementCount[w] > 0)
                .map((w) => (
                  <div
                    key={w}
                    style={{
                      width: `${(elementCount[w] / elementTotal) * 100}%`,
                      backgroundColor: WUXING_COLOR[w],
                    }}
                    title={`${w} ${elementCount[w]}`}
                  />
                ))}
            </div>
          </div>
        )}
      </Card>

      {/* 大运时间轴：可点击运柱，联动流年高亮 */}
      <Card
        className="frame-flow"
        title="大运流转"
        subtitle="十年一运；金色为当前行运，点击任一运柱查看这十年的基调"
      >
        {dayun.length === 0 ? (
          <EmptyState>暂无大运数据</EmptyState>
        ) : (
          <>
            <div className="flex items-stretch gap-0 overflow-x-auto pb-2">
              {dayun.map((d, i) => {
                const next = dayun[i + 1];
                const endAge = (next ? next.start_age : d.start_age + 10) - 1;
                const endYear = (next ? next.start_year : d.start_year + 10) - 1;
                const isCurrent =
                  effectiveAge != null &&
                  effectiveAge >= d.start_age &&
                  effectiveAge <= endAge;
                const selected = selDy === i;
                const gan = d.ganzhi.slice(0, 1);
                const zhi = d.ganzhi.slice(1, 2);
                const gWuxing = wuxingOfGan(gan);
                const zWuxing = wuxingOfZhi(zhi);
                const ganShen = tenGod(dayMaster, gan);
                const zhiShen = tenGodOfZhi(dayMaster, zhi);
                return (
                  <div key={i} className="flex min-w-[86px] flex-1 items-center">
                    <button
                      onClick={() => setSelDy(selected ? null : i)}
                      className={`btn-press flex w-full flex-col items-center gap-1 rounded-xl border px-1 py-2.5 text-center transition-all duration-300 ${
                        selected
                          ? 'border-gilt-400 bg-gilt-500/[0.14] shadow-[0_0_20px_-4px_rgba(201,162,39,0.6)]'
                          : isCurrent
                            ? 'border-gilt-500/60 bg-gilt-500/10 shadow-[0_0_14px_-4px_rgba(201,162,39,0.5)]'
                            : 'border-line bg-panel hover:border-gilt-500/40 hover:bg-gilt-500/[0.05]'
                      }`}
                      title={`${d.ganzhi}运 · ${d.start_year}–${endYear} · ${d.start_age}–${endAge}岁`}
                    >
                      <span className="text-[9px] tracking-wider text-t5">
                        {ganShen && (
                          <span style={{ color: SHISHEN_TONE[ganShen] ?? 'var(--t5)' }}>
                            {ganShen}
                          </span>
                        )}
                        {zhiShen && (
                          <span style={{ color: SHISHEN_TONE[zhiShen] ?? 'var(--t5)' }}>
                            ·{zhiShen}
                          </span>
                        )}
                      </span>
                      <span className="flex flex-col items-center leading-none">
                        <span
                          className="font-serif text-xl font-semibold"
                          style={{ color: WUXING_COLOR[gWuxing] ?? 'var(--t1)' }}
                        >
                          {gan}
                        </span>
                        <span
                          className="font-serif text-xl font-semibold"
                          style={{ color: WUXING_COLOR[zWuxing] ?? 'var(--t2)' }}
                        >
                          {zhi}
                        </span>
                      </span>
                      <span className="text-[10px] tabular text-t3">
                        {d.start_age}–{endAge} 岁
                      </span>
                      <span className="text-[9px] tabular text-t5">
                        {d.start_year}–{endYear}
                      </span>
                      {isCurrent && <Badge tone="gilt">当下行运</Badge>}
                    </button>
                    {i < dayun.length - 1 && (
                      <div
                        className={`h-px w-2 shrink-0 ${
                          isCurrent ? 'bg-gilt-500/60' : 'bg-line'
                        }`}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* 选中运柱的详情条 */}
            {selDayun && selWindow && (
              <div className="animate-fade-up mt-2 rounded-xl border border-gilt-500/35 bg-gilt-500/[0.07] p-4">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-gt font-serif text-lg font-semibold">
                    {selDayun.ganzhi}运
                  </span>
                  {(() => {
                    const shen = tenGod(dayMaster, selDayun.ganzhi.slice(0, 1));
                    const c = SHISHEN_TONE[shen] ?? '#C9A227';
                    return (
                      <span
                        className="inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium"
                        style={{ borderColor: `${c}55`, backgroundColor: `${c}1f`, color: c }}
                      >
                        {shen}
                      </span>
                    );
                  })()}
                  <span className="text-xs tabular text-t3">
                    {selWindow.startYear}–{selWindow.endYear} 年 · {selWindow.startAge}–
                    {selWindow.endAge} 岁
                  </span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-t2">
                  {DAYUN_NOTE[tenGod(dayMaster, selDayun.ganzhi.slice(0, 1))] ??
                    '此运基调以静守为宜。'}
                  <span className="ml-1 text-t4">
                    （干为{tenGod(dayMaster, selDayun.ganzhi.slice(0, 1))}，支为
                    {tenGodOfZhi(dayMaster, selDayun.ganzhi.slice(1, 2))}；传统命理参考）
                  </span>
                </p>
              </div>
            )}
          </>
        )}
      </Card>

      {/* 流年运势表：与选中大运联动高亮 */}
      <Card
        title="流年运势"
        subtitle={
          selWindow
            ? `${selDayun?.ganzhi}运覆盖的流年已鎏金标出（${selWindow.startYear}–${selWindow.endYear}）· 再点流年可取消选中`
            : '未来十年的流年干支与生肖；点运柱联动高亮，点流年反选所属大运'
        }
      >
        {!chart.liunian || chart.liunian.length === 0 ? (
          <EmptyState>暂无流年数据</EmptyState>
        ) : (
          <div className="grid grid-cols-5 gap-2 text-center md:grid-cols-10">
            {chart.liunian.map((ly) => {
              const w = wuxingOfGan((ly.ganzhi ?? '').slice(0, 1));
              const inSel =
                selWindow !== null && ly.year >= selWindow.startYear && ly.year <= selWindow.endYear;
              const isNow = ly.year === nowYear;
              // 该流年所属的大运下标（用于点击反向联动选中运柱）
              const ownerIdx = dayun.findIndex(
                (d, i) =>
                  ly.year >= d.start_year &&
                  ly.year < (dayun[i + 1] ? dayun[i + 1].start_year : d.start_year + 10),
              );
              return (
                <button
                  type="button"
                  key={ly.year}
                  disabled={ownerIdx === -1}
                  onClick={() => ownerIdx !== -1 && setSelDy(selDy === ownerIdx ? null : ownerIdx)}
                  className={`liunian-chip rounded-lg border py-2.5 ${
                    inSel
                      ? 'border-gilt-500/60 bg-gilt-500/[0.10] shadow-[0_0_12px_-4px_rgba(201,162,39,0.45)]'
                      : 'border-line bg-panel'
                  }`}
                  style={{ ['--tone' as string]: WUXING_COLOR[w] ?? '#8a8f98' }}
                  title={`${ly.ganzhi} 年 · 天干属${w || '—'} · 点击查看所属大运`}
                >
                  <div className="flex items-center justify-center gap-1 text-[11px] text-t4">
                    {ly.year}
                    {isNow && <Badge tone="gilt">今</Badge>}
                  </div>
                  <div className="mt-0.5 font-serif text-lg font-semibold tracking-wider" style={{ color: WUXING_COLOR[w] }}>
                    {ly.ganzhi}
                  </div>
                  <div className="text-[10px] text-t4">
                    {ly.zodiac}年{ly.age != null ? ` · ${ly.age}岁` : ''}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Card>

      {/* LLM 批示卡片 */}
      <Card
        title="命理批示"
        subtitle="传统术数参考解读，非科学预测；不诊断疾病、不替代医疗/法律/财务建议"
      >
        {!data.reading ? (
          <ErrorBox message={data.error ?? '批示生成失败（可重试）'} />
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {READING_META.map((m) => {
              const text = data.reading?.[m.key];
              if (!text) return null;
              const isWide = m.key === '命格总论' || m.key === '未来10年';
              return (
                <div
                  key={m.key}
                  className={`reading-tile rounded-xl border border-bd bg-panel p-4 ${isWide ? 'md:col-span-2' : ''}`}
                  style={{ ['--tone' as string]: m.tone }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="flex h-6 w-6 items-center justify-center rounded-md border text-xs font-semibold"
                      style={{
                        borderColor: `${m.tone}55`,
                        backgroundColor: `${m.tone}1f`,
                        color: m.tone,
                      }}
                    >
                      {m.icon}
                    </span>
                    <span className="text-sm font-medium text-t1">{m.label}</span>
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-t2">{text}</p>
                </div>
              );
            })}
          </div>
        )}
        <div className="mt-3 flex items-center gap-2 text-[11px] text-t4">
          模型 {data.model || '—'} · {((data.duration_ms ?? 0) / 1000).toFixed(1)}s
          {data.cached && (
            <span className="rounded border border-bd bg-panel px-1.5 py-0.5 text-t3">
              命中缓存（秒开）
            </span>
          )}
        </div>
        {/* 推理链路（思考过程）可折叠展示——命理批示的推理依据，增强可解释性 */}
        {data.reasoning && (
          <details className="mt-3 rounded-xl border border-bd bg-panel p-3">
            <summary className="cursor-pointer text-xs font-medium text-t3 hover:text-t1">
              查看模型推理链路（思考过程）
            </summary>
            <pre className="mt-2 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-t4">
              {data.reasoning}
            </pre>
          </details>
        )}
      </Card>
    </div>
  );
}

/** 紫微批示的六个维度 */
const ZIWEI_DIMS: { key: string; icon: string; wide?: boolean; tone: string }[] = [
  { key: '命身总论', icon: '命', wide: true, tone: TONE.gilt },
  { key: '事业官禄', icon: '禄', tone: TONE.azure },
  { key: '财帛', icon: '财', tone: TONE.amber },
  { key: '夫妻感情', icon: '姻', tone: TONE.rouge },
  { key: '迁移际遇', icon: '迁', tone: TONE.celadon },
  { key: '大限走势', icon: '限', wide: true, tone: TONE.violet },
];

/** 紫微十二宫的经典盘位：按宫位地支固定在 4×4 盘面上，中宫放概要 */
const PALACE_POS: Record<string, [number, number]> = {
  巳: [0, 0], 午: [0, 1], 未: [0, 2], 申: [0, 3],
  辰: [1, 0], 酉: [1, 3],
  卯: [2, 0], 戌: [2, 3],
  寅: [3, 0], 丑: [3, 1], 子: [3, 2], 亥: [3, 3],
};

function ZiweiSection({
  data,
  loading,
  error,
  onRefresh,
}: {
  data?: ZiweiReading | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const chart = data?.chart;
  return (
    <Card
      className="frame-flow"
      title="紫微斗数命盘"
      subtitle="十二宫盘面由 iztro-py 确定性排盘；批示为传统术数参考，非科学预测"
      right={
        <GhostButton onClick={onRefresh} disabled={loading}>
          {loading ? '生成中…' : '重算紫微批示'}
        </GhostButton>
      }
    >
      {loading && <RitualLoading engine="ziwei" label="正在排紫微盘并生成批示（首次约 2-3 分钟，之后命中缓存秒开）…" />}
      {error && <ErrorBox message={error} />}
      {!loading && !error && chart && (
        <div className="space-y-4">
          {/* 经典 4×4 十二宫盘（窄屏横向滚动） */}
          <div className="overflow-x-auto">
            <div className="grid min-w-[640px] grid-cols-4 gap-px overflow-hidden rounded-xl border border-line bg-line">
            {chart.palaces.map((p) => {
              const branch = p.ganzhi.slice(-1);
              const pos = PALACE_POS[branch];
              const isSoul = p.name === chart.soul_palace;
              const isBody = p.name === chart.body_palace;
              const branchWu = wuxingOfZhi(branch);
              return (
                <div
                  key={p.name}
                  style={
                    pos ? { gridRowStart: pos[0] + 1, gridColumnStart: pos[1] + 1 } : undefined
                  }
                  className={`card-hover min-h-[92px] bg-card p-2.5 transition-shadow ${
                    isSoul
                      ? 'bg-gilt-500/[0.08] shadow-[inset_0_0_0_1.5px_rgba(201,162,39,0.55),0_0_16px_-6px_rgba(201,162,39,0.5)]'
                      : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className={`text-xs font-medium ${isSoul ? 'text-gt' : 'text-t2'}`}>
                      {p.name}
                    </span>
                    <span
                      className="text-[10px] tabular"
                      style={{ color: WUXING_COLOR[branchWu] ?? 'var(--t5)' }}
                      title={`宫支 ${p.ganzhi} · 属${branchWu || '—'}`}
                    >
                      {p.ganzhi}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-x-1.5 gap-y-1">
                    {p.major_stars.length === 0 && (
                      <span className="text-[10px] text-t5">无主星</span>
                    )}
                    {p.major_stars.map((s, i) => (
                      <span
                        key={i}
                        className={`text-xs ${BRIGHTNESS_CLS[s.brightness ?? ''] ?? 'text-t2'}`}
                        title={`${s.name}${s.brightness ? ` · ${s.brightness}` : ''}${s.mutagen ? ` · 化${s.mutagen}` : ''}`}
                      >
                        {s.name}
                        {s.mutagen && (
                          <span
                            className={`ml-0.5 rounded border px-0.5 text-[9px] font-semibold ${
                              MUTAGEN_CLS[s.mutagen] ?? 'border-bd bg-panel text-t3'
                            }`}
                          >
                            {s.mutagen}
                          </span>
                        )}
                      </span>
                    ))}
                  </div>
                  <div className="mt-1 flex items-center gap-1">
                    {isSoul && <Badge tone="gilt">命</Badge>}
                    {isBody && <Badge>身</Badge>}
                    {p.dalimit && (
                      <span className="text-[9px] tabular text-t5">
                        大限 {p.dalimit[0]}-{p.dalimit[1]}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
            {/* 中宫：命宫/身宫摘要 + 太极静饰 + 星点明灭 */}
            <div className="relative col-span-2 row-span-2 flex flex-col items-center justify-center gap-2.5 bg-panel p-4 text-center">
              <Sparkles count={6} seed={9} className="opacity-70" />
              <MiniTaiji size={44} />
              <div className="text-[10px] tracking-[0.3em] text-t4">紫微斗数</div>
              <div className="text-sm text-t2">
                命宫 <span className="font-semibold text-gt">{chart.soul_palace || '—'}</span>
                {chart.soul_branch && `（${chart.soul_branch}宫）`}
              </div>
              <div className="text-sm text-t2">
                身宫 <span className="font-semibold text-t1">{chart.body_palace || '—'}</span>
              </div>
            </div>
          </div>
          </div>

          {/* 六维度批示 */}
          {data?.reading ? (
            <div className="grid gap-3 md:grid-cols-2">
              {ZIWEI_DIMS.map((d) => {
                const text = data.reading?.[d.key];
                if (!text) return null;
                return (
                  <div
                    key={d.key}
                    className={`reading-tile rounded-xl border border-bd bg-panel p-4 ${d.wide ? 'md:col-span-2' : ''}`}
                    style={{ ['--tone' as string]: d.tone }}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="flex h-6 w-6 items-center justify-center rounded-md border text-xs font-semibold"
                        style={{
                          borderColor: `${d.tone}55`,
                          backgroundColor: `${d.tone}1f`,
                          color: d.tone,
                        }}
                      >
                        {d.icon}
                      </span>
                      <span className="text-sm font-medium text-t1">{d.key}</span>
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-t2">{text}</p>
                  </div>
                );
              })}
            </div>
          ) : (
            <ErrorBox message={data?.error ?? '批示生成失败（可重试）'} />
          )}
          <div className="flex items-center gap-2 text-[11px] text-t4">
            模型 {data?.model || '—'} · {((data?.duration_ms ?? 0) / 1000).toFixed(1)}s
            {data?.cached && (
              <span className="rounded border border-bd bg-panel px-1.5 py-0.5 text-t3">
                命中缓存（秒开）
              </span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function Charts() {
  const engines = useAsync(() => api.engines(), []);
  const [date, setDate] = useState<string>(new Date().toISOString().slice(0, 10));
  const snapshot = useAsync(() => api.calendarSnapshot(DEFAULT_USER_ID, date), [date]);
  const [pid, setPid] = useState('');
  const detail = useAsync(
    () => (pid ? api.prediction(pid) : Promise.resolve(null)),
    [pid],
  );

  // 命理批示（默认走缓存，命中则秒出；「重新生成」才强制重算）
  const [refreshNonce, setRefreshNonce] = useState(0);
  const fortune = useAsync(
    () => api.fortuneReading(DEFAULT_USER_ID, refreshNonce > 0),
    [refreshNonce],
  );

  // 紫微批示（独立缓存与刷新，与八字批示并行加载互不阻塞）
  const [ziweiNonce, setZiweiNonce] = useState(0);
  const ziwei = useAsync(
    () => api.fortuneReadingZiwei(DEFAULT_USER_ID, ziweiNonce > 0),
    [ziweiNonce],
  );

  const payload = (snapshot.data?.payload ?? {}) as Record<string, any>;

  return (
    <div className="space-y-5">
      <PageHeader
        title="命盘"
        desc="本命八字、紫微十二宫、大运流年与批示。传统术数参考，非科学预测。"
        right={
          <PrimaryButton onClick={() => setRefreshNonce((n) => n + 1)} busy={fortune.loading}>
            {fortune.loading ? '批示生成中，约 2-3 分钟…' : '重新生成批示'}
          </PrimaryButton>
        }
      />

      {/* 章节锚点导航：命盘页内容长，吸顶小字快速跳转 */}
      <nav className="sticky top-2 z-20 flex flex-wrap gap-1.5 rounded-xl border border-line bg-page/90 px-2.5 py-2 backdrop-blur">
        {(
          [
            ['#charts-reading', '八字批示'],
            ['#charts-ziwei', '紫微批盘'],
            ['#charts-engines', '术式引擎'],
            ['#charts-imaging', '影像相法'],
            ['#charts-calendar', '历法快照'],
            ['#charts-lineage', '预测血缘'],
          ] as const
        ).map(([href, label]) => (
          <a
            key={href}
            href={href}
            className="rounded-full border border-line px-2.5 py-0.5 text-[11px] text-t4 transition-colors hover:border-gilt-400/60 hover:text-t1"
          >
            {label}
          </a>
        ))}
      </nav>

      {/* 命理批示（核心展示） */}
      <div id="charts-reading" className="scroll-mt-20">
        {fortune.loading && <RitualLoading engine="bazi" label="正在排盘并生成命理批示（推理模型思考 + 正文，约 2-3 分钟，请耐心等待）…" />}
        {fortune.error && <ErrorBox message={fortune.error} />}
        {!fortune.loading && !fortune.error && fortune.data && (
          <FortuneSection data={fortune.data} />
        )}
      </div>

      {/* 紫微批示（与八字并列的第二条解读线） */}
      <div id="charts-ziwei" className="scroll-mt-20">
        <ZiweiSection
          data={ziwei.data}
          loading={ziwei.loading}
          error={ziwei.error}
          onRefresh={() => setZiweiNonce((n) => n + 1)}
        />
      </div>

      {/* 术式引擎 */}
      <div id="charts-engines" className="scroll-mt-20">
      <Card
        title="术式引擎"
        subtitle="第 53 节：通过 Adapter 接入，输出统一 Signal。未接入的诚实降级，绝不假装可用"
      >
        {engines.loading && <Loading />}
        {engines.error && <ErrorBox message={engines.error} />}
        <div className="stagger grid gap-2 md:grid-cols-2">
          {(engines.data?.engines ?? []).map((e) => (
            <div
              key={e.source}
              className="row-hover flex items-center justify-between rounded-lg border border-line px-3 py-2"
            >
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-t1">
                    {ENGINE_LABEL[e.source] ?? e.source}
                  </span>
                  <Badge tone={e.available ? 'good' : 'default'}>
                    {e.available ? '可用' : '未接入'}
                  </Badge>
                </div>
                <div className="mt-0.5 text-[11px] text-t4">
                  参考 {ENGINE_REF[e.source] ?? e.engine}
                </div>
              </div>
              <div className="text-right text-[11px] text-t5">{e.version}</div>
            </div>
          ))}
        </div>
      </Card>
      </div>

      {/* 影像相学：面相 / 掌纹上传分析（隐私：原图即焚，默认不上云） */}
      <div id="charts-imaging" className="scroll-mt-20">
      <Card
        className="frame-flow"
        title="影像相法"
        subtitle="本地 OpenCV 特征分析，原图分析完立即焚毁、结果不入库；云端详批为逐项勾选的可选项"
      >
        <ImagingPanel />
      </Card>
      </div>

      {/* 历法快照 */}
      <div id="charts-calendar" className="scroll-mt-20">
      <Card
        title="历法内核快照"
        subtitle="第 6 节：所有术式共享同一个 Calendar Core，禁止各模块自己算日期"
        right={
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className={inputCls}
          />
        }
      >
        {snapshot.loading && <Loading />}
        {snapshot.error && <ErrorBox message={snapshot.error} />}
        {!snapshot.loading && !snapshot.error && snapshot.data?.degraded && (
          <ErrorBox message={`引擎降级：${String(snapshot.data.degrade_reason)}`} />
        )}
        {!snapshot.loading && !snapshot.error && !snapshot.data?.degraded && (
          <div className="grid gap-4 text-xs md:grid-cols-2">
            <div>
              <div className="mb-1 font-medium text-t2">目标日四柱</div>
              <div className="grid grid-cols-4 gap-2 text-center">
                {[
                  ['年', payload.year_ganzhi],
                  ['月', payload.month_ganzhi],
                  ['日', payload.day_ganzhi],
                  ['时', payload.hour_ganzhi],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-line bg-panel py-2.5">
                    <div className="text-[11px] text-t4">{k}</div>
                    <div className="mt-1 font-serif text-lg font-semibold tracking-[0.15em] text-gt">
                      {v || '—'}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 text-t4">
                农历 {payload.lunar_year} 年 {payload.lunar_month} 月 {payload.lunar_day} 日
                {payload.is_leap_month ? '（闰月）' : ''}
              </div>
              <div className="text-t4">节气 {payload.current_jieqi || '—'}</div>
            </div>

            <div>
              <div className="mb-1 font-medium text-t2">本命八字</div>
              <div className="grid grid-cols-4 gap-2 text-center">
                {[
                  ['年', payload.bazi?.year],
                  ['月', payload.bazi?.month],
                  ['日', payload.bazi?.day],
                  ['时', payload.bazi?.time],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-line bg-panel py-2.5">
                    <div className="text-[11px] text-t4">{k}</div>
                    <div className="mt-1 font-serif text-lg font-semibold tracking-[0.15em] text-gt">
                      {v || '—'}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-2 text-t4">日主 {payload.bazi?.day_master || '—'}</div>
              <div className="text-t4">
                十神（天干） 年 {payload.shishen?.year || '—'} · 月 {payload.shishen?.month || '—'} · 时{' '}
                {payload.shishen?.time || '—'}
              </div>
              {payload.ming_gong && (
                <div className="text-t4">命宫 {payload.ming_gong}</div>
              )}
            </div>
          </div>
        )}
      </Card>
      </div>

      {/* 预测血缘 */}
      <div id="charts-lineage" className="scroll-mt-20">
      <Card
        title="预测血缘"
        subtitle="第 80 节：任意一条预测都能追溯到候选 / 信号 / 规则 / Agent / Prompt / 模型"
      >
        <div className="flex gap-2">
          <input
            value={pid}
            onChange={(e) => setPid(e.target.value)}
            placeholder="输入 prediction_id"
            className={`flex-1 ${inputCls}`}
          />
        </div>

        {pid && detail.loading && <Loading />}
        {pid && detail.error && <ErrorBox message={detail.error} />}
        {detail.data && (
          <div className="mt-3 space-y-3">
            <div className="flex items-center gap-2 text-sm text-t1">
              {cleanDescription(detail.data.description, detail.data.event_type)}
              <Badge>{pct(detail.data.probability)}</Badge>
              {detail.data.integrity && (
                <Badge tone={detail.data.integrity.ok ? 'good' : 'bad'}>
                  {detail.data.integrity.ok ? '冻结完整' : '原文被篡改'}
                </Badge>
              )}
            </div>

            <div className="rounded border border-line">
              <div className="border-b border-line px-3 py-1.5 text-xs text-t3">
                信号（第 14 节统一 Schema）
              </div>
              {detail.data.signals.length === 0 && (
                <div className="px-3 py-2 text-xs text-t4">无信号</div>
              )}
              <ul className="divide-y divide-line">
                {detail.data.signals.map((s) => (
                  <li key={s.signal_id} className="px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="text-t1">
                        {SOURCE_LABEL[s.source] ?? s.source}
                      </span>
                      {s.degraded && <Badge tone="default">降级：{s.degrade_reason}</Badge>}
                      {s.dependency_group && (
                        <Badge tone="info">依赖组 {s.dependency_group}</Badge>
                      )}
                      <span className="ml-auto tabular text-t3">
                        direction {s.direction.toFixed(2)} · strength{' '}
                        {s.strength.toFixed(2)} · conf {s.confidence.toFixed(2)}
                      </span>
                    </div>
                    {s.evidence.length > 0 && (
                      <ul className="mt-1 space-y-0.5 text-t4">
                        {s.evidence.map((e, i) => (
                          <li key={i}>
                            · [{e.source}] {e.description}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            <div className="grid gap-2 text-xs md:grid-cols-2">
              <div className="rounded bg-panel p-2">
                <div className="mb-1 text-t3">证据依赖（第 20.12 节）</div>
                {Object.entries(detail.data.evidence_dependency).map(([g, srcs]) => (
                  <div key={g} className="text-t2">
                    {g}：{srcs.join('、')}
                    {srcs.length > 1 && (
                      <span className="ml-1 text-amber-400">
                        （{srcs.length} 源只算 1 份独立证据）
                      </span>
                    )}
                  </div>
                ))}
              </div>
              <div className="rounded bg-panel p-2">
                <div className="mb-1 text-t3">版本（第 79 节）</div>
                {Object.entries(detail.data.versions).map(([k, v]) => (
                  <div key={k} className="text-t2">
                    {k}：{v}
                  </div>
                ))}
                <div className="mt-1 text-t4">
                  Agent 分歧 {detail.data.agent_disagreement.toFixed(3)}
                </div>
              </div>
            </div>
          </div>
        )}
      </Card>
      </div>
    </div>
  );
}
