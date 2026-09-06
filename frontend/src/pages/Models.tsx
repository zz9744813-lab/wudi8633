import ReactECharts from 'echarts-for-react';
import { useState } from 'react';

import { api, DEFAULT_USER_ID } from '../api/client';
import { Badge, Card, EmptyState, ErrorBox, Loading, PageHeader, Stat } from '../components/ui';
import { RELIABILITY_COLOR, RELIABILITY_LABEL, num, pct } from '../lib/format';
import { useAsync } from '../lib/useAsync';

/**
 * 第 26 节 Personal Reliability Matrix
 * 第 77 节 层级可靠度
 * 第 79 节 模型版本管理
 * 第 84 节 North Star Metric：相对 Null Model 的 predictive skill
 *
 * 注意：矩阵里保存的是「相对 Null Model 的 skill」，不是命中率。
 */

function MatrixTable({
  title,
  rows,
  keyLabel,
}: {
  title: string;
  rows: {
    key: string;
    system?: string;
    domain?: string;
    time_scale?: string;
    sample_size: number;
    skill: number | null;
    brier: number | null;
    reliability: string;
    note?: string;
  }[];
  keyLabel: (r: { system?: string; domain?: string; time_scale?: string }) => string;
}) {
  if (rows.length === 0) return <EmptyState>暂无数据</EmptyState>;
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-t2">{title}</div>
      <table className="w-full text-xs">
        <thead className="text-t4">
          <tr className="border-b border-line">
            <th className="py-1 text-left">维度</th>
            <th className="py-1 text-right">样本</th>
            <th className="py-1 text-right">增益</th>
            <th className="py-1 text-right">误差</th>
            <th className="py-1 text-right">可靠度</th>
          </tr>
        </thead>
        <tbody className="tabular text-t2">
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-line">
              <td className="py-1">{keyLabel(r)}</td>
              <td className="py-1 text-right">{r.sample_size}</td>
              <td
                className={`py-1 text-right ${(r.skill ?? 0) > 0 ? 'text-jade-400' : (r.skill ?? 0) < 0 ? 'text-cinnabar-400' : ''}`}
              >
                {r.skill != null ? pct(r.skill, 1) : '—'}
              </td>
              <td className="py-1 text-right">{r.brier != null ? num(r.brier) : '—'}</td>
              <td className={`py-1 text-right ${RELIABILITY_COLOR[r.reliability] ?? ''}`}>
                {r.note ? r.note : RELIABILITY_LABEL[r.reliability] ?? r.reliability}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const SYSTEM_LABEL: Record<string, string> = {
  ziwei: '紫微',
  bazi: '八字',
  qimen: '奇门',
  liuyao: '六爻',
  meihua: '梅花',
  zhouyi: '周易',
  palm: '掌纹',
  face: '面相',
  reality: '现实',
  null: 'Null 基线',
};

const DOMAIN_LABEL: Record<string, string> = {
  career: '职业', money: '财务', study: '学习', social: '社交',
  relationship: '关系', travel: '出行', project: '项目', habit: '习惯',
  purchase: '消费', communication: '沟通', schedule: '日程',
  unexpected_event: '意外',
};

const SCALE_LABEL: Record<string, string> = {
  day: '日', week: '周', month: '月', year: '年',
};

/** 按术式的增益雷达图：各术式相对 Null 的 skill（-1~1 截显示，真实值在提示里）。 */
function SkillRadar({
  rows,
}: {
  rows: { system?: string; sample_size: number; skill: number | null }[];
}) {
  const dims = rows.filter((r) => r.system && r.system !== 'null' && r.system !== 'reality');
  if (dims.length === 0) return <EmptyState>尚无术式维度数据。样本积累后自动出现。</EmptyState>;

  const dark = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'dark';
  const axisColor = dark ? '#64748b' : '#5d6b84';
  const splitColor = dark ? '#1c2230' : '#e2e8f0';

  const clamp = (v: number | null) =>
    v == null ? 0 : Math.max(-1, Math.min(1, v));

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (p: { dataIndex: number }) => {
        const d = dims[p.dataIndex];
        return `${SYSTEM_LABEL[d.system ?? ''] ?? d.system}：skill ${d.skill != null ? pct(d.skill, 1) : '—'}（样本 ${d.sample_size}）`;
      },
    },
    radar: {
      indicator: dims.map((d) => ({
        name: SYSTEM_LABEL[d.system ?? ''] ?? d.system ?? '',
        min: -1,
        max: 1,
      })),
      radius: '68%',
      axisName: { color: axisColor, fontSize: 11 },
      splitLine: { lineStyle: { color: splitColor } },
      splitArea: { areaStyle: { color: [dark ? 'rgba(255,255,255,0.02)' : 'rgba(0,0,0,0.02)', 'transparent'] } },
      axisLine: { lineStyle: { color: splitColor } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: dims.map((d) => clamp(d.skill)),
            name: '术式增益',
            areaStyle: { color: 'rgba(217,185,106,0.18)' },
            lineStyle: { color: '#d9b96a', width: 1.5 },
            itemStyle: { color: '#d9b96a' },
            symbolSize: 4,
          },
          {
            value: dims.map(() => 0),
            name: 'Null 基线（skill=0）',
            lineStyle: { color: dark ? '#475569' : '#94a3b8', type: 'dashed' as const, width: 1 },
            itemStyle: { color: dark ? '#475569' : '#94a3b8' },
            symbol: 'none',
          },
        ],
      },
    ],
  };
  return <ReactECharts option={option} style={{ height: 300 }} notMerge />;
}

