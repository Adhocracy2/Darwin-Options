export type Leg = { symbol: string; qty: number; entry_price: number; exit_price: number | null };

export type Position = {
  id: string;
  strategy: string;
  underlying: string;
  status: string;
  legs: Leg[];
  max_loss: number;
  max_profit: number;
  opened_at: string;
  closed_at: string | null;
  exit_reason: string | null;
  commissions: number;
  realized_pnl: number;
  unrealized_pnl?: number | null;
  marks?: Record<string, number>;
};

export type Candidate = {
  strategy: string;
  underlying: string;
  description: string;
  net_credit: number;
  max_loss: number;
  max_profit: number;
  pop: number;
  expected_value: number;
  score: number;
  approved: boolean | null;
  qty: number | null;
  reasons: string[];
  notes: Record<string, unknown>;
  legs: { symbol: string; ratio: number; strike: number; expiry: string; right: string }[];
  attach_to: string | null;
};

export type Status = {
  mode: string;
  data: string;
  broker: string;
  account: { equity: number; buying_power: number; is_market_open: boolean } | null;
  gateway: {
    connected: boolean;
    error: string | null;
    ibkr?: boolean | null; // IB Gateway's own link to IBKR's servers (null = unknown)
    farms?: Record<string, "ok" | "broken" | "inactive">;
    changed_at?: number | null;
  } | null;
  universe: string[];
  allow_new_entries: boolean;
  strategies: string[];
  activity?: { prices_at: string | null; orders_at: string | null };
  network?: {
    internet: boolean | null; router: boolean | null; internet_ms: number | null; router_ms: number | null;
    internet_host: string; router_host: string | null; checked_at: string | null; error: string | null;
  } | null;
  version?: string; // deployed git revision (short)
  backtest?: { id: string; done: number; total: number; at: string | null; label: string } | null; // a backtest running now
};

export type PricePoint = { d: string; close: number; upper: number | null; lower: number | null; mid: number | null; rsi: number | null; macd_hist: number | null };
export type SymbolMetrics = {
  symbol: string;
  spot: number | null;
  vol: {
    iv: number | null; hv20: number | null; hv_ibkr: number | null; iv_rank: number | null; iv_percentile: number | null;
    iv_hv: number | null; iv_history: { d: string; iv: number }[]; hv_history: { d: string; hv: number }[];
  };
  tech: {
    price: number | null; rsi14: number | null; sma20: number | null; sma50: number | null; sma200: number | null; ema20: number | null;
    bollinger: { upper: number; mid: number; lower: number; pct_b: number | null; width: number | null } | null;
    macd: { macd: number; signal: number; hist: number } | null; trend: "up" | "down" | "mixed" | null;
  } & { price_series?: PricePoint[] };
  sentiment: { call_oi: number | null; put_oi: number | null; pcr_oi: number | null; call_volume: number | null; put_volume: number | null; pcr_volume: number | null };
};
export type Greeks = { delta: number; gamma: number; theta: number; vega: number; rho: number };
export type Metrics = {
  as_of: string;
  symbols: SymbolMetrics[];
  portfolio: {
    greeks: (Greeks & { delta_dollars: number | null; legs_without_prices: number; positions: (Greeks & { id: string; underlying: string; strategy: string })[] }) | null;
    lifecycle: {
      trades: number; closed: number; avg_dte_at_entry: number | null; avg_delta_at_entry: number | null; assignments: number;
      early_exercises: number; assignment_rate: number | null; realized_net: number; fees: number; roi: number | null;
      annualized_roi: number | null; days_trading: number | null;
    };
  };
  unavailable: string[];
};
export type NewsItem = { title: string; link: string | null; ts: string | null; source: string; symbol: string };

export type BacktestDataset = { id: string; path: string; label: string; first: string; last: string; days: number; symbols: string[] };
export type BacktestStats = { trades: number; win_rate: number | null; pnl: number; avg_win: number | null; avg_loss: number | null; predicted_win_rate: number | null };
export type BacktestRun = {
  id: string; label: string; dataset: string; start: string; end: string; capital: number; strategies: string[];
  status: "running" | "done" | "stopped" | "failed"; done: number; total: number; at: string | null;
  started: string; finished: string | null; error: string | null; failed_cycles?: number;
  overall?: (BacktestStats & { return_pct: number | null; max_drawdown: number; still_open: number; fees: number }) | null; // in lists
  results?: {
    overall: BacktestStats & { return_pct: number | null; max_drawdown: number; still_open: number; fees: number };
    by_year: Record<string, BacktestStats>;
    by_strategy: Record<string, BacktestStats>;
    by_exit_reason: Record<string, { count: number; pnl: number }>;
    equity: { ts: string; equity: number }[];
    trades: { id: string; strategy: string; opened: string; closed: string; pnl: number; max_loss: number; exit: string | null; pop: number | null; description: string | null }[];
  };
};

