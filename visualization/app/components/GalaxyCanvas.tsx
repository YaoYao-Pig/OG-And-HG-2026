"use client";

import {
  useEffect,
  useMemo,
  useRef,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  nodeFacetValue,
  type EdgeDensity,
  type GalaxyGraph,
  type ViewCommand,
} from "../galaxy-types";

const AREA_COLORS = [
  "#3155d6",
  "#258fd2",
  "#7447bc",
  "#2cbab4",
  "#ad438e",
  "#c63f5b",
  "#d2743d",
  "#c0b12f",
  "#4d9e4a",
];

type Camera = { x: number; y: number; zoom: number };

type CanvasState = {
  width: number;
  height: number;
  dpr: number;
  plotX: number;
  plotY: number;
  plotRadius: number;
  sidebarEdge: number;
  baseScale: number;
  camera: Camera;
  dragging: boolean;
  moved: boolean;
  pointerId: number | null;
  lastX: number;
  lastY: number;
  animation: number | null;
};

type CommunityVisual = {
  id: number;
  count: number;
  x: number;
  y: number;
  radius: number;
  angle: number;
  aspect: number;
  representativeIndex: number;
  color: string;
  facetCounts: Map<string, number>;
};

type EdgeLayer = {
  communityId: number;
  indices: number[];
};

