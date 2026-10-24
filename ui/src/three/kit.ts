/**
 * Building blocks shared by the 3D concepts: desks with a seated worker and a status light,
 * team floors, the "engine" brain, flow lines with moving dots, and floating HTML cards.
 */
import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { AgentState, Team } from "./teams";
import { STATE_COLOR } from "./teams";
import type { Stage } from "./stage";

export type Palette = {
  deskTop: string;
  deskLeg: string;
  monitor: string;
  screen: string; // screen glow when working
  chair: string;
  skin: string[];
  roughness: number;
};

const geo = {
  top: new THREE.BoxGeometry(1.1, 0.05, 0.62),
  leg: new THREE.BoxGeometry(0.05, 0.42, 0.05),
  monitor: new THREE.BoxGeometry(0.52, 0.32, 0.03),
  screen: new THREE.PlaneGeometry(0.47, 0.27),
  stand: new THREE.BoxGeometry(0.04, 0.14, 0.04),
  seat: new THREE.BoxGeometry(0.42, 0.06, 0.42),
  back: new THREE.BoxGeometry(0.42, 0.42, 0.05),
  body: new THREE.CapsuleGeometry(0.15, 0.26, 4, 10),
  head: new THREE.SphereGeometry(0.12, 16, 12),
  light: new THREE.SphereGeometry(0.05, 12, 8),
  keyboard: new THREE.BoxGeometry(0.34, 0.015, 0.11),
};

const matCache = new Map<string, THREE.MeshStandardMaterial>();
function mat(color: string, roughness = 0.7, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) {
  const key = `${color}|${roughness}|${JSON.stringify(extra)}`;
  let m = matCache.get(key);
  if (!m) { m = new THREE.MeshStandardMaterial({ color, roughness, ...extra }); matCache.set(key, m); }
  return m;
}

export type Desk = { group: THREE.Group; setState: (s: AgentState) => void; tick: (t: number) => void };

/** A desk, monitor, chair and a seated worker in `shirt` colour. Faces -z (the worker sits at +z). */
export function makeDesk(p: Palette, shirt: string, seed: number, shadows = false): Desk {
  const g = new THREE.Group();
  const add = (geom: THREE.BufferGeometry, m: THREE.Material, x: number, y: number, z: number) => {
    const mesh = new THREE.Mesh(geom, m);
    mesh.position.set(x, y, z);
    mesh.castShadow = shadows; mesh.receiveShadow = shadows;
    g.add(mesh);
    return mesh;
  };
  add(geo.top, mat(p.deskTop, p.roughness), 0, 0.45, 0);
  for (const [x, z] of [[-0.5, -0.26], [0.5, -0.26], [-0.5, 0.26], [0.5, 0.26]]) add(geo.leg, mat(p.deskLeg), x, 0.22, z);
  add(geo.stand, mat(p.monitor), 0, 0.54, -0.16);
  add(geo.monitor, mat(p.monitor, 0.4), 0, 0.74, -0.17);
  const screenMat = new THREE.MeshBasicMaterial({ color: p.screen });
  const screen = add(geo.screen, screenMat, 0, 0.74, -0.153);
  screen.castShadow = false;
  add(geo.keyboard, mat(p.monitor, 0.5), 0, 0.485, 0.05);
  // chair + worker
  add(geo.seat, mat(p.chair), 0, 0.32, 0.55);
  add(geo.back, mat(p.chair), 0, 0.55, 0.77);
  const body = add(geo.body, mat(shirt, 0.8), 0, 0.62, 0.52);
  const head = add(geo.head, mat(p.skin[seed % p.skin.length], 0.6), 0, 0.98, 0.5);
  const lightMat = new THREE.MeshBasicMaterial({ color: STATE_COLOR.idle });
  const light = add(geo.light, lightMat, 0, 1.22, 0.5);
  light.castShadow = false;

  let state: AgentState = "idle";
  const phase = seed * 1.7;
  return {
    group: g,
    setState(s) {
      state = s;
      lightMat.color.set(STATE_COLOR[s]);
      screenMat.color.set(s === "idle" ? p.monitor : s === "alert" ? "#ff8787" : p.screen);
    },
    tick(t) {
      // working: small typing bob; alert: the light pulses
      const bob = state === "working" ? Math.sin(t * 6 + phase) * 0.012 : 0;
      head.position.y = 0.98 + bob;
      body.rotation.x = state === "working" ? -0.08 : 0.04;
      light.scale.setScalar(state === "alert" || state === "waiting" ? 1 + 0.35 * Math.max(0, Math.sin(t * 4 + phase)) : 1);
    },
  };
}

