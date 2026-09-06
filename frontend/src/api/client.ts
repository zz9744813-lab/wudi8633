/** 后端 API 客户端。 */

import type {
  Aggregate,
  DailyAlmanac,
  EngineInfo,
  GateTestResponse,
  HistoryItem,
  OntologyItem,
  PredictionBrief,
  PredictionDetail,
  ReliabilityMatrix,
  RuleItem,
  Signal,
} from '../types';

export type { PredictionDetail, Signal };

const BASE = import.meta.env.VITE_BACKEND_URL ?? '';

// 命理批示等长请求（推理模型思考+正文）实测约 2-3 分钟，
// 加上中转站重试，给 5 分钟超时，避免浏览器默认断开。
const REQUEST_TIMEOUT_MS = 300_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      ...init,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status}: ${detail.slice(0, 300)}`);
    }
    return res.json() as Promise<T>;
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new Error('请求超时（后端生成较慢，请稍后重试）');
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined });

// 演示用默认用户；完整实现应做登录与鉴权（第 64 节隐私）
export const DEFAULT_USER_ID = Number(import.meta.env.VITE_USER_ID ?? 1);

/** Calendar Core 快照（后端 /api/calendar/snapshot）。第 6 节。 */
export interface CalendarSnapshot {
  target_date: string;
  degraded: boolean;
  degrade_reason: string | null;
  payload: Record<string, unknown>;
}

export interface GenerateResult {
  target_date: string;
  scanned: number;
  candidate_count: number;
  frozen: {
    prediction_id: string;
    event_type: string;
    probability: number;
    null_probability: number | null;
    sha256: string;
    visibility: string;
  }[];
  rejected: { event_type: string; decision: string; failed: string[]; reasons: string[] }[];
  budget_usage: Record<string, number>;
  notes: string[];
}

export interface LLMTierConfig {
  base_url: string;
  model: string;
  api_key_masked: string;
  has_api_key: boolean;
  configured: boolean;
  overridden_fields: string[];
}

export interface LLMTestResult {
  ok: boolean;
  configured: boolean;
  model?: string;
  duration_ms?: number;
  sample?: string;
  error: string | null;
}

/** 出生档案（第 64 节高敏感数据，本地优先） */
export interface BirthProfile {
  user_id: number;
  solar_birth_date: string;
  solar_birth_time: string;
  birth_time_known: boolean;
  gender: string;
  birth_place: string;
  longitude: number | null;
  latitude: number | null;
  use_true_solar_time: boolean;
}

/** 命理批示（第 6.1 节程序排盘 + LLM 解读，纯展示不进入 Fusion） */
export interface FortuneReading {
  ok: boolean;
  error: string | null;
  model: string;
  duration_ms: number;
  reasoning?: string;
  /** 是否命中缓存（false 表示本次实时调用 LLM 生成） */
  cached?: boolean;
  chart: {
    degraded: boolean;
    bazi: { year: string; month: string; day: string; time: string; day_master: string };
    day_master: string;
    shishen: Record<string, string>;
    wuxing: Record<string, string>;
    nayin: Record<string, string>;
    ming_gong: string;
    dayun: { start_age: number; start_year: number; ganzhi: string }[];
    liunian: { year: number; ganzhi: string; zodiac: string; age: number | null }[];
    birth_time_known: boolean;
    gender: string;
    /** 精确周岁：考虑本年生日是否已过 */
    current_age_exact?: number;
    /** 年份差（虚岁）：当前年 - 出生年 */
    current_age_nominal?: number;
  } | null;
  reading: Record<string, string> | null;
}

export const api = {
  meta: () => get<Record<string, unknown>>('/api/meta'),
  health: () => get<{ status: string; engines: Record<string, { available: boolean }> }>('/api/health'),

  llmConfig: () => get<{ tiers: Record<string, LLMTierConfig> }>('/api/system/llm-config'),
  saveLLMConfig: (tier: string, fields: { base_url?: string; model?: string; api_key?: string }) =>
    request<{ ok: boolean; tiers: Record<string, LLMTierConfig> }>('/api/system/llm-config', {
      method: 'PUT',
      body: JSON.stringify({ tier, ...fields }),
    }),
  testLLMConfig: (tier: string, draft?: { base_url?: string; model?: string; api_key?: string }) =>
    post<LLMTestResult>('/api/system/llm-config/test', { tier, ...(draft ?? {}) }),

  engines: () => get<{ engines: EngineInfo[]; available_count: number }>('/api/system/engines'),
  ontology: (domain?: string, scale?: string) =>
    get<{ count: number; items: OntologyItem[] }>(
      `/api/ontology${domain || scale ? `?${new URLSearchParams({ ...(domain ? { domain } : {}), ...(scale ? { scale } : {}) })}` : ''}`,
    ),
  rules: (status = 'active') =>
    get<{ count: number; items: RuleItem[] }>(`/api/rules?status=${status}`),

  generate: (userId: number, scale = 'day', limit = 20) =>
    post<GenerateResult>(`/api/predictions/generate?user_id=${userId}&scale=${scale}&limit=${limit}`),

  listPredictions: (userId: number, status?: string) =>
    get<{ count: number; items: PredictionBrief[] }>(
      `/api/predictions?user_id=${userId}${status ? `&status=${status}` : ''}`,
    ),

  duePredictions: (userId: number) =>
    get<{
      count: number;
      items: {
        prediction_id: string;
        event_type: string;
        description: string;
        probability: number;
        success_criteria: string[];
        failure_criteria: string[];
        window: [string, string];
        status: string;
      }[];
    }>(`/api/predictions/due?user_id=${userId}`),

  prediction: (id: string) => get<PredictionDetail>(`/api/predictions/${id}`),

  verify: (id: string, userReply?: string, quickAnswer?: string) => {
    const params = new URLSearchParams();
    if (userReply) params.set('user_reply', userReply);
    if (quickAnswer) params.set('quick_answer', quickAnswer);
    const qs = params.toString();
    return post<{
      prediction_id: string;
      outcome: number;
      confidence: number;
      needs_confirmation: boolean;
      disagreement: number;
      judges: { role: string; outcome: number; confidence: number }[];
      status: string;
    }>(`/api/predictions/${id}/verify${qs ? `?${qs}` : ''}`);
  },

  history: (userId: number) =>
    get<{ count: number; items: HistoryItem[] }>(`/api/predictions/history?user_id=${userId}`),

  overall: (userId?: number, domain?: string, timeScale?: string) => {
    const p = new URLSearchParams();
    if (userId) p.set('user_id', String(userId));
    if (domain) p.set('domain', domain);
    if (timeScale) p.set('time_scale', timeScale);
    return get<Aggregate>(`/api/analytics/overall?${p}`);
  },

  calibration: (userId?: number) =>
    get<{
      bins: { bin: string; n: number; predicted: number; actual: number; gap: number }[];
      overconfidence: number;
      sample_size: number;
      reliability: string;
    }>(`/api/analytics/calibration${userId ? `?user_id=${userId}` : ''}`),

  reliability: (userId?: number) =>
    get<ReliabilityMatrix>(`/api/analytics/reliability${userId ? `?user_id=${userId}` : ''}`),

  ablation: (userId?: number) =>
    get<{ runs: Record<string, unknown[]>; note: string }>(
      `/api/analytics/ablation${userId ? `?user_id=${userId}` : ''}`,
    ),

  futureTree: (userId: number, asOf?: string) =>
    get<{
      as_of: string;
      horizon_days: number;
      scenarios: { key: string; label: string; probability: number; description: string; evidence: string[] }[];
    }>(`/api/future-tree?user_id=${userId}${asOf ? `&as_of=${asOf}` : ''}`),

  counterfactual: (userId: number, interventions: { label: string; effects: Record<string, number> }[]) =>
    post<{
      as_of: string;
      scenarios: { key: string; label: string; dimensions: Record<string, number>; description: string }[];
    }>(`/api/counterfactual?user_id=${userId}`, { interventions }),

  gateTest: (payload: Record<string, unknown>) => post<GateTestResponse>('/api/adversarial/gate-test', payload),

  calendarSnapshot: (userId: number, targetDate?: string) =>
    get<CalendarSnapshot>(
      `/api/calendar/snapshot?user_id=${userId}${targetDate ? `&target_date=${targetDate}` : ''}`,
    ),

  createUser: (userKey: string, birth?: Record<string, unknown>) =>
    post<{ user_id: number; user_key: string }>('/api/users', {
      user_key: userKey,
      birth_profile: birth ?? null,
    }),

  listUsers: () => get<{ count: number; items: { id: number; user_key: string }[] }>('/api/users'),

  profile: (userId: number) => get<BirthProfile>(`/api/users/${userId}/profile`),
  updateProfile: (userId: number, fields: Record<string, unknown>) =>
    request<BirthProfile>(`/api/users/${userId}/profile`, {
      method: 'PUT',
      body: JSON.stringify(fields),
    }),

  fortuneReading: (userId: number, refresh = false) =>
    get<FortuneReading>(`/api/fortune/reading?user_id=${userId}${refresh ? '&refresh=true' : ''}`),

  /** 分术式批示（当前支持 ziwei）。与八字批示并行加载，各自缓存。 */
  fortuneReadingZiwei: (userId: number, refresh = false) =>
    get<ZiweiReading>(
      `/api/fortune/reading/ziwei?user_id=${userId}${refresh ? '&refresh=true' : ''}`,
    ),

  /** 今日锦囊：宜忌/吉神方位/冲煞/吉时/幸运色数/桃花引动（确定性，秒出） */
  fortuneDaily: (userId: number, date?: string) =>
    get<DailyAlmanac>(
      `/api/fortune/daily?user_id=${userId}${date ? `&date=${date}` : ''}`,
    ),

  /** 影像相学分析（面相/掌纹）。multipart 直传，后端默认不存图不发云。 */
  imagingHistory: (userId: number, kind: 'palm' | 'face') =>
    get<{ kind: string; items: ImagingHistoryItem[] }>(
      `/api/imaging/history?user_id=${userId}&kind=${kind}`,
    ),
  imagingPurge: (userId: number, kind?: 'palm' | 'face') =>
    request<{ deleted: number }>(
      `/api/imaging/records?user_id=${userId}${kind ? `&kind=${kind}` : ''}`,
      { method: 'DELETE' },
    ),
  imagingAnalyze: (form: FormData) =>
    request<ImagingAnalysis>('/api/imaging/analyze', {
      method: 'POST',
      body: form,
      // FormData 必须让浏览器自己拼 multipart 边界——不能带 JSON 头
      headers: {} as HeadersInit,
    }),
};

/** 紫微批示：十二宫盘面 + 六维度解读 */
export interface ZiweiReading {
  ok: boolean;
  error: string | null;
  model?: string;
  duration_ms?: number;
  reasoning?: string;
  cached?: boolean;
  chart: {
    degraded?: boolean;
    palaces: {
      name: string;
      ganzhi: string;
      dalimit: [number, number] | null;
      major_stars: { name: string; brightness: string; mutagen: string }[];
    }[];
    soul_palace: string;
    body_palace: string;
    soul_branch: string;
    prompt_text?: string;
  } | null;
  reading: Record<string, string> | null;
}

/** 影像分析结果（POST /api/imaging/analyze） */
export interface ImagingAnalysis {
  kind: 'palm' | 'face';
  detected: boolean;
  features: Record<string, unknown>;
  reading: string[];
  cloud: { used: boolean; text?: string; model?: string; duration_ms?: number; reason?: string };
  saved?: boolean;
  record_id?: number | null;
  privacy: {
    original_deleted: boolean;
    features_stored?: boolean;
    stored?: boolean;
    cloud_sent: boolean;
    note: string;
  };
}

/** 相法特征存档（GET /api/imaging/history） */
export interface ImagingHistoryItem {
  id: number;
  kind: 'palm' | 'face';
  captured_at: string;
  detected: boolean;
  hand?: string;
  features: Record<string, unknown>;
  reading: string[];
}
