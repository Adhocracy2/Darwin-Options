import type { World } from "../teams";

export type ConceptProps = {
  world: World;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
};