/** Desk positions in facing pairs, rows of `perRow` pairs. Returns positions, rotations and floor size. */
export function deskLayout(n: number, perRow = 0) {
  if (n === 1) return { spots: [{ x: 0, z: -0.3, ry: 0 }], width: 1.9, depth: 1.9 }; // a single desk: compact floor
  const pairs = Math.ceil(n / 2);
  const cols = perRow || Math.max(1, Math.ceil(Math.sqrt(pairs * 1.6)));
  const rows = Math.ceil(pairs / cols);
  const dx = 1.45, dz = 2.6;
  const out: { x: number; z: number; ry: number }[] = [];
  for (let i = 0; i < n; i++) {
    const pair = Math.floor(i / 2), col = pair % cols, row = Math.floor(pair / cols);
    const facing = i % 2 === 1;
    out.push({
      x: (col - (cols - 1) / 2) * dx,
      z: (row - (rows - 1) / 2) * dz + (facing ? -0.62 : 0.62),
      ry: facing ? Math.PI : 0,
    });
  }
  return { spots: out, width: cols * dx + 1.3, depth: rows * dz + 1.0 };
}

/** Rounded slab, top at y=0. */
export function roundedSlab(w: number, d: number, h: number, r: number, m: THREE.Material | THREE.Material[]) {
  const s = new THREE.Shape();
  const x = -w / 2, y = -d / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + d - r); s.quadraticCurveTo(x + w, y + d, x + w - r, y + d);
  s.lineTo(x + r, y + d); s.quadraticCurveTo(x, y + d, x, y + d - r);
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y);
  const g = new THREE.ExtrudeGeometry(s, { depth: h, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 2, curveSegments: 6 });
  g.rotateX(-Math.PI / 2);
  g.translate(0, -h, 0);
  return new THREE.Mesh(g, m);
}

export type TeamBlock = {
  group: THREE.Group;
  size: { w: number; d: number };
  signature: string;
  update: (team: Team) => void;
  tick: (t: number) => void;
  setHighlight: (on: boolean) => void;
  setWorking: (on: boolean) => void; // glowing border while the team is doing something
};

/** A glowing rounded-rectangle border, lying flat, just outside a w x d platform. */
function workFrame(w: number, d: number, color: string, t = 0.09, pad = 0.18) {
  const outer = new THREE.Shape(), r = 0.45, ow = w + pad * 2, od = d + pad * 2;
  const rr = (sh: THREE.Shape | THREE.Path, x: number, y: number, ww: number, hh: number, rad: number) => {
    sh.moveTo(x + rad, y); sh.lineTo(x + ww - rad, y); sh.quadraticCurveTo(x + ww, y, x + ww, y + rad);
    sh.lineTo(x + ww, y + hh - rad); sh.quadraticCurveTo(x + ww, y + hh, x + ww - rad, y + hh);
    sh.lineTo(x + rad, y + hh); sh.quadraticCurveTo(x, y + hh, x, y + hh - rad); sh.lineTo(x, y + rad); sh.quadraticCurveTo(x, y, x + rad, y);
  };
  rr(outer, -ow / 2, -od / 2, ow, od, r);
  const hole = new THREE.Path();
  rr(hole, -ow / 2 + t, -od / 2 + t, ow - 2 * t, od - 2 * t, r - t);
  outer.holes.push(hole);
  const geo = new THREE.ShapeGeometry(outer, 8);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });
  const m = new THREE.Mesh(geo, mat);
  m.position.y = 0.04;
  return { mesh: m, mat };
}

