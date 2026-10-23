/**
 * Concept 2 · Daylight Studio: a bright diorama office. Pastel team islands on a light floor
 * with soft shadows and plants; small name tags, details in the side panel.
 */
import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { Stage } from "../stage";
import { disposeGroup, makeBrain, makeCard, makeTeamBlock, roundedSlab, type Palette, type TeamBlock } from "../kit";
import { ORDER, type Team, type World } from "../teams";
import { useScene } from "../useScene";
import type { ConceptProps } from "./types";

const PALETTE: Palette = {
  deskTop: "#fffaf3", deskLeg: "#c9bfb2", monitor: "#3b3f45", screen: "#a5d8ff",
  chair: "#8e9aaf", skin: ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"], roughness: 0.85,
};

const HOME = { pos: new THREE.Vector3(11, 24, 28), target: new THREE.Vector3(0, 0, 0.3) };
const pastel = (c: string, k = 0.55) => new THREE.Color(c).lerp(new THREE.Color("#ffffff"), k).getStyle();

function plant(x: number, z: number, s = 1) {
  const g = new THREE.Group();
  const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.22 * s, 0.17 * s, 0.35 * s, 16), new THREE.MeshStandardMaterial({ color: "#d9cbb8", roughness: 0.9 }));
  pot.position.y = 0.17 * s;
  const leaves = new THREE.Mesh(new THREE.IcosahedronGeometry(0.45 * s, 0), new THREE.MeshStandardMaterial({ color: "#69b578", roughness: 0.8, flatShading: true }));
  leaves.position.y = 0.75 * s;
  leaves.scale.y = 1.3;
  for (const m of [pot, leaves]) { m.castShadow = true; g.add(m); }
  g.position.set(x, 0, z);
  return g;
}

const tag = (t: Team) =>
  `<div class="st-name"><i style="background:${t.color}"></i>${t.name}</div>
   <div class="st-big"><b>${t.headline.value}</b> ${t.headline.label}</div>
   ${t.alert ? `<div class="st-alert">${t.alert}</div>` : ""}`;

function build(stage: Stage, world: World) {
  const root = new THREE.Group();
  stage.add(root);

  root.add(new THREE.HemisphereLight("#ffffff", "#e8dccb", 1.6));
  const sun = new THREE.DirectionalLight("#fff4e0", 2.4);
  sun.position.set(-12, 22, 14);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -26, right: 26, top: 22, bottom: -22, near: 1, far: 70 });
  sun.shadow.bias = -0.0004;
  sun.shadow.radius = 4;
  root.add(sun);

  // floor: one big soft slab
  const floor = roundedSlab(46, 34, 0.5, 2, new THREE.MeshStandardMaterial({ color: "#f3eee7", roughness: 1 }));
  floor.position.y = -0.25;
  floor.receiveShadow = true;
  root.add(floor);

  // two rows: four teams behind, three in front, the engine in the middle
  const ids = ORDER.filter((id) => world.teams.some((t) => t.id === id));
  const back = ids.slice(0, 4), front = ids.slice(4);
  const spots = new Map<string, THREE.Vector3>();
  back.forEach((id, i) => spots.set(id, new THREE.Vector3((i - (back.length - 1) / 2) * 10, 0, -7.5)));
  front.forEach((id, i) => spots.set(id, new THREE.Vector3((i - (front.length - 1) / 2) * 11, 0, 7.5)));

  const blocks = new Map<string, TeamBlock>();
  const cards = new Map<string, ReturnType<typeof makeCard>>();
  ids.forEach((id) => {
    const team = world.teams.find((t) => t.id === id)!;
    const c = spots.get(id)!;
    const block = makeTeamBlock(team, {
      palette: PALETTE, shadows: true, edge: null, thickness: 0.18, perRow: 3,
      floor: (col) => new THREE.MeshStandardMaterial({ color: pastel(col, 0.6), roughness: 0.95 }),
      shirt: (col, j) => pastel(col, j % 2 ? 0.1 : 0.3),
    });
    block.group.position.copy(c);
    root.add(block.group);
    blocks.set(id, block);
    // a plant at a corner of each island
    root.add(plant(c.x + block.size.w / 2 + 0.6, c.z - block.size.d / 2 + 0.4, 1));

    const card = makeCard(team, "st-tag", stage, tag);
    card.obj.position.set(c.x, 2.3, c.z - block.size.d / 2 - 0.2);
    root.add(card.obj);
    cards.set(id, card);
  });

  // engine kiosk in the middle
  const kiosk = new THREE.Mesh(new THREE.CylinderGeometry(1.6, 1.9, 0.6, 40), new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.4 }));
  kiosk.position.y = 0.3;
  kiosk.castShadow = kiosk.receiveShadow = true;
  root.add(kiosk);
  const brain = makeBrain("#4dabf7", 900, 1.1);
  brain.group.position.set(0, 2, 0);
  root.add(brain.group);
  const hubEl = document.createElement("div");
  hubEl.className = "st-hub";
  const hub = new CSS2DObject(hubEl);
  hub.position.set(0, 3.6, 0);
  root.add(hub);
  const renderHub = (w: World) => {
    hubEl.innerHTML = `<b>Engine</b> · ${w.brain.mode} · ${w.brain.marketOpen ? "market open" : "market closed"}`;
  };
  renderHub(world);
  // planters along the walkway
  for (const x of [-20, -14, 14, 20]) root.add(plant(x, 0, 1.3));

  let speed = world.brain.marketOpen ? 1 : 0.4;
  const off = stage.onTick((_dt, t) => { brain.tick(t, speed); blocks.forEach((b) => b.tick(t)); });
  stage.flyTo(HOME.pos, HOME.target);

  let sel: string | null = null;
  return {
    update(w: World) {
      speed = w.brain.marketOpen ? 1 : 0.4;
      w.teams.forEach((t) => { blocks.get(t.id)?.update(t); cards.get(t.id)?.update(t); });
      renderHub(w);
    },
    select(id: string | null) {
      sel = id;
      cards.forEach((c, k) => c.el.classList.toggle("selected", k === id));
      if (id && spots.get(id)) stage.focus(spots.get(id)!.clone(), 14);
      else stage.flyTo(HOME.pos, HOME.target);
    },
    hover(id: string | null) {
      blocks.forEach((b, k) => b.setHighlight(k === id || k === sel));
      cards.forEach((c, k) => c.el.classList.toggle("hover", k === id));
    },
    dispose() { off(); disposeGroup(root); },
  };
}

export default function Studio(p: ConceptProps) {
  const ref = useScene(build, (el) => new Stage(el, {
    background: "#e9e4dc", shadows: true, toneMapping: true,
    camera: { position: [20, 36, 42], target: [0, 0, 0], fov: 30 },
    limits: { minDistance: 10, maxDistance: 70, maxPolarAngle: Math.PI * 0.42 },
  }), p.world, p.selected, p.onSelect, p.onHover);
  return <div ref={ref} className="scene3d theme-st" />;
}
