export type EdgeDensity = "signal" | "network" | "all";

export type GalaxyLanguage = {
  name: string;
  count: number;
  color?: string;
};

export type GalaxyFacetItem = {
  name: string;
  count: number;
  representative?: string;
  color?: string;
};

export type GalaxyAreaValue = {
  primary: string;
  tags?: string[];
  source?: string;
  confidence?: number;
  isAi?: boolean;
};

export type GalaxyAreas = Record<
  string,
  GalaxyAreaValue | string | undefined
>;

export type GalaxyCommunity = {
  id: number;
  count: number;
  label?: string;
  lang?: string;
  color?: string;
  x?: number;
  y?: number;
  radius?: number;
  angle?: number;
  aspect?: number;
  microCommunityCount?: number;
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
  facets?: Record<string, GalaxyFacetItem[]>;
  facetOrder?: string[];
  source?: "github" | "huggingface";
  sourceRevision?: string;
  sourceUrl?: string;
  generatedAt?: string;
  snapshotAt?: string;
  entityCounts?: Record<string, number>;
  relationKinds?: Record<string, number>;
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
  mc?: number;
  lang?: string;
  areas?: GalaxyAreas;
  sourceType?: string;
  author?: string;
  likes?: number;
  downloads?: number;
  trending?: number;
  updatedAt?: string;
  contributors: number;
  degree: number;
  topics: string[] | string;
  description?: string;
  url: string;
};

export type GalaxyEdge = {
  s: number;
  t: number;
  w?: number;
  shared?: number;
  strength: number;
  b?: 0 | 1;
  kind?: string;
  inferred?: 0 | 1;
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

export function nodeFacetValue(node: GalaxyNode, facet: string): string {
  const area = node.areas?.[facet];
  if (typeof area === "string") return area;
  if (area?.primary) return area.primary;
  if (facet === "language") return node.lang || "Unknown";
  if (facet === "type") return node.sourceType || "Unknown";
  return "Unclassified";
}