/** A team: a floor with its desks. `floor` makes the slab material(s) from the team colour. */
export function makeTeamBlock(team: Team, opts: {
  palette: Palette;
  floor: (color: string) => THREE.Material | THREE.Material[];
  thickness?: number;
  shirt?: (color: string, i: number) => string;
  edge?: string | null;
  shadows?: boolean;
  perRow?: number;
  minSize?: { w: number; d: number }; // smallest floor (default 3.2 x 2.8)
  name?: boolean; // paint the team's name on a strip along the floor's front edge
}): TeamBlock {
  const g = new THREE.Group();
  g.userData.teamId = team.id;
  const lay = deskLayout(team.agents.length, opts.perRow);
  const strip = opts.name ? 1.3 : 0; // extra floor in front of the desks for the name
  const w = Math.max(lay.width, opts.minSize?.w ?? 3.2), d = Math.max(lay.depth, opts.minSize?.d ?? 2.8) + strip;
  const slab = roundedSlab(w, d, opts.thickness ?? 0.25, 0.35, opts.floor(team.color));
  slab.receiveShadow = !!opts.shadows;
  g.add(slab);
  let edgeMat: THREE.LineBasicMaterial | null = null;
  if (opts.edge !== null) {
    edgeMat = new THREE.LineBasicMaterial({ color: opts.edge ?? team.color, transparent: true, opacity: 0.9 });
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(slab.geometry, 30), edgeMat);
    g.add(edges);
  }
  const desks = lay.spots.map((s, i) => {
    const desk = makeDesk(opts.palette, opts.shirt ? opts.shirt(team.color, i) : team.color, i + team.id.length, opts.shadows);
    desk.group.position.set(s.x, 0.01, s.z - strip / 2);
    desk.group.rotation.y = s.ry;
    g.add(desk.group);
    return desk;
  });
  if (opts.name) {
    const label = floorName(team.name, team.color, w - 0.4, 1.2);
    label.position.set(0, 0.06, d / 2 - 0.68);
    label.userData.keepColour = true;
    label.userData.nameLabel = true;
    g.add(label);
  }
  const update = (t: Team) => t.agents.forEach((a, i) => desks[i]?.setState(a.state));
  update(team);
  const frame = workFrame(w, d, "#7cf9ff");
  frame.mesh.userData.keepColour = true;
  g.add(frame.mesh);
  let working = false;
  return {
    group: g, size: { w, d },
    signature: `${team.id}:${team.agents.length}`,
    update,
    tick: (t) => {
      desks.forEach((x) => x.tick(t));
      const target = working ? 0.65 + 0.3 * Math.sin(t * 6) : 0;
      frame.mat.opacity += (target - frame.mat.opacity) * 0.15;
    },
    setWorking(on) { working = on; },
    setHighlight(on) {
      g.userData.baseY ??= g.position.y; // lift relative to where it was placed (top-ring platforms float)
      g.position.y = g.userData.baseY + (on ? 0.12 : 0);
      if (edgeMat) edgeMat.opacity = on ? 1 : 0.9;
    },
  };
}

/** Floating HTML card for a team. Clicking it picks the team. */
export function makeCard(team: Team, className: string, stage: Stage, render: (t: Team) => string) {
  const el = document.createElement("div");
  el.className = className;
  el.style.pointerEvents = "auto";
  el.innerHTML = render(team);
  el.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("a")) return; // a link opens its page, not the panel
    stage.onPick(team.id);
  });
  el.addEventListener("pointerenter", () => stage.onHover(team.id));
  el.addEventListener("pointerleave", () => stage.onHover(null));
  const obj = new CSS2DObject(el);
  return { obj, el, update: (t: Team) => { el.innerHTML = render(t); } };
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

/** The standard card body: name, big number, a few rows, optional alert pill. */
export function cardHtml(t: Team, opts: { rows?: number; step?: number; name?: boolean } = {}) {
  const rows = t.rows.slice(0, opts.rows ?? 3).map((r) =>
    `<div class="c-row"><span>${esc(r.label)}</span><b class="${r.tone ?? ""}">${esc(r.value)}</b></div>`).join("");
  const head = opts.name === false ? "" : `<div class="c-head"><i style="background:${t.color}"></i>${opts.step ? `<span class="c-step">${opts.step}</span>` : ""}${esc(t.name)}</div>`;
  return `${head}
    <div class="c-big"><b>${esc(t.headline.value)}</b><span>${esc(t.headline.label)}</span></div>
    ${rows}${t.alert ? `<div class="c-alert">${esc(t.alert)}</div>` : ""}
    ${t.link ? `<a class="c-link" href="${esc(t.link.href)}" target="_blank" rel="noopener noreferrer">${esc(t.link.label)} ↗</a>` : ""}`;
}

