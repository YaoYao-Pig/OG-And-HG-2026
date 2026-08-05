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

type GalaxyCanvasProps = {
  graph: GalaxyGraph;
  selectedIndex: number | null;
  hoveredIndex: number | null;
  facet: string;
  facetValue: string | null;
  density: EdgeDensity;
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

export function GalaxyCanvas({
  graph,
  selectedIndex,
  hoveredIndex,
  facet,
  facetValue,
  density,
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

  const annotationIndices = useMemo(() => {
    const candidates = graph.nodes
      .map((node, index) => ({ node, index, angle: Math.atan2(node.y, node.x) }))
      .filter(({ node }) => Boolean(node.label))
      .sort((a, b) => b.node.r - a.node.r);
    const chosen: typeof candidates = [];
    const usedFacetValues = new Set<string>();
    for (const candidate of candidates) {
      if (chosen.length >= 14) break;
      const separated = chosen.every((item) => {
        const delta = Math.abs(candidate.angle - item.angle);
        return Math.min(delta, Math.PI * 2 - delta) > 0.23;
      });
      const value = nodeFacetValue(candidate.node, facet);
      if (!separated || usedFacetValues.has(value)) continue;
      chosen.push(candidate);
      usedFacetValues.add(value);
    }
    return chosen.map(({ index }) => index);
  }, [facet, graph]);

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
      minStrength,
      onSelect,
      onHover,
    };
    requestDrawRef.current();
  }, [density, facet, facetValue, hoveredIndex, minStrength, onHover, onSelect, selectedIndex]);

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

    const nodeVisible = (index: number) =>
      propsRef.current.facetValue === null ||
      nodeFacetValue(graph.nodes[index], propsRef.current.facet) ===
        propsRef.current.facetValue;

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
      const point = worldToScreen(node.x, node.y);
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

    const draw = () => {
      const { width, height, dpr, plotX, plotY, plotRadius, camera } = state;
      const focused = propsRef.current.selectedIndex ?? propsRef.current.hoveredIndex;
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

      context.beginPath();
      for (let edgeIndex = 0; edgeIndex < graph.edges.length; edgeIndex += 1) {
        if (!edgeVisible(edgeIndex)) continue;
        const edge = graph.edges[edgeIndex];
        const sourceVisible = nodeVisible(edge.s);
        const targetVisible = nodeVisible(edge.t);
        if (propsRef.current.facetValue !== null && (!sourceVisible || !targetVisible)) continue;
        const source = graph.nodes[edge.s];
        const target = graph.nodes[edge.t];
        const a = worldToScreen(source.x, source.y);
        const b = worldToScreen(target.x, target.y);
        if (!inPlot(a.x, a.y, 20) && !inPlot(b.x, b.y, 20)) continue;
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
      }
      context.strokeStyle =
        propsRef.current.facetValue === null
          ? propsRef.current.density === "all"
            ? "rgba(215,220,224,.047)"
            : "rgba(215,220,224,.075)"
          : "rgba(225,230,233,.14)";
      context.lineWidth = propsRef.current.density === "all" ? 0.34 : 0.46;
      context.stroke();

      context.beginPath();
      for (let edgeIndex = 0; edgeIndex < Math.min(500, graph.edges.length); edgeIndex += 1) {
        if (!edgeVisible(edgeIndex)) continue;
        const edge = graph.edges[edgeIndex];
        if (propsRef.current.facetValue !== null && (!nodeVisible(edge.s) || !nodeVisible(edge.t))) continue;
        const source = graph.nodes[edge.s];
        const target = graph.nodes[edge.t];
        const a = worldToScreen(source.x, source.y);
        const b = worldToScreen(target.x, target.y);
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
      }
      context.strokeStyle = "rgba(245,245,242,.14)";
      context.lineWidth = 0.48;
      context.stroke();

      for (let index = graph.nodes.length - 1; index >= 0; index -= 1) {
        const node = graph.nodes[index];
        const point = worldToScreen(node.x, node.y);
        if (!inPlot(point.x, point.y, 8)) continue;
        const visible = nodeVisible(index);
        const radius = clamp(
          0.38 + Math.log1p(Math.max(node.r, 0)) * 0.2 + Math.log1p(node.degree) * 0.018,
          0.5,
          3.15,
        ) * clamp(Math.pow(camera.zoom, 0.12), 0.9, 1.3);
        const facetRank = facetOrder.get(nodeFacetValue(node, facet)) ?? 99;
        context.globalAlpha = visible ? (facetRank < 99 ? 0.82 : 0.5) : 0.035;
        context.beginPath();
        context.arc(point.x, point.y, radius, 0, Math.PI * 2);
        context.fillStyle = nodeColor(index);
        context.fill();
      }
      context.globalAlpha = 1;

      if (focused !== null) {
        context.fillStyle = "rgba(0,0,0,.7)";
        context.fillRect(plotX - plotRadius, plotY - plotRadius, plotRadius * 2, plotRadius * 2);
        const connected = new Set<number>([focused]);
        for (const edgeIndex of edgesByNode[focused] ?? []) {
          const edge = graph.edges[edgeIndex];
          const source = graph.nodes[edge.s];
          const target = graph.nodes[edge.t];
          const a = worldToScreen(source.x, source.y);
          const b = worldToScreen(target.x, target.y);
          connected.add(edge.s);
          connected.add(edge.t);
          context.beginPath();
          context.moveTo(a.x, a.y);
          context.lineTo(b.x, b.y);
          context.strokeStyle = "rgba(236,239,241,.55)";
          context.lineWidth = clamp(0.55 + Math.log1p(edge.strength) * 0.16, 0.6, 1.6);
          context.stroke();
        }
        for (const index of connected) {
          const node = graph.nodes[index];
          const point = worldToScreen(node.x, node.y);
          const isFocus = index === focused;
          context.beginPath();
          context.arc(point.x, point.y, isFocus ? 4 : 1.6, 0, Math.PI * 2);
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

    requestDrawRef.current = draw;

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
        draw();
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
      let bestDistance = 13;
      for (let index = 0; index < graph.nodes.length; index += 1) {
        if (!nodeVisible(index)) continue;
        const node = graph.nodes[index];
        const point = worldToScreen(node.x, node.y);
        const distance = Math.hypot(point.x - x, point.y - y);
        if (distance < bestDistance) {
          bestDistance = distance;
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
        draw();
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
      draw();
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
    };
  }, [annotationIndices, bounds, edgesByNode, facet, facetColors, facetOrder, graph]);

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
