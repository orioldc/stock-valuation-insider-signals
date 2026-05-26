import type { App, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import styles from "./cluster-detail.module.css";

interface Transaction {
  insider_name?: string;
  title?: string;
  date?: string;
  shares?: number;
  price?: number;
  value?: number;
  transaction_type?: string;
}

interface BuybackInfo {
  is_buyback?: boolean;
  trend?: string;
  delta_qoq?: number | null;
  delta_4q?: number | null;
  intensity_score_raw?: number | null;
  relevance_score?: number | null;
  tier?: string;
  tier_weight?: number;
  tier_percentile?: number | null;
  market_cap?: number | null;
  data_points?: number;
  latest_shares?: number | null;
  latest_date?: string | null;
}

interface ClusterData {
  cluster: {
    ticker?: string;
    company_name?: string;
    cluster_score?: number;
    score?: number;
    score_raw?: number;
    relevance_score?: number;
    tier?: string;
    tier_weight?: number;
    tier_percentile?: number;
    market_cap?: number;
    cluster_detected?: boolean;
    unique_insiders?: number;
    total_value?: number;
    date_range?: { start?: string; end?: string };
    start_date?: string;
    end_date?: string;
    buyback?: BuybackInfo;
  };
  activity: {
    transactions?: Transaction[];
    ticker?: string;
  };
}

const IMPLEMENTATION = { name: "Cluster Detail", version: "1.0.0" };

function getInsiderColor(title?: string): string {
  if (!title) return "#8b949e";
  const t = title.toLowerCase();
  if (t.includes("ceo") || t.includes("cfo") || t.includes("chief")) return "#d29922";
  if (t.includes("vp") || t.includes("officer") || t.includes("svp") || t.includes("evp")) return "#58a6ff";
  return "#8b949e";
}

function fmt$(n: number): string {
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function ClusterDetailApp() {
  const [hostContext, setHostContext] = useState<McpUiHostContext | undefined>();
  const { app, error } = useApp({
    appInfo: IMPLEMENTATION,
    capabilities: {},
    onAppCreated: (app) => {
      app.onhostcontextchanged = (params) => setHostContext((prev) => ({ ...prev, ...params }));
    },
  });

  useEffect(() => { if (app) setHostContext(app.getHostContext()); }, [app]);

  if (error) return <div className={styles.error}>ERROR: {error.message}</div>;
  if (!app) return <div className={styles.loading}>Connecting...</div>;

  return <Inner app={app} hostContext={hostContext} />;
}

function Inner({ app, hostContext }: { app: App; hostContext?: McpUiHostContext }) {
  const [data, setData] = useState<ClusterData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Data comes from structuredContent when tool is invoked
    const sc = (app as any).getStructuredContent?.() as ClusterData | undefined;
    if (sc) { setData(sc); setLoading(false); return; }

    // Fallback: try calling tool
    (async () => {
      try {
        // The ticker would come from context - default to showing what we have
        setLoading(false);
      } catch { setLoading(false); }
    })();
  }, [app]);

  if (loading) return <div className={styles.loading}>Loading...</div>;
  if (!data) return <div className={styles.empty}>No cluster data. Use get-cluster-detail tool with a ticker.</div>;

  return <ClusterView data={data} hostContext={hostContext} />;
}

