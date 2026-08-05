export type EdgeDensity = "signal" | "network" | "all";

export type GalaxyLanguage = {
  name: string;
  count: number;
  color?: string;
};

export type GalaxyCommunity = {
  id: number;
  count: number;
  label?: string;
  lang?: string;
  color?: string;
  x?: number;
  y?: number;
};

export type GalaxyMeta = {
  period?:
    | string
    | {
        start: string;
        end: string;
        months: number;
      };
  start?: string;
  end?: string;
  nodeCount: number;
  edgeCount: number;
  backboneCount?: number;
  componentCount?: number;
  mainComponentSize?: number;
  mainComponent?: number;
  twinIds?: Array<number | string>;
  twinNodeIds?: string[];
  twinNames?: string[];
  twinEdge?: {
    w: number;
    shared: number;
    strength: number;
  };
  languages?: GalaxyLanguage[];
  communities?: GalaxyCommunity[];
  bounds?: {
    minX: number;
    maxX: number;
    minY: number;
    maxY: number;
  };
};

export type GalaxyNode = {
  id: string;
  name: string;
  label?: string;
  x: number;
  y: number;
  r: number;
  c: number;
  lang: string;
  contributors: number;
  degree: number;
  topics: string[] | string;
  description: string;
  url: string;
};

export type GalaxyEdge = {
  s: number;
  t: number;
  w: number;
  shared: number;
  strength: number;
  b?: 0 | 1;
};

export type GalaxyGraph = {
  meta: GalaxyMeta;
  nodes: GalaxyNode[];
  edges: GalaxyEdge[];
};

export type ViewCommand = {
  id: number;
  type: "fit" | "focus" | "zoom";
  indices?: number[];
  factor?: number;
};