/** Glowing particle sphere: the engine that runs every cycle. */
export function makeBrain(color: string, count = 1400, radius = 1.6) {
  const g = new THREE.Group();
  const pos = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    // points spread through a sphere, denser toward the shell
    const u = Math.random(), v = Math.random(), r = radius * Math.cbrt(0.35 + 0.65 * Math.random());
    const th = 2 * Math.PI * u, ph = Math.acos(2 * v - 1);
    pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph) * 0.8, r * Math.sin(ph) * Math.sin(th)], i * 3);
  }
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const points = new THREE.Points(pg, new THREE.PointsMaterial({
    color, size: 0.05, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(radius * 0.55, 1),
    new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.35 }));
  g.add(points, core);
  return {
    group: g,
    tick(t: number, speed = 1) {
      points.rotation.y = t * 0.15 * speed;
      core.rotation.y = -t * 0.25 * speed;
      core.rotation.x = t * 0.1 * speed;
      const s = 1 + Math.sin(t * 2) * 0.03 * speed;
      core.scale.setScalar(s);
    },
  };
}

/** Dot colours for data flows: out = requests and orders, in = prices, fills and results. */
export const FLOW_OUT = "#ffd43b";
export const FLOW_IN = "#66d9e8";
const FLOW_DOWN = "#ff6b6b";
const FLOW_WARN = "#fcc419";

export type FlowState = "active" | "idle" | "warn" | "down";

/**
 * Two-way data link from a to b: a dotted arc per direction, side by side. Yellow dots travel
 * a -> b (out), cyan dots b -> a (in). Busy links move fast, idle ones crawl, a down link turns
 * red and stops.
 */
export function makeFlow(a: THREE.Vector3, b: THREE.Vector3, opts: { color: string; height?: number; dots?: number; lane?: number }) {
  const g = new THREE.Group();
  const side = new THREE.Vector3().subVectors(b, a).cross(new THREE.Vector3(0, 1, 0)).normalize().multiplyScalar(opts.lane ?? 0.14);
  const arc = (from: THREE.Vector3, to: THREE.Vector3, off: THREE.Vector3) => {
    const p0 = from.clone().add(off), p2 = to.clone().add(off);
    const mid = p0.clone().lerp(p2, 0.5);
    mid.y += opts.height ?? 2.5;
    return new THREE.QuadraticBezierCurve3(p0, mid, p2);
  };
  const outCurve = arc(a, b, side), inCurve = arc(b, a, side.clone().negate());
  const lineMat = new THREE.LineDashedMaterial({ color: opts.color, dashSize: 0.12, gapSize: 0.14, transparent: true, opacity: 0.5 });
  for (const c of [outCurve, inCurve]) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(c.getPoints(60)), lineMat);
    line.computeLineDistances();
    g.add(line);
  }
  const dotGeo = new THREE.SphereGeometry(0.07, 8, 6);
  const outMat = new THREE.MeshBasicMaterial({ color: FLOW_OUT });
  const inMat = new THREE.MeshBasicMaterial({ color: FLOW_IN });
  const n = opts.dots ?? 3;
  const mk = (m: THREE.Material) => Array.from({ length: n }, () => { const d = new THREE.Mesh(dotGeo, m); g.add(d); return d; });
  const outs = mk(outMat), ins = mk(inMat);
  let speed = 0.22, shown = n, phase = 0, last = 0;
  const apply = (st: FlowState) => {
    lineMat.color.set(st === "down" ? FLOW_DOWN : st === "warn" ? FLOW_WARN : opts.color);
    lineMat.opacity = st === "down" ? 0.85 : st === "idle" ? 0.28 : 0.55;
    speed = st === "active" ? 0.22 : st === "warn" ? 0.08 : st === "idle" ? 0.05 : 0;
    shown = st === "down" ? 0 : st === "idle" ? 1 : n;
    [...outs, ...ins].forEach((d, i) => { d.visible = i % n < shown; });
  };
  apply("active");
  return {
    group: g,
    setState: apply,
    tick(t: number) {
      phase += (t - last) * speed; // integrate so speed changes don't make dots jump
      last = t;
      for (let i = 0; i < n; i++) {
        const u = (phase + i / n) % 1;
        outs[i].position.copy(outCurve.getPointAt(u));
        ins[i].position.copy(inCurve.getPointAt((u + 0.5 / n) % 1));
      }
    },
  };
}

export function disposeGroup(g: THREE.Object3D) {
  g.traverse((o) => {
    // HTML labels only remove their element when they themselves are removed, not a parent
    if (o instanceof CSS2DObject) o.element.remove();
    const m = o as THREE.Mesh;
    if (m.geometry && !Object.values(geo).includes(m.geometry as never)) m.geometry.dispose();
  });
  g.removeFromParent();
}