/** 公众人物回测页签：只读静态产物，口径警示常显（C-006）。 */
function BacktestPanel() {
  const bt = useAsync(() => api.backtest(), []);
  const LABELS: Record<string, string> = SYSTEM_LABEL;

  if (bt.loading) return <Loading />;
  if (bt.error) return <ErrorBox message={bt.error} />;
  if (!bt.data?.available) return <EmptyState>{bt.data?.note ?? '暂无回测产物'}</EmptyState>;

  const d = bt.data;
  const rows = Object.entries(d.per_source ?? {});
  return (
    <div className="space-y-3">
      <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="人物样本" value={d.figures ?? '—'} hint="公开信息完整的名人" />
        <Stat
          label="年柱一致"
          value={`${d.pillar_ok}/${d.figures}`}
          tone={d.pillar_ok === d.figures ? 'good' : 'bad'}
          hint="八字年柱 vs 独立立春公式"
        />
        <Stat label="事件总数" value={d.n_events ?? '—'} />
        <Stat
          label="正向占比"
          value={d.n_events ? pct((d.n_positive ?? 0) / d.n_events, 0) : '—'}
          tone="warn"
          hint="数据集偏置，须联合解读"
        />
      </div>
      <table className="w-full text-xs">
        <thead className="text-t4">
          <tr className="border-b border-line">
            <th className="py-1 text-left">术式</th>
            <th className="py-1 text-right">表态率</th>
            <th className="py-1 text-right">命中/未中</th>
            <th className="py-1 text-right">命中率</th>
            <th className="py-1 text-right">二项 p 值</th>
          </tr>
        </thead>
        <tbody className="tabular text-t2">
          {rows.map(([src, b]) => (
            <tr key={src} className="border-b border-line">
              <td className="py-1">{LABELS[src] ?? src}</td>
              <td className="py-1 text-right">{pct(b.coverage, 0)}</td>
              <td className="py-1 text-right">
                {b.hit}/{b.miss}
                {b.abstain > 0 && <span className="text-t4">（弃权 {b.abstain}）</span>}
              </td>
              <td
                className={`py-1 text-right ${
                  b.hit_rate != null && b.p_value < 0.05 ? 'text-jade-400' : ''
                }`}
              >
                {b.hit_rate != null ? pct(b.hit_rate, 1) : '—'}
              </td>
              <td className="py-1 text-right">
                {b.p_value < 0.001 ? '<0.001' : b.p_value.toFixed(3)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] leading-relaxed text-t4">
        {d.caveat} 本表由 tools/backtest_figures.py 静态产出（74 位公众人物 × 165 个公开事件），
        用途是发现系统性 bug 与校准种子——例如「周易文献词频天然偏吉」正是靠本表识破后退出方向投票的。
      </p>
    </div>
  );
}

export default function Models() {
  const rel = useAsync(() => api.reliability(DEFAULT_USER_ID), []);
  const overall = useAsync(() => api.overall(DEFAULT_USER_ID), []);
  const [tab, setTab] = useState<'empirical' | 'backtest'>('empirical');

  return (
    <div className="space-y-5">
      <PageHeader
        title="模型"
        desc="各术式、各领域的相对预测能力。这里保存的是「相对 Null Model 的 skill」，不是命中率。"
      />

      <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="预测误差"
          value={overall.data ? num(overall.data.brier) : '—'}
          hint="Brier 评分 · 越低越好"
        />
        <Stat
          label="相对基线增益"
          value={
            overall.data?.skill_score != null ? pct(overall.data.skill_score, 1) : '—'
          }
          tone={(overall.data?.skill_score ?? 0) > 0 ? 'good' : 'bad'}
          hint="Skill vs Null · 第 84 节北极星指标"
        />
        <Stat
          label="判断锐度"
          value={overall.data ? num(overall.data.sharpness, 4) : '—'}
          hint="Sharpness · 离 0.5 越远越果断"
        />
      </div>

      {/* 页签：实证矩阵（个人样本）/ 公众回测（静态产物） */}
      <div className="flex gap-1 border-b border-line">
        {(
          [
            ['empirical', '实证矩阵'],
            ['backtest', '公众回测'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3 py-1.5 text-xs transition-colors ${
              tab === key
                ? 'border-gilt-400 font-medium text-t1'
                : 'border-transparent text-t4 hover:text-t2'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'backtest' && (
        <Card title="公众人物回测" subtitle="术式引擎的公开事实压力测试 —— 找系统级 bug，不证明效力">
          <BacktestPanel />
        </Card>
      )}

      {tab === 'empirical' && (
      <>
      <Card title="术式增益雷达" subtitle="各术式相对 Null 基线的 skill（样本不足时该维贴近 0）">
        {rel.loading && <Loading />}
        {rel.error && <ErrorBox message={rel.error} />}
        {rel.data && <SkillRadar rows={rel.data.by_system} />}
      </Card>

      <Card
        title="个人可靠度矩阵"
        subtitle="第 26 节：系统应允许得到「不好听」的结果 —— 若某术式无贡献，就显示无贡献"
      >
        {rel.loading && <Loading />}
        {rel.error && <ErrorBox message={rel.error} />}
        {!rel.loading && !rel.error && rel.data && (
          <div className="space-y-5">
            <MatrixTable
              title="按术式系统"
              rows={rel.data.by_system}
              keyLabel={(r) => SYSTEM_LABEL[r.system ?? ''] ?? (r.system ?? '—')}
            />
            <MatrixTable
              title="按领域"
              rows={rel.data.by_domain}
              keyLabel={(r) => DOMAIN_LABEL[r.domain ?? ''] ?? (r.domain ?? '—')}
            />
            <MatrixTable
              title="按时间尺度"
              rows={rel.data.by_time_scale}
              keyLabel={(r) => SCALE_LABEL[r.time_scale ?? ''] ?? (r.time_scale ?? '—')}
            />

            <div>
              <div className="mb-1.5 text-xs font-medium text-t2">
                融合权重（由实证增益学习得到）
              </div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(rel.data.fusion_weights).map(([k, v]) => (
                  <Badge key={k} tone={v > 1 ? 'good' : v < 1 ? 'bad' : 'default'}>
                    {SYSTEM_LABEL[k] ?? k} ×{v.toFixed(2)}
                  </Badge>
                ))}
                {Object.keys(rel.data.fusion_weights).length === 0 && (
                  <span className="text-xs text-t4">
                    尚无足够样本，全部按 1.0（不惩罚也不奖励，第 77 节弱先验）
                  </span>
                )}
              </div>
            </div>
          </div>
        )}
      </Card>
      </>
      )}
    </div>
  );
}
