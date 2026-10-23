/**
 * Darwin's parts as "teams" of workers at desks, built from the dashboard's live data.
 * All three 3D concepts draw the same teams; only the look and layout differ.
 */
import type { Candidate, Event, Performance, Position, RiskState, Status } from "../api";
import { signed, usd } from "../api";

export type AgentState = "working" | "waiting" | "idle" | "alert";

export type Agent = { name: string; role: string; state: AgentState };

export type Team = {
  id: TeamId;
  name: string;
  /** What this team does, one line. */
  job: string;
  color: string;
  agents: Agent[];
  /** Big number on the card and what it counts. */
  headline: { value: string; label: string };
  rows: { label: string; value: string; tone?: "pos" | "neg" }[];
  /** Short attention pill, e.g. "153 blocked". */
  alert?: string;
};

export type TeamId = "data" | "scanner" | "risk" | "trading" | "portfolio" | "exits" | "review";

/** Pipeline order: prices in, ideas, checks, orders, holdings, exits, lessons. */
export const ORDER: TeamId[] = ["data", "scanner", "risk", "trading", "portfolio", "exits", "review"];

export type Brain = {
  mode: string;
  marketOpen: boolean;
  gatewayUp: boolean;
  entriesOn: boolean;
  lastScan: string | null;
  candidates: number;
  approved: number;
  equity: number | null;
};

export type World = { teams: Team[]; brain: Brain; flow: Flow };

/** Last scan as a funnel: how many ideas reached each stage. */
export type Flow = { scanned: number; passedChecks: number; ordered: number; topBlock: string | null };

export type Inputs = {
  status: Status | null;
  open: Position[];
  closed: Position[];
  scan: { ts: string | null; candidates: Candidate[] };
  events: Event[];
  perf: Performance | null;
  risk: RiskState | null;
};

const STRATEGY_NAMES: Record<string, string> = {
  credit_spread: "Credit spreads", iron_condor: "Iron condors", iron_butterfly: "Iron butterflies",
  calendar: "Calendars", leaps_call: "LEAPS calls",
};

