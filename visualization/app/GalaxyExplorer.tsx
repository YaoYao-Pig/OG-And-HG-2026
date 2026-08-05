"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { GalaxyCanvas } from "./components/GalaxyCanvas";
import type {
  EdgeDensity,
  GalaxyGraph,
  GalaxyNode,
  ViewCommand,
} from "./galaxy-types";

type LoadState = "loading" | "ready" | "error";

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

function formatInteger(value: number) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}

function formatScore(value: number) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: value >= 100 ? 1 : 2,
  }).format(value);
}

function topicList(node: GalaxyNode) {
  if (Array.isArray(node.topics)) return node.topics.filter(Boolean).slice(0, 5);
  return node.topics.split("|").filter(Boolean).slice(0, 5);
}

async function fetchGraph(
  onProgress: (progress: number) => void,
  signal: AbortSignal,
) {
  const response = await fetch("/data/graph.json", { signal });
  if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
  const total = Number(response.headers.get("content-length")) || 0;
  if (!response.body) {
    onProgress(1);
    return (await response.json()) as GalaxyGraph;
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(total ? Math.min(received / total, 0.99) : Math.min(received / 5_000_000, 0.96));
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  onProgress(1);
  return JSON.parse(new TextDecoder().decode(bytes)) as GalaxyGraph;
}

export default function GalaxyExplorer() {
  const [graph, setGraph] = useState<GalaxyGraph | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadProgress, setLoadProgress] = useState(0.03);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [selectedLanguage, setSelectedLanguage] = useState<string | null>(null);
  const [density, setDensity] = useState<EdgeDensity>("all");
  const [minStrength, setMinStrength] = useState(0);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [viewCommand, setViewCommand] = useState<ViewCommand>({ id: 0, type: "fit" });
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchGraph(setLoadProgress, controller.signal)
      .then((payload) => {
        setGraph(payload);
        window.setTimeout(() => setLoadState("ready"), 160);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.error(error);
        setLoadState("error");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedIndex(null);
        setHoveredIndex(null);
        setSearchOpen(false);
        setMobileOpen(false);
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setMobileOpen(true);
        window.setTimeout(() => searchRef.current?.focus(), 0);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);

  const adjacency = useMemo(() => {
    if (!graph) return [] as number[][];
    const result = Array.from({ length: graph.nodes.length }, () => [] as number[]);
    graph.edges.forEach((edge, edgeIndex) => {
      result[edge.s]?.push(edgeIndex);
      result[edge.t]?.push(edgeIndex);
    });
    return result;
  }, [graph]);

  const strengthByNode = useMemo(() => {
    if (!graph) return [] as number[];
    const values = Array.from({ length: graph.nodes.length }, () => 0);
    for (const edge of graph.edges) {
      values[edge.s] += edge.strength;
      values[edge.t] += edge.strength;
    }
    return values;
  }, [graph]);

  const searchResults = useMemo(() => {
    if (!graph || query.trim().length < 2) return [];
    const needle = query.trim().toLocaleLowerCase();
    return graph.nodes
      .map((node, index) => ({ node, index }))
      .filter(({ node }) =>
        `${node.name} ${node.lang} ${Array.isArray(node.topics) ? node.topics.join(" ") : node.topics}`
          .toLocaleLowerCase()
          .includes(needle),
      )
      .sort((a, b) => {
        const aStarts = a.node.name.toLocaleLowerCase().startsWith(needle) ? 1 : 0;
        const bStarts = b.node.name.toLocaleLowerCase().startsWith(needle) ? 1 : 0;
        return bStarts - aStarts || b.node.r - a.node.r;
      })
      .slice(0, 7);
  }, [graph, query]);

  const selectedNode =
    graph && selectedIndex !== null ? graph.nodes[selectedIndex] : null;
  const hoveredNode =
    graph && hoveredIndex !== null ? graph.nodes[hoveredIndex] : null;

  const selectedConnections = useMemo(() => {
    if (!graph || selectedIndex === null) return [];
    return (adjacency[selectedIndex] ?? [])
      .map((edgeIndex) => {
        const edge = graph.edges[edgeIndex];
        const otherIndex = edge.s === selectedIndex ? edge.t : edge.s;
        return { edge, otherIndex, node: graph.nodes[otherIndex] };
      })
      .sort((a, b) => b.edge.strength - a.edge.strength)
      .slice(0, 5);
  }, [adjacency, graph, selectedIndex]);

  const languages = useMemo(() => {
    if (!graph) return [];
    return (graph.meta.languages ?? []).slice(0, 9).map((item) => ({
      ...item,
      representative:
        graph.nodes.find((node) => node.lang === item.name)?.name ?? "—",
    }));
  }, [graph]);

  const twinIndices = useMemo(() => {
    if (!graph) return [];
    if (graph.meta.twinIds?.length === 2 && graph.meta.twinIds.every((id) => typeof id === "number")) {
      return graph.meta.twinIds as number[];
    }
    const names = graph.meta.twinNames ?? [
      "OneCommunityGlobal/HGNRest",
      "OneCommunityGlobal/HighestGoodNetworkApp",
    ];
    return names
      .map((name) => graph.nodes.findIndex((node) => node.name === name))
      .filter((index) => index >= 0);
  }, [graph]);

  const focusNode = useCallback((index: number) => {
    setSelectedIndex(index);
    setSelectedLanguage(null);
    setQuery("");
    setSearchOpen(false);
    setMobileOpen(true);
    setViewCommand((current) => ({
      id: current.id + 1,
      type: "focus",
      indices: [index],
    }));
  }, []);

  const focusTwins = useCallback(() => {
    if (twinIndices.length !== 2) return;
    setSelectedLanguage(null);
    setSelectedIndex(twinIndices[1]);
    setViewCommand((current) => ({
      id: current.id + 1,
      type: "focus",
      indices: twinIndices,
    }));
  }, [twinIndices]);

  const overview = useCallback(() => {
    setSelectedIndex(null);
    setSelectedLanguage(null);
    setHoveredIndex(null);
    setViewCommand((current) => ({ id: current.id + 1, type: "fit" }));
  }, []);

  const searchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && searchResults[0]) focusNode(searchResults[0].index);
  };

  return (
    <main className="scientific-plate">
      {graph ? (
        <GalaxyCanvas
          graph={graph}
          selectedIndex={selectedIndex}
          hoveredIndex={hoveredIndex}
          language={selectedLanguage}
          density={density}
          minStrength={minStrength}
          viewCommand={viewCommand}
          onSelect={(index) => {
            setSelectedIndex(index);
            if (index !== null) {
              setSelectedLanguage(null);
              setMobileOpen(true);
            }
          }}
          onHover={setHoveredIndex}
        />
      ) : null}

      <header className="mobile-masthead">
        <button type="button" onClick={overview}>OpenGalaxy</button>
        <span>2025—26</span>
        <button
          type="button"
          aria-label="打开搜索与说明"
          onClick={() => {
            setMobileOpen(true);
            window.setTimeout(() => searchRef.current?.focus(), 220);
          }}
        >
          Search
        </button>
      </header>

      <aside className={`editorial-column ${mobileOpen ? "is-open" : ""}`}>
        <button
          className="mobile-sheet-handle"
          type="button"
          onClick={() => setMobileOpen((open) => !open)}
          aria-expanded={mobileOpen}
        >
          <span>{selectedNode?.name ?? "OpenGalaxy / 4,243 repositories"}</span>
          <b>{mobileOpen ? "Close" : "Info"}</b>
        </button>

        <div className="editorial-scroll">
          <p className="figure-index">FIG. 01 — OPEN SOURCE COLLABORATION NETWORK</p>
          <button className="plate-title" type="button" onClick={overview}>
            OpenGalaxy <span>2025—26</span>
          </button>
          <div className="editorial-rule"><i /></div>

          <div className="plain-search">
            <label className="sr-only" htmlFor="repository-search">搜索仓库</label>
            <input
              id="repository-search"
              ref={searchRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              onKeyDown={searchKeyDown}
              placeholder="Search repository / language / topic"
              autoComplete="off"
            />
            <span>⌘K</span>
            {searchOpen && query.trim().length >= 2 ? (
              <div className="plain-search-results" role="listbox" aria-label="仓库搜索结果">
                {searchResults.length ? searchResults.map(({ node, index }) => (
                  <button
                    type="button"
                    role="option"
                    aria-selected={false}
                    key={node.id}
                    onClick={() => focusNode(index)}
                  >
                    <strong>{node.name}</strong>
                    <small>{node.lang} / {formatScore(node.r)}</small>
                  </button>
                )) : <p>NO MATCHING REPOSITORY</p>}
              </div>
            ) : null}
          </div>

          {selectedNode ? (
            <section className="repository-sheet">
              <button className="text-back" type="button" onClick={overview}>← Return to overview</button>
              <p className="repo-classification">{selectedNode.lang || "Unknown"} / COMMUNITY {selectedNode.c + 1}</p>
              <h1>{selectedNode.name}</h1>
              <p className="repo-copy">{selectedNode.description || "No repository description available."}</p>

              <dl className="plain-metrics">
                <div><dt>OpenRank</dt><dd>{formatScore(selectedNode.r)}</dd></div>
                <div><dt>Contributors</dt><dd>{formatInteger(selectedNode.contributors)}</dd></div>
                <div><dt>Direct links</dt><dd>{formatInteger(selectedNode.degree)}</dd></div>
                <div><dt>Link strength</dt><dd>{formatScore(strengthByNode[selectedIndex ?? 0] ?? 0)}</dd></div>
              </dl>

              {topicList(selectedNode).length ? (
                <p className="topic-line">{topicList(selectedNode).map((topic) => `#${topic}`).join("   ")}</p>
              ) : null}

              <div className="relation-index">
                <div className="table-header"><span>Strongest relation</span><span>Shared</span><span>Strength</span></div>
                {selectedConnections.map(({ edge, node, otherIndex }) => (
                  <button type="button" key={`${edge.s}-${edge.t}`} onClick={() => focusNode(otherIndex)}>
                    <span>{node.name}</span>
                    <span>{edge.shared}</span>
                    <span>{formatScore(edge.strength)}</span>
                  </button>
                ))}
              </div>

              <a className="plain-link" href={selectedNode.url} target="_blank" rel="noreferrer">
                View repository on GitHub ↗
              </a>
            </section>
          ) : (
            <>
              <section className="plate-introduction">
                <p>
                  OpenGalaxy is generated from the contributor collaboration network of active GitHub repositories observed across twelve complete months. The graph contains <b>4,243 repositories</b> and <b>46,173 relations</b>.
                </p>
                <p>
                  OpenGalaxy 由 2025 年 8 月至 2026 年 7 月间的 GitHub 开源协作网络生成。节点代表仓库；连接表示至少两位已知非 Bot 贡献者同时参与过两个仓库。
                </p>
              </section>

              <section className="area-index">
                <div className="area-header">
                  <span>Area / 领域</span>
                  <span>Representative repo / 代表项目</span>
                  <span>Count</span>
                </div>
                <button
                  type="button"
                  className={selectedLanguage === null ? "is-active" : ""}
                  onClick={() => setSelectedLanguage(null)}
                >
                  <i className="all-areas" />
                  <span><b>ALL</b><small>Complete network</small></span>
                  <span>{graph ? formatInteger(graph.nodes.length) : "—"}</span>
                </button>
                {languages.slice(0, 8).map((item, index) => (
                  <button
                    type="button"
                    key={item.name}
                    className={selectedLanguage === item.name ? "is-active" : ""}
                    onClick={() => setSelectedLanguage((current) => current === item.name ? null : item.name)}
                  >
                    <i style={{ background: AREA_COLORS[index] }} />
                    <span><b>{item.name || "Unknown"}</b><small>{item.representative}</small></span>
                    <span>{formatInteger(item.count)}</span>
                  </button>
                ))}
              </section>

              <button className="strongest-relation" type="button" onClick={focusTwins}>
                <span>Strongest recorded relation</span>
                <b>HGNRest ↔ HighestGoodNetworkApp</b>
                <small>{graph?.meta.twinEdge?.shared ?? 102} shared contributors / strength {formatScore(graph?.meta.twinEdge?.strength ?? 371.8995)}</small>
              </button>
            </>
          )}

          <section className="plot-controls" aria-label="图谱绘制控制">
            <span>Plot</span>
            <button type="button" className={density === "all" ? "is-active" : ""} onClick={() => setDensity("all")}>All relations</button>
            <button type="button" className={density === "signal" ? "is-active" : ""} onClick={() => setDensity("signal")}>Structural spine</button>
            <label>
              Strength ≥ {minStrength.toFixed(1)}
              <input type="range" min="0" max="10" step="0.5" value={minStrength} onChange={(event) => setMinStrength(Number(event.target.value))} />
            </label>
          </section>

          <div className="method-notes">
            <p>[1] 数据周期：2025.08—2026.07；来源完整性检查：PASS。</p>
            <p>[2] 关系表示共享已知非 Bot 贡献者形成的协作亲和，不代表代码依赖或组织归属。</p>
          </div>
        </div>
      </aside>

      <div className="plate-meta" aria-label="图谱统计">
        <span>2025.08 — 2026.07</span>
        <span>NODES <b>4,243</b></span>
        <span>EDGES <b>46,173</b></span>
      </div>

      {hoveredNode && selectedIndex === null ? (
        <div className="hover-readout">
          <strong>{hoveredNode.name}</strong>
          <span>{hoveredNode.lang} / OpenRank {formatScore(hoveredNode.r)} / {formatInteger(hoveredNode.degree)} links</span>
        </div>
      ) : null}

      <nav className="plain-map-controls" aria-label="地图控制">
        <button type="button" onClick={() => setViewCommand((current) => ({ id: current.id + 1, type: "zoom", factor: 1.35 }))} aria-label="放大">＋</button>
        <button type="button" onClick={() => setViewCommand((current) => ({ id: current.id + 1, type: "zoom", factor: 0.74 }))} aria-label="缩小">−</button>
        <button type="button" onClick={() => setViewCommand((current) => ({ id: current.id + 1, type: "fit" }))}>FIT</button>
      </nav>

      <footer className="plate-footer">
        <span>OPEN GALAXY / COLLABORATION ATLAS / 2026</span>
        <span>DRAG TO EXPLORE · SCROLL TO SCALE · CLICK TO INSPECT</span>
      </footer>

      {loadState !== "ready" ? (
        <div className={`minimal-loader state-${loadState}`} role="status" aria-live="polite">
          {loadState === "error" ? (
            <>
              <p>Unable to load graph data.</p>
              <button type="button" onClick={() => window.location.reload()}>Retry</button>
            </>
          ) : (
            <>
              <p>OpenGalaxy / loading field data</p>
              <div><i style={{ width: `${Math.max(3, loadProgress * 100)}%` }} /></div>
              <span>{Math.round(loadProgress * 100).toString().padStart(2, "0")}%</span>
            </>
          )}
        </div>
      ) : null}
    </main>
  );
}
