/**
 * The 3D office ("Command Center"): teams on coloured floors in a ring around the engine, the
 * Supervisor's desk at the engine, and the outside systems Darwin talks to (IB Gateway on AI1,
 * IBKR's servers). Every link shows two-way traffic: yellow dots out, cyan dots back.
 */
import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { Stage } from "../stage";
import {
  cardHtml, disposeGroup, makeBrain, makeCard, makeTeamBlock, roundedSlab,
  type Palette, type TeamBlock,
} from "../kit";
import type { LineState, SystemInfo, Team, TeamId, World } from "../teams";
import { useScene } from "../useScene";
import type { ConceptProps } from "./types";

const PALETTE: Palette = {
  deskTop: "#e9ecef", deskLeg: "#495057", monitor: "#212529", screen: "#74c0fc",
  chair: "#343a40", skin: ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"], roughness: 0.6,
};

const HOME = { pos: new THREE.Vector3(0, 32, 49), target: new THREE.Vector3(0, 8.2, 0.8) }; // circle a little below centre, under the logo
// infrastructure shown: fly in to Technology and what it looks after (set once TECH is known, below)
let SHOWN = { pos: new THREE.Vector3(), target: new THREE.Vector3() };

// Three layers of planes: the six teams that run every cycle on the floor (bottom ring), the
// engine's circle floating above them (middle), and a top ring for Supervisor, Review and the
// News Desk. Angles in degrees: 0 = right (towards IB Gateway), 90 = back.
const MID_Y = 7; // height of the engine's circle (high enough to clear the floor teams behind it)
const TOP_Y = 12.5; // height of the top ring
const ANGLE: Record<TeamId | "supervisor", number> = {
  data: 0, scanner: 60, risk: 120, exits: 180, portfolio: 240, trading: 300, // nothing right behind the circle
  supervisor: 135, technology: 45, review: 225, news: 315, // Technology sits on the side facing the infrastructure
};
const TOP = new Set<string>(["supervisor", "review", "news", "technology"]);
const BOTTOM_R = { x: 9.8, z: 9.8 }, TOP_R = { x: 8, z: 8 }; // true circles, so the gaps between platforms are even
const posOf = (id: string) => {
  const top = TOP.has(id), r = top ? TOP_R : BOTTOM_R, a = (ANGLE[id as TeamId] * Math.PI) / 180;
  return new THREE.Vector3(Math.cos(a) * r.x, top ? TOP_Y : 0, -Math.sin(a) * r.z);
};
const BRAIN_Y = 4.4; // the brain floats this high above the circle, clear of the figures on its face
// Outside systems, drawn small just beyond the ring on the right (SYS = their scale).
const SYS = 0.55;
// The infrastructure the Technology team looks after sits just beyond its platform (further out from
// the brain), hidden until the + on the platform is clicked.
const TECH = posOf("technology");
const OUT = new THREE.Vector3(TECH.x, 0, TECH.z).normalize(); // away from the brain
const SIDE = new THREE.Vector3(-OUT.z, 0, OUT.x); // across
const GATEWAY = TECH.clone().addScaledVector(OUT, 5.5);
const ROUTER = TECH.clone().addScaledVector(OUT, 7).addScaledVector(SIDE, 4.6);
const ISP = TECH.clone().addScaledVector(OUT, 10.5).addScaledVector(SIDE, 4.2);
const IBKR = TECH.clone().addScaledVector(OUT, 12.5).addScaledVector(SIDE, -1.2).add(new THREE.Vector3(0, 0.4, 0));
{
  const target = TECH.clone().addScaledVector(OUT, 6).addScaledVector(SIDE, 1.5);
  SHOWN = { target, pos: target.clone().add(new THREE.Vector3(3, 14, 22)) };
}
const LINE: Record<LineState, string> = { ok: "#51cf66", down: "#ff6b6b", unknown: "#5c677d" };
const SUPERVISOR = posOf("supervisor");
const PLAZA_R = 4.2; // radius of the engine's circle

function gatewayBuilding() {
  const g = new THREE.Group();
  const body = roundedSlab(3.6, 2.6, 2.8, 0.25, new THREE.MeshStandardMaterial({ color: "#1d2742", roughness: 0.5, metalness: 0.3 }));
  body.position.y = 2.8;
  g.add(body);
  // glowing rack lights on the front
  const lightMat = new THREE.MeshBasicMaterial({ color: "#74c0fc" });
  for (let r = 0; r < 5; r++) for (let c = 0; c < 3; c++) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.1, 0.02), lightMat);
    m.position.set(-1.05 + c * 1.05, 0.6 + r * 0.45, 1.33);
    g.add(m);
  }
  // status ring on the ground and a spinning antenna
  const ringMat = new THREE.MeshBasicMaterial({ color: "#51cf66" });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(2.6, 0.06, 8, 80), ringMat);
  ring.rotation.x = Math.PI / 2;
  ring.position.y = -0.25;
  g.add(ring);
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 1.2), new THREE.MeshStandardMaterial({ color: "#adb5bd" }));
  mast.position.y = 3.4;
  const dish = new THREE.Mesh(new THREE.TorusGeometry(0.35, 0.04, 8, 32), new THREE.MeshBasicMaterial({ color: "#74c0fc" }));
  dish.position.y = 3.95;
  g.add(mast, dish);
  return {
    group: g,
    setUp(up: boolean) { ringMat.color.set(up ? "#51cf66" : "#ff6b6b"); lightMat.color.set(up ? "#74c0fc" : "#5c2a2a"); },
    tick(t: number) { dish.rotation.y = t * 1.2; dish.rotation.x = Math.sin(t) * 0.3; },
  };
}

