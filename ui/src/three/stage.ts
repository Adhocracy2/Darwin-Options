/**
 * A three.js stage: WebGL scene + HTML labels (CSS2D), orbit camera, click/hover picking on
 * anything tagged with `userData.teamId`, smooth camera focus, and an animation loop.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";

export type StageOptions = {
  background: string;
  fog?: { color: string; near: number; far: number };
  camera: { position: [number, number, number]; target: [number, number, number]; fov?: number };
  shadows?: boolean;
  toneMapping?: boolean;
  limits?: { minDistance: number; maxDistance: number; maxPolarAngle: number };
};

type Tick = (dt: number, t: number) => void;

export class Stage {
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly renderer: THREE.WebGLRenderer;
  readonly labels: CSS2DRenderer;
  readonly controls: OrbitControls;
  onPick: (teamId: string | null) => void = () => {};
  onHover: (teamId: string | null) => void = () => {};

  private ticks = new Set<Tick>();
  private clock = new THREE.Clock();
  private raf = 0;
  private ro: ResizeObserver;
  private ray = new THREE.Raycaster();
  private pointer = new THREE.Vector2();
  private hovered: string | null = null;
  private tween: { from: THREE.Vector3; to: THREE.Vector3; camFrom: THREE.Vector3; camTo: THREE.Vector3; t: number } | null = null;
  private down: { x: number; y: number } | null = null;

  constructor(private el: HTMLElement, o: StageOptions) {
    this.scene.background = new THREE.Color(o.background);
    if (o.fog) this.scene.fog = new THREE.Fog(o.fog.color, o.fog.near, o.fog.far);

    this.camera = new THREE.PerspectiveCamera(o.camera.fov ?? 35, 1, 0.1, 400);
    this.camera.position.set(...o.camera.position);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    if (o.toneMapping) this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    if (o.shadows) {
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    }
    el.appendChild(this.renderer.domElement);

    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    el.appendChild(this.labels.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(...o.camera.target);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = false;
    const lim = o.limits ?? { minDistance: 8, maxDistance: 60, maxPolarAngle: Math.PI * 0.42 };
    Object.assign(this.controls, lim);
    this.controls.update();

    const c = this.renderer.domElement;
    c.addEventListener("pointermove", this.move);
    c.addEventListener("pointerdown", this.press);
    c.addEventListener("pointerup", this.release);
    c.addEventListener("pointerleave", this.leave);

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(el);
    this.resize();
    this.loop();
  }

  add(...o: THREE.Object3D[]) { this.scene.add(...o); }
  onTick(f: Tick) { this.ticks.add(f); return () => this.ticks.delete(f); }

  /** Glide the camera to look at `target` from `distance` away, keeping the current direction. */
  focus(target: THREE.Vector3, distance: number) {
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    this.tween = {
      from: this.controls.target.clone(), to: target.clone(),
      camFrom: this.camera.position.clone(), camTo: target.clone().add(dir.multiplyScalar(distance)), t: 0,
    };
  }

  /** Back to a saved view. */
  flyTo(position: THREE.Vector3, target: THREE.Vector3) {
    this.tween = { from: this.controls.target.clone(), to: target.clone(), camFrom: this.camera.position.clone(), camTo: position.clone(), t: 0 };
  }

  private resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
  }

  private pick(e: PointerEvent): string | null {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.pointer, this.camera);
    // lines and dots only count when the click is right on them (the default 1 unit grabs clicks meant
    // for things next to an outline, like the + on the Technology platform)
    this.ray.params.Line = { threshold: 0.05 };
    this.ray.params.Points = { threshold: 0.3 };
    for (const hit of this.ray.intersectObjects(this.scene.children, true)) {
      let hidden = false; // hidden things (and anything inside a hidden group) can't be clicked
      for (let p: THREE.Object3D | null = hit.object; p; p = p.parent) if (!p.visible) { hidden = true; break; }
      if (hidden) continue;
      let o: THREE.Object3D | null = hit.object;
      while (o && o.userData.teamId == null) o = o.parent;
      if (o) return o.userData.teamId as string;
    }
    return null;
  }

  private move = (e: PointerEvent) => {
    const id = this.pick(e);
    if (id !== this.hovered) {
      this.hovered = id;
      this.renderer.domElement.style.cursor = id ? "pointer" : "";
      this.onHover(id);
    }
  };
  private press = (e: PointerEvent) => { this.down = { x: e.clientX, y: e.clientY }; };
  private release = (e: PointerEvent) => {
    // a click, not the end of a drag
    if (this.down && Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) < 5) this.onPick(this.pick(e));
    this.down = null;
  };
  private leave = () => { if (this.hovered) { this.hovered = null; this.onHover(null); } };

  private loop = () => {
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const t = this.clock.elapsedTime;
    if (this.tween) {
      const tw = this.tween;
      tw.t = Math.min(1, tw.t + dt / 0.9);
      const k = 1 - Math.pow(1 - tw.t, 3);
      this.controls.target.lerpVectors(tw.from, tw.to, k);
      this.camera.position.lerpVectors(tw.camFrom, tw.camTo, k);
      if (tw.t >= 1) this.tween = null;
    }
    for (const f of this.ticks) f(dt, t);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  };

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.controls.dispose();
    this.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose?.();
      const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
      mats.forEach((x) => x.dispose());
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.labels.domElement.remove();
  }
}