const ago = (ts: string | null) => {
  if (!ts) return "never";
  const m = Math.round((Date.now() - new Date(ts).getTime()) / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
};

/** "max positions on SPY reached" from "max positions on SPY reached" / "score -0.04 below min 0.0" -> "score below min". */
function reasonKey(r: string) {
  return r.replace(/-?\d+(\.\d+)?/g, "").replace(/\s+/g, " ").replace(/: .*/, "").trim();
}

export function buildWorld(d: Inputs): World {
  const s = d.status;
  const marketOpen = !!s?.account?.is_market_open;
  const gatewayUp = s?.gateway ? s.gateway.connected : !!s;
  const busy: AgentState = !gatewayUp ? "alert" : marketOpen ? "working" : "idle";
  const cands = d.scan.candidates;
  const approved = cands.filter((c) => c.approved).length;
  const blocked = cands.length - approved;
  const reasons = new Map<string, number>();
  for (const c of cands) for (const r of c.reasons) reasons.set(reasonKey(r), (reasons.get(reasonKey(r)) ?? 0) + 1);
  const topBlock = [...reasons.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  const today = new Date().toISOString().slice(0, 10);
  const opensToday = d.events.filter((e) => e.kind === "open" && e.ts.startsWith(today)).length;
  const opens = d.events.filter((e) => e.kind === "open").length;
  const closes = d.events.filter((e) => e.kind === "close");
  const errors = d.events.filter((e) => e.kind === "error" && e.ts.startsWith(today)).length;
  const unreal = d.open.reduce((a, p) => a + (p.unrealized_pnl ?? 0), 0);
  const openRisk = d.open.reduce((a, p) => a + p.max_loss, 0);
  const strategies = s?.strategies ?? Object.keys(STRATEGY_NAMES);
  const universe = s?.universe ?? [];
  const closedN = d.perf?.overall.trades ?? d.closed.length;
  const riskCap = d.risk ? (d.risk.capital ?? d.risk.equity) * d.risk.max_portfolio_risk_pct : null;

  const teams: Team[] = [
    {
      id: "data", name: "Market Data", color: "#22b8cf",
      job: "Pulls prices from IB Gateway and saves every quote for replays.",
      agents: [
        ...universe.map((t) => ({ name: t, role: `Watches ${t} and its option chains`, state: busy })),
        { name: "Recorder", role: "Saves every price received", state: busy },
      ],
      headline: { value: String(universe.length), label: universe.length === 1 ? "ticker" : "tickers" },
      rows: [
        { label: "IB Gateway", value: gatewayUp ? "Connected" : "Offline", tone: gatewayUp ? "pos" : "neg" },
        { label: "Market", value: marketOpen ? "Open" : "Closed" },
        { label: "Watching", value: universe.join(" · ") || "–" },
      ],
      alert: gatewayUp ? undefined : "Gateway offline",
    },
    {
      id: "scanner", name: "Scanner", color: "#4c6ef5",
      job: "Builds and scores trade ideas for every strategy and ticker.",
      agents: strategies.map((id) => ({
        name: STRATEGY_NAMES[id] ?? id, role: `Finds ${STRATEGY_NAMES[id]?.toLowerCase() ?? id} worth trading`,
        state: busy === "alert" ? "alert" : cands.some((c) => c.strategy === id) ? busy : "idle",
      })),
      headline: { value: String(cands.length), label: "ideas last scan" },
      rows: [
        { label: "Last scan", value: ago(d.scan.ts) },
        { label: "Best score", value: cands.length ? `${(Math.max(...cands.map((c) => c.score)) * 100).toFixed(1)}%` : "–" },
        { label: "Strategies", value: String(strategies.length) },
      ],
    },
    {
      id: "risk", name: "Risk Desk", color: "#e64980",
      job: "Checks every idea against the account rules before it can trade.",
      agents: [
        { name: "Sizing", role: "1–5% of the account per trade", state: busy },
        { name: "Never naked", role: "Every short option is covered", state: busy },
        { name: "Limits", role: "Positions, ticker and group caps", state: blocked > 0 ? "waiting" : busy },
      ],
      headline: { value: String(blocked), label: "ideas blocked" },
      rows: [
        { label: "Passed", value: String(approved), tone: approved ? "pos" : undefined },
        { label: "Money at risk", value: riskCap ? `${usd(openRisk)} of ${usd(riskCap)}` : usd(openRisk) },
        { label: "Top reason", value: topBlock ?? "–" },
      ],
      alert: d.risk?.halted ? "Trading halted" : blocked && !approved ? `${blocked} blocked` : undefined,
    },
    {
      id: "trading", name: "Trading", color: "#fab005",
      job: "Places approved trades as one combo order and works the fill.",
      agents: [
        { name: "Orders", role: "Sends combo orders to IBKR", state: s?.allow_new_entries ? busy : "idle" },
        { name: "Fills", role: "Tracks fills and cancels stale orders", state: busy },
      ],
      headline: { value: String(opensToday), label: "trades opened today" },
      rows: [
        { label: "New entries", value: s?.allow_new_entries ? "On" : "Off", tone: s?.allow_new_entries ? "pos" : "neg" },
        { label: "Opened (all time)", value: String(opens) },
        { label: "Errors today", value: String(errors), tone: errors ? "neg" : undefined },
      ],
      alert: s && !s.allow_new_entries ? "Entries off" : undefined,
    },
    {
      id: "portfolio", name: "Portfolio", color: "#40c057",
      job: "Holds the open trades: one desk per position.",
      agents: d.open.length
        ? d.open.map((p) => ({
            name: `${p.underlying} ${p.strategy.replace(/_/g, " ")}`,
            role: `Max loss ${usd(p.max_loss)} · P&L ${signed(p.unrealized_pnl)}`,
            state: (p.unrealized_pnl ?? 0) < -0.5 * p.max_loss ? "alert" : busy === "alert" ? "alert" : busy,
          }))
        : [{ name: "Empty desk", role: "No open positions", state: "idle" }],
      headline: { value: String(d.open.length), label: d.open.length === 1 ? "open position" : "open positions" },
      rows: [
        { label: "Unrealized", value: signed(unreal), tone: unreal >= 0 ? "pos" : "neg" },
        { label: "Max loss", value: usd(openRisk) },
        { label: "Account", value: usd(d.risk?.equity ?? s?.account?.equity) },
      ],
    },
    {
      id: "exits", name: "Exits & Rolls", color: "#fd7e14",
      job: "Watches open trades and closes them by the rules.",
      agents: [
        { name: "Take profit", role: "Closes at 50% of max profit", state: d.open.length ? busy : "idle" },
        { name: "Stop loss", role: "Closes losers early", state: d.open.length ? busy : "idle" },
        { name: "Strike guard", role: "Closes before a short strike is hit", state: d.open.length ? busy : "idle" },
        { name: "Calendar", role: "Time exits and the one allowed roll", state: d.open.length ? busy : "idle" },
      ],
      headline: { value: String(closes.length), label: closes.length === 1 ? "exit so far" : "exits so far" },
      rows: [
        { label: "Watching", value: `${d.open.length} position${d.open.length === 1 ? "" : "s"}` },
        { label: "Last exit", value: closes.length ? ago(closes[closes.length - 1].ts) : "–" },
      ],
    },
    {
      id: "review", name: "Review", color: "#be4bdb",
      job: "Learns from closed trades and suggests rule changes.",
      agents: [
        { name: "Scorekeeper", role: "Win rate and profit per strategy", state: closedN ? "working" : "waiting" },
        { name: "Coach", role: "Suggests changes after 20+ trades per group", state: closedN >= 20 ? "working" : "waiting" },
      ],
      headline: { value: `${closedN}/30`, label: "closed trades (trial)" },
      rows: [
        { label: "Win rate", value: d.perf?.overall.win_rate != null ? `${Math.round(d.perf.overall.win_rate * 100)}%` : "–" },
        { label: "Realized", value: signed(d.perf?.overall.total_pnl ?? 0), tone: (d.perf?.overall.total_pnl ?? 0) >= 0 ? "pos" : "neg" },
      ],
    },
  ];

  return {
    teams,
    brain: {
      mode: s ? (s.mode === "live" ? "LIVE" : s.mode === "paper" ? "Paper" : "Sim") : "…",
      marketOpen, gatewayUp, entriesOn: !!s?.allow_new_entries,
      lastScan: d.scan.ts, candidates: cands.length, approved, equity: d.risk?.equity ?? s?.account?.equity ?? null,
    },
    flow: { scanned: cands.length, passedChecks: approved, ordered: opensToday, topBlock },
  };
}

export const STATE_COLOR: Record<AgentState, string> = {
  working: "#51cf66", waiting: "#fcc419", idle: "#868e96", alert: "#ff6b6b",
};

export const STATE_LABEL: Record<AgentState, string> = {
  working: "Working", waiting: "Waiting", idle: "Idle (market closed)", alert: "Needs attention",
};

export { ago };