export type ChatMessage = { role: "user" | "assistant"; content: string; tools?: string[] };

export type Stats = {
  trades: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy: number | null;
  profit_factor: number | null;
  total_pnl: number;
  largest_loss: number | null;
};

export type Performance = {
  overall: Stats;
  by_strategy: Record<string, Stats>;
  by_exit_reason: Record<string, { count: number; pnl: number }>;
  max_drawdown: number | null;
  open_positions: number;
};

export type Snapshot = { ts: string; equity: number; open_risk: number; realized: number; unrealized: number };
export type Event = { id: number; ts: string; kind: string; position_id: string | null; message: string };

export type FieldKind = "pct" | "mult" | "days" | "price" | "bool" | "delta" | "widths" | "time";
export type FieldMeta = { label: string; help: string; kind: FieldKind; min: number; max: number };
export type FieldValue = number | boolean | string | number[];
export type SettingField = { key: string; value: FieldValue; default: FieldValue; changed: boolean };
export type ExitField = SettingField;
export type StrategySettings = {
  name: string;
  type: string;
  label: string;
  description: string;
  builtin: boolean;
  removable: boolean;
  same_day: boolean;
  same_day_ok: boolean;
  enabled: boolean;
  enabled_default: boolean;
  open_positions: number;
  entry: SettingField[];
  exit: SettingField[];
};
export type StrategyType = {
  type: string;
  label: string;
  description: string;
  same_day_ok: boolean;
  entry: Record<string, FieldValue>;
  same_day_entry: Record<string, FieldValue> | null;
  exit: Record<string, FieldValue>;
  same_day_exit: Record<string, FieldValue> | null;
};
export type TickerSetting = { symbol: string; enabled: boolean; in_config: boolean; open_positions: number };
export type Settings = {
  allow_new_entries: boolean;
  allow_new_entries_default: boolean;
  strategies: StrategySettings[];
  types: StrategyType[];
  same_day: {
    entry_start: string; entry_end: string; close_by_time: string; live_data: boolean;
    max_risk_per_trade_pct: number; max_trades_per_day: number; max_open: number; daily_loss_limit_pct: number;
    profit_targets: [string, number][]; lock_profit_at: number; lock_profit_floor: number; losing_time_stop: string;
    skip_dates: string[];
  };
  tickers: TickerSetting[];
  fields: Record<string, FieldMeta>;
};
export type StrategyUpdate = {
  type?: string;
  label?: string;
  enabled?: boolean;
  entry?: Record<string, FieldValue | null>;
  exit?: Record<string, FieldValue | null>;
};
export type SettingsUpdate = {
  allow_new_entries?: boolean;
  tickers?: Record<string, boolean>;
  strategies: Record<string, StrategyUpdate>;
};

/** Error carrying the server's message (FastAPI puts it in `detail`). */
export class ApiError extends Error {}

async function req<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text();
    let msg = `${method} ${path}: ${r.status} ${text}`;
    try { const j = JSON.parse(text); if (typeof j.detail === "string") msg = j.detail; } catch { /* not JSON */ }
    throw new ApiError(msg);
  }
  return r.json();
}

export type Payoff = {
  prices: number[];
  expiry: number[];
  exit: number[];
  spot: number;
  pnl_now: number;
  strikes: number[];
  breakevens: number[];
  max_profit: number | null; // null = keeps rising (unlimited)
  max_loss: number;
  expiry_date: string;
  exit_date: string;
  exit_dte: number;
  has_later_legs: boolean;
  entry_credit: number;
  underlying?: string;
  example?: boolean;
  summary?: PositionSummary; // open positions only
  prices_at?: string | null; // UTC time of the oldest price used (saved or live)
};
export type SummaryLeg = { symbol: string; qty: number; strike: number; right: "C" | "P"; expiry: string; entry_price: number; mark: number | null };
export type Outlook = {
  paths: number; days: number; exit_date: string; sigma: number; expected_pnl: number; chance_profit: number;
  chances: { take_profit: number; stop_loss: number; threatened: number; time_exit: number };
};
export type PositionSummary = {
  legs: SummaryLeg[];
  spot: number;
  open_value: number; // + received, - paid
  close_value: number | null; // + received, - paid, to close now
  pnl_now: number | null;
  commissions: number;
  days_left: number;
  expiry: string;
  nearest_short: { strike: number; right: "C" | "P"; distance_pct: number } | null;
  max_profit: number;
  max_loss: number;
  rules: {
    take_profit?: { pct: number; pnl: number; close_cost?: number; close_value?: number };
    stop_loss?: { pnl: number; mult?: number; pct?: number; close_cost?: number; close_value?: number };
    time_exit?: { date: string; dte?: number; time?: string };
    threatened?: { strike: number; buffer_pct: number };
  };
  outlook?: Outlook | null;
};
export type StrategyPayoff = { name: string; label: string; description: string; source: string; payoff: Payoff };
export type PayoffRequest = { strategy: string; net_credit: number; qty: number; legs: { symbol: string; ratio: number }[] };

