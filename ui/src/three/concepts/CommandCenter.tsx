/**
 * Concept 1 · Command Center: dark, neon, isometric. Teams on coloured floors in a ring around
 * the engine, joined by roads and dotted data lines; glass cards float beside each team.
 */
import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { Stage } from "../stage";
import { cardHtml, disposeGroup, makeBrain, makeCard, makeFlow, makeTeamBlock, type Palette, type TeamBlock } from "../kit";
import { ORDER, type World } from "../teams";
import { useScene } from "../useScene";
import type { ConceptProps } from "./types";

const PALETTE: Palette = {
  deskTop: "#e9ecef", deskLeg: "#495057", monitor: "#212529", screen: "#74c0fc",
  chair: "#343a40", skin: ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"], roughness: 0.6,
};

const HOME = { pos: new THREE.Vector3(0, 21, 24), target: new THREE.Vector3(0, 0, 1) };

function build(stage: Stage, world: World) {
  const root = new THREE.Group();
  stage.add(root);

  // lights: cool ambient, a key light, coloured rim from the brain
  root.add(new THREE.HemisphereLight("#9db4ff", "#0b1020", 1.4));
  const key = new THREE.DirectionalLight("#ffffff", 1.6);
  key.position.set(8, 16, 10);
  root.add(key);
  const glow = new THREE.PointLight("#5ad8ff", 30, 14, 1.6);
  glow.position.set(0, 1.5, 0);
  root.add(glow);

  // floor: dark ground with a faint grid
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), new THREE.MeshStandardMaterial({ color: "#0d1426", roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.3;
  root.add(ground);
  const grid = new THREE.GridHelper(80, 80, "#1c2a4a", "#141f38");
  grid.position.y = -0.29;
  root.add(grid);

  // teams on an ellipse, in pipeline order, starting top-left
  const ids = ORDER.filter((id) => world.teams.some((t) => t.id === id));
  const blocks = new Map<string, TeamBlock>();
  const cards = new Map<string, ReturnType<typeof makeCard>>();
  const flows: ReturnType<typeof makeFlow>[] = [];
  const centers = new Map<string, THREE.Vector3>();
  const roadMat = new THREE.MeshStandardMaterial({ color: "#1b2540", roughness: 0.9 });
  ids.forEach((id, i) => {
    const team = world.teams.find((t) => t.id === id)!;
    const a = Math.PI * 0.85 - (i / ids.length) * Math.PI * 2;
    const c = new THREE.Vector3(Math.cos(a) * 10.5, 0, -Math.sin(a) * 7.5);
    centers.set(id, c);
    const block = makeTeamBlock(team, {
      palette: PALETTE,
      floor: (col) => new THREE.MeshStandardMaterial({ color: col, roughness: 0.45, metalness: 0.1, emissive: col, emissiveIntensity: 0.18 }),
      shirt: (col, j) => new THREE.Color(col).lerp(new THREE.Color(j % 2 ? "#ffffff" : "#1a1a1a"), 0.35).getStyle(),
      edge: "#ffffff",
    });
    block.group.position.copy(c);
    block.group.rotation.y = Math.PI / 4; // isometric: every floor at the same angle
    root.add(block.group);
    blocks.set(id, block);

    // road from the team to the hub
    const len = c.length() - 2.2;
    const road = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.04, len), roadMat);
    road.position.copy(c.clone().multiplyScalar(0.5));
    road.position.y = -0.27;
    road.lookAt(new THREE.Vector3(0, -0.27, 0));
    root.add(road);

    // data line team -> engine
    const f = makeFlow(c.clone().setY(1.4), new THREE.Vector3(0, 1.6, 0), { color: team.color, height: 3.2, dots: 3, speed: 0.18 + i * 0.01 });
    root.add(f.group);
    flows.push(f);

    // card floats above the outer edge
    const card = makeCard(team, "cc-card", stage, (t) => cardHtml(t));
    const out = c.clone().normalize();
    card.obj.position.copy(c).add(new THREE.Vector3(out.x * 1.5, 3.4, out.z * 1.2));
    root.add(card.obj);
    cards.set(id, card);
  });

  // hub: dark plaza with the engine
  const plaza = new THREE.Mesh(new THREE.CylinderGeometry(3, 3.2, 0.2, 48), new THREE.MeshStandardMaterial({ color: "#16203a", roughness: 0.7 }));
  plaza.position.y = -0.2;
  root.add(plaza);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(3.05, 0.03, 8, 96), new THREE.MeshBasicMaterial({ color: "#5ad8ff" }));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = -0.09;
  root.add(ring);
  const brain = makeBrain("#7ee3ff");
  brain.group.position.set(0, 1.7, 0);
  root.add(brain.group);
  const hubEl = document.createElement("div");
  hubEl.className = "cc-hub";
  const hub = new CSS2DObject(hubEl);
  hub.position.set(0, 0.1, 3.6); // front edge of the plaza, clear of the floors behind
  root.add(hub);
  const renderHub = (w: World) => {
    const b = w.brain;
    hubEl.innerHTML = `<b>THE ENGINE</b><span>${b.mode} · ${b.marketOpen ? "market open" : "market closed"}${b.gatewayUp ? "" : " · gateway offline"}</span>`;
  };
  renderHub(world);

  let speed = world.brain.marketOpen ? 1 : 0.35;
  const off = stage.onTick((_dt, t) => {
    brain.tick(t, speed);
    flows.forEach((f) => f.tick(t * speed));
    blocks.forEach((b) => b.tick(t));
  });

  stage.flyTo(HOME.pos, HOME.target);
  let sel: string | null = null;
  return {
    update(w: World) {
      speed = w.brain.marketOpen ? 1 : 0.35;
      w.teams.forEach((t) => { blocks.get(t.id)?.update(t); cards.get(t.id)?.update(t); });
      renderHub(w);
    },
    select(id: string | null) {
      sel = id;
      cards.forEach((c, k) => c.el.classList.toggle("selected", k === id));
      if (id && centers.get(id)) stage.focus(centers.get(id)!.clone().setY(0.5), 13);
      else stage.flyTo(HOME.pos, HOME.target);
    },
    hover(id: string | null) {
      blocks.forEach((b, k) => b.setHighlight(k === id || k === sel));
      cards.forEach((c, k) => c.el.classList.toggle("hover", k === id));
    },
    dispose() { off(); disposeGroup(root); },
  };
}

export default function CommandCenter(p: ConceptProps) {
  const ref = useScene(build, (el) => new Stage(el, {
    background: "#0a0f1e", fog: { color: "#0a0f1e", near: 30, far: 75 }, toneMapping: true,
    camera: { position: [0, 30, 34], target: [0, 0, 1], fov: 34 },
    limits: { minDistance: 8, maxDistance: 55, maxPolarAngle: Math.PI * 0.4 },
  }), p.world, p.selected, p.onSelect, p.onHover);
  return <div ref={ref} className="scene3d theme-cc" />;
}
