import { useMemo, useState } from 'react';

import { api, DEFAULT_USER_ID } from '../api/client';
import {
  Badge,
  Card,
  EmptyState,
  ErrorBox,
  Loading,
  PageHeader,
  ProbBar,
  Stat,
} from '../components/ui';
import { DOMAIN_LABEL, pct, shortDateTime } from '../lib/format';
import { useAsync } from '../lib/useAsync';

/**
 * 第 51 节 Prediction History
 *
 *   默认必须同时展示：成功 / 失败 / 部分 / 无法判断
 *   禁止产品设计诱导只看「神预测」。
 */

type ResultFilter = 'all' | 'hit' | 'partial' | 'miss';

export default function Timeline() {
  const hist = useAsync(() => api.history(DEFAULT_USER_ID), []);
  const items = hist.data?.items ?? [];

  // 结果与领域双维筛选：默认「全部」——第 51 节不许只展示成功
  const [resultFilter, setResultFilter] = useState<ResultFilter>('all');
  const [domainFilter, setDomainFilter] = useState<string>('all');

  const domains = useMemo(
    () => [...new Set(items.map((i) => i.event_type.split('.')[0]))].sort(),
    [items],
  );

  // 先领域后结果：chips 上的 N 与下方列表口径一致（随领域联动）
  const domainItems =
    domainFilter === 'all'
      ? items
      : items.filter((i) => i.event_type.startsWith(`${domainFilter}.`));
  const filtered = domainItems.filter((i) => {
    if (resultFilter === 'hit') return i.outcome === 1;
    if (resultFilter === 'partial') return i.outcome > 0 && i.outcome < 1;
    if (resultFilter === 'miss') return i.outcome === 0;
    return true;
  });

  // 顶部统计卡保持全局口径；chips 计数随领域联动
  const statFull = items.filter((i) => i.outcome === 1).length;
  const statPartial = items.filter((i) => i.outcome > 0 && i.outcome < 1).length;
  const statNone = items.filter((i) => i.outcome === 0).length;
  const chipFull = domainItems.filter((i) => i.outcome === 1).length;
  const chipPartial = domainItems.filter((i) => i.outcome > 0 && i.outcome < 1).length;
  const chipNone = domainItems.filter((i) => i.outcome === 0).length;
  const meanBrier =
    items.length > 0 ? items.reduce((a, b) => a + b.brier, 0) / items.length : null;

  return (
    <div className="space-y-5">
      <PageHeader title="时间线" desc="全部已验证预测，按时间倒序。成功与失败同等展示。" />

      <div className="stagger grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="完全命中" value={statFull} tone="good" />
        <Stat label="部分命中" value={statPartial} tone="warn" />
        <Stat label="未命中" value={statNone} tone="bad" />
        <Stat
          label="平均误差"
          value={meanBrier != null ? meanBrier.toFixed(3) : '—'}
          hint="概率质量，越低越好"
        />
      </div>

      <Card
        title="全部结果"
        subtitle="第 51 节：不得隐藏失败预测；命中率只是直观数据，质量以概率评分与校准为准"
      >
        {/* 筛选 chips：结果维度 */}
        {items.length > 0 && (
          <div className="mb-3 flex flex-wrap items-center gap-1.5">
            {(
              [
                ['all', '全部'],
                ['hit', '命中'],
                ['partial', '部分'],
                ['miss', '未中'],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setResultFilter(k)}
                className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
                  resultFilter === k
                    ? 'border-gilt-400/60 bg-gilt-500/10 text-gt'
                    : 'border-line text-t4 hover:text-t2'
                }`}
              >
                {label}
                {k === 'hit' ? ` ${chipFull}` : k === 'partial' ? ` ${chipPartial}` : k === 'miss' ? ` ${chipNone}` : ` ${domainItems.length}`}
              </button>
            ))}
            {domains.length > 1 && (
              <>
                <span className="mx-1 h-3 w-px bg-line" />
                <button
                  onClick={() => setDomainFilter('all')}
                  className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
                    domainFilter === 'all'
                      ? 'border-gilt-400/60 bg-gilt-500/10 text-gt'
                      : 'border-line text-t4 hover:text-t2'
                  }`}
                >
                  全部领域
                </button>
                {domains.map((d) => (
                  <button
                    key={d}
                    onClick={() => setDomainFilter(d)}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
                      domainFilter === d
                        ? 'border-gilt-400/60 bg-gilt-500/10 text-gt'
                        : 'border-line text-t4 hover:text-t2'
                    }`}
                  >
                    {DOMAIN_LABEL[d] ?? d}
                  </button>
                ))}
              </>
            )}
          </div>
        )}

        {hist.loading && <Loading />}
        {hist.error && <ErrorBox message={hist.error} />}
        {!hist.loading && !hist.error && items.length === 0 && (
          <EmptyState>还没有已验证的预测。先去「验证」页提交结果。</EmptyState>
        )}
        {!hist.loading && !hist.error && items.length > 0 && filtered.length === 0 && (
          <EmptyState>该筛选下没有记录。</EmptyState>
        )}

        <ul className="divide-y divide-line">
          {filtered.map((it) => (
            <li
              key={it.prediction_id}
              className="row-hover -mx-2 flex items-center gap-4 rounded-lg px-2 py-2.5"
            >
              <div className="w-28 shrink-0 text-xs text-t4">
                {shortDateTime(it.judged_at)}
              </div>

              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-t1">
                  {it.description ?? it.label ?? it.event_type}
                </div>
                <div className="truncate text-[10px] text-t5">
                  {it.label ?? it.event_type}
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <ProbBar p={it.probability} className="w-28" />
                  <span className="text-xs tabular text-t2">
                    {pct(it.probability)}
                  </span>
                  {it.null_probability != null && (
                    <span className="text-[11px] text-t4">
                      基线 {pct(it.null_probability)}
                    </span>
                  )}
                </div>
              </div>

              <div className="w-28 shrink-0 text-right">
                <Badge
                  tone={
                    it.outcome === 1 ? 'good' : it.outcome > 0 ? 'warn' : 'bad'
                  }
                >
                  {it.outcome === 1
                    ? '命中'
                    : it.outcome > 0
                      ? `部分 ${pct(it.outcome)}`
                      : '未命中'}
                </Badge>
              </div>

              <div className="w-20 shrink-0 text-right text-xs tabular text-t3">
                误差 {it.brier.toFixed(3)}
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