function ibkrIsland() {
  const g = new THREE.Group();
  const base = roundedSlab(6.2, 4.4, 0.5, 0.6, new THREE.MeshStandardMaterial({ color: "#2b1d2f", roughness: 0.6, emissive: "#e03131", emissiveIntensity: 0.08 }));
  g.add(base);
  g.add(new THREE.LineSegments(new THREE.EdgesGeometry(base.geometry, 30), new THREE.LineBasicMaterial({ color: "#ff8787" })));
  // server towers with blinking lights
  const towerMat = new THREE.MeshStandardMaterial({ color: "#16131c", roughness: 0.4, metalness: 0.4 });
  const blink: THREE.MeshBasicMaterial[] = [];
  for (const [x, z, h] of [[-1.8, -0.8, 2.6], [-0.6, -0.9, 3.4], [0.6, -0.8, 2.9], [1.8, -0.9, 2.2], [-1.2, 1.0, 1.8], [1.2, 1.0, 2.0]]) {
    const tw = new THREE.Mesh(new THREE.BoxGeometry(0.9, h, 0.9), towerMat);
    tw.position.set(x, h / 2, z);
    g.add(tw);
    for (let k = 0; k < Math.floor(h / 0.5); k++) {
      const m = new THREE.MeshBasicMaterial({ color: "#ff8787" });
      blink.push(m);
      const l = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.05, 0.02), m);
      l.position.set(x, 0.35 + k * 0.5, z + 0.46);
      g.add(l);
    }
  }
  let up: boolean | null = true;
  return {
    group: g,
    setUp(v: boolean | null) { up = v; },
    tick(t: number) {
      blink.forEach((m, i) => {
        const on = Math.sin(t * 3 + i * 1.7) > -0.2;
        m.color.set(up === false ? (on ? "#ff2020" : "#2a0a0a") : up === null ? "#868e96" : on ? "#ff8787" : "#5c1f1f");
      });
      g.position.y = IBKR.y + Math.sin(t * 0.8) * 0.08; // it floats
    },
  };
}

function routerBox() {
  const g = new THREE.Group();
  const body = roundedSlab(1.8, 1.1, 0.45, 0.15, new THREE.MeshStandardMaterial({ color: "#e9ecef", roughness: 0.4 }));
  body.position.y = 0.45;
  g.add(body);
  const antMat = new THREE.MeshStandardMaterial({ color: "#343a40" });
  for (const x of [-0.6, 0, 0.6]) {
    const a = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.9), antMat);
    a.position.set(x, 0.9, -0.4);
    a.rotation.z = x * 0.35;
    g.add(a);
  }
  const ledMat = new THREE.MeshBasicMaterial({ color: LINE.unknown });
  for (let i = 0; i < 5; i++) {
    const l = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.06, 0.02), ledMat);
    l.position.set(-0.5 + i * 0.25, 0.24, 0.56);
    g.add(l);
  }
  const padMat = new THREE.MeshBasicMaterial({ color: LINE.unknown, transparent: true, opacity: 0.35 });
  const pad = new THREE.Mesh(new THREE.RingGeometry(1.25, 1.4, 48), padMat);
  pad.rotation.x = -Math.PI / 2;
  pad.position.y = -0.27;
  g.add(pad);
  return { group: g, set(st: LineState) { ledMat.color.set(LINE[st]); padMat.color.set(LINE[st]); } };
}

function ispTower() {
  const g = new THREE.Group();
  const frame = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.9, 4.2, 4, 6, true),
    new THREE.MeshBasicMaterial({ color: "#8aa4c8", wireframe: true }));
  frame.position.y = 2.1;
  g.add(frame);
  const beaconMat = new THREE.MeshBasicMaterial({ color: LINE.unknown });
  const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.22, 16, 12), beaconMat);
  beacon.position.y = 4.35;
  g.add(beacon);
  const ringMat = new THREE.MeshBasicMaterial({ color: LINE.unknown, transparent: true, opacity: 0.5 });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.03, 8, 40), ringMat);
  ring.position.y = 4.35;
  g.add(ring);
  const pad = new THREE.Mesh(new THREE.RingGeometry(1.2, 1.35, 48), ringMat);
  pad.rotation.x = -Math.PI / 2;
  pad.position.y = -0.27;
  g.add(pad);
  return {
    group: g,
    set(st: LineState) { beaconMat.color.set(LINE[st]); ringMat.color.set(LINE[st]); },
    tick(t: number) { ring.scale.setScalar(1 + ((t * 0.8) % 1) * 1.5); ringMat.opacity = 0.5 * (1 - ((t * 0.8) % 1)); },
  };
}

/** A plain status line (no traffic): green working, red broken, gray unknown. */
/**
 * A small database floating over the Market Data platform: while prices are being saved for
 * backtests it glows, its rings spin and price chips rise from the platform into it.
 */
