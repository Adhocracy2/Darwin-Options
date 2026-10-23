import type { World } from "../teams";

export type ConceptProps = {
  world: World;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  /** The Supervisor is answering: animate its desk and link. */
  busy?: boolean;
};
