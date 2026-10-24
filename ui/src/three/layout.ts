/**
 * The 3D office layout the owner manages from the Teams panel: each team's name, colour, description,
 * parent (where its dotted line comes from) and level, plus departments that group teams on a level.
 * Saved on the server (/api/office); positions on the rings are worked out from it here.
 */
import type { SystemInfo, Team, World } from "./teams";

export type Level = "top" | "bottom";
export type TeamStyle = { name?: string; color?: string; job?: string; parent?: string; level?: Level; dept?: string };
export type Dept = { id: string; name: string; color: string; level: Level };
export type Layout = { teams: Record<string, TeamStyle>; departments: Dept[] };

/** The only colours offered for teams and departments. */
export const PALETTE_COLOURS = [
  "#22b8cf", "#15aabf", "#4c6ef5", "#748ffc", "#be4bdb", "#e64980", "#fa5252",
  "#fd7e14", "#fab005", "#ffd43b", "#82c91e", "#40c057", "#20c997", "#868e96",
];

/** Where each team sits by default (degrees: 0 = right, 90 = back) and which ring it's on. */
export const DEFAULT_ANGLE: Record<string, number> = {
  data: 0, scanner: 60, risk: 120, exits: 180, portfolio: 240, trading: 300,
  supervisor: 135, technology: 45, review: 225, news: 315,
};
const DEFAULT_TOP = new Set(["supervisor", "review", "news", "technology"]);
export const ENGINE = "engine";

export const DEFAULT_LAYOUT: Layout = {
  teams: { risk: { dept: "risk" }, portfolio: { dept: "risk" }, review: { dept: "quant" }, trading: { dept: "execution" }, exits: { dept: "execution" } },
  departments: [
    { id: "risk", name: "Risk", color: "#e64980", level: "bottom" },
    { id: "quant", name: "Quant", color: "#be4bdb", level: "top" },
    { id: "execution", name: "Execution", color: "#fab005", level: "bottom" },
  ],
};

/** The saved layout, or the default when nothing has been saved (or it's unreadable). */
export function normalise(raw: unknown): Layout {
  const r = raw as Partial<Layout> | null;
  if (!r || typeof r !== "object" || !r.teams || !Array.isArray(r.departments)) return DEFAULT_LAYOUT;
  return { teams: r.teams, departments: r.departments };
}

export const deptOf = (l: Layout, id: string) => {
  const d = l.teams[id]?.dept;
  return d ? l.departments.find((x) => x.id === d) ?? null : null;
};
export const levelOf = (l: Layout, id: string): Level =>
  deptOf(l, id)?.level ?? l.teams[id]?.level ?? (DEFAULT_TOP.has(id) ? "top" : "bottom");

/** A team's parent, falling back to the engine when it's missing, itself, or would make a loop. */
export function parentOf(l: Layout, id: string, ids: string[]): string {
  const p = l.teams[id]?.parent;
  if (!p || p === ENGINE || p === id || !ids.includes(p)) return ENGINE;
  let cur: string | undefined = p;
  for (let i = 0; i < 20 && cur && cur !== ENGINE; i++) {
    if (cur === id) return ENGINE; // loop
    const next: string | undefined = l.teams[cur]?.parent;
    cur = next && ids.includes(next) ? next : undefined;
  }
  return p;
}

/** The world with the owner's names, colours and descriptions on the teams (and the Supervisor). */
export function applyLayout(w: World, l: Layout): World {
  const style = (x: Team | SystemInfo) => {
    const s = l.teams[x.id];
    return s ? { ...x, name: s.name?.trim() || x.name, color: s.color || x.color, job: s.job?.trim() || x.job } : x;
  };
  return { ...w, teams: w.teams.map((t) => style(t) as Team), systems: w.systems.map((s) => (s.id === "supervisor" ? style(s) as SystemInfo : s)), layout: l };
}

export type Placement = { angle: number; level: Level; radius: number };
export type DeptArc = { dept: Dept; from: number; to: number; level: Level; radius: number };

const BASE_R: Record<Level, number> = { bottom: 9.8, top: 8 };
const SPACING = 6.6; // ring length each platform needs

/**
 * Angles on each ring: a department's teams sit next to each other with a gap either side, and each
 * department gets an arc (drawn as a faint shape). Groups keep roughly their default order.
 */
export function placeTeams(l: Layout, ids: string[]): { at: Map<string, Placement>; arcs: DeptArc[] } {
  const at = new Map<string, Placement>();
  const arcs: DeptArc[] = [];
  const ang = (id: string) => DEFAULT_ANGLE[id] ?? 200;
  for (const level of ["bottom", "top"] as Level[]) {
    // the top ring keeps its front (270°, in front of the engine's circle) clear: teams go round from there
    const from = level === "top" ? 270 : 0;
    const key = (id: string) => (ang(id) - from + 360) % 360;
    const mine = ids.filter((id) => levelOf(l, id) === level).sort((a, b) => key(a) - key(b));
    if (!mine.length) continue;
    // groups: each department's teams together, then single teams
    const groups: { dept: Dept | null; ids: string[] }[] = [];
    for (const id of mine) {
      const d = deptOf(l, id);
      const g = d ? groups.find((x) => x.dept?.id === d.id) : null;
      if (g) g.ids.push(id); else groups.push({ dept: d, ids: [id] });
    }
    const hasDept = groups.some((g) => g.dept);
    const gap = hasDept && groups.length > 1 ? 0.5 : 0;
    const front = level === "top" ? 1 : 0; // empty slot at the front of the top ring
    const total = mine.length + gap * groups.length + front;
    const unit = 360 / total;
    const radius = Math.max(BASE_R[level], (total * SPACING) / (2 * Math.PI));
    let a = level === "top" ? from + unit : ang(groups[0].ids[0]);
    for (const g of groups) {
      const first = a;
      for (const id of g.ids) { at.set(id, { angle: a, level, radius }); a += unit; }
      if (g.dept) arcs.push({ dept: g.dept, from: first - unit * 0.47, to: a - unit + unit * 0.47, level, radius });
      a += gap * unit;
    }
  }
  return { at, arcs };
}

/** A string that changes whenever the layout changes how the scene is built. */
export const layoutKey = (l: Layout | undefined) => (l ? JSON.stringify(l) : "");