export type ReviewGroup = { label: string; trades: number; win_rate: number; avg_return_on_risk: number; total_pnl: number };
export type Review = {
  trades: number;
  min_trades_per_group: number;
  note?: string;
  strategies: {
    strategy: string;
    overall: ReviewGroup;
    model?: { predicted_win_rate: number; actual_win_rate: number };
    groups: Record<string, ReviewGroup[]>;
  }[];
  suggestions: { strategy: string; title: string; detail: string; setting: string | null; value: unknown }[];
  execution?: { orders: number; avg_slippage_per_share: number; avg_slippage_pct_of_mid: number | null };
};

export type RiskState = {
  equity: number;
  stage: { min_equity: number; risk_per_trade_pct: number; max_portfolio_risk_pct: number; max_positions: number };
  risk_per_trade_pct: number;
  max_portfolio_risk_pct: number;
  max_positions: number;
  peak: number;
  drawdown: number;
  day_change_pct: number;
  measured: { trades: number; mean_return_on_risk: number | null; kelly: number | null; win_rate: number | null };
  size_multiplier: number;
  halted: boolean;
  reasons: string[];
  goals: { target: number; reached: boolean; progress: number }[];
  open_risk: number;
  open_positions: number;
  max_loss_per_trade: number;
  smallest_trade_max_loss: number | null;
  per_trade_cap: number;
  pilot: boolean;
  entries_left_today: number | null;
  capital_mode: boolean;
  account_equity: number;
  capital: number | null;
  limits: { drawdown_reduce_at: number; drawdown_halt_at: number; daily_loss_halt_pct: number; max_group_risk_pct: number };
};

export const api = {
  status: () => req<Status>("/api/status"),
  positions: (status: "open" | "closed" | "all" = "open") => req<Position[]>(`/api/positions?status=${status}`),
  closePosition: (id: string) => req<{ closed: boolean }>(`/api/positions/${id}/close`, "POST"),
  scan: () => req<{ ts: string | null; candidates: Candidate[] }>("/api/scan"),
  runScan: () => req<{ ts: string | null; candidates: Candidate[] }>("/api/scan", "POST"),
  runCycle: (force = false) => req<Record<string, unknown>>(`/api/run?force=${force}`, "POST"),
  events: () => req<Event[]>("/api/events"),
  equity: () => req<Snapshot[]>("/api/equity"),
  performance: () => req<Performance>("/api/performance"),
  review: () => req<Review>("/api/review"),
  risk: () => req<RiskState>("/api/risk"),
  resetRisk: () => req<RiskState>("/api/risk/reset", "POST"),
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (u: SettingsUpdate) => req<Settings>("/api/settings", "PUT", u),
  strategyPayoffs: () => req<StrategyPayoff[]>("/api/payoff/strategies"),
  payoff: (r: PayoffRequest) => req<Payoff>("/api/payoff", "POST", r),
  backtestDatasets: () => req<BacktestDataset[]>("/api/backtests/datasets"),
  backtests: () => req<BacktestRun[]>("/api/backtests"),
  backtest: (id: string) => req<BacktestRun>(`/api/backtests/${id}`),
  startBacktest: (b: { dataset: string; start?: string; end?: string; capital?: number }) => req<BacktestRun>("/api/backtests", "POST", b),
  stopBacktest: (id: string) => req<{ stopping: string }>(`/api/backtests/${id}/stop`, "POST"),
  metrics: (fresh = false) => req<Metrics>(`/api/metrics${fresh ? "?fresh=true" : ""}`),
  news: () => req<NewsItem[]>("/api/news"),
  supervisor: () => req<{ available: boolean; reason?: string; model?: string }>("/api/supervisor"),
  supervisorChat: (messages: ChatMessage[]) =>
    req<{ reply: string; tools_used: string[] }>("/api/supervisor/chat", "POST",
      { messages: messages.map(({ role, content }) => ({ role, content })) }),
  positionPayoff: (id: string, fresh = false) => req<Payoff>(`/api/positions/${id}/payoff${fresh ? "?fresh=true" : ""}`),
};

export const usd = (n: number | null | undefined, digits = 0) =>
  n == null ? "–" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits, minimumFractionDigits: digits });

export const pct = (n: number | null | undefined) => (n == null ? "–" : `${(n * 100).toFixed(1)}%`);

export const signed = (n: number | null | undefined) => (n == null ? "–" : `${n >= 0 ? "+" : "−"}${usd(Math.abs(n))}`);