type GalaxyCanvasProps = {
  graph: GalaxyGraph;
  selectedIndex: number | null;
  hoveredIndex: number | null;
  facet: string;
  facetValue: string | null;
  density: EdgeDensity;
  glowEnabled: boolean;
  minStrength: number;
  viewCommand: ViewCommand;
  onSelect: (index: number | null) => void;
  onHover: (index: number | null) => void;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function easeOutQuart(value: number) {
  return 1 - Math.pow(1 - value, 4);
}

function shortName(name: string) {
  const segments = name.split("/");
  return segments.at(-1) || name;
}

function colorWithAlpha(color: string, alpha: number) {
  const value = color.trim();
  if (/^#[\da-f]{3}$/i.test(value)) {
    const [r, g, b] = value
      .slice(1)
      .split("")
      .map((part) => Number.parseInt(part + part, 16));
    return `rgba(${r},${g},${b},${alpha})`;
  }
  if (/^#[\da-f]{6}$/i.test(value)) {
    return `rgba(${Number.parseInt(value.slice(1, 3), 16)},${Number.parseInt(
      value.slice(3, 5),
      16,
    )},${Number.parseInt(value.slice(5, 7), 16)},${alpha})`;
  }
  return `rgba(180,188,194,${alpha})`;
}

function stableUnit(seed: number, salt: number) {
  const value = Math.sin(seed * 12.9898 + salt * 78.233) * 43758.5453;
  return value - Math.floor(value);
}

export function GalaxyCanvas({
  graph,
  selectedIndex,
  hoveredIndex,
  facet,
  facetValue,
  density,
  glowEnabled,
  minStrength,
  viewCommand,
  onSelect,
  onHover,
}: GalaxyCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<CanvasState>({
    width: 1,
    height: 1,
    dpr: 1,
    plotX: 0,
    plotY: 0,
    plotRadius: 1,
    sidebarEdge: 0,
    baseScale: 1,
    camera: { x: 0, y: 0, zoom: 0.96 },
    dragging: false,
    moved: false,
    pointerId: null,
    lastX: 0,
    lastY: 0,
    animation: null,
  });
  const requestDrawRef = useRef<() => void>(() => undefined);
  const animateToRef = useRef<(camera: Camera, duration?: number) => void>(
    () => undefined,
  );
  const propsRef = useRef({
    selectedIndex,
    hoveredIndex,
    facet,
    facetValue,
    density,
    glowEnabled,
    minStrength,
    onSelect,
    onHover,
  });

  const bounds = useMemo(() => {
    if (graph.meta.bounds) return graph.meta.bounds;
    let minX = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;
    for (const node of graph.nodes) {
      minX = Math.min(minX, node.x);
      maxX = Math.max(maxX, node.x);
      minY = Math.min(minY, node.y);
      maxY = Math.max(maxY, node.y);
    }
    return { minX, maxX, minY, maxY };
  }, [graph]);

  const facetOrder = useMemo(
    () =>
      new Map(
        (
          graph.meta.facets?.[facet] ??
          (facet === "language" ? graph.meta.languages : undefined) ??
          []
        )
          .filter((item) => facet !== "ai" || item.name !== "Non-AI")
          .map((item, index) => [item.name, index]),
      ),
    [facet, graph],
  );

  const facetColors = useMemo(
    () =>
      new Map(
        (
          graph.meta.facets?.[facet] ??
          (facet === "language" ? graph.meta.languages : undefined) ??
          []
        ).map((item, index) => [
          item.name,
          item.color ?? AREA_COLORS[index % AREA_COLORS.length],
        ]),
      ),
    [facet, graph],
  );

  const nodeVisuals = useMemo(() => {
    const scores = graph.nodes.map(
      (node) =>
        Math.log1p(Math.max(node.r, 0)) * 1.55 +
        Math.log1p(Math.max(node.degree, 0)) * 0.9 +
        Math.log1p(Math.max(node.contributors, 0)) * 0.22,
    );
    const rankedIndices = scores
      .map((score, index) => ({ score, index }))
      .sort((a, b) => b.score - a.score)
      .map(({ index }) => index);
    const tiers = new Uint8Array(graph.nodes.length);
    const starEnd = Math.min(
      graph.nodes.length,
      Math.max(80, Math.ceil(graph.nodes.length * 0.05)),
    );
    const coreEnd = Math.min(
      starEnd,
      Math.max(20, Math.ceil(graph.nodes.length * 0.006)),
    );
    const landmarkEnd = Math.min(
      coreEnd,
      Math.max(12, Math.ceil(graph.nodes.length * 0.0014)),
    );
    rankedIndices.forEach((index, rank) => {
      tiers[index] = rank < landmarkEnd ? 3 : rank < coreEnd ? 2 : rank < starEnd ? 1 : 0;
    });
    return {
      scores,
      tiers,
      rankedIndices,
      starIndices: rankedIndices.slice(0, starEnd),
      landmarkIndices: rankedIndices.slice(0, Math.min(48, landmarkEnd)),
    };
  }, [graph]);

  const communityVisuals = useMemo(() => {
    type Accumulator = {
      id: number;
      indices: number[];
      sumX: number;
      sumY: number;
      facetCounts: Map<string, number>;
      representativeIndex: number;
    };
    const accumulators = new Map<number, Accumulator>();
    graph.nodes.forEach((node, index) => {
      let accumulator = accumulators.get(node.c);
      if (!accumulator) {
        accumulator = {
          id: node.c,
          indices: [],
          sumX: 0,
          sumY: 0,
          facetCounts: new Map(),
          representativeIndex: index,
        };
        accumulators.set(node.c, accumulator);
      }
      accumulator.indices.push(index);
      accumulator.sumX += node.x;
      accumulator.sumY += node.y;
      const value = nodeFacetValue(node, facet);
      accumulator.facetCounts.set(value, (accumulator.facetCounts.get(value) ?? 0) + 1);
      if (
        nodeVisuals.scores[index] >
        nodeVisuals.scores[accumulator.representativeIndex]
      ) {
        accumulator.representativeIndex = index;
      }
    });

    const metaById = new Map(
      (graph.meta.communities ?? []).map((community) => [community.id, community]),
    );
    const visuals: CommunityVisual[] = [];
    for (const accumulator of accumulators.values()) {
      const meta = metaById.get(accumulator.id);
      const x = meta?.x ?? accumulator.sumX / accumulator.indices.length;
      const y = meta?.y ?? accumulator.sumY / accumulator.indices.length;
      let varianceX = 0;
      let varianceY = 0;
      let covariance = 0;
      for (const index of accumulator.indices) {
        const dx = graph.nodes[index].x - x;
        const dy = graph.nodes[index].y - y;
        varianceX += dx * dx;
        varianceY += dy * dy;
        covariance += dx * dy;
      }
      varianceX /= Math.max(accumulator.indices.length, 1);
      varianceY /= Math.max(accumulator.indices.length, 1);
      covariance /= Math.max(accumulator.indices.length, 1);
      const trace = varianceX + varianceY;
      const discriminant = Math.sqrt(
        Math.max(0, (varianceX - varianceY) ** 2 + 4 * covariance ** 2),
      );
      const major = Math.max((trace + discriminant) / 2, 0.00001);
      const minor = Math.max((trace - discriminant) / 2, major * 0.12);
      const dominantFacet = [...accumulator.facetCounts.entries()].sort(
        (a, b) => b[1] - a[1],
      )[0]?.[0];
      visuals.push({
        id: accumulator.id,
        count: accumulator.indices.length,
        x,
        y,
        radius:
          meta?.radius ?? clamp(Math.sqrt(major) * 2.7, 0.035, 0.38),
        angle:
          meta?.angle ?? 0.5 * Math.atan2(2 * covariance, varianceX - varianceY),
        aspect:
          meta?.aspect ?? clamp(Math.sqrt(major / minor), 1.05, 2.35),
        representativeIndex: accumulator.representativeIndex,
        color: facetColors.get(dominantFacet) ?? meta?.color ?? "#939ca2",
        facetCounts: accumulator.facetCounts,
      });
    }
    return visuals.sort((a, b) => b.count - a.count);
  }, [facet, facetColors, graph, nodeVisuals.scores]);

  const annotationIndices = useMemo(() => {
    const candidates = communityVisuals
      .map((community) => ({
        index: community.representativeIndex,
        angle: Math.atan2(community.y, community.x),
        score: community.count * (0.78 + Math.hypot(community.x, community.y) * 0.65),
      }))
      .sort((a, b) => b.score - a.score);
    const chosen: typeof candidates = [];
    for (const candidate of candidates) {
      if (chosen.length >= 16) break;
      const separated = chosen.every((item) => {
        const delta = Math.abs(candidate.angle - item.angle);
        return Math.min(delta, Math.PI * 2 - delta) > 0.2;
      });
      if (!separated) continue;
      chosen.push(candidate);
    }
    return chosen.map(({ index }) => index);
  }, [communityVisuals]);

  const edgeLayers = useMemo(() => {
    const within = new Map<number, number[]>();
    const cross: number[] = [];
    graph.edges.forEach((edge, edgeIndex) => {
      const sourceCommunity = graph.nodes[edge.s]?.c;
      const targetCommunity = graph.nodes[edge.t]?.c;
      if (sourceCommunity === targetCommunity && sourceCommunity !== undefined) {
        const indices = within.get(sourceCommunity) ?? [];
        indices.push(edgeIndex);
        within.set(sourceCommunity, indices);
      } else {
        cross.push(edgeIndex);
      }
    });
    const withinLayers: EdgeLayer[] = [...within.entries()]
      .map(([communityId, indices]) => ({
        communityId,
        indices,
      }))
      .sort((a, b) => b.indices.length - a.indices.length);
    const skeleton = graph.edges
      .map((edge, index) => ({
        index,
        score:
          (edge.b === 1 ? 5 : 0) +
          Math.log1p(Math.max(edge.strength, 0)) +
          Math.log1p(Math.max(edge.w ?? edge.strength, 0)) * 0.25,
      }))
      .sort((a, b) => b.score - a.score)
      .slice(0, Math.min(2_400, Math.max(720, Math.ceil(graph.nodes.length * 0.1))))
      .map(({ index }) => index);
    return { cross, withinLayers, skeleton };
  }, [graph]);

  const edgesByNode = useMemo(() => {
    const result = Array.from({ length: graph.nodes.length }, () => [] as number[]);
    graph.edges.forEach((edge, edgeIndex) => {
      result[edge.s]?.push(edgeIndex);
      result[edge.t]?.push(edgeIndex);
    });
    return result;
  }, [graph]);

  useEffect(() => {
    propsRef.current = {
      selectedIndex,
      hoveredIndex,
      facet,
      facetValue,
      density,
      glowEnabled,
      minStrength,
      onSelect,
      onHover,
    };
    requestDrawRef.current();
  }, [density, facet, facetValue, glowEnabled, hoveredIndex, minStrength, onHover, onSelect, selectedIndex]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;
    const state = stateRef.current;
    const spanX = Math.max(bounds.maxX - bounds.minX, 0.001);
    const spanY = Math.max(bounds.maxY - bounds.minY, 0.001);
    state.camera = {
      x: (bounds.minX + bounds.maxX) / 2,
      y: (bounds.minY + bounds.maxY) / 2,
      zoom: 0.96,
    };

    const nodeColor = (index: number) => {
      const value = nodeFacetValue(graph.nodes[index], facet);
      return facetColors.get(value) ?? "#a5aaad";
    };

    const worldToScreen = (x: number, y: number) => ({
      x: (x - state.camera.x) * state.baseScale * state.camera.zoom + state.plotX,
      y: (y - state.camera.y) * state.baseScale * state.camera.zoom + state.plotY,
    });

    const screenX = new Float32Array(graph.nodes.length);
    const screenY = new Float32Array(graph.nodes.length);
    const screenInPlot = new Uint8Array(graph.nodes.length);
    const visibleMask = new Uint8Array(graph.nodes.length);
    const communityColorById = new Map(
      communityVisuals.map((community) => [community.id, community.color]),
    );
    const glowSprites = new Map<string, HTMLCanvasElement>();
    const glowSprite = (color: string) => {
      const cached = glowSprites.get(color);
      if (cached) return cached;
      const sprite = document.createElement("canvas");
      sprite.width = 128;
      sprite.height = 128;
      const spriteContext = sprite.getContext("2d");
      if (spriteContext) {
        const gradient = spriteContext.createRadialGradient(64, 64, 0, 64, 64, 64);
        gradient.addColorStop(0, colorWithAlpha(color, 0.82));
        gradient.addColorStop(0.28, colorWithAlpha(color, 0.42));
        gradient.addColorStop(0.68, colorWithAlpha(color, 0.1));
        gradient.addColorStop(1, colorWithAlpha(color, 0));
        spriteContext.fillStyle = gradient;
        spriteContext.fillRect(0, 0, 128, 128);
      }
      glowSprites.set(color, sprite);
      return sprite;
    };

    const nodeVisible = (index: number) =>
      visibleMask[index] === 1;

    const edgeVisible = (edgeIndex: number) => {
      const edge = graph.edges[edgeIndex];
      if (edge.strength < propsRef.current.minStrength) return false;
      if (propsRef.current.density === "all") return true;
      if (propsRef.current.density === "network") {
        return edge.b === 1 || edgeIndex < Math.min(20_000, graph.edges.length);
      }
      return graph.meta.backboneCount
        ? edge.b === 1
        : edgeIndex < Math.min(7_500, graph.edges.length);
    };

    const inPlot = (x: number, y: number, margin = 0) =>
      Math.hypot(x - state.plotX, y - state.plotY) <= state.plotRadius + margin;

    const drawCallout = (index: number, emphasized = false) => {
      const node = graph.nodes[index];
      if (!node) return;
      if (!emphasized && !nodeVisible(index)) return;
      const point = { x: screenX[index], y: screenY[index] };
      if (!inPlot(point.x, point.y, 5)) return;
      const angle = Math.atan2(point.y - state.plotY, point.x - state.plotX);
      const directionX = Math.cos(angle);
      const directionY = Math.sin(angle);
      const rim = {
        x: state.plotX + directionX * (state.plotRadius + 9),
        y: state.plotY + directionY * (state.plotRadius + 9),
      };
      const knee = {
        x: state.plotX + directionX * (state.plotRadius + (emphasized ? 34 : 25)),
        y: state.plotY + directionY * (state.plotRadius + (emphasized ? 34 : 25)),
      };
      const rightSide = directionX >= 0;
      const tail = clamp(Math.abs(directionX) * 50 + 18, 18, 64) * (rightSide ? 1 : -1);
      let labelX = knee.x + tail;
      const labelY = knee.y;
      if (rightSide) labelX = Math.min(labelX, state.width - 18);
      else labelX = Math.max(labelX, state.sidebarEdge + 14);

      context.save();
      context.strokeStyle = emphasized ? "rgba(255,255,255,.72)" : "rgba(215,220,224,.42)";
      context.lineWidth = emphasized ? 0.85 : 0.6;
      context.setLineDash([1.5, 3]);
      context.beginPath();
      context.moveTo(point.x, point.y);
      context.lineTo(rim.x, rim.y);
      context.lineTo(knee.x, knee.y);
      context.lineTo(labelX, labelY);
      context.stroke();
      context.setLineDash([]);

      context.beginPath();
      context.arc(point.x, point.y, emphasized ? 8 : 5.5, 0, Math.PI * 2);
      context.strokeStyle = emphasized ? "rgba(255,255,255,.85)" : "rgba(220,224,228,.38)";
      context.stroke();
      context.beginPath();
      context.arc(labelX, labelY, 2.2, 0, Math.PI * 2);
      context.fillStyle = emphasized ? "#ffffff" : "rgba(231,234,236,.72)";
      context.fill();

      context.font = `${emphasized ? 650 : 580} ${emphasized ? 13 : 11}px Arial, sans-serif`;
      context.textAlign = rightSide ? "right" : "left";
      context.textBaseline = "bottom";
      context.fillStyle = emphasized ? "#ffffff" : "rgba(236,239,241,.82)";
      const offset = rightSide ? -7 : 7;
      context.fillText(emphasized ? node.name : shortName(node.name), labelX + offset, labelY - 5);
      context.restore();
    };

    let lowDetailUntil = 0;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;

    const draw = () => {
      const { width, height, dpr, plotX, plotY, plotRadius, camera } = state;
      const requestedFocus =
        propsRef.current.selectedIndex ?? propsRef.current.hoveredIndex;
      const lowDetail =
        state.dragging ||
        state.animation !== null ||
        performance.now() < lowDetailUntil;
      const cameraScale = state.baseScale * camera.zoom;
      const plotMarginSquared = (plotRadius + 24) ** 2;
      for (let index = 0; index < graph.nodes.length; index += 1) {
        const node = graph.nodes[index];
        const x = (node.x - camera.x) * cameraScale + plotX;
        const y = (node.y - camera.y) * cameraScale + plotY;
        screenX[index] = x;
        screenY[index] = y;
        const deltaX = x - plotX;
        const deltaY = y - plotY;
        screenInPlot[index] =
          deltaX * deltaX + deltaY * deltaY <= plotMarginSquared ? 1 : 0;
        visibleMask[index] =
          propsRef.current.facetValue === null ||
          nodeFacetValue(node, propsRef.current.facet) === propsRef.current.facetValue
            ? 1
            : 0;
      }
      const focused =
        requestedFocus !== null && visibleMask[requestedFocus] === 1
          ? requestedFocus
          : null;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);

      context.save();
      context.beginPath();
      context.arc(plotX, plotY, plotRadius, 0, Math.PI * 2);
      context.clip();
      context.fillStyle = "#020202";
      context.fillRect(plotX - plotRadius, plotY - plotRadius, plotRadius * 2, plotRadius * 2);

      const vignette = context.createRadialGradient(
        plotX,
        plotY,
        plotRadius * 0.12,
        plotX,
        plotY,
        plotRadius,
      );
      vignette.addColorStop(0, "rgba(255,255,255,.015)");
      vignette.addColorStop(0.72, "rgba(255,255,255,0)");
      vignette.addColorStop(1, "rgba(0,0,0,.34)");
      context.fillStyle = vignette;
      context.fillRect(plotX - plotRadius, plotY - plotRadius, plotRadius * 2, plotRadius * 2);

      const nebulaFade = propsRef.current.glowEnabled
        ? clamp((2.4 - camera.zoom) / 1.1, 0, 1)
        : 0;
      const largestCommunity = communityVisuals[0]?.count ?? 1;
      context.globalCompositeOperation = "source-over";
      for (const community of communityVisuals) {
        if (nebulaFade <= 0) break;
        const point = worldToScreen(community.x, community.y);
        const activeCount = propsRef.current.facetValue
          ? community.facetCounts.get(propsRef.current.facetValue) ?? 0
          : community.count;
        if (activeCount === 0 && propsRef.current.facetValue !== null) continue;
        const activeShare = clamp(activeCount / Math.max(community.count, 1), 0.08, 1);
        const nebulaColor = propsRef.current.facetValue
          ? facetColors.get(propsRef.current.facetValue) ?? community.color
          : community.color;
        const baseRadius = clamp(
          community.radius * state.baseScale * camera.zoom * 1.28,
          12,
          plotRadius * 0.46,
        );
        if (
          Math.hypot(point.x - plotX, point.y - plotY) >
          plotRadius + baseRadius * community.aspect
        ) {
          continue;
        }
        for (let lobe = 0; lobe < 3; lobe += 1) {
          const phase = stableUnit(community.id + 1, lobe + 11) * Math.PI * 2;
          const offset = baseRadius * (lobe === 0 ? 0 : 0.12 + stableUnit(community.id, lobe) * 0.08);
          const lobeRadius = baseRadius * (lobe === 0 ? 0.88 : 0.56 + stableUnit(community.id, lobe + 5) * 0.2);
          const alpha =
            (propsRef.current.facetValue === null ? 0.032 : 0.045) *
            activeShare *
            Math.sqrt(community.count / largestCommunity) *
            nebulaFade *
            (lobe === 0 ? 1 : 0.58);
          context.save();
          context.translate(
            point.x + Math.cos(phase) * offset,
            point.y + Math.sin(phase) * offset,
          );
          context.rotate(community.angle + (lobe - 1) * 0.09);
          const aspect = Math.sqrt(community.aspect) * (lobe === 2 ? 0.92 : 1);
          context.globalAlpha = alpha;
          const sprite = glowSprite(nebulaColor);
          context.drawImage(
            sprite,
            -lobeRadius * aspect,
            -lobeRadius / aspect,
            lobeRadius * aspect * 2,
            (lobeRadius / aspect) * 2,
          );
          context.restore();
        }
      }
      context.globalAlpha = 1;

      const strokeEdges = (
        indices: number[],
        strokeStyle: string,
        lineWidth: number,
        batchSize = 0,
      ) => {
        context.strokeStyle = strokeStyle;
        context.lineWidth = lineWidth;
        context.beginPath();
        let segmentCount = 0;
        const flush = () => {
          if (segmentCount === 0) return;
          context.stroke();
          context.beginPath();
          segmentCount = 0;
        };
        for (const edgeIndex of indices) {
          if (!edgeVisible(edgeIndex)) continue;
          const edge = graph.edges[edgeIndex];
          if (
            propsRef.current.facetValue !== null &&
            (!nodeVisible(edge.s) || !nodeVisible(edge.t))
          ) {
            continue;
          }
          const sourceX = screenX[edge.s];
          const sourceY = screenY[edge.s];
          const targetX = screenX[edge.t];
          const targetY = screenY[edge.t];
          if (screenInPlot[edge.s] === 0 && screenInPlot[edge.t] === 0) {
            const deltaX = targetX - sourceX;
            const deltaY = targetY - sourceY;
            const lengthSquared = deltaX * deltaX + deltaY * deltaY;
            const projection =
              lengthSquared > 0
                ? clamp(
                    ((plotX - sourceX) * deltaX + (plotY - sourceY) * deltaY) /
                      lengthSquared,
                    0,
                    1,
                  )
                : 0;
            const closestX = sourceX + deltaX * projection - plotX;
            const closestY = sourceY + deltaY * projection - plotY;
            if (closestX * closestX + closestY * closestY > (plotRadius + 2) ** 2) {
              continue;
            }
          }
          context.moveTo(sourceX, sourceY);
          context.lineTo(targetX, targetY);
          segmentCount += 1;
          if (batchSize > 0 && segmentCount >= batchSize) flush();
        }
        flush();
      };

      const edgeAlphaScale = clamp(
        Math.sqrt(46_000 / Math.max(graph.edges.length, 1)),
        0.5,
        1,
      );
      context.globalCompositeOperation = "source-over";
      if (!lowDetail) {
        strokeEdges(
          edgeLayers.cross,
          propsRef.current.facetValue === null
            ? `rgba(203,211,216,${0.026 * edgeAlphaScale})`
            : `rgba(220,226,230,${0.075 * edgeAlphaScale})`,
          propsRef.current.density === "all" ? 0.28 : 0.42,
        );
      }

      context.globalCompositeOperation = "lighter";
      if (!lowDetail) {
        for (const layer of edgeLayers.withinLayers) {
          strokeEdges(
            layer.indices,
            colorWithAlpha(
              (propsRef.current.facetValue
                ? facetColors.get(propsRef.current.facetValue)
                : undefined) ??
                communityColorById.get(layer.communityId) ??
                "#aab0b4",
              (propsRef.current.facetValue === null
                ? propsRef.current.density === "all"
                  ? 0.023
                  : 0.038
                : 0.064) * edgeAlphaScale,
            ),
            propsRef.current.density === "all" ? 0.32 : 0.43,
            220,
          );
        }
      }

      context.globalCompositeOperation = "source-over";
      strokeEdges(
        edgeLayers.skeleton,
        propsRef.current.facetValue === null
          ? `rgba(238,241,242,${0.095 * edgeAlphaScale})`
          : `rgba(246,247,247,${0.18 * edgeAlphaScale})`,
        propsRef.current.density === "all" ? 0.48 : 0.62,
      );

      const zoomRadius = clamp(Math.pow(camera.zoom, 0.12), 0.9, 1.34);
      const maxScore = Math.max(nodeVisuals.scores[nodeVisuals.rankedIndices[0] ?? 0] ?? 1, 1);
      const nodeRadius = (index: number) => {
        const tier = nodeVisuals.tiers[index];
        const scoreShare = clamp(nodeVisuals.scores[index] / maxScore, 0, 1);
        if (tier === 3) return (3.2 + scoreShare * 3.1) * zoomRadius;
        if (tier === 2) return (1.75 + scoreShare * 2.05) * zoomRadius;
        if (tier === 1) return (0.82 + scoreShare * 1.35) * zoomRadius;
        return clamp(0.31 + Math.log1p(graph.nodes[index].degree) * 0.045, 0.35, 0.72) * zoomRadius;
      };

      context.globalCompositeOperation = "lighter";
      if (propsRef.current.glowEnabled) {
        for (const index of [...nodeVisuals.landmarkIndices].reverse()) {
          if (!nodeVisible(index)) continue;
          if (screenInPlot[index] === 0) continue;
          const radius = nodeRadius(index);
          const haloRadius = radius * 3.2 + 5;
          context.globalAlpha = 0.2;
          const sprite = glowSprite(nodeColor(index));
          context.drawImage(
            sprite,
            screenX[index] - haloRadius,
            screenY[index] - haloRadius,
            haloRadius * 2,
            haloRadius * 2,
          );
        }
      }

      for (let index = graph.nodes.length - 1; index >= 0; index -= 1) {
        if (nodeVisuals.tiers[index] !== 0) continue;
        const node = graph.nodes[index];
        if (screenInPlot[index] === 0) continue;
        const visible = nodeVisible(index);
        const facetRank = facetOrder.get(nodeFacetValue(node, facet)) ?? 99;
        const size = nodeRadius(index) * 1.65;
        context.globalAlpha = visible ? (facetRank < 99 ? 0.72 : 0.46) : 0.024;
        context.fillStyle = nodeColor(index);
        context.fillRect(screenX[index] - size / 2, screenY[index] - size / 2, size, size);
      }

      for (const index of [...nodeVisuals.starIndices].reverse()) {
        const tier = nodeVisuals.tiers[index];
        const node = graph.nodes[index];
        if (screenInPlot[index] === 0) continue;
        const visible = nodeVisible(index);
        const facetRank = facetOrder.get(nodeFacetValue(node, facet)) ?? 99;
        context.globalAlpha = visible
          ? tier >= 2
            ? 0.94
            : facetRank < 99
              ? 0.84
              : 0.58
          : 0.03;
        context.beginPath();
        context.arc(screenX[index], screenY[index], nodeRadius(index), 0, Math.PI * 2);
        context.fillStyle = nodeColor(index);
        context.fill();
      }
      context.globalAlpha = 1;
      context.globalCompositeOperation = "source-over";

      for (const index of nodeVisuals.landmarkIndices.slice(0, 18)) {
        if (!nodeVisible(index)) continue;
        if (screenInPlot[index] === 0) continue;
        const radius = nodeRadius(index);
        context.beginPath();
        context.arc(screenX[index], screenY[index], radius + 3.8, 0, Math.PI * 2);
        context.strokeStyle = "rgba(238,241,242,.28)";
        context.lineWidth = 0.55;
        context.stroke();
        context.beginPath();
        context.arc(screenX[index], screenY[index], radius + 7.2, 0, Math.PI * 2);
        context.strokeStyle = "rgba(220,225,228,.12)";
        context.lineWidth = 0.45;
        context.stroke();
      }

      if (focused !== null) {
        context.fillStyle =
          propsRef.current.selectedIndex !== null
            ? "rgba(0,0,0,.59)"
            : "rgba(0,0,0,.29)";
        context.fillRect(plotX - plotRadius, plotY - plotRadius, plotRadius * 2, plotRadius * 2);
        const connected = new Set<number>([focused]);
        for (const edgeIndex of edgesByNode[focused] ?? []) {
          if (!edgeVisible(edgeIndex)) continue;
          const edge = graph.edges[edgeIndex];
          if (!nodeVisible(edge.s) || !nodeVisible(edge.t)) continue;
          connected.add(edge.s);
          connected.add(edge.t);
          context.beginPath();
          context.moveTo(screenX[edge.s], screenY[edge.s]);
          context.lineTo(screenX[edge.t], screenY[edge.t]);
          context.strokeStyle = "rgba(236,239,241,.55)";
          context.lineWidth = clamp(0.55 + Math.log1p(edge.strength) * 0.16, 0.6, 1.6);
          context.stroke();
        }
        for (const index of connected) {
          const isFocus = index === focused;
          context.beginPath();
          context.arc(screenX[index], screenY[index], isFocus ? 4 : 1.6, 0, Math.PI * 2);
          context.fillStyle = isFocus ? "#ffffff" : nodeColor(index);
          context.globalAlpha = isFocus ? 1 : 0.82;
          context.fill();
        }
        context.globalAlpha = 1;
      }
      context.restore();

      context.beginPath();
      context.arc(plotX, plotY, plotRadius, 0, Math.PI * 2);
      context.strokeStyle = "rgba(225,229,232,.16)";
      context.lineWidth = 0.7;
      context.stroke();

      if (width >= 900 && camera.zoom < 1.32 && focused === null) {
        for (const index of annotationIndices) drawCallout(index);
      }
      if (focused !== null) drawCallout(focused, true);
    };

    let scheduledDraw: number | null = null;
    const scheduleDraw = () => {
      if (scheduledDraw !== null) return;
      scheduledDraw = requestAnimationFrame(() => {
        scheduledDraw = null;
        draw();
      });
    };
    requestDrawRef.current = scheduleDraw;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      state.width = Math.max(rect.width, 1);
      state.height = Math.max(rect.height, 1);
      state.dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(state.width * state.dpr);
      canvas.height = Math.round(state.height * state.dpr);
      const desktop = state.width >= 900;
      state.sidebarEdge = desktop ? clamp(state.width * 0.305, 360, 470) : 0;
      const availableWidth = state.width - state.sidebarEdge;
      state.plotRadius = desktop
        ? Math.min(state.height * 0.435, availableWidth * 0.43)
        : Math.min(state.width * 0.57, state.height * 0.38);
      state.plotX = desktop
        ? state.sidebarEdge + availableWidth * 0.56
        : state.width * 0.5;
      state.plotY = desktop ? state.height * 0.5 : state.height * 0.46;
      state.baseScale = Math.min(
        (state.plotRadius * 1.82) / spanX,
        (state.plotRadius * 1.82) / spanY,
      );
      draw();
    };

    const animateTo = (target: Camera, duration = 560) => {
      if (state.animation !== null) cancelAnimationFrame(state.animation);
      state.animation = null;
      const from = { ...state.camera };
      const start = performance.now();
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const actualDuration = reduced ? 1 : duration;
      const frame = (time: number) => {
        const progress = clamp((time - start) / actualDuration, 0, 1);
        const eased = easeOutQuart(progress);
        state.camera = {
          x: from.x + (target.x - from.x) * eased,
          y: from.y + (target.y - from.y) * eased,
          zoom: from.zoom + (target.zoom - from.zoom) * eased,
        };
        scheduleDraw();
        if (progress < 1) state.animation = requestAnimationFrame(frame);
        else state.animation = null;
      };
      state.animation = requestAnimationFrame(frame);
    };
    animateToRef.current = animateTo;

    const findNearest = (clientX: number, clientY: number) => {
      const rect = canvas.getBoundingClientRect();
      const x = clientX - rect.left;
      const y = clientY - rect.top;
      if (!inPlot(x, y, 8)) return null;
      let bestIndex: number | null = null;
      let bestDistanceSquared = 13 ** 2;
      for (let index = 0; index < graph.nodes.length; index += 1) {
        if (!nodeVisible(index)) continue;
        const deltaX = screenX[index] - x;
        const deltaY = screenY[index] - y;
        const distanceSquared = deltaX * deltaX + deltaY * deltaY;
        if (distanceSquared < bestDistanceSquared) {
          bestDistanceSquared = distanceSquared;
          bestIndex = index;
        }
      }
      return bestIndex;
    };

    const pointerDown = (event: PointerEvent) => {
      state.dragging = true;
      state.moved = false;
      state.pointerId = event.pointerId;
      state.lastX = event.clientX;
      state.lastY = event.clientY;
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add("is-dragging");
    };
    const pointerMove = (event: PointerEvent) => {
      if (state.dragging && state.pointerId === event.pointerId) {
        const deltaX = event.clientX - state.lastX;
        const deltaY = event.clientY - state.lastY;
        if (Math.abs(deltaX) + Math.abs(deltaY) > 1) state.moved = true;
        state.camera.x -= deltaX / (state.baseScale * state.camera.zoom);
        state.camera.y -= deltaY / (state.baseScale * state.camera.zoom);
        state.lastX = event.clientX;
        state.lastY = event.clientY;
        scheduleDraw();
        return;
      }
      propsRef.current.onHover(findNearest(event.clientX, event.clientY));
    };
    const pointerUp = (event: PointerEvent) => {
      if (state.pointerId !== event.pointerId) return;
      if (!state.moved) propsRef.current.onSelect(findNearest(event.clientX, event.clientY));
      state.dragging = false;
      state.pointerId = null;
      canvas.classList.remove("is-dragging");
      try {
        canvas.releasePointerCapture(event.pointerId);
      } catch {
        // Browser already released this pointer.
      }
      scheduleDraw();
    };
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const worldX = (x - state.plotX) / (state.baseScale * state.camera.zoom) + state.camera.x;
      const worldY = (y - state.plotY) / (state.baseScale * state.camera.zoom) + state.camera.y;
      const zoom = clamp(state.camera.zoom * Math.exp(-event.deltaY * 0.0011), 0.5, 11);
      state.camera.zoom = zoom;
      state.camera.x = worldX - (x - state.plotX) / (state.baseScale * zoom);
      state.camera.y = worldY - (y - state.plotY) / (state.baseScale * zoom);
      lowDetailUntil = performance.now() + 90;
      if (settleTimer !== null) clearTimeout(settleTimer);
      settleTimer = setTimeout(() => {
        settleTimer = null;
        scheduleDraw();
      }, 100);
      scheduleDraw();
    };
    const doubleClick = (event: MouseEvent) => {
      const index = findNearest(event.clientX, event.clientY);
      if (index === null) return;
      const node = graph.nodes[index];
      propsRef.current.onSelect(index);
      animateTo({ x: node.x, y: node.y, zoom: 3.5 }, 520);
    };
    const leave = () => {
      if (!state.dragging) propsRef.current.onHover(null);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    canvas.addEventListener("pointerdown", pointerDown);
    canvas.addEventListener("pointermove", pointerMove);
    canvas.addEventListener("pointerup", pointerUp);
    canvas.addEventListener("pointercancel", pointerUp);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("wheel", wheel, { passive: false });
    canvas.addEventListener("dblclick", doubleClick);
    resize();

    return () => {
      observer.disconnect();
      canvas.removeEventListener("pointerdown", pointerDown);
      canvas.removeEventListener("pointermove", pointerMove);
      canvas.removeEventListener("pointerup", pointerUp);
      canvas.removeEventListener("pointercancel", pointerUp);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("wheel", wheel);
      canvas.removeEventListener("dblclick", doubleClick);
      if (state.animation !== null) cancelAnimationFrame(state.animation);
      if (scheduledDraw !== null) cancelAnimationFrame(scheduledDraw);
      if (settleTimer !== null) clearTimeout(settleTimer);
      if (requestDrawRef.current === scheduleDraw) {
        requestDrawRef.current = () => undefined;
      }
      if (animateToRef.current === animateTo) {
        animateToRef.current = () => undefined;
      }
    };
  }, [
    annotationIndices,
    bounds,
    communityVisuals,
    edgeLayers,
    edgesByNode,
    facet,
    facetColors,
    facetOrder,
    graph,
    nodeVisuals,
  ]);

  useEffect(() => {
    const state = stateRef.current;
    if (viewCommand.type === "zoom") {
      animateToRef.current(
        { ...state.camera, zoom: clamp(state.camera.zoom * (viewCommand.factor ?? 1), 0.5, 11) },
        220,
      );
      return;
    }
    if (viewCommand.type === "fit") {
      animateToRef.current(
        {
          x: (bounds.minX + bounds.maxX) / 2,
          y: (bounds.minY + bounds.maxY) / 2,
          zoom: 0.96,
        },
        520,
      );
      return;
    }
    const indices = viewCommand.indices?.filter(
      (index) => index >= 0 && index < graph.nodes.length,
    );
    if (!indices?.length) return;
    const points = indices.map((index) => graph.nodes[index]);
    const minX = Math.min(...points.map((point) => point.x));
    const maxX = Math.max(...points.map((point) => point.x));
    const minY = Math.min(...points.map((point) => point.y));
    const maxY = Math.max(...points.map((point) => point.y));
    const span = Math.max(maxX - minX, maxY - minY, (bounds.maxX - bounds.minX) * 0.025);
    const zoom = clamp(
      (state.plotRadius * 0.62) / (span * state.baseScale),
      indices.length === 1 ? 3.2 : 1.7,
      indices.length === 1 ? 6 : 4.2,
    );
    animateToRef.current(
      { x: (minX + maxX) / 2, y: (minY + maxY) / 2, zoom },
      560,
    );
  }, [bounds, graph, viewCommand]);

  const contextMenu = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    onSelect(null);
  };

  return (
    <canvas
      ref={canvasRef}
      className="galaxy-canvas"
      aria-label="开源仓库协作星图。拖动平移，滚轮缩放，点击节点查看详情。"
      role="img"
      tabIndex={0}
      onContextMenu={contextMenu}
    />
  );
}
