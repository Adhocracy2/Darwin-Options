/**
 * Concept 3 · Assembly Line: the teams in pipeline order along a glowing track. Trade ideas
 * travel the track; at the Risk Desk the blocked share (from the last real scan) turns red and
 * drops off. Chips on the track show the funnel: ideas -> passed -> ordered.
 */
import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { Stage } from "../stage";
import { cardHtml, disposeGroup, makeBrain, makeCard, makeTeamBlock, type Palette, type TeamBlock } from "../kit";
import { ORDER, type World } from "../teams";
import { useScene } from "../useScene";
import type { ConceptProps } from "./types";

const PALETTE: Palette = {
  deskTop: "#dee2e6", deskLeg: "#343a40", monitor: "#1a1d21", screen: "#63e6be",
  chair: "#2b3036", skin: ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"], roughness: 0.6,
};

const HOME = { pos: new THREE.Vector3(0, 25, 26), target: new THREE.Vector3(0, 0, 1.6) };
const IDEA = new THREE.Color("#74c0fc"), PASSED = new THREE.Color("#69db7c"), BLOCKED = new THREE.Color("#ff6b6b"), IDLE = new THREE.Color("#868e96");

function build(stage: Stage, world: World) {
  const root = new THREE.Group();
  stage.add(root);
  root.add(new THREE.HemisphereLight("#cfe3ff", "#101418", 1.5));
  const key = new THREE.DirectionalLight("#ffffff", 1.8);
  key.position.set(-6, 18, 12);
  root.add(key);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(140, 140), new THREE.MeshStandardMaterial({ color: "#14191f", roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.3;
  root.add(ground);

  // stations: top row left->right, bottom row right->left (a loop)
  const ids = ORDER.filter((id) => world.teams.some((t) => t.id === id));
  const top = ids.slice(0, 4), bottom = ids.slice(4);
  const station = new Map<string, { center: THREE.Vector3; track: THREE.Vector3 }>();
  top.forEach((id, i) => {
    const x = (i - (top.length - 1) / 2) * 10;
    station.set(id, { center: new THREE.Vector3(x, 0, -6.8), track: new THREE.Vector3(x, 0.15, -2.6) });
  });
  bottom.forEach((id, i) => {
    const x = ((bottom.length - 1) / 2 - i) * 10;
    station.set(id, { center: new THREE.Vector3(x, 0, 6.8), track: new THREE.Vector3(x, 0.15, 2.6) });
  });
  const ends = top.length ? (top.length - 1) / 2 * 10 + 4.5 : 15;
  const pts = [...top.map((id) => station.get(id)!.track), new THREE.Vector3(ends, 0.15, 0),
               ...bottom.map((id) => station.get(id)!.track), new THREE.Vector3(-ends, 0.15, 0)];
  const curve = new THREE.CatmullRomCurve3(pts, true, "centripetal");

  // the track: a dark road with a glowing rail
  const road = new THREE.Mesh(new THREE.TubeGeometry(curve, 400, 0.55, 6, true), new THREE.MeshStandardMaterial({ color: "#1f262e", roughness: 0.9 }));
  road.scale.y = 0.08;
  road.position.y = -0.25;
  root.add(road);
  const rail = new THREE.Mesh(new THREE.TubeGeometry(curve, 400, 0.05, 6, true), new THREE.MeshBasicMaterial({ color: "#3bc9db" }));
  root.add(rail);

  // where each station sits along the track (0..1)
  const samples = curve.getSpacedPoints(800);
  const uOf = (p: THREE.Vector3) => {
    let best = 0;
    samples.forEach((s, i) => { if (s.distanceToSquared(p) < samples[best].distanceToSquared(p)) best = i; });
    return best / 800;
  };
  const uRisk = station.has("risk") ? uOf(station.get("risk")!.track) : 0.35;
  const uData = station.has("data") ? uOf(station.get("data")!.track) : 0;

  const blocks = new Map<string, TeamBlock>();
  const cards = new Map<string, ReturnType<typeof makeCard>>();
  ids.forEach((id, i) => {
    const team = world.teams.find((t) => t.id === id)!;
    const s = station.get(id)!;
    const block = makeTeamBlock(team, {
      palette: PALETTE, perRow: 3, edge: team.color,
      floor: (col) => [
        new THREE.MeshStandardMaterial({ color: "#232a33", roughness: 0.8 }),
        new THREE.MeshStandardMaterial({ color: new THREE.Color(col).lerp(new THREE.Color("#232a33"), 0.55), roughness: 0.6 }),
      ],
      shirt: (col) => col,
    });
    block.group.position.copy(s.center);
    root.add(block.group);
    blocks.set(id, block);
    // a short stub from the floor to the track
    const stub = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.04, Math.abs(s.track.z - s.center.z) - block.size.d / 2),
      new THREE.MeshBasicMaterial({ color: team.color }));
    stub.position.set(s.center.x, -0.2, (s.track.z + s.center.z + Math.sign(s.track.z - s.center.z) * block.size.d / 2) / 2);
    root.add(stub);

    const card = makeCard(team, "pl-card", stage, (t) => cardHtml(t, { rows: 2, step: i + 1 }));
    const away = Math.sign(s.center.z);
    // top row: card floats behind the floor; bottom row: card sits in front so it doesn't cover the desks
    if (away < 0) card.obj.position.set(s.center.x, 2.8, s.center.z - (block.size.d / 2 + 0.6));
    else card.obj.position.set(s.center.x, 0, s.center.z + block.size.d / 2 + 2.2);
    root.add(card.obj);
    cards.set(id, card);
  });

  // funnel chips on the track
  const chip = (cls: string) => {
    const el = document.createElement("div");
    el.className = `pl-chip ${cls}`;
    const o = new CSS2DObject(el);
    root.add(o);
    return { el, o };
  };
  const chips = { ideas: chip("ideas"), passed: chip("passed"), ordered: chip("ordered") };
  const between = (a: string, b: string) => {
    const pa = station.get(a)?.track, pb = station.get(b)?.track;
    return pa && pb ? pa.clone().lerp(pb, 0.5).setY(0.9) : new THREE.Vector3();
  };
  chips.ideas.o.position.copy(between("scanner", "risk"));
  chips.passed.o.position.copy(between("risk", "trading"));
  chips.ordered.o.position.copy(station.get("trading")?.track.clone().add(new THREE.Vector3(3.2, 0.9, 1.3)) ?? new THREE.Vector3());

  // the engine in the middle of the loop
  const brain = makeBrain("#63e6be", 900, 1.2);
  brain.group.position.set(0, 1.5, 0);
  root.add(brain.group);

  // packets
  const packetGeo = new THREE.BoxGeometry(0.28, 0.2, 0.28);
  type Packet = { mesh: THREE.Mesh; mat: THREE.MeshBasicMaterial; u: number; state: "idea" | "passed" | "blocked"; fall: number; decided: boolean };
  const packets: Packet[] = Array.from({ length: 36 }, (_, i) => {
    const mat = new THREE.MeshBasicMaterial({ color: IDEA, transparent: true });
    const mesh = new THREE.Mesh(packetGeo, mat);
    root.add(mesh);
    return { mesh, mat, u: (uData + i / 36) % 1, state: "idea", fall: 0, decided: false };
  });

  let flow = world.flow, open = world.brain.marketOpen;
  const blockShare = () => (flow.scanned ? 1 - flow.passedChecks / flow.scanned : 0);
  const renderChips = () => {
    chips.ideas.el.innerHTML = `<b>${flow.scanned}</b> ideas`;
    chips.passed.el.innerHTML = `<b>${flow.passedChecks}</b> passed${flow.topBlock && !flow.passedChecks ? `<span>${flow.topBlock}</span>` : ""}`;
    chips.ordered.el.innerHTML = `<b>${flow.ordered}</b> ordered today`;
  };
  renderChips();

  const passes = (from: number, to: number, mark: number) => (from <= to ? from < mark && mark <= to : from < mark || mark <= to);
  const off = stage.onTick((dt, t) => {
    const speed = (open ? 0.045 : 0.018) * (flow.scanned ? 1 : 0.6);
    brain.tick(t, open ? 1 : 0.4);
    blocks.forEach((b) => b.tick(t));
    for (const p of packets) {
      if (p.state === "blocked") {
        p.fall += dt;
        p.mesh.position.y -= dt * 2.2;
        p.mesh.rotation.x += dt * 4;
        p.mat.opacity = Math.max(0, 1 - p.fall / 1.2);
        if (p.fall > 1.2) { p.state = "idea"; p.u = uData; p.fall = 0; p.decided = false; p.mat.opacity = 1; p.mesh.rotation.set(0, 0, 0); }
        continue;
      }
      const next = (p.u + speed * dt) % 1;
      if (!p.decided && passes(p.u, next, uRisk)) {
        p.decided = true;
        p.state = Math.random() < blockShare() ? "blocked" : "passed";
        if (p.state === "blocked") p.mat.color.copy(BLOCKED);
      }
      if (passes(p.u, next, uData)) { p.state = "idea"; p.decided = false; }
      p.u = next;
      if (p.state === "blocked") continue;
      const pos = curve.getPointAt(p.u);
      p.mesh.position.set(pos.x, 0.32 + Math.sin(t * 5 + p.u * 40) * 0.03, pos.z);
      p.mat.color.copy(!flow.scanned ? IDLE : p.state === "passed" ? PASSED : IDEA);
    }
  });

  stage.flyTo(HOME.pos, HOME.target);
  let sel: string | null = null;
  return {
    update(w: World) {
      flow = w.flow; open = w.brain.marketOpen;
      w.teams.forEach((t) => { blocks.get(t.id)?.update(t); cards.get(t.id)?.update(t); });
      renderChips();
    },
    select(id: string | null) {
      sel = id;
      cards.forEach((c, k) => c.el.classList.toggle("selected", k === id));
      const s = id ? station.get(id) : null;
      if (s) stage.focus(s.center.clone().lerp(s.track, 0.35), 13);
      else stage.flyTo(HOME.pos, HOME.target);
    },
    hover(id: string | null) {
      blocks.forEach((b, k) => b.setHighlight(k === id || k === sel));
      cards.forEach((c, k) => c.el.classList.toggle("hover", k === id));
    },
    dispose() { off(); disposeGroup(root); },
  };
}

export default function Pipeline(p: ConceptProps) {
  const ref = useScene(build, (el) => new Stage(el, {
    background: "#101418", fog: { color: "#101418", near: 34, far: 80 }, toneMapping: true,
    camera: { position: [0, 34, 38], target: [0, 0, 0.5], fov: 34 },
    limits: { minDistance: 8, maxDistance: 60, maxPolarAngle: Math.PI * 0.42 },
  }), p.world, p.selected, p.onSelect, p.onHover);
  return <div ref={ref} className="scene3d theme-pl" />;
}
