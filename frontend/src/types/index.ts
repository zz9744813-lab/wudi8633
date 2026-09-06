/** 前端类型定义，与后端 Pydantic Schema 对应。 */

export type Domain =
  | 'career' | 'money' | 'study' | 'social' | 'relationship'
  | 'travel' | 'project' | 'habit' | 'purchase'
  | 'communication' | 'schedule' | 'unexpected_event';

export type TimeScale = 'day' | 'week' | 'month' | 'year';

export type PredictionStatus =
  | 'CANDIDATE' | 'REJECTED' | 'REWRITE' | 'EXPERIMENTAL'
  | 'RESEARCH'
  | 'FROZEN' | 'VERIFY_REQUIRED' | 'WAITING_USER'
  | 'VERIFIED' | 'EXPIRED_UNVERIFIED' | 'LEAKED';

/** 方案第 14 节 统一 Signal Schema */
export interface Signal {
  signal_id: string;
  source: string;
  domain: string;
  target_event: string;
  direction: number;
  strength: number;
  confidence: number;
  time_window: { start: string; end: string };
  time_scale: TimeScale;
  evidence: { source: string; rule_id?: string | null; description: string; weight: number }[];
  counter_evidence: { source: string; rule_id?: string | null; description: string; weight: number }[];
  rule_ids: string[];
  dependency_group?: string | null;
  engine_version: string;
  prompt_version?: string | null;
  degraded: boolean;
  degrade_reason?: string | null;
}

export interface PredictionBrief {
  prediction_id: string;
  domain: string;
  event_type: string;
  description: string;
  probability: number;
  null_probability: number | null;
  time_scale: TimeScale;
  status: PredictionStatus;
  visibility_mode: 'VISIBLE' | 'HIDDEN';
  window: [string, string];
  verification_due_at: string | null;
  sha256_head: string;
  /** 读取端重建的详批（情景/多法印证/建议/幸运参考），非冻结内容 */
  narrative?: string;
  /** 正向同向的术式来源（如 ["bazi","ziwei"]），多法交叉徽标用 */
  supporting_sources?: string[];
  opposing_sources?: string[];
  /** ≥2 个术式源同向（多方法交叉印证达成） */
  crossed?: boolean;
}

/** 今日锦囊（/api/fortune/daily）：老黄历 + 民俗元素，纯确定性派生 */
export interface DailyAlmanac {
  date: string;
  day_ganzhi: string;
  day_wuxing: string;
  lunar_date: string;
  yi: string[];
  ji: string[];
  xi_dir: string;
  cai_dir: string;
  fu_dir: string;
  chong: string;
  sha_direction: string;
  day_god: string;
  pengzu: string[];
  lucky_hours: string[];
  lucky_color: string;
  lucky_color_aux: string;
  lucky_numbers: number[];
  day_zhi: string;
  day_master?: string;
  day_master_wuxing?: string;
  day_master_relation?: string;
  peach_blossom_stars?: {
    hongluan: string;
    tianxi: string;
    xianchi: string[];
  };
  peach_activated?: string[];
  clash_birth_day?: boolean;
  /** 日卦·周易经文参读（确定性日粒度起卦；文献参考，非效力宣称） */
  daily_gua?: {
    name: string;
    short: string;
    /** 六爻阴阳（初→上，1 阳 0 阴），供绘制卦象 */
    lines: number[];
    moving_yao: number;
    gua_ci: string | null;
    yao_ci: string | null;
    xiang: string;
    upper_gua?: string;
    lower_gua?: string;
    upper_wuxing?: string;
    lower_wuxing?: string;
    /** 日主强弱判定（身强/身弱/中和；有出生档案时才有） */
    natal_verdict?: string;
    /** 卦之上下卦五行相对日主的喜忌句（命数结合） */
    natal_notes?: string[];
  };
  /** 本日参读：窗口覆盖当日的在库预测（与卦并列展示，非因果） */
  related_predictions?: {
    prediction_id: string;
    description: string;
    probability: number;
    null_probability?: number | null;
    time_scale: string;
    status: string;
    window: [string, string];
  }[];
}

/** 方案第 49 节：预测详情必须完全可解释 */
export interface PredictionDetail extends PredictionBrief {
  success_criteria: string[];
  failure_criteria: string[];
  grading_rule: string;
  frozen_at: string | null;
  signals: Signal[];
  agent_disagreement: number;
  evidence_dependency: Record<string, string[]>;
  integrity: {
    stored_hash: string;
    recomputed_hash: string | null;
    ok: boolean;
    rebuild_matches_payload?: boolean;
  } | null;
  versions: {
    model: string;
    fusion: string;
    prompt: string;
    rule: string;
    engine: string;
  };
  lineage: { candidate_id: string | null; version: number };
  outcome: {
    outcome: number;
    confidence: number;
    needs_confirmation: boolean;
    disagreement: number;
  } | null;
}

export interface HistoryItem {
  prediction_id: string;
  event_type: string;
  /** 本体中文名（如「临时工作安排」） */
  label?: string;
  /** 冻结断言原文（含日期，如「9月4日（周五）临时工作安排。」） */
  description?: string;
  probability: number;
  outcome: number;
  null_probability: number | null;
  brier: number;
  judged_at: string;
}

/** 方案第 19 节聚合评分 */
export interface Aggregate {
  sample_size: number;
  brier: number | null;
  log_loss: number | null;
  sharpness: number | null;
  observed_rate: number | null;
  mean_probability: number | null;
  reliability: 'low' | 'medium' | 'high';
  ci: [number, number];
  skill_score: number | null;
  null_brier: number | null;
  overconfidence: number | null;
  bins: {
    bin: string;
    n: number;
    predicted: number;
    actual: number;
    gap: number;
  }[];
}

/** 方案第 26 节 Personal Reliability Matrix */
export interface ReliabilityCell {
  key: string;
  system?: string;
  domain?: string;
  time_scale?: string;
  sample_size: number;
  skill: number | null;
  brier: number | null;
  null_brier: number | null;
  reliability: 'low' | 'medium' | 'high';
  note?: string;
}

export interface ReliabilityMatrix {
  overall: Aggregate;
  by_system: ReliabilityCell[];
  by_domain: ReliabilityCell[];
  by_time_scale: ReliabilityCell[];
  fusion_weights: Record<string, number>;
}

export interface EngineInfo {
  source: string;
  engine: string;
  version: string;
  available: boolean;
}

export interface RuleItem {
  rule_id: string;
  school: string;
  description: string;
  domains: string[];
  supported_windows: string[];
  version: string;
  status: string;
}

export interface OntologyItem {
  event_type: string;
  domain: string;
  label: string;
  success_criteria: string[];
  failure_criteria: string[];
  preferred_scales: string[];
}

export interface GateAttackResult {
  attack: string;
  verdict: 'PASS' | 'FAIL' | 'WARN' | 'SKIP';
  severity: number;
  reason: string;
  details?: Record<string, unknown>;
}

export interface GateTestResponse {
  decision: 'PASS' | 'REWRITE' | 'REJECT' | 'EXPERIMENTAL';
  attacks: GateAttackResult[];
}
