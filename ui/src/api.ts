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
};

export type Status = {
  mode: string;
  data: string;
  broker: string;
  broker_kind: string;
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

async function req<T>(path: string, method = "GET"): Promise<T> {
  const r = await fetch(path, { method });
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

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
};

export const usd = (n: number | null | undefined, digits = 0) =>
  n == null ? "–" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits, minimumFractionDigits: digits });

export const pct = (n: number | null | undefined) => (n == null ? "–" : `${(n * 100).toFixed(1)}%`);

export const signed = (n: number | null | undefined) => (n == null ? "–" : `${n >= 0 ? "+" : "−"}${usd(Math.abs(n))}`);