function dataSaver() {
  const g = new THREE.Group();
  const bodyMat = new THREE.MeshStandardMaterial({ color: "#1b3a4a", roughness: 0.4, metalness: 0.4, emissive: "#22b8cf", emissiveIntensity: 0.05 });
  const ringMat = new THREE.MeshBasicMaterial({ color: "#22b8cf", transparent: true, opacity: 0.35 });
  for (let i = 0; i < 3; i++) { // stacked disks: the usual database symbol
    const disk = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.26, 32), bodyMat);
    disk.position.y = i * 0.32;
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.56, 0.025, 6, 40), ringMat);
    ring.rotation.x = Math.PI / 2;
    ring.position.y = i * 0.32 + 0.13;
    g.add(disk, ring);
  }
  const halo = new THREE.Mesh(new THREE.TorusGeometry(0.85, 0.03, 8, 48), new THREE.MeshBasicMaterial({
    color: "#66d9e8", transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
  halo.rotation.x = Math.PI / 2;
  halo.position.y = 0.32;
  g.add(halo);
  const chipGeo = new THREE.BoxGeometry(0.12, 0.12, 0.12);
  const chipMat = new THREE.MeshBasicMaterial({ color: "#66d9e8", transparent: true });
  const chips = Array.from({ length: 10 }, (_, i) => {
    const m = new THREE.Mesh(chipGeo, chipMat.clone());
    m.userData = { phase: i / 10, angle: (i / 10) * Math.PI * 2 };
    m.visible = false;
    g.add(m);
    return m;
  });
  let on = false;
  return {
    group: g,
    set(saving: boolean) { on = saving; },
    tick(t: number, dt: number) {
      const k = on ? 1 : 0;
      bodyMat.emissiveIntensity += ((on ? 0.6 : 0.05) - bodyMat.emissiveIntensity) * Math.min(1, dt * 4);
      ringMat.opacity = 0.35 + 0.4 * k * (0.5 + 0.5 * Math.sin(t * 6));
      (halo.material as THREE.MeshBasicMaterial).opacity = k * (0.6 - 0.6 * ((t * 1.2) % 1));
      halo.scale.setScalar(1 + ((t * 1.2) % 1) * 0.8 * k);
      g.rotation.y += dt * (on ? 1.5 : 0.2);
      for (const c of chips) {
        c.visible = on;
        if (!on) continue;
        const u = (t * 0.6 + c.userData.phase) % 1; // rise from the platform (-2.2) into the database (0)
        const r = 1.1 * (1 - u);
        c.position.set(Math.cos(c.userData.angle) * r, -2.2 + u * 2.4, Math.sin(c.userData.angle) * r);
        (c.material as THREE.MeshBasicMaterial).opacity = Math.min(1, u * 4) * (1 - Math.max(0, (u - 0.85) / 0.15));
        c.rotation.x += dt * 3;
      }
    },
  };
}

function statusLine(a: THREE.Vector3, b: THREE.Vector3, height = 1.2) {
  const mid = a.clone().lerp(b, 0.5);
  mid.y += height;
  const mat = new THREE.MeshBasicMaterial({ color: LINE.unknown });
  const mesh = new THREE.Mesh(new THREE.TubeGeometry(new THREE.QuadraticBezierCurve3(a, mid, b), 40, 0.06, 6), mat);
  return { group: mesh, set(st: LineState) { mat.color.set(LINE[st]); } };
}

// The order the engine hands out work in a backtest (shown as a fast relay).
const CYCLE = ["data", "scanner", "risk", "trading", "portfolio", "exits", "review"];
// Which teams do each step of a real trading cycle (the trader reports the step it's on).
const STEP_TEAMS: Record<string, string[]> = {
  reconcile: ["portfolio"], manage: ["exits", "portfolio"], scan: ["data", "scanner"], risk: ["risk"],
  orders: ["trading"], collect: ["data"], snapshot: ["portfolio"],
};

/**
 * Task flows on the curved links. A task (a glowing cube in the team's colour) leaves the brain,
 * rides the link to the team, waits there while the team works, and comes back white (done).
 * Only real work is shown: `targets()` names the teams working right now (the trader's current
 * cycle step, or every team in turn during a backtest) and they get tasks until the step ends;
 * while the Supervisor answers, tasks shuttle to its desk; `burst` sends one task to each team
 * given (a cycle that just finished, or the teams the Supervisor looked at).
 */
function taskRunner(root: THREE.Group, curves: Map<string, THREE.CubicBezierCurve3>,
  blockOf: (id: string) => TeamBlock | undefined, targets: () => string[], supervising: () => boolean,
  fast: () => boolean, colourOf: (id: string) => string) {
  const geo = new THREE.BoxGeometry(0.5, 0.5, 0.5);
  const haloGeo = new THREE.SphereGeometry(0.7, 16, 12);
  type Task = { id: string; t: number; phase: "out" | "wait" | "back"; mesh: THREE.Group; mat: THREE.MeshBasicMaterial; done?: () => void };
  const live: Task[] = [];
  const DUR = 1.1, WAIT = 0.5;

  const send = (id: string, done?: () => void) => {
    if (!curves.has(id)) { done?.(); return; }
    const mat = new THREE.MeshBasicMaterial({ color: colourOf(id) });
    const halo = new THREE.Mesh(haloGeo, new THREE.MeshBasicMaterial({
      color: colourOf(id), transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false }));
    const g = new THREE.Group();
    g.add(new THREE.Mesh(geo, mat), halo);
    g.position.copy(curves.get(id)!.getPointAt(0)); // start at the brain, not the origin
    root.add(g);
    live.push({ id, t: 0, phase: "out", mesh: g, mat, done });
  };

  let step = 0, relaying = false, supTask = false;
  const nextRelay = () => {
    const ids = targets();
    if (!ids.length) { relaying = false; return; }
    const id = ids[step % ids.length];
    step++;
    send(id, nextRelay);
  };

  return {
    burst(ids: string[]) { ids.forEach((id, i) => setTimeout(() => send(id), i * 250)); },
    tick(dt: number) {
      if (!relaying && targets().length) { relaying = true; nextRelay(); }
      if (!supTask && supervising()) {
        supTask = true;
        const loop = () => (supervising() ? send("supervisor", loop) : (supTask = false));
        loop();
      }
      for (let i = live.length - 1; i >= 0; i--) {
        const k = live[i];
        const curve = curves.get(k.id)!;
        k.t += (dt * (fast() ? 3 : 1)) / (k.phase === "wait" ? WAIT : DUR); // a backtest relays 3x faster
        if (k.t >= 1) {
          k.t = 0;
          if (k.phase === "out") { k.phase = "wait"; blockOf(k.id)?.setHighlight(true); }
          else if (k.phase === "wait") { k.phase = "back"; k.mat.color.set("#ffffff"); blockOf(k.id)?.setHighlight(false); }
          else { k.mesh.removeFromParent(); live.splice(i, 1); k.done?.(); continue; }
        }
        const u = k.phase === "out" ? k.t : k.phase === "wait" ? 1 : 1 - k.t;
        k.mesh.position.copy(curve.getPointAt(u));
        k.mesh.rotation.y += dt * 3;
      }
    },
  };
}

function build(stage: Stage, world: World) {
  const root = new THREE.Group();
  stage.add(root);

  root.add(new THREE.HemisphereLight("#9db4ff", "#0b1020", 1.4));
  const key = new THREE.DirectionalLight("#ffffff", 1.6);
  key.position.set(8, 16, 10);
  root.add(key);
  const glow = new THREE.PointLight("#5ad8ff", 30, 14, 1.6);
  glow.position.set(0, MID_Y + BRAIN_Y - 0.5, 0);
  root.add(glow);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(160, 160), new THREE.MeshStandardMaterial({ color: "#0d1426", roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.3;
  root.add(ground);
  const grid = new THREE.GridHelper(110, 110, "#1c2a4a", "#141f38");
  grid.position.y = -0.29;
  root.add(grid);
  // thick curved blue links from the edge of the engine's circle to each platform: down to the
  // floor teams, up to the top teams
  // dotted line from the brain to each platform, in the team's colour; tasks travel along it
  const brainAt = new THREE.Vector3(0, MID_Y + BRAIN_Y, -0.3);
  const platformLink = (c: THREE.Vector3, color: string) => {
    const dir = new THREE.Vector3(c.x, 0, c.z).normalize();
    const end = c.clone().sub(dir.clone().multiplyScalar(1.0)).setY(c.y + 0.1); // onto the platform's inner half
    const up = c.y > brainAt.y;
    const curve = new THREE.CubicBezierCurve3(brainAt.clone(), brainAt.clone().add(dir.clone().multiplyScalar(3.5)),
      end.clone().add(new THREE.Vector3(0, up ? 0.8 : 3.2, 0)), end);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(80)),
      new THREE.LineDashedMaterial({ color, dashSize: 0.22, gapSize: 0.18, transparent: true, opacity: 0.85 }));
    line.computeLineDistances();
    root.add(line);
    return curve;
  };
  const linkCurves = new Map<string, THREE.CubicBezierCurve3>();

  const blocks = new Map<string, TeamBlock>();
  const cards = new Map<string, ReturnType<typeof makeCard>>();
  const centers = new Map<string, THREE.Vector3>();

  // ---- teams on the ring
  for (const team of world.teams) {
    const c = posOf(team.id);
    centers.set(team.id, c);
    const block = makeTeamBlock(team, {
      palette: PALETTE,
      floor: (col) => new THREE.MeshStandardMaterial({ color: col, roughness: 0.45, metalness: 0.1, emissive: col, emissiveIntensity: 0.18 }),
      shirt: (col, j) => new THREE.Color(col).lerp(new THREE.Color(j % 2 ? "#ffffff" : "#1a1a1a"), 0.35).getStyle(),
      edge: "#ffffff",
      minSize: team.id === "technology" ? { w: 3.6, d: 4.9 } : undefined, // room for the IBKR status strip
    });
    block.group.position.copy(c);
    block.group.rotation.y = Math.PI / 4;
    root.add(block.group);
    blocks.set(team.id, block);
    const top = TOP.has(team.id);
    linkCurves.set(team.id, platformLink(c, team.color));

    const card = makeCard(team, "cc-card compact", stage, (t) => cardHtml(t, { rows: 1 })); // full details on click
    const out = c.clone().normalize();
    // back teams: card floats above and behind; front teams: card sits below, in front of the floor,
    // so nothing covers the middle (the engine and its summary)
    if (team.id === "technology") card.obj.position.copy(c).add(new THREE.Vector3(4.6, 0.6, 1.2)); // to the right: clear of its strip and the banner
    else if (top) card.obj.position.copy(c).add(new THREE.Vector3((c.x >= 0 ? 1 : -1) * 3.4, 0.4, 0)); // beside its platform
    else if (c.z > 1) card.obj.position.copy(c).add(new THREE.Vector3(out.x * 2.2, -0.3, out.z * 3.2));
    else card.obj.position.copy(c).add(new THREE.Vector3((c.x >= 0 ? 1 : -1) * 3.6, 0, -1.2)); // beside, at floor level, clear of the circle
    root.add(card.obj);
    cards.set(team.id, card);
  }

  // ---- Technology platform: IBKR link status written on its floor, with a + that shows the infrastructure
  const techBlock = blocks.get("technology");
  const techCanvas = document.createElement("canvas");
  techCanvas.width = 1024; techCanvas.height = 256;
  const techTex = new THREE.CanvasTexture(techCanvas);
  techTex.colorSpace = THREE.SRGBColorSpace;
  const techStrip = new THREE.Mesh(new THREE.PlaneGeometry(3.3, 0.82),
    new THREE.MeshBasicMaterial({ map: techTex, transparent: true, depthWrite: false, toneMapped: false }));
  techStrip.rotation.x = -Math.PI / 2;
  techStrip.position.set(0, 0.06, (techBlock?.size.d ?? 4.9) / 2 - 0.55); // along the front edge, just above the floor
  techStrip.userData.teamId = "tech-toggle";
  techBlock?.group.add(techStrip);
  let infraShown = false;
  const renderTech = (w: World) => {
    const g = techCanvas.getContext("2d")!;
    g.clearRect(0, 0, 1024, 256);
    const gwUp = w.brain.gatewayUp, link = w.links.gatewayIbkr;
    const [label, col] = !gwUp ? ["IB GATEWAY OFFLINE", "#ff8787"] : link === "down" ? ["IBKR LINK LOST", "#ff8787"]
      : link === "warn" ? ["IBKR DATA FARM DOWN", "#fcc419"] : ["IBKR LINKED", "#69db7c"];
    g.textBaseline = "middle";
    g.font = "800 92px system-ui, sans-serif";
    g.fillStyle = col;
    g.shadowColor = col;
    g.shadowBlur = 24;
    g.fillText(label, 40, 132);
    // the toggle: + to show the infrastructure, − to hide it
    g.shadowColor = "#5ad8ff";
    g.shadowBlur = 18;
    g.strokeStyle = "#5ad8ff";
    g.lineWidth = 10;
    g.beginPath(); g.arc(912, 128, 78, 0, Math.PI * 2); g.stroke();
    g.fillStyle = "#e7ecf5";
    g.save();
    g.translate(912, 128);
    g.rotate(Math.PI / 4); // the platform is turned 45°: turn the sign back so it reads as + / − from the front
    g.fillRect(-50, -7, 100, 14);
    if (!infraShown) g.fillRect(-7, -50, 14, 100);
    g.restore();
    techTex.needsUpdate = true;
  };
  const setInfra = (show: boolean) => {
    infraShown = show;
    infra.visible = show;
    for (const id of ["gateway", "ibkr", "router", "isp"]) { const c = cards.get(id); if (c) c.obj.visible = show; }
    renderTech(current);
    if (show) stage.flyTo(SHOWN.pos, SHOWN.target); // ease back to fit the infrastructure in
    else stage.flyTo(HOME.pos, HOME.target);
  };
  const pick = stage.onPick;
  stage.onPick = (id) => (id === "tech-toggle" ? setInfra(!infraShown) : pick(id));

  // ---- the engine
  const engine = new THREE.Group();
  engine.userData.teamId = "engine";
  const plaza = new THREE.Mesh(new THREE.CylinderGeometry(PLAZA_R, PLAZA_R + 0.2, 0.2, 64),
    new THREE.MeshStandardMaterial({ color: "#5ad8ff", roughness: 0.3, transparent: true, opacity: 0.08, depthWrite: false }));
  plaza.position.y = -0.2;
  // the ring under the brain: a see-through core line inside two soft halos, all additive so it glows
  const glowMat = (opacity: number) => new THREE.MeshBasicMaterial({
    color: "#5ad8ff", transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const ringCoreMat = glowMat(0.55), ringHaloMat = glowMat(0.16), ringOuterMat = glowMat(0.06);
  const ring = new THREE.Group();
  ring.add(new THREE.Mesh(new THREE.TorusGeometry(PLAZA_R + 0.05, 0.03, 8, 160), ringCoreMat));
  ring.add(new THREE.Mesh(new THREE.TorusGeometry(PLAZA_R + 0.05, 0.11, 10, 160), ringHaloMat));
  ring.add(new THREE.Mesh(new THREE.TorusGeometry(PLAZA_R + 0.05, 0.26, 12, 160), ringOuterMat));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = -0.09;
  const brain = makeBrain("#7ee3ff");
  brain.group.position.set(0, BRAIN_Y, -0.3);
  engine.add(plaza, ring, brain.group);
  // the circle's face: FORECAST (realised + unrealised) in the middle, six figures round the inside edge
  const hudCanvas = document.createElement("canvas");
  hudCanvas.width = hudCanvas.height = 1024;
  const hudTex = new THREE.CanvasTexture(hudCanvas);
  hudTex.colorSpace = THREE.SRGBColorSpace;
  hudTex.anisotropy = 8;
  const hud = new THREE.Mesh(new THREE.CircleGeometry(PLAZA_R - 0.05, 96),
    new THREE.MeshBasicMaterial({ map: hudTex, transparent: true, depthWrite: false, toneMapped: false }));
  hud.rotation.x = -Math.PI / 2; // lying on the circle
  hud.position.y = -0.085;
  engine.add(hud);
  const money = (v: number) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  const pct = (v: number | null, signed = false) => v == null ? "–" : `${signed && v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`;
  const colour = (v: number | null) => (v == null || Math.abs(v) < 1e-9 ? "#e7ecf5" : v > 0 ? "#69db7c" : "#ff8787");
  const STRETCH = 1.55; // text drawn tall: the camera sees the circle at a slant
  const text = (g: CanvasRenderingContext2D, str: string, x: number, y: number, font: string, col: string, glow: string, blur: number) => {
    g.save();
    g.translate(x, y);
    g.scale(1, STRETCH);
    g.font = font;
    g.fillStyle = col;
    g.shadowColor = glow;
    g.shadowBlur = blur;
    g.fillText(str, 0, 0);
    g.restore();
  };
  const renderHud = (w: World) => {
    const m = w.summary;
    const g = hudCanvas.getContext("2d")!;
    g.clearRect(0, 0, 1024, 1024);
    g.textAlign = "center";
    g.textBaseline = "middle";
    const total = m.realized + m.unrealized;
    text(g, "FORECAST P&L", 512, 420, "700 46px system-ui, sans-serif", "#8fd3ff", "rgba(90,216,255,0.9)", 18);
    text(g, money(total), 512, 535, "800 150px system-ui, sans-serif", colour(total), colour(total), 30);
    // round the inside edge, clockwise from the top left (canvas top = far side of the circle)
    const cells: [string, string, string, number][] = [
      ["REALISED", money(m.realized), colour(m.realized), 150], ["UNREALISED", money(m.unrealized), colour(m.unrealized), 90],
      ["OPEN", String(m.open), "#e7ecf5", 30], ["%RPT", pct(m.riskPerTrade), "#e7ecf5", 330],
      ["%DD", pct(m.drawdown), m.drawdown && m.drawdown > 0.05 ? "#ff8787" : "#e7ecf5", 270], ["%TODAY", pct(m.today, true), colour(m.today), 210],
    ];
    for (const [label, value, col, deg] of cells) {
      const a = (deg * Math.PI) / 180, x = 512 + Math.cos(a) * 365, y = 512 - Math.sin(a) * 340;
      text(g, label, x, y - 44, "700 32px system-ui, sans-serif", "#8fd3ff", "rgba(90,216,255,0.8)", 14);
      text(g, value, x, y + 18, "700 60px system-ui, sans-serif", col, col, 18);
    }
    hudTex.needsUpdate = true;
  };
  engine.position.y = MID_Y; // the middle layer
  root.add(engine);
  centers.set("engine", new THREE.Vector3(0, MID_Y + 0.5, 0));

  // ---- the Supervisor: its own small platform on the ring, like the teams', with one desk
  const supTeam = (busy: boolean): Team => ({
    id: "supervisor" as TeamId, name: "Supervisor", job: "", color: "#868e96", rows: [], // gray platform
    headline: { value: "", label: "" }, agents: [{ name: "Supervisor", role: "", state: busy ? "waiting" : "working" }],
  });
  const supBlock = makeTeamBlock(supTeam(false), {
    palette: { ...PALETTE, screen: "#ffe066" },
    floor: (col) => new THREE.MeshStandardMaterial({ color: col, roughness: 0.45, metalness: 0.15, emissive: col, emissiveIntensity: 0.12 }),
    shirt: () => "#ffd43b", // gold shirt so the Supervisor still stands out
    edge: "#ffffff",
    minSize: { w: 3.2, d: 3.6 }, // same size as the smaller team floors
  });
  supBlock.group.position.copy(SUPERVISOR);
  linkCurves.set("supervisor", platformLink(SUPERVISOR, "#ffd43b"));
  supBlock.group.rotation.y = Math.PI / 4;
  root.add(supBlock.group);
  blocks.set("supervisor", supBlock);
  centers.set("supervisor", SUPERVISOR.clone().setY(SUPERVISOR.y + 0.6));
  let supBusy = false;

  // ---- infrastructure (hidden until the + on the Technology platform is clicked)
  const infra = new THREE.Group();
  infra.visible = false;
  root.add(infra);
  const gw = gatewayBuilding();
  gw.group.position.copy(GATEWAY);
  gw.group.scale.setScalar(SYS);
  gw.group.userData.teamId = "gateway";
  gw.group.rotation.y = Math.atan2(-OUT.x, -OUT.z); // front faces the Technology platform
  infra.add(gw.group);
  centers.set("gateway", GATEWAY.clone().add(new THREE.Vector3(0, 0.6, 0)));

  const ib = ibkrIsland();
  ib.group.position.copy(IBKR);
  ib.group.scale.setScalar(SYS);
  ib.group.userData.teamId = "ibkr";
  ib.group.rotation.y = -0.35;
  infra.add(ib.group);
  centers.set("ibkr", IBKR.clone());

  const at = (id: string) => centers.get(id)!.clone();
  // connections to IB Gateway and IBKR: plain status lines (green working, red down)
  // Technology platform -> IB Gateway (Darwin's connection), IB Gateway -> IBKR
  const linkTechGw = statusLine(TECH.clone().addScaledVector(OUT, 2.2).add(new THREE.Vector3(0, 0.1, 0)),
    GATEWAY.clone().add(new THREE.Vector3(0, 0.6, 0)), 0.5);
  const linkGwIbkr = statusLine(GATEWAY.clone().add(new THREE.Vector3(0, 1.7, 0)), IBKR.clone().add(new THREE.Vector3(0, 0.3, 0)), 1.0);
  for (const l of [linkTechGw, linkGwIbkr]) infra.add(l.group);
  const asLine = (st: string): LineState => (st === "down" ? "down" : st === "warn" ? "unknown" : "ok");

  // ---- home network: router and ISP, joined by plain status lines AI1 -> router -> ISP -> internet
  const router = routerBox();
  router.group.position.copy(ROUTER);
  router.group.scale.setScalar(SYS * 1.2);
  router.group.rotation.y = -0.5;
  router.group.userData.teamId = "router";
  infra.add(router.group);
  centers.set("router", ROUTER.clone().add(new THREE.Vector3(0, 0.3, 0)));
  const isp = ispTower();
  isp.group.position.copy(ISP);
  isp.group.scale.setScalar(SYS);
  isp.group.userData.teamId = "isp";
  infra.add(isp.group);
  centers.set("isp", ISP.clone().add(new THREE.Vector3(0, 0.9, 0)));
  const up = (v: THREE.Vector3, y: number) => v.clone().add(new THREE.Vector3(0, y, 0));
  const lineAiRouter = statusLine(up(GATEWAY, 0.3), up(ROUTER, 0.4), 0.7);
  const lineRouterIsp = statusLine(up(ROUTER, 0.4), up(ISP, 0.3), 0.7);
  const lineIspIbkr = statusLine(up(ISP, 2.4), up(IBKR, 0.2), 0.9);
  for (const l of [lineAiRouter, lineRouterIsp, lineIspIbkr]) infra.add(l.group);

  // cards for the outside systems take the same format as the teams'
  const asTeam = (s: SystemInfo) => s as unknown as Team;
  const sysOf = (w: World, id: string) => w.systems.find((x) => x.id === id)!;
  const sysCard = (id: string, pos: THREE.Vector3, rows = 3) => {
    const card = makeCard(asTeam(sysOf(world, id)), `cc-card sys mini sys-${id}`, stage, (t) => cardHtml(t, { rows }));
    card.obj.position.copy(pos);
    card.obj.visible = false; // shown with the rest of the infrastructure
    infra.add(card.obj);
    cards.set(id, card);
  };
  // small cards: name and status only; click for the full details
  sysCard("gateway", GATEWAY.clone().add(new THREE.Vector3(0, -0.4, 0)).addScaledVector(SIDE, -2.2), 0);
  sysCard("ibkr", IBKR.clone().add(new THREE.Vector3(0, -0.6, 0)).addScaledVector(SIDE, -2.4), 0);
  sysCard("router", ROUTER.clone().add(new THREE.Vector3(0, -0.4, 0)).addScaledVector(SIDE, 1.8), 0);
  sysCard("isp", ISP.clone().add(new THREE.Vector3(0, -0.4, 0)).addScaledVector(SIDE, 1.8), 0);
  const supCard = makeCard(asTeam(sysOf(world, "supervisor")), "cc-sup", stage,
    () => `<i></i><b>SUPERVISOR</b><span>Click to chat</span>`);
  supCard.obj.position.copy(SUPERVISOR).add(new THREE.Vector3(0, 2.1, 0));
  root.add(supCard.obj);
  cards.set("supervisor", supCard);

  let speed = 1;
  let current = world;
  let working: string[] = [], fast = false, lastFinished: string | null = null;
  // Market Data: saving prices for backtests
  const saver = dataSaver();
  const dataAt = centers.get("data");
  if (dataAt) {
    saver.group.position.copy(dataAt).add(new THREE.Vector3(0, 2.6, 0));
    root.add(saver.group);
  }
  const saveTagEl = document.createElement("div");
  saveTagEl.className = "cc-save";
  saveTagEl.innerHTML = "<i></i>SAVING PRICES<span>for backtests</span>";
  const saveTag = new CSS2DObject(saveTagEl);
  saveTag.position.copy(saver.group.position).add(new THREE.Vector3(0, 1.5, 0));
  saveTag.visible = false;
  root.add(saveTag);
  const setSaving = (w: World) => {
    const recent = (ts: string | null, s: number) => !!ts && Date.now() - new Date(ts).getTime() < s * 1000;
    const cy = w.brain.cycle;
    const saving = recent(w.brain.pricesSavedAt, 20) || (!!cy && !cy.finished && ["scan", "collect"].includes(cy.step ?? "") && recent(cy.at, 600));
    saver.set(saving);
    saveTag.visible = saving;
  };

  const tasks = taskRunner(root, linkCurves, (id) => blocks.get(id),
    () => working, () => supBusy, () => fast, (id) => world.teams.find((t) => t.id === id)?.color ?? "#ffd43b");
  const apply = (w: World) => {
    w.teams.forEach((t) => { blocks.get(t.id)?.update(t); cards.get(t.id)?.update(t); });
    for (const id of ["gateway", "ibkr", "router", "isp"]) cards.get(id)?.update(asTeam(sysOf(w, id)));
    renderHud(w);
    lineAiRouter.set(w.net.aiRouter);
    lineRouterIsp.set(w.net.routerIsp);
    lineIspIbkr.set(w.net.ispInternet);
    router.set(w.net.aiRouter);
    isp.set(w.net.routerIsp === "ok" ? "ok" : w.net.routerIsp);
    linkTechGw.set(w.brain.gatewayUp ? "ok" : "down");
    renderTech(w);
    linkGwIbkr.set(asLine(w.links.gatewayIbkr));
    setSaving(w);
    // animate only real work: a backtest, or the step the trader is on right now
    const cy = w.brain.cycle;
    const fresh = (ts?: string | null) => !!ts && Date.now() - new Date(ts).getTime() < 10 * 60_000;
    fast = w.brain.backtesting;
    working = fast ? CYCLE : cy && !cy.finished && cy.step && fresh(cy.at) ? STEP_TEAMS[cy.step] ?? [] : [];
    // a cycle that finished since the last update (and recently): replay its steps once
    if (cy?.finished && cy.finished !== lastFinished) {
      if (lastFinished !== null && fresh(cy.finished)) tasks.burst([...new Set(cy.steps.flatMap((x) => STEP_TEAMS[x] ?? []))]);
      lastFinished = cy.finished;
    }
    gw.setUp(w.brain.gatewayUp);
    ib.setUp(!w.brain.gatewayUp ? null : w.links.gatewayIbkr === "down" ? false : true);
    speed = w.brain.marketOpen ? 1 : 0.35;
  };
  apply(world);

  // HTML cards always draw over the 3D scene. Fade a card when, from the current camera, it overlaps
  // the brain on screen and is farther away than the brain (so the brain should hide it).
  const brainCentre = new THREE.Vector3(), cardAt = new THREE.Vector3(), edge = new THREE.Vector3(), camUp = new THREE.Vector3();
  const BRAIN_R = 1.6;
  const occlude = () => {
    const cam = stage.camera, box = stage.renderer.domElement.getBoundingClientRect();
    const toScreen = (v: THREE.Vector3) => {
      const q = v.clone().project(cam);
      return { x: box.left + ((q.x + 1) / 2) * box.width, y: box.top + ((1 - q.y) / 2) * box.height };
    };
    brain.group.getWorldPosition(brainCentre);
    camUp.set(0, 1, 0).applyQuaternion(cam.quaternion);
    const c = toScreen(brainCentre), e = toScreen(edge.copy(brainCentre).addScaledVector(camUp, BRAIN_R));
    const r = Math.hypot(e.x - c.x, e.y - c.y) * 0.9; // brain's radius on screen
    const brainDist = cam.position.distanceTo(brainCentre);
    cards.forEach((card) => {
      card.obj.getWorldPosition(cardAt);
      let behind = false;
      if (cam.position.distanceTo(cardAt) > brainDist) {
        const rc = card.el.getBoundingClientRect();
        const nx = Math.max(rc.left, Math.min(c.x, rc.right)), ny = Math.max(rc.top, Math.min(c.y, rc.bottom));
        behind = Math.hypot(nx - c.x, ny - c.y) < r; // the card's rectangle reaches into the brain's disc
      }
      if (card.el.classList.contains("behind") !== behind) card.el.classList.toggle("behind", behind);
    });
  };

  const off = stage.onTick((dt, t) => {
    occlude();
    brain.tick(t, supBusy ? 1.6 : speed);
    const pulse = 0.5 + 0.5 * Math.sin(t * (supBusy ? 4 : 1.6)); // the ring breathes
    ringCoreMat.opacity = 0.4 + 0.25 * pulse;
    ringHaloMat.opacity = 0.1 + 0.12 * pulse;
    ringOuterMat.opacity = 0.03 + 0.06 * pulse;
    tasks.tick(dt);
    saver.tick(t, dt);
    blocks.forEach((b) => b.tick(t));
    gw.tick(t);
    isp.tick(t);
    ib.tick(t);
  });

  stage.flyTo(HOME.pos, HOME.target);
  let sel: string | null = null;
  return {
    update(w: World) { current = w; apply(w); },
    select(id: string | null) {
      sel = id;
      cards.forEach((c, k) => c.el.classList.toggle("selected", k === id));
      const c = id ? centers.get(id) : null;
      if (c) stage.focus(c, ["ibkr", "gateway", "isp", "router"].includes(id!) ? 9 : 13);
      else stage.flyTo(HOME.pos, HOME.target);
    },
    hover(id: string | null) {
      blocks.forEach((b, k) => b.setHighlight(k === id || k === sel));
      cards.forEach((c, k) => c.el.classList.toggle("hover", k === id));
    },
    taskBurst(ids: string[]) { tasks.burst(ids); },
    setBusy(busy: boolean) {
      supBusy = busy;
      supBlock.update(supTeam(busy));
      apply(current);
    },
    dispose() { off(); stage.onPick = pick; disposeGroup(root); },
  };
}

export default function CommandCenter(p: ConceptProps) {
  const ref = useScene(build, (el) => new Stage(el, {
    background: "#0a0f1e", fog: { color: "#0a0f1e", near: 40, far: 95 }, toneMapping: true,
    camera: { position: [0, 28, 56], target: [0, 5.8, 0.5], fov: 34 },
    limits: { minDistance: 6, maxDistance: 70, maxPolarAngle: Math.PI * 0.42 },
  }), p.world, p.selected, p.onSelect, p.onHover, p.busy, p.burst);
  return <div ref={ref} className="scene3d theme-cc" />;
}
