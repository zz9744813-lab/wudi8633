import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Suspense, lazy, useState } from 'react';

import { api } from './api/client';
import { useAsync } from './lib/useAsync';
// 路由懒加载：echarts 仅实验室/模型页用到，整页级懒加载让首屏不背 1MB 图表包
const Future = lazy(() => import('./pages/Future'));
const Verify = lazy(() => import('./pages/Verify'));
const Timeline = lazy(() => import('./pages/Timeline'));
const Labs = lazy(() => import('./pages/Labs'));
const Charts = lazy(() => import('./pages/Charts'));
const Rules = lazy(() => import('./pages/Rules'));
const Models = lazy(() => import('./pages/Models'));
const Settings = lazy(() => import('./pages/Settings'));

/**
 * 第 47 节 UI 信息架构：
 *   首页不应围绕「紫微 / 八字 / 奇门 / 六爻 / 梅花」，而应围绕 Future。
 */
const NAV_GROUPS: {
  label: string;
  items: { to: string; label: string; hint: string; icon: () => React.ReactElement }[];
}[] = [
  {
    label: '观未来',
    items: [
      { to: '/future', label: '未来', hint: '今日预测与已冻结账本', icon: IconFuture },
      { to: '/verify', label: '验证', hint: '待验证收件箱', icon: IconVerify },
      { to: '/timeline', label: '时间线', hint: '历史成败全量展示', icon: IconTimeline },
    ],
  },
  {
    label: '察自身',
    items: [
      { to: '/labs', label: '实验室', hint: '校准曲线与消融实验', icon: IconLabs },
      { to: '/charts', label: '命盘', hint: '术式引擎与历法快照', icon: IconCharts },
      { to: '/rules', label: '规则', hint: 'Rule Registry', icon: IconRules },
      { to: '/models', label: '模型', hint: '可靠度矩阵与版本', icon: IconModels },
    ],
  },
  {
    label: '系统',
    items: [
      { to: '/settings', label: '设置', hint: 'Provider / 预算 / 隐私', icon: IconSettings },
    ],
  },
];

function IconBase({ children }: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4 shrink-0"
    >
      {children}
    </svg>
  );
}

function IconFuture() {
  return (
    <IconBase>
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
      <circle cx="12" cy="12" r="3" />
    </IconBase>
  );
}
function IconVerify() {
  return (
    <IconBase>
      <path d="M9 12l2 2 4-4" />
      <circle cx="12" cy="12" r="9" />
    </IconBase>
  );
}
function IconTimeline() {
  return (
    <IconBase>
      <path d="M4 6h16M4 12h10M4 18h14" />
    </IconBase>
  );
}
function IconLabs() {
  return (
    <IconBase>
      <path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3" />
      <path d="M8 15h8" />
    </IconBase>
  );
}
function IconCharts() {
  return (
    <IconBase>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3a9 9 0 0 1 0 18M12 8v4l3 2" />
    </IconBase>
  );
}
function IconRules() {
  return (
    <IconBase>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M15 3v4h4M9 12h6M9 16h4" />
    </IconBase>
  );
}
function IconModels() {
  return (
    <IconBase>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </IconBase>
  );
}
function IconSettings() {
  return (
    <IconBase>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.7a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.4 2.7h4l.4-2.7a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" />
    </IconBase>
  );
}

/** 后端离线时主内容区顶部的醒目横幅（可重试） */
function OfflineBanner() {
  const health = useAsync(() => api.health(), []);
  if (health.loading || !health.error) return null;
  return (
    <div className="mb-4 flex items-center gap-3 rounded-xl border border-cinnabar-500/30 bg-cinnabar-500/[0.07] px-4 py-2.5">
      <span className="h-2 w-2 shrink-0 rounded-full bg-cinnabar-400" />
      <div className="flex-1 text-xs text-t1">
        后端服务离线，页面数据不可用。请用桌面快捷方式或
        <span className="mx-1 font-mono text-t2">uvicorn app.main:app --port 8765</span>
        启动后端。
      </div>
      <button
        onClick={() => health.reload()}
        className="btn-press shrink-0 rounded-md border border-cinnabar-500/40 px-2.5 py-1 text-xs text-cinnabar-400 hover:bg-cinnabar-500/10"
      >
        重试连接
      </button>
    </div>
  );
}

