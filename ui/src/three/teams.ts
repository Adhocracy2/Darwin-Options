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
  /** Optional outside link, shown on hover (e.g. IBKR's web login). */
  link?: { href: string; label: string };
};

export type TeamId = "data" | "scanner" | "risk" | "trading" | "portfolio" | "exits" | "review" | "news" | "technology";

/** Pipeline order: prices in, ideas, checks, orders, holdings, exits, lessons. */
export const ORDER: TeamId[] = ["data", "scanner", "risk", "trading", "portfolio", "exits", "review", "news", "technology"];

export type Brain = {
  mode: string;
  marketOpen: boolean;
  gatewayUp: boolean;
  entriesOn: boolean;
  lastScan: string | null;
  candidates: number;
  approved: number;
  equity: number | null;
  backtesting: boolean; // a backtest is running (the team relay runs fast to show it)
  cycle: Status["cycle"]; // what the trader is doing now (step) or did in its last cycle
  pricesSavedAt: string | null; // when the recorder last saved a price (for backtests)
};

/** How busy a data link is: dots move fast (active), slowly (idle), not at all (down, red line). */
export type LinkState = "active" | "idle" | "warn" | "down";

export type Links = {
  teamEngine: LinkState; // every team <-> the engine
  dataGateway: LinkState; // Market Data <-> IB Gateway: requests out, prices in
  tradingGateway: LinkState; // Trading <-> IB Gateway: orders out, fills in
  portfolioGateway: LinkState; // Portfolio <-> IB Gateway: position checks out, holdings in
  gatewayIbkr: LinkState; // IB Gateway <-> IBKR's servers
};

export type SystemId = "engine" | "gateway" | "ibkr" | "supervisor" | "router" | "isp";

/** A plain status line (no traffic shown): working, broken, or not known. */
export type LineState = "ok" | "down" | "unknown";

/** AI1 -> router -> ISP -> internet (IBKR), from the dashboard's ping checks. */
export type NetLines = { aiRouter: LineState; routerIsp: LineState; ispInternet: LineState };

/** Things that aren't desk teams (no desks), shown in the same card and side panel format. */
export type SystemInfo = Omit<Team, "id"> & { id: SystemId };

/** Account summary shown on the engine's circle. Percentages are fractions (0.02 = 2%). */
export type Summary = {
  realized: number; unrealized: number; riskPerTrade: number | null; drawdown: number | null;
  today: number | null; open: number;
};

