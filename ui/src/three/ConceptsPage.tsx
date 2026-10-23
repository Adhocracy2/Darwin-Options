/**
 * 3D preview: three concepts of the dashboard as teams at desks. Desktop only for now.
 * Open with #/3d (or #/3d/1, #/3d/2, #/3d/3).
 */
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import type { Candidate, Event, Performance, Position, RiskState, Status } from "../api";
import { api } from "../api";
import { ago, buildWorld, STATE_COLOR, STATE_LABEL, type AgentState, type Team } from "./teams";
import "./concepts.css";

const CONCEPTS = [
  { n: 1, name: "Command Center", blurb: "Dark and neon, closest to the reference", C: lazy(() => import("./concepts/CommandCenter")) },
  { n: 2, name: "Daylight Studio", blurb: "Bright office diorama, details in the side panel", C: lazy(() => import("./concepts/Studio")) },
  { n: 3, name: "Assembly Line", blurb: "Teams in pipeline order; ideas flow and get filtered", C: lazy(() => import("./concepts/Pipeline")) },
];

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

function Details({ team, onClose }: { team: Team; onClose: () => void }) {
  return (
    <aside className="c3-panel">
      <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
      <div className="c3-panel-head"><i style={{ background: team.color }} />{team.name}</div>
      <p className="c3-job">{team.job}</p>
      <div className="c3-big"><b>{team.headline.value}</b> {team.headline.label}</div>
      {team.alert && <div className="c3-alert">{team.alert}</div>}
      <dl>{team.rows.map((r) => <div key={r.label}><dt>{r.label}</dt><dd className={r.tone}>{r.value}</dd></div>)}</dl>
      <h4>At the desks ({team.agents.length})</h4>
      <ul className="c3-agents">
        {team.agents.map((a, i) => (
          <li key={i}><i style={{ background: STATE_COLOR[a.state] }} title={STATE_LABEL[a.state]} />
            <div><b>{a.name}</b><span>{a.role}</span></div></li>
        ))}
      </ul>
    </aside>
  );
}

export default function ConceptsPage() {
  const initial = Number(location.hash.match(/#\/3d\/(\d)/)?.[1] ?? 1);
  const [n, setN] = useState(CONCEPTS.some((c) => c.n === initial) ? initial : 1);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const data = useData();
  const world = useMemo(() => buildWorld(data), [data]);
  const team = world.teams.find((t) => t.id === selected) ?? null;
  const concept = CONCEPTS.find((c) => c.n === n)!;

  useEffect(() => { history.replaceState(null, "", `#/3d/${n}`); setSelected(null); }, [n]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
      if (["1", "2", "3"].includes(e.key)) setN(Number(e.key));
    };
    const h = () => { const m = Number(location.hash.match(/#\/3d\/(\d)/)?.[1]); if (CONCEPTS.some((c) => c.n === m)) setN(m); };
    window.addEventListener("keydown", k);
    window.addEventListener("hashchange", h);
    return () => { window.removeEventListener("keydown", k); window.removeEventListener("hashchange", h); };
  }, []);

  const b = world.brain;
  return (
    <div className={`c3 c3-${n}`}>
      <header className="c3-bar">
        <a className="c3-back" href="#/">← Dashboard</a>
        <div className="c3-title">Darwin <span>3D preview</span></div>
        <nav className="c3-switch">
          {CONCEPTS.map((c) => (
            <button key={c.n} className={c.n === n ? "on" : ""} onClick={() => setN(c.n)} title={c.blurb}>
              <span>{c.n}</span>{c.name}
            </button>
          ))}
        </nav>
        <div className="c3-status">
          <span className={b.gatewayUp ? "ok" : "bad"}>{b.gatewayUp ? "Gateway connected" : "Gateway offline"}</span>
          <span>{b.mode}</span>
          <span>{b.marketOpen ? "Market open" : "Market closed"}</span>
          <span>Last scan {ago(b.lastScan)}</span>
        </div>
      </header>

      <div className="c3-stage">
        {data.at ? (
          <Suspense fallback={<div className="c3-loading">Loading 3D…</div>}>
            <concept.C key={n} world={world} selected={selected} onSelect={setSelected} onHover={setHovered} />
          </Suspense>
        ) : <div className="c3-loading">{data.error ? "Couldn't reach the dashboard API." : "Loading data…"}</div>}
        {team && <Details team={team} onClose={() => setSelected(null)} />}
        <div className="c3-legend">
          {(Object.keys(STATE_COLOR) as AgentState[]).map((s) => <span key={s}><i style={{ background: STATE_COLOR[s] }} />{STATE_LABEL[s]}</span>)}
          <span className="c3-hint">Drag to rotate · scroll to zoom · click a team · keys 1–3 switch · Esc to reset</span>
        </div>
        {hovered && !team && <div className="c3-tip">{world.teams.find((t) => t.id === hovered)?.job}</div>}
        {data.error && <div className="c3-error">{data.error}</div>}
        <div className="c3-mobile">The 3D preview is built for desktop screens. A mobile version comes later.</div>
      </div>
    </div>
  );
}