/** 日间/夜间主题切换。持久化到 localStorage，index.html 内联脚本防 FOUC。 */
function ThemeToggle() {
  const [dark, setDark] = useState<boolean>(
    () => document.documentElement.dataset.theme === 'dark',
  );

  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? 'dark' : 'light';
    localStorage.setItem('xm-theme', next ? 'dark' : 'light');
  };

  return (
    <button
      onClick={toggle}
      className="btn-press flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] text-t2 hover:bg-navh hover:text-t1"
      title={dark ? '切换到日间模式' : '切换到夜间模式'}
    >
      {dark ? (
        // 太阳图标
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" className="h-4 w-4 shrink-0">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        // 月亮图标
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
      <span>{dark ? '日间模式' : '夜间模式'}</span>
    </button>
  );
}

/** 侧边栏底部的后端在线状态 */
function BackendStatus() {
  const health = useAsync(() => api.health(), []);
  const online = !health.error && health.data?.status != null;
  const engineOk = health.data
    ? Object.values(health.data.engines).filter((e) => e.available).length
    : 0;
  const engineTotal = health.data ? Object.keys(health.data.engines).length : 7;

  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex items-center gap-2 text-[11px]">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            health.loading
              ? 'bg-slate-500'
              : online
                ? 'status-dot bg-jade-400'
                : 'bg-cinnabar-400'
          }`}
        />
        <span className={online ? 'text-t2' : 'text-t4'}>
          {health.loading ? '连接中…' : online ? '后端在线' : '后端离线'}
        </span>
        {online && (
          <span className="ml-auto tabular text-t4">
            引擎 {engineOk}/{engineTotal}
          </span>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-t5">
        传统术数与个人预测实验平台，
        <br />
        不是经科学验证的预知系统。
      </p>
    </div>
  );
}

export default function App() {
  const location = useLocation();

  return (
    <div className="flex h-full">
      {/* 侧边导航 */}
      <nav className="relative flex w-14 shrink-0 flex-col border-r border-line bg-page md:w-56">
        {/* 顶部鎏金光晕 */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(60%_100%_at_50%_0%,rgba(217,185,106,0.08),transparent)]"
        />

        <div className="relative flex items-center justify-center gap-3 px-5 py-6 md:justify-start">
          {/* 印章式 Logo：鎏金锥形环 + 玄字 */}
          <div className="relative flex h-9 w-9 items-center justify-center">
            <div className="absolute inset-0 rounded-xl bg-[conic-gradient(from_210deg,#ecd9a0,#8a6d10,#d9b96a,#5c490c,#ecd9a0)] opacity-95 shadow-[0_0_18px_-2px_rgba(201,162,39,0.5)]" />
            <div className="absolute inset-[1.5px] rounded-[10px] bg-page" />
            <span className="text-gilt-grad relative text-lg font-bold leading-none">玄</span>
          </div>
          <div className="hidden md:block">
            <div className="text-base font-semibold tracking-[0.2em] text-t1">玄鉴</div>
            <div className="text-[10px] tracking-[0.25em] text-t4">XUANMIRROR</div>
          </div>
        </div>

        <div className="relative flex-1 space-y-4 overflow-y-auto px-3">
          {NAV_GROUPS.map((g) => (
            <div key={g.label}>
              <div className="mb-1 hidden px-3 text-[10px] font-medium tracking-[0.25em] text-t5 md:block">
                {g.label}
              </div>
              <ul className="space-y-0.5">
                {g.items.map((n) => (
                  <li key={n.to}>
                    <NavLink
                      to={n.to}
                      className={({ isActive }) =>
                        `group relative flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] transition-all duration-200 ${
                          isActive
                            ? 'bg-nava font-medium text-t1 shadow-[inset_0_1px_0_0_var(--card-hl)]'
                            : 'text-t3 hover:bg-navh hover:text-t1'
                        }`
                      }
                      title={n.hint}
                    >
                      {({ isActive }) => (
                        <>
                          {/* 激活时左侧鎏金指示条 */}
                          <span
                            className={`absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-gilt-400 shadow-[0_0_6px_rgba(217,185,106,0.7)] transition-all duration-200 ${
                              isActive ? 'opacity-100' : 'opacity-0'
                            }`}
                          />
                          <span
                            className={`transition-all duration-200 group-hover:translate-x-0.5 ${isActive ? 'text-gt' : 'text-t4 group-hover:text-t2'}`}
                          >
                            <n.icon />
                          </span>
                          <span className="hidden flex-1 transition-transform duration-200 group-hover:translate-x-0.5 md:inline">
                            {n.label}
                          </span>
                        </>
                      )}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="border-t border-line px-3 py-2">
          <ThemeToggle />
        </div>

        <BackendStatus />
      </nav>

      {/* 内容区 */}
      <main className="relative flex-1 overflow-y-auto bg-page">
        {/* 背景氛围光 */}
        <div
          aria-hidden
          className="pointer-events-none fixed inset-y-0 right-0 left-14 md:left-56 bg-[radial-gradient(50%_35%_at_70%_0%,rgba(217,185,106,0.05),transparent),radial-gradient(40%_30%_at_20%_100%,rgba(56,130,246,0.04),transparent)]"
        />
        <div className="relative mx-auto max-w-6xl px-6 py-6">
          <OfflineBanner />
          {/* 路由切换时的入场动效 */}
          <div key={location.pathname} className="animate-fade-up space-y-5">
            <Suspense fallback={<div className="py-20 text-center text-xs text-t4">页面加载中…</div>}>
            <Routes location={location}>
              <Route path="/" element={<Navigate to="/future" replace />} />
              <Route path="/future" element={<Future />} />
              <Route path="/verify" element={<Verify />} />
              <Route path="/timeline" element={<Timeline />} />
              <Route path="/labs" element={<Labs />} />
              <Route path="/charts" element={<Charts />} />
              <Route path="/rules" element={<Rules />} />
              <Route path="/models" element={<Models />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
            </Suspense>
          </div>
        </div>
      </main>
    </div>
  );
}
