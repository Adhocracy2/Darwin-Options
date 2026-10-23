import { useEffect, useRef } from "react";
import { Stage } from "./stage";
import type { World } from "./teams";

export type SceneHandle = {
  update: (w: World) => void;
  select: (id: string | null) => void;
  hover: (id: string | null) => void;
  setBusy?: (busy: boolean) => void;
  dispose?: () => void;
};

/**
 * Runs a 3D scene in `ref`. `build` makes the scene for a world; it's rebuilt only when the
 * shape changes (teams or desk counts), otherwise `update` refreshes cards and desk lights.
 */
export function useScene(
  build: (stage: Stage, world: World) => SceneHandle,
  makeStage: (el: HTMLElement) => Stage,
  world: World,
  selected: string | null,
  onSelect: (id: string | null) => void,
  onHover: (id: string | null) => void,
  busy = false,
) {
  const ref = useRef<HTMLDivElement>(null);
  const st = useRef<{ stage: Stage; handle: SceneHandle; shape: string } | null>(null);
  const shape = world.teams.map((t) => `${t.id}:${t.agents.length}`).join("|");
  const cb = useRef({ onSelect, onHover });
  cb.current = { onSelect, onHover };

  useEffect(() => {
    const el = ref.current!;
    const stage = makeStage(el);
    stage.onPick = (id) => cb.current.onSelect(id);
    stage.onHover = (id) => { st.current?.handle.hover(id); cb.current.onHover(id); };
    st.current = { stage, handle: build(stage, world), shape };
    return () => { st.current?.handle.dispose?.(); stage.dispose(); st.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const s = st.current;
    if (!s) return;
    if (s.shape !== shape) {
      s.handle.dispose?.();
      s.handle = build(s.stage, world);
      s.shape = shape;
    } else {
      s.handle.update(world);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world]);

  useEffect(() => { st.current?.handle.select(selected); }, [selected]);
  useEffect(() => { st.current?.handle.setBusy?.(busy); }, [busy]);

  return ref;
}
