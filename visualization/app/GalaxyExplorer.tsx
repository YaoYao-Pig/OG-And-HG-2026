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
import {
  nodeFacetValue,
  type EdgeDensity,
  type GalaxyFacetItem,
  type GalaxyGraph,
  type GalaxyNode,
  type ViewCommand,
} from "./galaxy-types";

type LoadState = "loading" | "ready" | "error";
type SourceKey = "github" | "huggingface";

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

const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");
const GLOW_PREFERENCE_KEY = "open-galaxy:glow-enabled";

const SOURCE_COPY = {
  github: {
    title: "OpenGalaxy",
    period: "2025—26",
    figure: "FIG. 01 — OPEN SOURCE COLLABORATION NETWORK",
    dataFile: "/data/graph.json",
    defaultFacet: "language",
    search: "Search repository / language / topic",
    noun: "repositories",
    relation: "relations",
    link: "View repository on GitHub ↗",
    footer: "OPEN GALAXY / COLLABORATION ATLAS / 2026",
  },
  huggingface: {
    title: "ModelGalaxy",
    period: "DAILY",
    figure: "FIG. 02 — HUGGING FACE MODEL LINEAGE & HUB ECOSYSTEM",
    dataFile: "/data/huggingface.json",
    defaultFacet: "task",
    search: "Search model / dataset / Space / author",
    noun: "artifacts",
    relation: "relations",
    link: "View artifact on Hugging Face ↗",
    footer: "MODEL GALAXY / MODEL LINEAGE ATLAS / 2026",
  },
} as const;

const FACET_LABELS: Record<string, { short: string; full: string }> = {
  language: { short: "LANG", full: "Language / 语言" },
  ai: { short: "AI", full: "AI Area / AI 领域" },
  domain: { short: "DOMAIN", full: "Domain / 总领域" },
  type: { short: "TYPE", full: "Artifact / 载体" },
  task: { short: "TASK", full: "AI Task / 任务" },
  library: { short: "LIB", full: "Library / 框架" },
  organization: { short: "ORG", full: "Organization / 组织" },
};

function assetPath(path: string) {
  return `${BASE_PATH}${path.startsWith("/") ? path : `/${path}`}`;
}

function formatInteger(value: number) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}

function formatCompact(value: number) {
  return new Intl.NumberFormat("en-US", {
    notation: value >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: value >= 10_000 ? 1 : 0,
  }).format(value);
}

function formatScore(value: number) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: value >= 100 ? 1 : 2,
  }).format(value);
}

function topicList(node: GalaxyNode) {
  if (Array.isArray(node.topics)) return node.topics.filter(Boolean).slice(0, 6);
  return node.topics.split("|").filter(Boolean).slice(0, 6);
}

function facetItems(graph: GalaxyGraph | null, facet: string): GalaxyFacetItem[] {
  if (!graph) return [];
  if (graph.meta.facets?.[facet]) return graph.meta.facets[facet];
  if (facet === "language") return graph.meta.languages ?? [];
  return [];
}

async function fetchGraph(
  url: string,
  onProgress: (progress: number) => void,
  signal: AbortSignal,
) {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
  let progress = 0.08;
  onProgress(progress);
  const progressTimer = window.setInterval(() => {
    progress = Math.min(0.94, progress + (0.94 - progress) * 0.035);
    onProgress(progress);
  }, 240);
  try {
    const payload = (await response.json()) as GalaxyGraph;
    onProgress(1);
    return payload;
  } finally {
    window.clearInterval(progressTimer);
  }
}