function ClusterView({ data, hostContext }: { data: ClusterData; hostContext?: McpUiHostContext }) {
  const { cluster, activity } = data;
  const ticker = cluster.ticker ?? activity.ticker ?? "???";
  const score = cluster.cluster_score ?? cluster.score ?? 0;
  const detected = cluster.cluster_detected ?? score > 0;
  const txns = activity.transactions ?? [];
  const totalValue = cluster.total_value ?? txns.reduce((s, t) => s + (t.value ?? 0), 0);
  const uniqueInsiders = cluster.unique_insiders ?? new Set(txns.map((t) => t.insider_name)).size;
  const buyback = cluster.buyback;
  const fmtPct = (v: number | null | undefined) =>
    typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "n/a";

  const dateRange = useMemo(() => {
    const start = cluster.date_range?.start ?? cluster.start_date;
    const end = cluster.date_range?.end ?? cluster.end_date;
    if (start && end) return `${start} → ${end}`;
    const dates = txns.map((t) => t.date).filter(Boolean).sort();
    if (dates.length) return `${dates[0]} → ${dates[dates.length - 1]}`;
    return "—";
  }, [cluster, txns]);

  const avgSize = txns.length > 0 ? totalValue / txns.length : 0;

  // Timeline
  const sortedTxns = useMemo(() =>
    [...txns].filter((t) => t.date).sort((a, b) => (a.date ?? "").localeCompare(b.date ?? "")),
  [txns]);

  const timeRange = useMemo(() => {
    if (!sortedTxns.length) return { min: 0, max: 1 };
    const min = new Date(sortedTxns[0].date!).getTime();
    const max = new Date(sortedTxns[sortedTxns.length - 1].date!).getTime();
    return { min, max: max === min ? max + 1 : max };
  }, [sortedTxns]);

  return (
    <main className={styles.container} style={{
      paddingTop: hostContext?.safeAreaInsets?.top,
      paddingRight: hostContext?.safeAreaInsets?.right,
      paddingBottom: hostContext?.safeAreaInsets?.bottom,
      paddingLeft: hostContext?.safeAreaInsets?.left,
    }}>
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <h1 className={styles.ticker}>{ticker}</h1>
          <span className={styles.company}>{cluster.company_name ?? ""}</span>
        </div>
        <div className={styles.headerRight}>
          <span className={styles.score}>{score.toFixed(1)}</span>
          <span className={`${styles.badge} ${detected ? styles.badgeCluster : styles.badgeNone}`}>
            {detected ? "🔥 CLUSTER" : "NO CLUSTER"}
          </span>
          {buyback && (
            <span className={`${styles.badge} ${buyback.is_buyback ? styles.badgeCluster : styles.badgeNone}`}>
              {buyback.is_buyback ? "💰 BUYBACK" : "NO BUYBACK"}
            </span>
          )}
          {cluster.tier && (
            <span className={`${styles.badge} ${styles.badgeNone}`}>
              {cluster.tier.toUpperCase()}{typeof cluster.market_cap === "number" && cluster.market_cap > 0 ? ` · $${(cluster.market_cap / 1e9).toFixed(1)}B` : ""}
            </span>
          )}
        </div>
      </header>

      <div className={styles.stats}>
        <div className={styles.stat}><span className={styles.statLabel}>Insiders</span><span className={styles.statValue}>{uniqueInsiders}</span></div>
        <div className={styles.stat}><span className={styles.statLabel}>Total Value</span><span className={styles.statValue}>{fmt$(totalValue)}</span></div>
        <div className={styles.stat}><span className={styles.statLabel}>Avg Size</span><span className={styles.statValue}>{fmt$(avgSize)}</span></div>
        <div className={styles.stat}><span className={styles.statLabel}>Range</span><span className={styles.statValue}>{dateRange}</span></div>
        {typeof cluster.relevance_score === "number" && (
          <div className={styles.stat}>
            <span className={styles.statLabel}>Insider Relevance</span>
            <span className={styles.statValue}>{cluster.relevance_score.toFixed(2)}</span>
          </div>
        )}
        {buyback && (
          <>
            <div className={styles.stat}><span className={styles.statLabel}>Buyback QoQ</span><span className={styles.statValue}>{fmtPct(buyback.delta_qoq)}</span></div>
            <div className={styles.stat}><span className={styles.statLabel}>Buyback 4Q</span><span className={styles.statValue}>{fmtPct(buyback.delta_4q)}</span></div>
            {typeof buyback.relevance_score === "number" && (
              <div className={styles.stat}>
                <span className={styles.statLabel}>Buyback Relevance</span>
                <span className={styles.statValue}>{buyback.relevance_score.toFixed(2)}</span>
              </div>
            )}
          </>
        )}
      </div>

      <div className={styles.timeline}>
        <div className={styles.timelineTrack}>
          {sortedTxns.map((txn, i) => {
            const pos = ((new Date(txn.date!).getTime() - timeRange.min) / (timeRange.max - timeRange.min)) * 100;
            return (
              <div key={i} className={styles.timelineDot} style={{ left: `${pos}%`, borderColor: getInsiderColor(txn.title) }} title={`${txn.insider_name} — ${txn.date}`} />
            );
          })}
        </div>
      </div>

      <div className={styles.txnList}>
        {sortedTxns.map((txn, i) => (
          <div key={i} className={styles.txn}>
            <div className={styles.txnDot} style={{ background: getInsiderColor(txn.title) }} />
            <div className={styles.txnInfo}>
              <span className={styles.txnName}>{txn.insider_name ?? "Unknown"}</span>
              <span className={styles.txnTitle}>{txn.title ?? ""}</span>
            </div>
            <div className={styles.txnMeta}>
              <span className={styles.txnDate}>{txn.date ?? "—"}</span>
              <span className={styles.txnValue}>{txn.shares?.toLocaleString() ?? "—"} @ ${txn.price?.toFixed(2) ?? "—"}</span>
              <span className={styles.txnTotal}>{txn.value ? fmt$(txn.value) : "—"}</span>
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><ClusterDetailApp /></StrictMode>,
);
