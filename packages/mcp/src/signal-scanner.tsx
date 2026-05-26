import type { App, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { Fragment, StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import styles from "./signal-scanner.module.css";

interface Signal {
  ticker: string;
  company_name?: string;
  name?: string;
  sector?: string;
  composite_score?: number;
  cluster_score?: number;
  buyback_score?: number;
  insider_count?: number;
  num_insiders?: number;
  has_cluster?: boolean;
  cluster_detected?: boolean;
}

type SortKey = "composite_score" | "cluster_score" | "buyback_score" | "insider_count" | "ticker";

const IMPLEMENTATION = { name: "Signal Scanner", version: "1.0.0" };

function SignalScannerApp() {
  const [hostContext, setHostContext] = useState<McpUiHostContext | undefined>();
  const { app, error } = useApp({
    appInfo: IMPLEMENTATION,
    capabilities: {},
    onAppCreated: (app) => {
      app.onhostcontextchanged = (params) => {
        setHostContext((prev) => ({ ...prev, ...params }));
      };
    },
  });

  useEffect(() => {
    if (app) setHostContext(app.getHostContext());
  }, [app]);

  if (error) return <div className={styles.error}>ERROR: {error.message}</div>;
  if (!app) return <div className={styles.loading}>Connecting...</div>;

  return <Inner app={app} hostContext={hostContext} />;
}

interface ClusterDetail {
  ticker: string;
  cluster: any;
  activity: any;
}

function Inner({ app, hostContext }: { app: App; hostContext?: McpUiHostContext }) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("composite_score");
  const [sortAsc, setSortAsc] = useState(false);
  const [sectorFilter, setSectorFilter] = useState("");
  const [clusterOnly, setClusterOnly] = useState(false);
  const [minScore, setMinScore] = useState(0);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [clusterDetail, setClusterDetail] = useState<ClusterDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await app.callServerTool({
        name: "get-signal-scanner",
        arguments: { limit: 100, cluster_only: clusterOnly, min_score: minScore || undefined, sector: sectorFilter || undefined },
      });
      const sc = result.structuredContent as any;
      setSignals(sc?.signals ?? []);
    } catch (e) {
      console.error("Failed to fetch signals:", e);
    } finally {
      setLoading(false);
    }
  }, [app, clusterOnly, minScore, sectorFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const sectors = useMemo(() => {
    const s = new Set(signals.map((sig) => sig.sector).filter(Boolean));
    return Array.from(s).sort() as string[];
  }, [signals]);

  const sorted = useMemo(() => {
    const filtered = signals.filter((s) => {
      if (sectorFilter && s.sector !== sectorFilter) return false;
      if (clusterOnly && !s.has_cluster && !s.cluster_detected) return false;
      if (minScore && (s.composite_score ?? 0) < minScore) return false;
      return true;
    });
    return [...filtered].sort((a, b) => {
      const av = (a as any)[sortKey] ?? 0;
      const bv = (b as any)[sortKey] ?? 0;
      if (typeof av === "string") return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortAsc ? av - bv : bv - av;
    });
  }, [signals, sortKey, sortAsc, sectorFilter, clusterOnly, minScore]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const handleRowClick = async (ticker: string) => {
    if (selectedTicker === ticker) {
      setSelectedTicker(null);
      setClusterDetail(null);
      return;
    }
    setSelectedTicker(ticker);
    setDetailLoading(true);
    try {
      const result = await app.callServerTool({ name: "get-cluster-detail", arguments: { ticker } });
      const sc = result.structuredContent as any;
      setClusterDetail({ ticker, cluster: sc?.cluster, activity: sc?.activity });
    } catch (e) {
      console.error("Cluster detail call failed:", e);
      setClusterDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const maxScore = useMemo(() => Math.max(...sorted.map((s) => s.composite_score ?? 0), 1), [sorted]);

  return (
    <main className={styles.container} style={{
      paddingTop: hostContext?.safeAreaInsets?.top,
      paddingRight: hostContext?.safeAreaInsets?.right,
      paddingBottom: hostContext?.safeAreaInsets?.bottom,
      paddingLeft: hostContext?.safeAreaInsets?.left,
    }}>
      <header className={styles.header}>
        <h1 className={styles.title}>🔍 Insider Signal Scanner</h1>
        <div className={styles.controls}>
          <select value={sectorFilter} onChange={(e) => setSectorFilter(e.target.value)}>
            <option value="">All Sectors</option>
            {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className={styles.toggle}>
            <input type="checkbox" checked={clusterOnly} onChange={(e) => setClusterOnly(e.target.checked)} />
            Clusters only
          </label>
          <label className={styles.slider}>
            Min: {minScore}
            <input type="range" min={0} max={200} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
          </label>
        </div>
      </header>

      {loading ? (
        <div className={styles.loading}>Loading signals...</div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>#</th>
                <th className={styles.sortable} onClick={() => handleSort("ticker")}>Ticker</th>
                <th>Name</th>
                <th>Sector</th>
                <th className={styles.sortable} onClick={() => handleSort("composite_score")}>
                  Score {sortKey === "composite_score" ? (sortAsc ? "▲" : "▼") : ""}
                </th>
                <th className={styles.sortable} onClick={() => handleSort("cluster_score")}>Cluster</th>
                <th className={styles.sortable} onClick={() => handleSort("buyback_score")}>Buyback</th>
                <th className={styles.sortable} onClick={() => handleSort("insider_count")}>Insiders</th>
                <th>🔥</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((sig, i) => {
                const score = sig.composite_score ?? 0;
                const pct = (score / maxScore) * 100;
                const isSelected = selectedTicker === sig.ticker;
                const rowClass = isSelected ? styles.rowSelected : score >= 150 ? styles.rowHigh : score >= 80 ? styles.rowMed : "";
                return (
                  <Fragment key={sig.ticker}>
                    <tr className={`${styles.row} ${rowClass}`} onClick={() => handleRowClick(sig.ticker)} style={{ cursor: "pointer" }}>
                      <td className={styles.rank}>{i + 1}</td>
                      <td className={styles.ticker}>{sig.ticker}</td>
                      <td className={styles.name}>{sig.company_name ?? sig.name ?? "—"}</td>
                      <td>{sig.sector ?? "—"}</td>
                      <td>
                        <div className={styles.scoreBar}>
                          <div className={styles.scoreBarFill} style={{ width: `${pct}%` }} />
                          <span className={styles.scoreLabel}>{score.toFixed(3)}</span>
                        </div>
                      </td>
                      <td className={styles.num}>{(sig.cluster_score ?? 0).toFixed(1)}</td>
                      <td className={styles.num}>{(sig.buyback_score ?? 0).toFixed(1)}</td>
                      <td className={styles.num}>{sig.insider_count ?? sig.num_insiders ?? "—"}</td>
                      <td>{(sig.has_cluster || sig.cluster_detected) ? "🔥" : ""}</td>
                    </tr>
                    {isSelected && (
                      <tr className={styles.detailRow}>
                        <td colSpan={9}>
                          {detailLoading ? (
                            <div className={styles.detailLoading}>Loading cluster detail for {sig.ticker}...</div>
                          ) : clusterDetail ? (
                            <DetailPanel detail={clusterDetail} />
                          ) : (
                            <div className={styles.detailLoading}>No data available</div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function DetailPanel({ detail }: { detail: ClusterDetail }) {
  const cluster = detail.cluster ?? {};
  const activity = detail.activity ?? {};
  const summary = activity.summary ?? {};
  const trades = cluster.trades ?? [];
  const purchases = activity.purchases ?? [];

  return (
    <div className={styles.detailPanel}>
      <div className={styles.detailHeader}>
        <h3>{detail.ticker} — Cluster Detail</h3>
        <span className={styles.detailBadge}>
          {cluster.cluster_detected ? "🔥 Cluster Active" : "No Cluster"}
        </span>
      </div>

      <div className={styles.detailStats}>
        <div className={styles.stat}>
          <span className={styles.statLabel}>Cluster Score</span>
          <span className={styles.statValue}>{(cluster.score ?? 0).toFixed(1)}</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>Unique Insiders</span>
          <span className={styles.statValue}>{summary.unique_insiders ?? "—"}</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>Total Value</span>
          <span className={styles.statValue}>${((summary.total_value ?? 0) / 1000).toFixed(0)}K</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>Total Purchases</span>
          <span className={styles.statValue}>{summary.total_purchases ?? purchases.length}</span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statLabel}>Date Range</span>
          <span className={styles.statValue}>{summary.date_range?.earliest ?? "—"} → {summary.date_range?.latest ?? "—"}</span>
        </div>
      </div>

      {trades.length > 0 && (
        <>
          <h4 className={styles.detailSubhead}>Recent Cluster Trades (90d)</h4>
          <table className={styles.detailTable}>
            <thead>
              <tr><th>Date</th><th>Insider</th><th>Shares</th><th>Price</th><th>Value</th></tr>
            </thead>
            <tbody>
              {trades.map((t: any, i: number) => (
                <tr key={i}>
                  <td>{t.date}</td>
                  <td>{t.name}</td>
                  <td className={styles.num}>{(t.shares ?? 0).toLocaleString()}</td>
                  <td className={styles.num}>${(t.price ?? 0).toFixed(2)}</td>
                  <td className={styles.num}>${(t.value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><SignalScannerApp /></StrictMode>,
);