export default function GalaxyExplorer() {
  const [source, setSource] = useState<SourceKey>("github");
  const [graph, setGraph] = useState<GalaxyGraph | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadProgress, setLoadProgress] = useState(0.03);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [activeFacet, setActiveFacet] = useState("language");
  const [selectedFacetValue, setSelectedFacetValue] = useState<string | null>(null);
  const [density, setDensity] = useState<EdgeDensity>("all");
  const [glowEnabled, setGlowEnabled] = useState(true);
  const [glowPreferenceReady, setGlowPreferenceReady] = useState(false);
  const [minStrength, setMinStrength] = useState(0);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [areasExpanded, setAreasExpanded] = useState(false);
  const [viewCommand, setViewCommand] = useState<ViewCommand>({ id: 0, type: "fit" });
  const searchRef = useRef<HTMLInputElement>(null);
  const copy = SOURCE_COPY[source];

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const storedPreference = window.localStorage.getItem(GLOW_PREFERENCE_KEY);
        if (storedPreference === "off") setGlowEnabled(false);
      } catch {
        // Storage can be unavailable in privacy-restricted browser contexts.
      }
      setGlowPreferenceReady(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!glowPreferenceReady) return;
    try {
      window.localStorage.setItem(
        GLOW_PREFERENCE_KEY,
        glowEnabled ? "on" : "off",
      );
    } catch {
      // The switch still works for this session when storage is unavailable.
    }
  }, [glowEnabled, glowPreferenceReady]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (window.location.hash.toLowerCase() === "#huggingface") {
        setSource("huggingface");
        setGraph(null);
        setLoadState("loading");
        setLoadProgress(0.03);
        setActiveFacet("task");
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let readyTimer: number | undefined;
    fetchGraph(assetPath(copy.dataFile), setLoadProgress, controller.signal)
      .then((payload) => {
        const order = payload.meta.facetOrder ?? Object.keys(payload.meta.facets ?? {});
        const nextFacet = order.includes(copy.defaultFacet)
          ? copy.defaultFacet
          : order[0] ?? (source === "github" ? "language" : "type");
        setGraph(payload);
        setActiveFacet(nextFacet);
        readyTimer = window.setTimeout(() => setLoadState("ready"), 160);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.error(error);
        setLoadState("error");
      });
    return () => {
      controller.abort();
      if (readyTimer !== undefined) window.clearTimeout(readyTimer);
    };
  }, [copy.dataFile, copy.defaultFacet, source]);

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

  const changeSource = useCallback((next: SourceKey) => {
    if (next === source) return;
    setSource(next);
    setGraph(null);
    setLoadState("loading");
    setLoadProgress(0.03);
    setSelectedIndex(null);
    setHoveredIndex(null);
    setSelectedFacetValue(null);
    setQuery("");
    setSearchOpen(false);
    setMobileOpen(false);
    setAreasExpanded(false);
    setDensity("all");
    setMinStrength(0);
    setViewCommand((current) => ({ id: current.id + 1, type: "fit" }));
    window.history.replaceState(null, "", next === "huggingface" ? "#huggingface" : "#github");
  }, [source]);

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

  const availableFacets = useMemo(() => {
    if (!graph) return [];
    const preferred = source === "github"
      ? ["language", "ai", "domain"]
      : ["type", "task", "library", "organization"];
    const available = new Set([
      ...(graph.meta.facetOrder ?? []),
      ...Object.keys(graph.meta.facets ?? {}),
      ...(graph.meta.languages?.length ? ["language"] : []),
    ]);
    return [...preferred.filter((key) => available.has(key)), ...[...available].filter((key) => !preferred.includes(key))];
  }, [graph, source]);

  const areas = useMemo(
    () => facetItems(graph, activeFacet).filter((item) => activeFacet !== "ai" || item.name !== "Non-AI"),
    [activeFacet, graph],
  );

  const searchResults = useMemo(() => {
    if (!graph || query.trim().length < 2) return [];
    const needle = query.trim().toLocaleLowerCase();
    return graph.nodes
      .map((node, index) => ({ node, index }))
      .filter(({ node }) =>
        `${node.name} ${node.author ?? ""} ${node.lang} ${node.sourceType ?? ""} ${Object.values(node.areas ?? {}).map((value) => typeof value === "string" ? value : value?.primary ?? "").join(" ")} ${Array.isArray(node.topics) ? node.topics.join(" ") : node.topics}`
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

  const selectedNode = graph && selectedIndex !== null ? graph.nodes[selectedIndex] : null;
  const hoveredNode = graph && hoveredIndex !== null ? graph.nodes[hoveredIndex] : null;

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

  const strongestPair = useMemo(() => {
    if (!graph?.edges.length) return null;
    if (source === "github") {
      const names = graph.meta.twinNames ?? [
        "OneCommunityGlobal/HGNRest",
        "OneCommunityGlobal/HighestGoodNetworkApp",
      ];
      const indices = names
        .map((name) => graph.nodes.findIndex((node) => node.name === name))
        .filter((index) => index >= 0);
      if (indices.length === 2) {
        const edge = graph.edges.find((item) =>
          (item.s === indices[0] && item.t === indices[1]) ||
          (item.t === indices[0] && item.s === indices[1]));
        return { indices, edge: edge ?? graph.edges[0] };
      }
    }
    const edge = graph.edges[0];
    return { indices: [edge.s, edge.t], edge };
  }, [graph, source]);

  const focusNode = useCallback((index: number) => {
    setSelectedIndex(index);
    setSelectedFacetValue(null);
    setQuery("");
    setSearchOpen(false);
    setMobileOpen(true);
    setViewCommand((current) => ({ id: current.id + 1, type: "focus", indices: [index] }));
  }, []);

  const focusStrongest = useCallback(() => {
    if (!strongestPair || strongestPair.indices.length !== 2) return;
    setSelectedFacetValue(null);
    setSelectedIndex(strongestPair.indices[1]);
    setViewCommand((current) => ({
      id: current.id + 1,
      type: "focus",
      indices: strongestPair.indices,
    }));
  }, [strongestPair]);

  const overview = useCallback(() => {
    setSelectedIndex(null);
    setSelectedFacetValue(null);
    setHoveredIndex(null);
    setViewCommand((current) => ({ id: current.id + 1, type: "fit" }));
  }, []);

  const searchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && searchResults[0]) focusNode(searchResults[0].index);
  };

  const strongestNames = strongestPair && graph
    ? strongestPair.indices.map((index) => graph.nodes[index]?.name ?? "—")
    : [];

  const sourceTabs = (className: string) => (
    <nav className={className} aria-label="数据源">
      <button type="button" className={source === "github" ? "is-active" : ""} onClick={() => changeSource("github")}>01 GITHUB</button>
      <button type="button" className={source === "huggingface" ? "is-active" : ""} onClick={() => changeSource("huggingface")}>02 HUGGING FACE</button>
    </nav>
  );

  return (
    <main className={`scientific-plate source-${source}`}>
      {graph ? (
        <GalaxyCanvas
          graph={graph}
          selectedIndex={selectedIndex}
          hoveredIndex={hoveredIndex}
          facet={activeFacet}
          facetValue={selectedFacetValue}
          density={density}
          glowEnabled={glowEnabled}
          minStrength={minStrength}
          viewCommand={viewCommand}
          onSelect={(index) => {
            setSelectedIndex(index);
            if (index !== null) {
              setSelectedFacetValue(null);
              setMobileOpen(true);
            }
          }}
          onHover={setHoveredIndex}
        />
      ) : null}

      <header className="mobile-masthead">
        <button type="button" onClick={overview}>{copy.title}</button>
        {sourceTabs("mobile-source-tabs")}
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
          <span>{selectedNode?.name ?? `${copy.title} / ${formatInteger(graph?.nodes.length ?? 0)} ${copy.noun}`}</span>
          <b>{mobileOpen ? "Close" : "Info"}</b>
        </button>

        <div className="editorial-scroll">
          {sourceTabs("source-tabs")}
          <p className="figure-index">{copy.figure}</p>
          <button className="plate-title" type="button" onClick={overview}>
            {copy.title} <span>{copy.period}</span>
          </button>
          <div className="editorial-rule"><i /></div>

          <div className="plain-search">
            <label className="sr-only" htmlFor="repository-search">搜索图谱</label>
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
              placeholder={copy.search}
              autoComplete="off"
            />
            <span>⌘K</span>
            {searchOpen && query.trim().length >= 2 ? (
              <div className="plain-search-results" role="listbox" aria-label="图谱搜索结果">
                {searchResults.length ? searchResults.map(({ node, index }) => (
                  <button
                    type="button"
                    role="option"
                    aria-selected={false}
                    key={node.id}
                    onClick={() => focusNode(index)}
                  >
                    <strong>{node.name}</strong>
                    <small>{nodeFacetValue(node, activeFacet)} / {formatScore(node.r)}</small>
                  </button>
                )) : <p>NO MATCHING ARTIFACT</p>}
              </div>
            ) : null}
          </div>

          {selectedNode ? (
            <section className="repository-sheet">
              <button className="text-back" type="button" onClick={overview}>← Return to overview</button>
              <p className="repo-classification">
                {((selectedNode.sourceType ?? selectedNode.lang) || "Unknown").toUpperCase()} / {nodeFacetValue(selectedNode, activeFacet).toUpperCase()}
              </p>
              <h1>{selectedNode.name}</h1>
              <p className="repo-copy">{selectedNode.description || "No public description available."}</p>

              <dl className="plain-metrics">
                {source === "github" ? (
                  <>
                    <div><dt>OpenRank</dt><dd>{formatScore(selectedNode.r)}</dd></div>
                    <div><dt>Contributors</dt><dd>{formatInteger(selectedNode.contributors)}</dd></div>
                    <div><dt>Direct links</dt><dd>{formatInteger(selectedNode.degree)}</dd></div>
                    <div><dt>Link strength</dt><dd>{formatScore(strengthByNode[selectedIndex ?? 0] ?? 0)}</dd></div>
                  </>
                ) : (
                  <>
                    <div><dt>Trending</dt><dd>{formatScore(selectedNode.trending ?? selectedNode.r)}</dd></div>
                    <div><dt>Likes</dt><dd>{formatCompact(selectedNode.likes ?? 0)}</dd></div>
                    <div><dt>Downloads</dt><dd>{formatCompact(selectedNode.downloads ?? 0)}</dd></div>
                    <div><dt>Direct links</dt><dd>{formatInteger(selectedNode.degree)}</dd></div>
                  </>
                )}
              </dl>

              {topicList(selectedNode).length ? (
                <p className="topic-line">{topicList(selectedNode).map((topic) => `#${topic}`).join("   ")}</p>
              ) : null}

              <div className="relation-index">
                <div className="table-header"><span>Strongest relation</span><span>{source === "github" ? "Shared" : "Kind"}</span><span>Strength</span></div>
                {selectedConnections.map(({ edge, node, otherIndex }) => (
                  <button type="button" key={`${edge.s}-${edge.t}-${edge.kind ?? "relation"}`} onClick={() => focusNode(otherIndex)}>
                    <span>{node.name}</span>
                    <span>{source === "github" ? (edge.shared ?? 0) : (edge.kind ?? "link").replaceAll("_", " ")}</span>
                    <span>{formatScore(edge.strength)}</span>
                  </button>
                ))}
              </div>

              <a className="plain-link" href={selectedNode.url} target="_blank" rel="noreferrer">
                {copy.link}
              </a>
            </section>
          ) : (
            <>
              <section className="plate-introduction">
                {source === "github" ? (
                  <>
                    <p>
                      OpenGalaxy is generated from the contributor collaboration network of active GitHub repositories observed across twelve complete months. The graph contains <b>{formatInteger(graph?.nodes.length ?? 19_771)} repositories</b> and <b>{formatInteger(graph?.edges.length ?? 106_650)} relations</b>.
                    </p>
                    <p>
                      OpenGalaxy 由 2025 年 8 月至 2026 年 7 月间的 GitHub 开源协作网络生成。现在可按编程语言、AI 子领域与总技术领域切换观察。
                    </p>
                  </>
                ) : (
                  <>
                    <p>
                      ModelGalaxy distills the daily <b>Hugging Face Hub</b> snapshot into a high-signal model lineage and artifact ecosystem. This plate contains <b>{formatInteger(graph?.nodes.length ?? 0)} artifacts</b> and <b>{formatInteger(graph?.edges.length ?? 0)} declared or inferred relations</b>.
                    </p>
                    <p>
                      数据来自 cfahlgren1/hub-stats 的每日快照。模型、数据集与 Space 通过谱系、发布者和受控语义亲和形成可浏览星图；全量五百余万条记录不会直接下发浏览器。
                    </p>
                  </>
                )}
              </section>

              <section className="area-index">
                <nav className="facet-switch" aria-label="Area 分类维度">
                  {availableFacets.map((key) => (
                    <button
                      type="button"
                      key={key}
                      className={activeFacet === key ? "is-active" : ""}
                      onClick={() => {
                        setActiveFacet(key);
                        setSelectedFacetValue(null);
                        setSelectedIndex(null);
                        setAreasExpanded(false);
                      }}
                    >
                      {FACET_LABELS[key]?.short ?? key.toUpperCase()}
                    </button>
                  ))}
                </nav>
                <div className="area-header">
                  <span>{FACET_LABELS[activeFacet]?.full ?? "Area / 领域"}</span>
                  <span>Representative / 代表项目</span>
                  <span>Count</span>
                </div>
                <button
                  type="button"
                  className={selectedFacetValue === null ? "is-active" : ""}
                  onClick={() => setSelectedFacetValue(null)}
                >
                  <i className="all-areas" />
                  <span><b>ALL</b><small>Complete network</small></span>
                  <span>{graph ? formatInteger(graph.nodes.length) : "—"}</span>
                </button>
                {(areasExpanded ? areas : areas.slice(0, 9)).map((item, index) => {
                  const representative = item.representative ?? graph?.nodes.find((node) => nodeFacetValue(node, activeFacet) === item.name)?.name ?? "—";
                  return (
                    <button
                      type="button"
                      key={item.name}
                      className={selectedFacetValue === item.name ? "is-active" : ""}
                      onClick={() => setSelectedFacetValue((current) => current === item.name ? null : item.name)}
                    >
                      <i style={{ background: item.color ?? AREA_COLORS[index % AREA_COLORS.length] }} />
                      <span><b>{item.name || "Unknown"}</b><small>{representative}</small></span>
                      <span>{formatInteger(item.count)}</span>
                    </button>
                  );
                })}
                {areas.length > 9 ? (
                  <button
                    type="button"
                    className="area-expand"
                    aria-expanded={areasExpanded}
                    onClick={() => setAreasExpanded((expanded) => !expanded)}
                  >
                    <span>{areasExpanded ? "SHOW TOP AREAS" : `VIEW ALL ${areas.length} AREAS`}</span>
                    <span>{areasExpanded ? "−" : `+${areas.length - 9}`}</span>
                  </button>
                ) : null}
              </section>

              {strongestPair && strongestNames.length === 2 ? (
                <button className="strongest-relation" type="button" onClick={focusStrongest}>
                  <span>{source === "github" ? "Strongest recorded relation" : "Strongest visible lineage"}</span>
                  <b>{strongestNames[0]} ↔ {strongestNames[1]}</b>
                  <small>
                    {source === "github"
                      ? `${strongestPair.edge.shared ?? 0} shared contributors / strength ${formatScore(strongestPair.edge.strength)}`
                      : `${(strongestPair.edge.kind ?? "relation").replaceAll("_", " ")} / strength ${formatScore(strongestPair.edge.strength)}`}
                  </small>
                </button>
              ) : null}
            </>
          )}

          <section className="plot-controls" aria-label="图谱绘制控制">
            <span>Plot</span>
            <button type="button" className={density === "all" ? "is-active" : ""} onClick={() => setDensity("all")}>All relations</button>
            <button type="button" className={density === "signal" ? "is-active" : ""} onClick={() => setDensity("signal")}>Structural spine</button>
            <button
              type="button"
              role="switch"
              aria-checked={glowEnabled}
              aria-label="辉光效果"
              className={`glow-toggle${glowEnabled ? " is-active" : ""}`}
              onClick={() => setGlowEnabled((enabled) => !enabled)}
            >
              Glow <span aria-hidden="true">{glowEnabled ? "On" : "Off"}</span>
            </button>
            <label>
              Strength ≥ {minStrength.toFixed(1)}
              <input type="range" min="0" max="10" step="0.5" value={minStrength} onChange={(event) => setMinStrength(Number(event.target.value))} />
            </label>
          </section>

          <div className="method-notes">
            {source === "github" ? (
              <>
                <p>[1] 数据周期：2025.08—2026.07；元数据分类按 curated label → topic → description 逐级回填。</p>
                <p>[2] 关系表示共享已知非 Bot 贡献者形成的协作亲和，不代表代码依赖或组织归属。</p>
              </>
            ) : (
              <>
                <p>[1] 来源：cfahlgren1/hub-stats / revision {graph?.meta.sourceRevision?.slice(0, 8) ?? "current"} / daily snapshot.</p>
                <p>[2] 谱系与同发布者关系为直接信号；语义亲和边标记为 inferred，不等同于依赖关系。</p>
              </>
            )}
            <p>[3] 概览等高线表示节点密度；比例尺使用布局坐标，不代表物理距离。</p>
          </div>
        </div>
      </aside>

      <div className="plate-meta" aria-label="图谱统计">
        <span>{source === "github" ? "2025.08 — 2026.07" : (graph?.meta.snapshotAt ?? graph?.meta.generatedAt ?? "DAILY SNAPSHOT").slice(0, 10)}</span>
        <span>NODES <b>{formatInteger(graph?.nodes.length ?? 0)}</b></span>
        <span>EDGES <b>{formatInteger(graph?.edges.length ?? 0)}</b></span>
      </div>

      {hoveredNode && selectedIndex === null ? (
        <div className="hover-readout">
          <strong>{hoveredNode.name}</strong>
          <span>{nodeFacetValue(hoveredNode, activeFacet)} / {source === "github" ? `OpenRank ${formatScore(hoveredNode.r)}` : `Trend ${formatScore(hoveredNode.trending ?? hoveredNode.r)}`} / {formatInteger(hoveredNode.degree)} links</span>
        </div>
      ) : null}

      <nav className="plain-map-controls" aria-label="地图控制">
        <button type="button" onClick={() => setViewCommand((current) => ({ id: current.id + 1, type: "zoom", factor: 1.35 }))} aria-label="放大">＋</button>
        <button type="button" onClick={() => setViewCommand((current) => ({ id: current.id + 1, type: "zoom", factor: 0.74 }))} aria-label="缩小">−</button>
        <button type="button" onClick={() => setViewCommand((current) => ({ id: current.id + 1, type: "fit" }))}>FIT</button>
      </nav>

      <footer className="plate-footer">
        <span>{copy.footer}</span>
        <span>DRAG TO EXPLORE · SCROLL TO SCALE · CLICK TO INSPECT</span>
      </footer>

      {loadState !== "ready" ? (
        <div className={`minimal-loader state-${loadState}`} role="status" aria-live="polite">
          {loadState === "error" ? (
            <>
              <p>Unable to load {copy.title} field data.</p>
              <button type="button" onClick={() => window.location.reload()}>Retry</button>
            </>
          ) : (
            <>
              <p>{copy.title} / loading field data</p>
              <div><i style={{ width: `${Math.max(3, loadProgress * 100)}%` }} /></div>
              <span>{Math.round(loadProgress * 100).toString().padStart(2, "0")}%</span>
            </>
          )}
        </div>
      ) : null}
    </main>
  );
}
