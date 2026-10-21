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
  account: { equity: number; buying_power: number; is_market_open: boolean };
  universe: string[];
  allow_new_entries: boolean;
  strategies: string[];
};

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

export type FieldKind = "pct" | "mult" | "days" | "price" | "bool";
export type FieldMeta = { label: string; help: string; kind: FieldKind; min: number; max: number };
export type ExitField = { key: string; value: number | boolean; default: number | boolean; changed: boolean };
export type StrategySettings = {
  name: string;
  label: string;
  description: string;
  enabled: boolean;
  enabled_default: boolean;
  open_positions: number;
  exit: ExitField[];
};
export type Settings = {
  allow_new_entries: boolean;
  allow_new_entries_default: boolean;
  strategies: StrategySettings[];
  fields: Record<string, FieldMeta>;
};
export type SettingsUpdate = {
  allow_new_entries?: boolean;
  strategies: Record<string, { enabled?: boolean; exit?: Record<string, number | boolean | null> }>;
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
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (u: SettingsUpdate) => req<Settings>("/api/settings", "PUT", u),
  strategyPayoffs: () => req<StrategyPayoff[]>("/api/payoff/strategies"),
  payoff: (r: PayoffRequest) => req<Payoff>("/api/payoff", "POST", r),
  positionPayoff: (id: string) => req<Payoff>(`/api/positions/${id}/payoff`),
};

export const usd = (n: number | null | undefined, digits = 0) =>
  n == null ? "–" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits, minimumFractionDigits: digits });

export const pct = (n: number | null | undefined) => (n == null ? "–" : `${(n * 100).toFixed(1)}%`);

export const signed = (n: number | null | undefined) => (n == null ? "–" : `${n >= 0 ? "+" : "−"}${usd(Math.abs(n))}`);