/** A team's name painted flat on its floor (turns with the platform). Fits inside maxW x h; long names wrap. */
export function floorName(text: string, color: string, maxW: number, h: number, underline = true) {
  const c = document.createElement("canvas");
  const g = c.getContext("2d")!;
  const font = "900 100px system-ui, sans-serif";
  g.font = font;
  const label = text.toUpperCase();
  const width = (t: string) => g.measureText(t).width;
  // one line, or two split at the best space: whichever gives the bigger letters on the floor
  const lineH = 96;
  const letters = (ls: string[]) => {
    const cw = Math.max(...ls.map(width)) + 40, ch = ls.length * lineH + 22;
    return Math.min(h, (maxW * ch) / cw) / ls.length;
  };
  let lines = [label];
  const words = label.split(" ");
  for (let i = 1; i < words.length; i++) {
    const pair = [words.slice(0, i).join(" "), words.slice(i).join(" ")];
    if (letters(pair) > letters(lines)) lines = pair;
  }
  const cw = Math.ceil(Math.max(...lines.map(width))) + 40;
  c.width = cw; c.height = lines.length * lineH + 22;
  g.font = font;
  g.textAlign = "center"; g.textBaseline = "middle";
  g.shadowColor = color; g.shadowBlur = 20;
  g.fillStyle = "#ffffff";
  lines.forEach((l, i) => g.fillText(l, cw / 2, lineH / 2 + 4 + i * lineH));
  g.shadowBlur = 0;
  g.fillStyle = color;
  if (underline) g.fillRect(20, c.height - 12, cw - 40, 10); // the team's colour under the name
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  let pw = h * (cw / c.height), ph = h;
  if (pw > maxW) { ph *= maxW / pw; pw = maxW; }
  const m = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false }));
  m.rotation.x = -Math.PI / 2;
  return m;
}

/**
 * The Technology team's status on its own platform. OK: a thumbs-up lying on the floor in a corner
 * (grey when unknown). Problem: the thumbs-up goes, everything on the platform turns red and a bright red
 * border flashes around it. Things marked userData.keepColour (name, status strip) stay as they are.
 */
export function techAlarm(block: TeamBlock, iconUrl: string, at: { x: number; z: number }, size: number, spin = 0) {
  const tex = new THREE.TextureLoader().load(iconUrl);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const iconMat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false });
  const icon = new THREE.Mesh(new THREE.PlaneGeometry(size, size), iconMat);
  icon.rotation.set(-Math.PI / 2, 0, spin); // flat on the floor; spin turns it upright as seen from the front
  icon.position.set(at.x, 0.08, at.z); // clear of the floor so they don't fight (flicker)
  icon.renderOrder = 2;
  icon.userData.keepColour = true;
  block.group.add(icon);

  const border = workFrame(block.size.w, block.size.d, "#ff1a1a", 0.22, 0.32);
  border.mesh.userData.keepColour = true;
  block.group.add(border.mesh);

  const red = new THREE.MeshStandardMaterial({ color: "#ff2a2a", emissive: "#ff0000", emissiveIntensity: 0.5, roughness: 0.5 });
  const redLine = new THREE.LineBasicMaterial({ color: "#ff4d4d" });
  const original = new Map<THREE.Mesh | THREE.LineSegments, THREE.Material | THREE.Material[]>();
  let state: "ok" | "bad" | "unknown" = "unknown";
  const paint = (on: boolean) => block.group.traverse((o) => {
    const m = o as THREE.Mesh | THREE.LineSegments;
    if (!(m as THREE.Mesh).isMesh && !(m as THREE.LineSegments).isLineSegments) return;
    if (m.userData.keepColour) return;
    if (on) { if (!original.has(m)) original.set(m, m.material); m.material = (m as THREE.LineSegments).isLineSegments ? redLine : red; }
    else if (original.has(m)) { m.material = original.get(m)!; original.delete(m); }
  });
  return {
    set(s: "ok" | "bad" | "unknown") {
      if (s === state) return;
      state = s;
      icon.visible = s !== "bad";
      iconMat.color.set(s === "ok" ? "#ffffff" : "#5c677d"); // the outline is green; grey when unknown
      iconMat.opacity = s === "ok" ? 1 : 0.55;
      paint(s === "bad");
    },
    tick(t: number) {
      const flash = state === "bad" ? 0.5 + 0.5 * Math.sin(t * 7) : 0;
      border.mat.opacity = state === "bad" ? 0.35 + 0.65 * flash : 0;
      red.emissiveIntensity = 0.35 + 0.45 * flash;
    },
  };
}