export type World = { teams: Team[]; systems: SystemInfo[]; brain: Brain; flow: Flow; links: Links; net: NetLines; summary: Summary };

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
        { label: "Prices saved", value: ago(s?.activity?.prices_at ?? null) },
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
        { label: "Last checked", value: ago(d.scan.ts) },
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
        { label: "Re-priced", value: ago(s?.cycle?.finished ?? null) },
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
        { label: "Last checked", value: ago(s?.cycle?.finished ?? null) },
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
    {
      // placeholder team: desks are set up, the work is assigned later
      id: "news", name: "News Desk", color: "#20c997",
      job: "Will watch the news, earnings and ex-dividend dates. Not tasked yet.",
      agents: [
        { name: "Headlines", role: "Market news (not tasked yet)", state: "idle" },
        { name: "Calendar", role: "Earnings and ex-dividend dates (not tasked yet)", state: "idle" },
      ],
      headline: { value: "–", label: "not tasked yet" },
      rows: [{ label: "Status", value: "Desks ready" }],
    },
  ];

  // ---- outside systems and the links to them
  const gw = s?.gateway;
  const ibkrUp = gw ? (gw.connected ? gw.ibkr ?? null : null) : null;
  const farms = Object.entries(gw?.farms ?? {});
  const broken = farms.filter(([, v]) => v === "broken").map(([k]) => k);
  const recent = (ts: string | null | undefined, mins: number) => !!ts && Date.now() - new Date(ts).getTime() < mins * 60_000;
  const pricesAt = s?.activity?.prices_at ?? null, ordersAt = s?.activity?.orders_at ?? null;
  const moving = marketOpen || recent(pricesAt, 10);
  const links: Links = {
    teamEngine: marketOpen ? "active" : "idle",
    dataGateway: !gatewayUp ? "down" : moving ? "active" : "idle",
    tradingGateway: !gatewayUp ? "down" : recent(ordersAt, 15) || (marketOpen && !!s?.allow_new_entries) ? "active" : "idle",
    portfolioGateway: !gatewayUp ? "down" : marketOpen ? "active" : "idle",
    gatewayIbkr: !gatewayUp || ibkrUp === false ? "down" : broken.length ? "warn" : moving ? "active" : "idle",
  };
  // ---- home network: ping google; if that fails, ping the router
  const n = s?.network ?? null;
  const net: NetLines = !n || n.checked_at == null
    ? { aiRouter: "unknown", routerIsp: "unknown", ispInternet: "unknown" }
    : n.internet ? { aiRouter: "ok", routerIsp: "ok", ispInternet: "ok" }
    : n.router ? { aiRouter: "ok", routerIsp: "down", ispInternet: "unknown" }
    : n.router === false ? { aiRouter: "down", routerIsp: "unknown", ispInternet: "unknown" }
    : { aiRouter: "unknown", routerIsp: "down", ispInternet: "unknown" };
  const ms = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v)} ms`);
  const checked = n?.checked_at ? ago(n.checked_at) : n?.error ?? "not checked";

  const systems: SystemInfo[] = [
    {
      id: "router", name: "Router", color: "#adb5bd",
      job: `Your home internet gateway${n?.router_host ? ` (${n.router_host})` : ""}. AI1 reaches everything through it.`,
      agents: [],
      headline: {
        value: n?.router ? "Up" : n?.router === false ? "Down" : "Unknown",
        label: n?.router === false ? "no answer from AI1" : n?.internet ? "internet answers too" : "",
      },
      rows: [
        { label: "Address", value: n?.router_host ?? "–" },
        { label: "Ping", value: n?.internet ? "skipped: internet OK" : ms(n?.router_ms) },
        { label: "Checked", value: checked },
      ],
      alert: n?.router === false ? "Router not answering" : undefined,
    },
    {
      id: "isp", name: "ISP / Internet", color: "#4dabf7",
      job: "Your internet provider: the link from the router out to the internet (and IBKR).",
      agents: [],
      headline: {
        value: n?.internet ? "Online" : n?.internet === false ? "Down" : "Unknown",
        label: n?.internet ? `${n.internet_host} answers` : n?.internet === false ? `${n.internet_host} doesn't answer` : "",
      },
      rows: [
        { label: "Test", value: n ? `ping ${n.internet_host}` : "–" },
        { label: "Ping", value: ms(n?.internet_ms), tone: n?.internet ? "pos" : n?.internet === false ? "neg" : undefined },
        { label: "Checked", value: checked },
      ],
      alert: n?.internet === false ? (n.router ? "Internet down (router OK)" : "No internet") : undefined,
    },
    {
      id: "gateway", name: "IB Gateway", color: "#ced4da",
      job: "IBKR's desktop app on AI1 (port 4002). Every price and order goes through it.",
      agents: [],
      headline: { value: gatewayUp ? "Online" : "Offline", label: gatewayUp ? "Darwin connected" : "Darwin can't reach it" },
      rows: [
        { label: "Prices last moved", value: ago(pricesAt) },
        { label: "Last order", value: ago(ordersAt) },
        { label: "Account", value: s ? (s.mode === "live" ? "LIVE" : "Paper") : "–" },
      ],
      alert: gatewayUp ? undefined : (gw?.error ? "Offline: log in to IB Gateway on AI1" : "Offline"),
    },
    {
      id: "ibkr", name: "IBKR", color: "#e03131",
      job: "Interactive Brokers' servers: market data, order routing and the paper account.",
      agents: [],
      headline: {
        value: ibkrUp === true ? "Linked" : ibkrUp === false ? "Link lost" : "Unknown",
        label: ibkrUp === null ? "IB Gateway reports this" : "IB Gateway to IBKR",
      },
      rows: [
        { label: "Data farms", value: farms.length ? `${farms.length - broken.length}/${farms.length} OK` : "–", tone: broken.length ? "neg" : farms.length ? "pos" : undefined },
        { label: "Farms", value: farms.map(([k]) => k).join(" · ") || "–" },
        { label: "US market", value: marketOpen ? "Open" : "Closed" },
      ],
      alert: ibkrUp === false ? "IBKR link lost" : broken.length ? `Farm down: ${broken.join(", ")}` : undefined,
      link: { href: "https://www.interactivebrokers.com.au/sso/Login", label: "Open IBKR web login" },
    },
    {
      id: "engine", name: "The Engine", color: "#5ad8ff",
      job: "Runs the cycle every few minutes in market hours: prices, ideas, checks, orders, exits.",
      agents: [],
      headline: { value: s ? (s.mode === "live" ? "LIVE" : s.mode === "paper" ? "Paper" : "Sim") : "…", label: "trading mode" },
      rows: [
        { label: "Market", value: marketOpen ? "Open: cycling" : "Closed: resting" },
        { label: "New entries", value: s?.allow_new_entries ? "On" : "Off", tone: s?.allow_new_entries ? "pos" : "neg" },
        { label: "Last scan", value: ago(d.scan.ts) },
      ],
    },
    {
      id: "supervisor", name: "Supervisor", color: "#ffd43b",
      job: "Ask about anything in the system. The Supervisor can look things up, not trade.",
      agents: [],
      headline: { value: "Ask me", label: "click to chat" },
      rows: [],
    },
  ];

  // ---- Technology: looks after the software, AI1, the network, IB Gateway and the IBKR link
  const checks: [string, boolean | null][] = [
    ["Darwin", !!s], ["AI1", !!s], ["Router", n?.router ?? null], ["Internet", n?.internet ?? null],
    ["IB Gateway", s ? gatewayUp : null], ["IBKR link", ibkrUp],
  ];
  const ok = checks.filter(([, v]) => v === true).length;
  const down = checks.filter(([, v]) => v === false).map(([k]) => k);
  const deskState = (v: boolean | null | undefined): AgentState => (v === false ? "alert" : v == null ? "waiting" : "working");
  teams.push({
    id: "technology", name: "Technology", color: "#748ffc",
    job: "Keeps the software, the AI1 server, the router and internet, IB Gateway and the IBKR link running.",
    agents: [
      { name: "Software", role: `Darwin ${s?.version ?? ""}: trader, dashboard, deploys`.replace("  ", " "), state: deskState(!!s) },
      { name: "Server", role: "AI1: the machine everything runs on", state: deskState(!!s) },
      { name: "Network", role: `Router ${n?.router_host ?? ""} and the internet (ISP)`, state: deskState(n?.checked_at ? n.internet : null) },
      { name: "Broker link", role: "IB Gateway and its link to IBKR", state: deskState(s ? gatewayUp && ibkrUp !== false : null) },
    ],
    headline: { value: `${ok}/${checks.length}`, label: "systems OK" },
    rows: [
      { label: "Down", value: down.length ? down.join(", ") : "nothing", tone: down.length ? "neg" : "pos" },
      // the six things Technology monitors
      ...checks.map(([name, v]) => ({ label: name, value: v === true ? "OK" : v === false ? "Down" : "Unknown",
                                       tone: v === true ? "pos" as const : v === false ? "neg" as const : undefined })),
      { label: "Version", value: s?.version ?? "–" },
    ],
    alert: down.length ? `${down.join(", ")} down` : undefined,
  });

  return {
    teams,
    systems,
    links,
    net,
    summary: {
      realized: d.perf?.overall.total_pnl ?? 0, unrealized: unreal,
      riskPerTrade: d.risk?.risk_per_trade_pct ?? null, drawdown: d.risk?.drawdown ?? null,
      today: d.risk?.day_change_pct ?? null, open: d.open.length,
    },
    brain: {
      mode: s ? (s.mode === "live" ? "LIVE" : s.mode === "paper" ? "Paper" : "Sim") : "…",
      marketOpen, gatewayUp, entriesOn: !!s?.allow_new_entries,
      lastScan: d.scan.ts, candidates: cands.length, approved, equity: d.risk?.equity ?? s?.account?.equity ?? null,
      backtesting: !!s?.backtest,
      cycle: s?.cycle ?? null,
      pricesSavedAt: s?.activity?.prices_at ?? null,
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
