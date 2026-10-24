/**
 * The 3D office: Darwin's parts as teams at desks around the engine, the outside systems it talks
 * to (IB Gateway, IBKR) and the Supervisor you can chat with. Desktop only for now. Open with #/3d.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { ActivityRow, Candidate, Event, Performance, Position, RiskState, Status } from "../api";
import { api } from "../api";
import { ReceivedCard } from "../components/ReceivedCard";
import { ActivityLog } from "./ActivityLog";
import { applyLayout, DEFAULT_LAYOUT, normalise, type Layout } from "./layout";
import { TeamsPanel, type TeamBase } from "./TeamsPanel";
import { DataStorePanel } from "./DataStorePanel";
import { ChatPanel } from "./ChatPanel";
import { formatCountdown, marketClock } from "./marketClock";
import { ago, buildWorld, STATE_COLOR, STATE_LABEL, type AgentState, type SystemInfo, type Team } from "./teams";
import logoDark from "../assets/logo-dark.png";
import "./concepts.css";

const Office = lazy(() => import("./concepts/CommandCenter"));

function useData() {
  const [d, setD] = useState<{
    status: Status | null; open: Position[]; closed: Position[]; scan: { ts: string | null; candidates: Candidate[] };
    events: Event[]; perf: Performance | null; risk: RiskState | null; error: string | null; at: Date | null;
  }>({ status: null, open: [], closed: [], scan: { ts: null, candidates: [] }, events: [], perf: null, risk: null, error: null, at: null });
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [status, open, closed, scan, events, perf, risk] = await Promise.all([
          api.status(), api.positions("open"), api.positions("closed"), api.scan(), api.events(), api.performance(),
          api.risk().catch(() => null),
        ]);
        if (alive) setD({ status, open, closed, scan, events, perf, risk, error: null, at: new Date() });
        return !!status.backtest || !!status.account?.is_market_open;
      } catch (e) {
        if (alive) setD((x) => ({ ...x, error: String(e) }));
        return false;
      }
    };
    // every 30 s; every 4 s while the market is open or a backtest runs, so the work shows as it happens
    let t: ReturnType<typeof setTimeout>;
    const loop = async () => {
      const fast = await load();
      if (alive) t = setTimeout(loop, fast ? 4_000 : 30_000);
    };
    loop();
    return () => { alive = false; clearTimeout(t); };
  }, []);
  return d;
}

/** Buttons that minimize IB Gateway's windows on AI1's desktop. */
function GatewayWindows() {
  const [msg, setMsg] = useState<string | null>(null);
  const run = (target: "gateway" | "messages") => {
    setMsg("…");
    api.minimizeGateway(target)
      .then((r) => setMsg(r.minimized.length ? `Minimised: ${[...new Set(r.minimized)].join(", ")}` : "No such window open"))
      .catch((e) => setMsg(e instanceof Error ? e.message : String(e)));
  };
  return (
    <div className="c3-winbtns">
      <h4>IB Gateway on AI1's desktop</h4>
      <button onClick={() => run("gateway")}>Minimise gateway window</button>
      <button onClick={() => run("messages")}>Hide login messages</button>
      {msg && <p className="c3-job">{msg}</p>}
    </div>
  );
}

function Details({ team, onClose, status }: { team: Team | SystemInfo; onClose: () => void; status: Status | null }) {
  return (
    <aside className="c3-panel">
      <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
      <div className="c3-panel-head"><i style={{ background: team.color }} />{team.name}</div>
      <p className="c3-job">{team.job}</p>
      <div className="c3-big"><b>{team.headline.value}</b> {team.headline.label}</div>
      {team.alert && <div className="c3-alert">{team.alert}</div>}
      {team.link && <a className="c3-link" href={team.link.href} target="_blank" rel="noopener noreferrer">{team.link.label} ↗</a>}
      <dl>{team.rows.map((r) => <div key={r.label}><dt>{r.label}</dt><dd className={r.tone}>{r.value}</dd></div>)}</dl>
      {team.id === "data" && <ReceivedCard status={status} compact />}
      {(team.id === "gateway" || team.id === "technology") && <GatewayWindows />}
      {team.agents.length > 0 && <h4>At the desks ({team.agents.length})</h4>}
      <ul className="c3-agents">
        {team.agents.map((a, i) => (
          <li key={i}><i style={{ background: STATE_COLOR[a.state] }} title={STATE_LABEL[a.state]} />
            <div><b>{a.name}</b><span>{a.role}</span></div></li>
        ))}
      </ul>
    </aside>
  );
}

