/**
 * The 3D office: Darwin's parts as teams at desks around the engine, the outside systems it talks
 * to (IB Gateway, IBKR) and the Supervisor you can chat with. Desktop only for now. Open with #/3d.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { Candidate, Event, Performance, Position, RiskState, Status } from "../api";
import { api } from "../api";
import { ChatPanel } from "./ChatPanel";
import { ago, buildWorld, STATE_COLOR, STATE_LABEL, type AgentState, type SystemInfo, type Team } from "./teams";
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
      } catch (e) {
        if (alive) setD((x) => ({ ...x, error: String(e) }));
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return d;
}

function Details({ team, onClose }: { team: Team | SystemInfo; onClose: () => void }) {
  return (
    <aside className="c3-panel">
      <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
      <div className="c3-panel-head"><i style={{ background: team.color }} />{team.name}</div>
      <p className="c3-job">{team.job}</p>
      <div className="c3-big"><b>{team.headline.value}</b> {team.headline.label}</div>
      {team.alert && <div className="c3-alert">{team.alert}</div>}
      {team.link && <a className="c3-link" href={team.link.href} target="_blank" rel="noopener noreferrer">{team.link.label} ↗</a>}
      <dl>{team.rows.map((r) => <div key={r.label}><dt>{r.label}</dt><dd className={r.tone}>{r.value}</dd></div>)}</dl>
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

export default function OfficePage() {
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = useData();
  const world = useMemo(() => buildWorld(data), [data]);
  const all: (Team | SystemInfo)[] = [...world.teams, ...world.systems];
  const picked = selected && selected !== "supervisor" ? all.find((t) => t.id === selected) ?? null : null;
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
        <div className="c3-status">
          <span className={b.gatewayUp ? "ok" : "bad"} title={gw?.error ?? ""}>{b.gatewayUp ? "IB Gateway online" : "IB Gateway offline"}</span>
          <span className={link === "down" ? "bad" : link === "warn" ? "warn" : gw?.ibkr ? "ok" : ""}>
            {link === "down" && b.gatewayUp ? "IBKR link lost" : link === "warn" ? "IBKR farm down" : gw?.ibkr ? "IBKR linked" : "IBKR unknown"}
          </span>
          {(() => {
            const n = data.status?.network;
            if (!n || n.checked_at == null) return null;
            return <span className={n.internet ? "ok" : "bad"}>{n.internet ? "Internet OK" : n.router ? "Internet down" : "Router down"}</span>;
          })()}
          <span>{b.mode}</span>
          <span>{b.marketOpen ? "Market open" : "Market closed"}</span>
          <span>Last scan {ago(b.lastScan)}</span>
        </div>
      </header>

      <div className="c3-stage">
        {data.at ? (
          <Suspense fallback={<div className="c3-loading">Loading 3D…</div>}>
            <Office world={world} selected={selected} onSelect={setSelected} onHover={setHovered} busy={busy} burst={burst} />
          </Suspense>
        ) : <div className="c3-loading">{data.error ? "Couldn't reach the dashboard API." : "Loading data…"}</div>}
        {selected === "supervisor" && <ChatPanel onClose={() => setSelected(null)} onBusy={onBusy} onTools={onTools} />}
        {picked && <Details team={picked} onClose={() => setSelected(null)} />}
        <div className="c3-legend">
          {(Object.keys(STATE_COLOR) as AgentState[]).map((s) => <span key={s}><i style={{ background: STATE_COLOR[s] }} />{STATE_LABEL[s]}</span>)}
          <span className="c3-sep" />
          <span><i className="task" />Task: engine → team (team colour)</span>
          <span><i className="task done" />Done: back to the engine</span>
          <span><i className="line-down" />Link down</span>
          <span><i className="line-ok" />Network OK</span>
          <span className="c3-hint">Drag to rotate · scroll to zoom · click anything · Esc to reset</span>
        </div>
        {hovered && !picked && selected !== "supervisor" && <div className="c3-tip">{all.find((t) => t.id === hovered)?.job}</div>}
        {data.error && data.at && <div className="c3-error">{data.error}</div>}
        <div className="c3-mobile">The 3D office is built for desktop screens. A mobile version comes later.</div>
      </div>
    </div>
  );
}
