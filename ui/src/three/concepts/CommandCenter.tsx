/**
 * The 3D office ("Command Center"): teams on coloured floors in a ring around the engine, the
 * Supervisor's desk at the engine, and the outside systems Darwin talks to (IB Gateway on AI1,
 * IBKR's servers). Every link shows two-way traffic: yellow dots out, cyan dots back.
 */
import * as THREE from "three";
import { Stage } from "../stage";
import {
  cardHtml, disposeGroup, makeBrain, makeCard, makeDesk, makeFlow, makeTeamBlock, roundedSlab,
  type Palette, type TeamBlock,
} from "../kit";
import type { SystemInfo, Team, TeamId, World } from "../teams";
import { useScene } from "../useScene";
import type { ConceptProps } from "./types";

const PALETTE: Palette = {
  deskTop: "#e9ecef", deskLeg: "#495057", monitor: "#212529", screen: "#74c0fc",
  chair: "#343a40", skin: ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"], roughness: 0.6,
};

const HOME = { pos: new THREE.Vector3(9, 29, 35), target: new THREE.Vector3(7.5, 0, -0.8) };

// Where each team sits on the ring (degrees, 0 = right, 90 = back). Market Data, Trading and
// Portfolio face IB Gateway on the right; the rest follow the pipeline round the back.
const ANGLE: Record<TeamId, number> = { data: 28, scanner: 80, risk: 132, review: 184, exits: 236, portfolio: 290, trading: 336 };
const GATEWAY = new THREE.Vector3(19, 0, 0.8);
const IBKR = new THREE.Vector3(27.5, 2.6, -7);
const SUPERVISOR = new THREE.Vector3(0, 0, 2.05);

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
      g.position.y = IBKR.y + Math.sin(t * 0.8) * 0.12; // it floats
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
  glow.position.set(0, 1.5, 0);
  root.add(glow);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(160, 160), new THREE.MeshStandardMaterial({ color: "#0d1426", roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.3;
  root.add(ground);
  const grid = new THREE.GridHelper(110, 110, "#1c2a4a", "#141f38");
  grid.position.y = -0.29;
  root.add(grid);
  const roadMat = new THREE.MeshStandardMaterial({ color: "#1b2540", roughness: 0.9 });
  const road = (from: THREE.Vector3, to: THREE.Vector3, width = 1.1) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(width, 0.04, from.distanceTo(to)), roadMat);
    m.position.copy(from.clone().lerp(to, 0.5)).setY(-0.27);
    m.lookAt(to.clone().setY(-0.27));
    root.add(m);
  };

  const blocks = new Map<string, TeamBlock>();
  const cards = new Map<string, ReturnType<typeof makeCard>>();
  const centers = new Map<string, THREE.Vector3>();
  const teamFlows: ReturnType<typeof makeFlow>[] = [];

  // ---- teams on the ring
  for (const team of world.teams) {
    const a = (ANGLE[team.id] * Math.PI) / 180;
    const c = new THREE.Vector3(Math.cos(a) * 10.5, 0, -Math.sin(a) * 7.5);
    centers.set(team.id, c);
    const block = makeTeamBlock(team, {
      palette: PALETTE,
      floor: (col) => new THREE.MeshStandardMaterial({ color: col, roughness: 0.45, metalness: 0.1, emissive: col, emissiveIntensity: 0.18 }),
      shirt: (col, j) => new THREE.Color(col).lerp(new THREE.Color(j % 2 ? "#ffffff" : "#1a1a1a"), 0.35).getStyle(),
      edge: "#ffffff",
    });
    block.group.position.copy(c);
    block.group.rotation.y = Math.PI / 4;
    root.add(block.group);
    blocks.set(team.id, block);
    road(c.clone().multiplyScalar(2.6 / c.length()), c.clone().multiplyScalar(1 - 2.2 / c.length()));

    const f = makeFlow(c.clone().setY(1.4), new THREE.Vector3(0, 1.6, -0.3), { color: team.color, height: 3.2 });
    root.add(f.group);
    teamFlows.push(f);

    const card = makeCard(team, "cc-card", stage, (t) => cardHtml(t));
    const out = c.clone().normalize();
    card.obj.position.copy(c).add(new THREE.Vector3(out.x * 1.5, 3.4, out.z * 1.2));
    root.add(card.obj);
    cards.set(team.id, card);
  }

  // ---- the engine
  const engine = new THREE.Group();
  engine.userData.teamId = "engine";
  const plaza = new THREE.Mesh(new THREE.CylinderGeometry(3, 3.2, 0.2, 48), new THREE.MeshStandardMaterial({ color: "#16203a", roughness: 0.7 }));
  plaza.position.y = -0.2;
  const ring = new THREE.Mesh(new THREE.TorusGeometry(3.05, 0.03, 8, 96), new THREE.MeshBasicMaterial({ color: "#5ad8ff" }));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = -0.09;
  const brain = makeBrain("#7ee3ff");
  brain.group.position.set(0, 1.7, -0.3);
  engine.add(plaza, ring, brain.group);
  root.add(engine);
  centers.set("engine", new THREE.Vector3(0, 0.5, 0));

  // ---- the Supervisor: a desk on a podium at the front of the plaza, facing the engine
  const sup = new THREE.Group();
  sup.userData.teamId = "supervisor";
  sup.position.copy(SUPERVISOR);
  const podium = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 1.05, 0.16, 40), new THREE.MeshStandardMaterial({ color: "#2b2410", roughness: 0.5, metalness: 0.4 }));
  podium.position.set(0, 0, 0.25);
  const podiumRing = new THREE.Mesh(new THREE.TorusGeometry(1.0, 0.03, 8, 64), new THREE.MeshBasicMaterial({ color: "#ffd43b" }));
  podiumRing.rotation.x = Math.PI / 2;
  podiumRing.position.set(0, 0.09, 0.25);
  const supDesk = makeDesk({ ...PALETTE, screen: "#ffe066" }, "#e8b923", 3);
  supDesk.group.position.y = 0.08;
  sup.add(podium, podiumRing, supDesk.group);
  root.add(sup);
  centers.set("supervisor", SUPERVISOR.clone().setY(0.6));
  const supFlow = makeFlow(SUPERVISOR.clone().setY(1.3), new THREE.Vector3(0, 1.6, -0.3), { color: "#ffd43b", height: 1.4, dots: 2, lane: 0.1 });
  root.add(supFlow.group);
  let supBusy = false;

  // ---- IB Gateway (on AI1) and IBKR (their servers)
  const gw = gatewayBuilding();
  gw.group.position.copy(GATEWAY);
  gw.group.userData.teamId = "gateway";
  gw.group.rotation.y = -Math.PI / 2 + 0.25;
  root.add(gw.group);
  centers.set("gateway", GATEWAY.clone().setY(1));
  road(new THREE.Vector3(13.5, 0, 0.5), GATEWAY.clone().add(new THREE.Vector3(-2.2, 0, 0)), 1.6);

  const ib = ibkrIsland();
  ib.group.position.copy(IBKR);
  ib.group.userData.teamId = "ibkr";
  ib.group.rotation.y = -0.35;
  root.add(ib.group);
  centers.set("ibkr", IBKR.clone());

  const at = (id: string) => centers.get(id)!.clone();
  const linkDataGw = makeFlow(at("data").setY(1.3), GATEWAY.clone().setY(2.2), { color: "#22b8cf", height: 2.6 });
  const linkTradeGw = makeFlow(at("trading").setY(1.3), GATEWAY.clone().setY(1.6), { color: "#fab005", height: 2.2 });
  const linkPortGw = makeFlow(at("portfolio").setY(1.3), GATEWAY.clone().setY(1.0), { color: "#40c057", height: 2.8 });
  const linkGwIbkr = makeFlow(GATEWAY.clone().setY(3.2), IBKR.clone().setY(IBKR.y + 0.6), { color: "#ff8787", height: 3.2, dots: 4, lane: 0.2 });
  for (const f of [linkDataGw, linkTradeGw, linkPortGw, linkGwIbkr]) root.add(f.group);

  // cards for the outside systems take the same format as the teams'
  const asTeam = (s: SystemInfo) => s as unknown as Team;
  const sysOf = (w: World, id: string) => w.systems.find((x) => x.id === id)!;
  const sysCard = (id: string, pos: THREE.Vector3) => {
    const card = makeCard(asTeam(sysOf(world, id)), "cc-card sys", stage, (t) => cardHtml(t));
    card.obj.position.copy(pos);
    root.add(card.obj);
    cards.set(id, card);
  };
  sysCard("gateway", GATEWAY.clone().add(new THREE.Vector3(0.5, 5.6, 0)));
  sysCard("ibkr", IBKR.clone().add(new THREE.Vector3(0, 5.6, 0)));
  const supCard = makeCard(asTeam(sysOf(world, "supervisor")), "cc-sup", stage,
    () => `<i></i><b>SUPERVISOR</b><span>Click to chat</span>`);
  supCard.obj.position.copy(SUPERVISOR).add(new THREE.Vector3(0, 2.2, 0.4));
  root.add(supCard.obj);
  cards.set("supervisor", supCard);

  let speed = 1;
  let current = world;
  const apply = (w: World) => {
    w.teams.forEach((t) => { blocks.get(t.id)?.update(t); cards.get(t.id)?.update(t); });
    for (const id of ["gateway", "ibkr"]) cards.get(id)?.update(asTeam(sysOf(w, id)));
    teamFlows.forEach((f) => f.setState(w.links.teamEngine));
    linkDataGw.setState(w.links.dataGateway);
    linkTradeGw.setState(w.links.tradingGateway);
    linkPortGw.setState(w.links.portfolioGateway);
    linkGwIbkr.setState(w.links.gatewayIbkr);
    gw.setUp(w.brain.gatewayUp);
    ib.setUp(!w.brain.gatewayUp ? null : w.links.gatewayIbkr === "down" ? false : true);
    supFlow.setState(supBusy ? "active" : "idle");
    speed = w.brain.marketOpen ? 1 : 0.35;
  };
  apply(world);
  supDesk.setState("working");

  const off = stage.onTick((_dt, t) => {
    brain.tick(t, supBusy ? 1.6 : speed);
    for (const f of [...teamFlows, linkDataGw, linkTradeGw, linkPortGw, linkGwIbkr, supFlow]) f.tick(t);
    blocks.forEach((b) => b.tick(t));
    supDesk.tick(t);
    gw.tick(t);
    ib.tick(t);
    podiumRing.scale.setScalar(supBusy ? 1 + 0.06 * Math.sin(t * 6) : 1);
  });

  stage.flyTo(HOME.pos, HOME.target);
  let sel: string | null = null;
  return {
    update(w: World) { current = w; apply(w); },
    select(id: string | null) {
      sel = id;
      cards.forEach((c, k) => c.el.classList.toggle("selected", k === id));
      const c = id ? centers.get(id) : null;
      if (c) stage.focus(c, id === "ibkr" || id === "gateway" ? 15 : id === "supervisor" ? 9 : 13);
      else stage.flyTo(HOME.pos, HOME.target);
    },
    hover(id: string | null) {
      blocks.forEach((b, k) => b.setHighlight(k === id || k === sel));
      cards.forEach((c, k) => c.el.classList.toggle("hover", k === id));
    },
    setBusy(busy: boolean) {
      supBusy = busy;
      supDesk.setState(busy ? "waiting" : "working");
      apply(current);
    },
    dispose() { off(); disposeGroup(root); },
  };
}

export default function CommandCenter(p: ConceptProps) {
  const ref = useScene(build, (el) => new Stage(el, {
    background: "#0a0f1e", fog: { color: "#0a0f1e", near: 40, far: 95 }, toneMapping: true,
    camera: { position: [11, 38, 46], target: [7.5, 0, -0.8], fov: 34 },
    limits: { minDistance: 6, maxDistance: 70, maxPolarAngle: Math.PI * 0.42 },
  }), p.world, p.selected, p.onSelect, p.onHover, p.busy);
  return <div ref={ref} className="scene3d theme-cc" />;
}
