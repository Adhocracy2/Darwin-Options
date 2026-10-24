/** Activity Log: a 3D panel swinging in from the left with everything the teams did, filterable. */
import { useEffect, useState } from "react";
import type { ActivityRow } from "../api";
import { api } from "../api";

export const TEAM_NAMES: Record<string, string> = {
  data: "Market Data", scanner: "Scanner", risk: "Risk Desk", trading: "Trade Desk", portfolio: "Portfolio",
  exits: "Exits & Rolls", review: "Review", news: "News Desk", technology: "Technology", supervisor: "Supervisor",
};
const TEAM_COLOR: Record<string, string> = {
  data: "#22b8cf", scanner: "#4c6ef5", risk: "#e64980", trading: "#fab005", portfolio: "#40c057", exits: "#fd7e14",
  review: "#be4bdb", news: "#20c997", technology: "#748ffc", supervisor: "#ffd43b",
};

export function ActivityLog({ onClose, extra = [], names = {} }: { onClose: () => void; extra?: ActivityRow[]; names?: Record<string, string> }) {
  const nameOf = (id: string) => names[id] ?? TEAM_NAMES[id];
  const [rows, setRows] = useState<ActivityRow[]>([]);
  const [cycles, setCycles] = useState<string[]>([]);
  const [team, setTeam] = useState("");
  const [cycle, setCycle] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.activity(team || undefined, cycle || undefined)
      .then((r) => { if (alive) { setRows(r.rows); setCycles(r.cycles); setErr(null); } })
      .catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 8000);
    return () => { alive = false; clearInterval(t); };
  }, [team, cycle]);
  const shown = [...extra.filter((r) => (!team || r.team === team) && (!cycle || r.cycle === cycle)), ...rows];
  const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  return (
    <div className="alog-scene" onClick={onClose}>
      <aside className="alog" onClick={(e) => e.stopPropagation()}>
        <header>
          <h3>Activity Log</h3>
          <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="alog-filters">
          <select value={team} onChange={(e) => setTeam(e.target.value)}>
            <option value="">All teams</option>
            {Object.keys(TEAM_NAMES).map((k) => <option key={k} value={k}>{nameOf(k)}</option>)}
          </select>
          <select value={cycle} onChange={(e) => setCycle(e.target.value)}>
            <option value="">All cycles</option>
            {[...new Set([...extra.map((r) => r.cycle).filter(Boolean) as string[], ...cycles])].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        {err && <p className="alog-empty">{err}</p>}
        <ol className="alog-list">
          {shown.length === 0 && <li className="alog-empty">Nothing yet. The log fills as the trader runs cycles (or press Demo).</li>}
          {shown.map((r, i) => (
            <li key={`${r.id}-${i}`} style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}>
              <span className="alog-time">{time(r.ts)}</span>
              <span className="alog-team" style={{ color: TEAM_COLOR[r.team ?? ""] ?? "var(--c3-muted)" }}>{(r.team && nameOf(r.team)) || "Engine"}</span>
              <span className="alog-msg">{r.message}</span>
              {r.cycle && <span className="alog-cycle">{r.cycle.split(" ").pop()}</span>}
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