/** Countdown to the US market's next open or close (New York hours, holidays, early closes). */
function MarketCountdown() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  const c = marketClock(now);
  return (
    <div className={`c3-clock ${c.open ? "open" : "shut"}`}>
      <span>{c.open ? "Market open · closes in" : "Market closed · opens in"}</span>
      <b>{formatCountdown(c.inMs)}</b>
      {c.early && <em>{c.open ? "early close 1:00pm NY" : "early close that day"}</em>}
    </div>
  );
}

const STEP_LABEL: Record<string, string> = {
  reconcile: "Portfolio checking positions with IBKR", manage: "Exits checking open trades", scan: "Market Data + Scanner building ideas",
  risk: "Risk Desk checking ideas", orders: "Trade Desk placing an order", collect: "Market Data saving prices", snapshot: "Portfolio recording equity",
};

/** The trader's heartbeat: what it's doing right now, how many cycles today, when the next one starts. */
function CycleActivity({ status }: { status: Status | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const c = status?.cycle;
  if (!c) return <div className="c3-activity idle">No trading cycle has run yet</div>;
  const hhmm = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const running = !c.finished && now - new Date(c.at).getTime() < 10 * 60_000;
  const count = c.count ? `Cycle ${c.count} today` : "Cycle";
  if (running) {
    return (
      <div className="c3-activity running">
        <i />{count} · <b>{STEP_LABEL[c.step ?? ""] ?? "working"}</b> · started {hhmm(c.started)}
      </div>
    );
  }
  const r = c.result;
  const did = c.error ? `failed: ${c.error}` : r ? `${r.opened} opened, ${r.exits} closed` : "";
  const next = c.next_at && status?.account?.is_market_open ? Math.max(0, new Date(c.next_at).getTime() - now) : null;
  const mmss = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, "0")}`;
  return (
    <div className={`c3-activity ${c.error ? "error" : "idle"}`}>
      {count} done at {hhmm(c.finished ?? c.at)}{did && ` · ${did}`}
      {next !== null && <> · next cycle in <b>{next > 0 ? mmss(next) : "a moment"}</b></>}
    </div>
  );
}

/** What Darwin is doing right now, under the logo: paper trading, live trading, or a backtest running. */
function ModeBanner({ status, demo }: { status: Status | null; demo?: boolean }) {
  if (!status) return null;
  if (demo) {
    return <div className="c3-mode demo"><span className="c3-mode-dot" /><b>DEMO CYCLE</b><span className="c3-mode-sub">simulated: nothing is traded</span></div>;
  }
  const bt = status.backtest;
  if (bt) {
    const p = bt.total ? bt.done / bt.total : 0;
    return (
      <div className="c3-mode backtest" title={bt.label}>
        <span className="c3-mode-dot" /><b>BACKTESTING</b>
        <span className="c3-mode-date">{bt.at ? new Date(bt.at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "starting…"}</span>
        <span className="c3-mode-bar"><i style={{ width: `${(p * 100).toFixed(1)}%` }} /></span>
        <span className="c3-mode-count">{bt.done}/{bt.total || "?"} days</span>
      </div>
    );
  }
  const live = status.mode === "live";
  return (
    <div className={`c3-mode ${live ? "live" : "paper"}`}>
      <span className="c3-mode-dot" /><b>{live ? "LIVE TRADING" : status.mode === "sim" ? "SIMULATION" : "PAPER TRADING"}</b>
    </div>
  );
}

export default function OfficePage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = useData();
  // Demo: a scripted cycle played on top of the real data, to show what each animation means
  const [demo, setDemo] = useState<Status["cycle"]>(null);
  const [demoLog, setDemoLog] = useState<ActivityRow[]>([]);
  const [showLog, setShowLog] = useState(false);
  const runDemo = useCallback(() => {
    const STEPS: [string, number, string, string][] = [
      ["reconcile", 2600, "portfolio", "checking positions against IBKR"],
      ["manage", 2600, "exits", "checking open trades against the exit rules"],
      ["scan", 4200, "scanner", "fetching option chains from IBKR and building trade ideas"],
      ["risk", 2800, "risk", "checking ideas against the risk rules"],
      ["orders", 3200, "trading", "placing an order with IBKR"],
      ["collect", 3600, "data", "saving option prices for backtests"],
      ["snapshot", 2200, "portfolio", "recording equity"],
    ];
    const started = new Date().toISOString();
    const cyc = "demo";
    let t = 0, id = -1;
    const steps: string[] = [];
    setDemoLog([]);
    STEPS.forEach(([step, ms, team, msg]) => {
      setTimeout(() => {
        steps.push(step);
        const now = new Date().toISOString();
        setDemo({ started, finished: null, step, steps: [...steps], at: now, count: 0, day: "demo" });
        setDemoLog((l) => [{ id: id--, ts: now, cycle: cyc, team, message: `(demo) ${msg}` }, ...l]);
      }, t);
      t += ms;
    });
    setTimeout(() => {
      const now = new Date().toISOString();
      setDemo({ started, finished: now, step: null, steps: [...steps], at: now, count: 0, day: "demo", result: { opened: 1, exits: 0 } });
      setDemoLog((l) => [{ id: id--, ts: now, cycle: cyc, team: null, message: "(demo) cycle done: 1 opened, 0 closed" }, ...l]);
    }, t);
    setTimeout(() => setDemo(null), t + 4000);
  }, []);
  const view = useMemo(() => {
    if (!demo || !data.status) return data;
    const saving = demo.step === "collect" ? new Date().toISOString() : data.status.activity?.prices_at ?? null;
    return { ...data, status: { ...data.status, cycle: demo, activity: { prices_at: saving, orders_at: data.status.activity?.orders_at ?? null } } };
  }, [data, demo]);
  // the owner's layout (Teams panel): live preview while editing, saved on the server
  const [layout, setLayout] = useState<Layout>(DEFAULT_LAYOUT);
  const [draft, setDraft] = useState<Layout | null>(null);
  const [showTeams, setShowTeams] = useState(false);
  const [teamsErr, setTeamsErr] = useState<string | null>(null);
  useEffect(() => { api.office().then((r) => setLayout(normalise(r))).catch(() => undefined); }, []);
  const shownLayout = draft ?? layout;
  const [sceneLayout, setSceneLayout] = useState<Layout>(shownLayout);
  useEffect(() => { const t = setTimeout(() => setSceneLayout(shownLayout), 250); return () => clearTimeout(t); }, [shownLayout]);
  const base = useMemo(() => buildWorld(view), [view]);
  const world = useMemo(() => applyLayout(base, sceneLayout), [base, sceneLayout]);
  const teamBases: TeamBase[] = useMemo(() => [
    ...base.teams.map((t) => ({ id: t.id, name: t.name, color: t.color, job: t.job })),
    ...base.systems.filter((x) => x.id === "supervisor").map((x) => ({ id: x.id, name: x.name, color: "#868e96", job: x.job })),
  ], [base]);
  const unsaved = !!draft && JSON.stringify(draft) !== JSON.stringify(layout);
  const closeTeams = () => {
    if (unsaved && !confirm("Close without saving your team changes?")) return;
    setDraft(null); setShowTeams(false); setTeamsErr(null);
  };
  const saveTeams = () => {
    if (!draft) return;
    api.saveOffice(draft).then((r) => { const l = normalise(r); setLayout(l); setDraft(l); setTeamsErr(null); })
      .catch((e) => setTeamsErr(String(e.message ?? e)));
  };
  const resetTeams = () => {
    api.resetOffice().then(() => { setLayout(DEFAULT_LAYOUT); setDraft(DEFAULT_LAYOUT); setTeamsErr(null); })
      .catch((e) => setTeamsErr(String(e.message ?? e)));
  };
  const names = useMemo(() => Object.fromEntries(world.teams.map((t) => [t.id, t.name])), [world]);
  const all: (Team | SystemInfo)[] = [...world.teams, ...world.systems];
  const picked = selected && selected !== "supervisor" && selected !== "datastore" ? all.find((t) => t.id === selected) ?? null : null;
  const onBusy = useCallback((b: boolean) => setBusy(b), []);
  // the Supervisor's lookups, as the teams whose work it read (shown as task flows to them)
  const [burst, setBurst] = useState<{ ids: string[]; n: number } | null>(null);
  const onTools = useCallback((tools: string[]) => {
    const TEAMS: Record<string, string[]> = {
      get_overview: ["data", "scanner", "risk", "portfolio", "technology"], get_positions: ["portfolio"], get_position_detail: ["portfolio", "exits"],
      get_last_scan: ["scanner", "risk"], get_recent_events: ["trading", "exits"], get_risk: ["risk"], get_settings: ["scanner"],
      get_market_data: ["data"],
    };
    const ids = [...new Set(tools.flatMap((t) => TEAMS[t] ?? []))];
    if (ids.length) setBurst((b) => ({ ids, n: (b?.n ?? 0) + 1 }));
  }, []);

  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setSelected(null); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  const b = world.brain;
  const gw = data.status?.gateway;
  const link = world.links.gatewayIbkr;
  return (
    <div className="c3 c3-1">
      <header className="c3-bar">
        <a className="c3-back" href="#/">← Dashboard</a>
        <div className="c3-title">Darwin <span>3D office</span></div>
        <button className={`c3-ask ${selected === "supervisor" ? "on" : ""}`} onClick={() => setSelected(selected === "supervisor" ? null : "supervisor")}>
          Ask the Supervisor
        </button>
        <button className="c3-demo" onClick={runDemo} disabled={!!demo} title="Play a simulated cycle to see what each animation means">
          {demo ? "Demo running…" : "Demo"}
        </button>
        <div className="c3-status">
          {(() => {
            // green text = OK; amber box = warning; red box = problem. The tooltip says what's wrong.
            const n = data.status?.network;
            const farms = Object.entries(gw?.farms ?? {}).filter(([, v]) => v === "broken").map(([k]) => k);
            const items: { label: string; state: "ok" | "warn" | "bad" | "unknown"; why: string }[] = [
              { label: "IB Gateway", state: b.gatewayUp ? "ok" : "bad", why: b.gatewayUp ? "Darwin is connected" : `Offline${gw?.error ? `: ${gw.error}` : ""}` },
              {
                label: "IBKR",
                state: !b.gatewayUp ? "unknown" : link === "down" ? "bad" : link === "warn" ? "warn" : gw?.ibkr ? "ok" : "unknown",
                why: !b.gatewayUp ? "unknown while IB Gateway is offline" : link === "down" ? "IB Gateway lost its link to IBKR"
                  : link === "warn" ? `data farm down: ${farms.join(", ")}` : gw?.ibkr ? "linked" : "not reported yet",
              },
              {
                label: "Internet",
                state: !n || n.checked_at == null ? "unknown" : n.internet ? "ok" : n.router ? "warn" : "bad",
                why: !n || n.checked_at == null ? "not checked yet" : n.internet ? `${n.internet_host} answers` : n.router ? "internet down (router OK)" : "router not answering",
              },
            ];
            return items.map((x) => <span key={x.label} className={`c3-pill ${x.state}`} title={x.why}>{x.label}</span>);
          })()}
          <span>{b.mode}</span>
          <span>{b.marketOpen ? "Market open" : "Market closed"}</span>
          <span>Last scan {ago(b.lastScan)}</span>
        </div>
      </header>

      <div className="c3-stage">
        <img className="c3-logo" src={logoDark} alt="Darwin Options" />
        <ModeBanner status={view.status} demo={!!demo} />
        <MarketCountdown />
        <CycleActivity status={view.status} />
        <button className="c3-logbtn" onClick={() => setShowLog(true)} title="Everything the teams did, by team and cycle">Activity Log</button>
        {showLog && <ActivityLog onClose={() => setShowLog(false)} extra={demoLog} names={names} />}
        <button className="c3-logbtn teams" onClick={() => { setSelected(null); setDraft(layout); setShowTeams(true); }} title="Names, colours, lines, levels and departments">Teams</button>
        {showTeams && draft && (
          <TeamsPanel layout={draft} teams={teamBases} onChange={setDraft} onSave={saveTeams} onReset={resetTeams}
            onClose={closeTeams} saved={!unsaved} error={teamsErr} />
        )}
        {data.at ? (
          <Suspense fallback={<div className="c3-loading">Loading 3D…</div>}>
            <Office world={world} selected={selected} onSelect={setSelected} onHover={setHovered} busy={busy} burst={burst} />
          </Suspense>
        ) : <div className="c3-loading">{data.error ? "Couldn't reach the dashboard API." : "Loading data…"}</div>}
        {selected === "supervisor" && <ChatPanel onClose={() => setSelected(null)} onBusy={onBusy} onTools={onTools} />}
        {selected === "datastore" && <DataStorePanel onClose={() => setSelected(null)} />}
        {picked && <Details team={picked} onClose={() => setSelected(null)} status={data.status} />}
        <div className="c3-legend">
          {(Object.keys(STATE_COLOR) as AgentState[]).map((s) => <span key={s}><i style={{ background: STATE_COLOR[s] }} />{STATE_LABEL[s]}</span>)}
          <span className="c3-sep" />
          <span><i className="task" />Task: engine → team (team colour)</span>
          <span><i className="task done" />Done: back to the engine</span>
          <span><i className="line-down" />Link down</span>
          <span><i className="line-ok" />Network OK</span>
          <span className="c3-hint">Drag to rotate · scroll to zoom · click anything · Esc to reset</span>
        </div>
        {hovered && !picked && selected !== "supervisor" && (() => {
          const tip = hovered === "tech-toggle" ? "Show or hide what Technology looks after: IB Gateway, the router, the ISP and IBKR"
            : hovered === "datastore" ? "Saved prices for backtests. Click to see what was stored most recently."
            : all.find((t) => t.id === hovered)?.job;
          return tip ? <div className="c3-tip">{tip}</div> : null;
        })()}
        {data.error && data.at && <div className="c3-error">{data.error}</div>}
        <div className="c3-mobile">The 3D office is built for desktop screens. A mobile version comes later.</div>
      </div>
    </div>
  );
}
