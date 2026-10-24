/** Teams panel: the owner sets each team's name, colour, description, parent and level, and groups teams into departments. */
import { useState } from "react";
import { ENGINE, PALETTE_COLOURS, levelOf, type Dept, type Layout, type Level, type TeamStyle } from "./layout";

export type TeamBase = { id: string; name: string; color: string; job: string };

function Swatches({ value, onPick }: { value: string; onPick: (c: string) => void }) {
  return (
    <div className="tp-swatches">
      {PALETTE_COLOURS.map((c) => (
        <button key={c} type="button" className={c === value.toLowerCase() ? "on" : ""} style={{ background: c }} onClick={() => onPick(c)} aria-label={c} />
      ))}
    </div>
  );
}

export function TeamsPanel({ layout, teams, onChange, onSave, onReset, onClose, saved, error }: {
  layout: Layout; teams: TeamBase[];
  onChange: (l: Layout) => void; onSave: () => void; onReset: () => void; onClose: () => void;
  saved: boolean; error: string | null;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const style = (id: string): TeamStyle => layout.teams[id] ?? {};
  const setTeam = (id: string, patch: Partial<TeamStyle>) => {
    const next = { ...style(id), ...patch };
    for (const k of Object.keys(next) as (keyof TeamStyle)[]) if (next[k] === undefined || next[k] === "") delete next[k];
    onChange({ ...layout, teams: { ...layout.teams, [id]: next } });
  };
  const setDept = (id: string, patch: Partial<Dept>) =>
    onChange({ ...layout, departments: layout.departments.map((d) => (d.id === id ? { ...d, ...patch } : d)) });
  const addDept = () => {
    let id = "dept", n = 1;
    while (layout.departments.some((d) => d.id === id)) id = `dept-${++n}`;
    const color = PALETTE_COLOURS[(layout.departments.length * 3) % PALETTE_COLOURS.length];
    onChange({ ...layout, departments: [...layout.departments, { id, name: `New department${n > 1 ? ` ${n}` : ""}`, color, level: "bottom" }] });
  };
  const removeDept = (id: string) => {
    const t = Object.fromEntries(Object.entries(layout.teams).map(([k, v]) => [k, v.dept === id ? { ...v, dept: undefined } : v]));
    onChange({ teams: JSON.parse(JSON.stringify(t)), departments: layout.departments.filter((d) => d.id !== id) });
  };
  const nameOf = (id: string) => style(id).name || teams.find((t) => t.id === id)?.name || id;

  return (
    <aside className="tp" onKeyDown={(e) => e.stopPropagation()}>
      <header>
        <h3>Teams</h3>
        <button className="c3-x" onClick={onClose} aria-label="Close">×</button>
      </header>
      <p className="tp-note">Changes show in the office straight away. Save keeps them. Only changes how the office looks, not how Darwin trades.</p>

      <section>
        <h4>Departments <button className="tp-add" onClick={addDept}>+ Add</button></h4>
        {layout.departments.length === 0 && <p className="tp-note">No departments yet.</p>}
        {layout.departments.map((d) => {
          const members = teams.filter((t) => style(t.id).dept === d.id);
          return (
            <div key={d.id} className="tp-dept" style={{ borderColor: d.color }}>
              <div className="tp-row">
                <input value={d.name} maxLength={40} onChange={(e) => setDept(d.id, { name: e.target.value })} aria-label="Department name" />
                <select value={d.level} onChange={(e) => setDept(d.id, { level: e.target.value as Level })} aria-label="Level">
                  <option value="top">Top</option><option value="bottom">Bottom</option>
                </select>
                <button className="tp-del" onClick={() => removeDept(d.id)} title="Delete department (its teams stay)">Delete</button>
              </div>
              <Swatches value={d.color} onPick={(c) => setDept(d.id, { color: c })} />
              <div className="tp-members">
                {teams.map((t) => (
                  <label key={t.id} className={style(t.id).dept === d.id ? "on" : ""}>
                    <input type="checkbox" checked={style(t.id).dept === d.id}
                      onChange={(e) => setTeam(t.id, { dept: e.target.checked ? d.id : undefined })} />
                    {nameOf(t.id)}
                  </label>
                ))}
              </div>
              {members.length === 0 && <p className="tp-note">Tick the teams that belong here.</p>}
            </div>
          );
        })}
      </section>

      <section>
        <h4>Teams</h4>
        {teams.map((t) => {
          const s = style(t.id);
          const colour = s.color || t.color;
          const dept = layout.departments.find((d) => d.id === s.dept);
          const isOpen = open === t.id;
          return (
            <div key={t.id} className={`tp-team ${isOpen ? "open" : ""}`}>
              <button className="tp-team-head" onClick={() => setOpen(isOpen ? null : t.id)}>
                <i style={{ background: colour }} />{nameOf(t.id)}
                <span>{dept ? dept.name : levelOf(layout, t.id) === "top" ? "Top" : "Bottom"}</span>
              </button>
              {isOpen && (
                <div className="tp-team-body">
                  <label>Name<input value={s.name ?? ""} placeholder={t.name} maxLength={40} onChange={(e) => setTeam(t.id, { name: e.target.value })} /></label>
                  <label>Colour</label>
                  <Swatches value={colour} onPick={(c) => setTeam(t.id, { color: c })} />
                  <label>Description<textarea rows={3} value={s.job ?? ""} placeholder={t.job} maxLength={300} onChange={(e) => setTeam(t.id, { job: e.target.value })} /></label>
                  <div className="tp-row">
                    <label>Line from
                      <select value={s.parent ?? ENGINE} onChange={(e) => setTeam(t.id, { parent: e.target.value === ENGINE ? undefined : e.target.value })}>
                        <option value={ENGINE}>The Engine (brain)</option>
                        {teams.filter((x) => x.id !== t.id).map((x) => <option key={x.id} value={x.id}>{nameOf(x.id)}</option>)}
                      </select>
                    </label>
                    <label>Department
                      <select value={s.dept ?? ""} onChange={(e) => setTeam(t.id, { dept: e.target.value || undefined })}>
                        <option value="">None</option>
                        {layout.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    </label>
                    <label>Level
                      <select value={levelOf(layout, t.id)} disabled={!!dept} title={dept ? "Set by its department" : undefined}
                        onChange={(e) => setTeam(t.id, { level: e.target.value as Level })}>
                        <option value="top">Top</option><option value="bottom">Bottom</option>
                      </select>
                    </label>
                  </div>
                  {dept && <p className="tp-note">Level comes from the {dept.name} department.</p>}
                </div>
              )}
            </div>
          );
        })}
      </section>

      <footer>
        {error && <span className="tp-err">{error}</span>}
        <button className="tp-reset" onClick={() => { if (confirm("Put every team back to the default look?")) onReset(); }}>Reset to defaults</button>
        <button className="tp-save" onClick={onSave} disabled={saved}>{saved ? "Saved" : "Save"}</button>
      </footer>
    </aside>
  );
}

