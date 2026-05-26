import type { App, McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { useApp } from "@modelcontextprotocol/ext-apps/react";
import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import styles from "./valuation-card.module.css";

interface ValuationData {
  ticker?: string;
  company_name?: string;
  current_price?: number;
  intrinsic_value?: number;
  synthesized_value?: number;
  dcf_value?: number;
  relative_value?: number;
  wacc?: number;
  growth_rate?: number;
  terminal_growth_rate?: number;
  risk_flags?: string[];
  insider_signal?: { cluster_detected?: boolean; cluster_score?: number; insider_count?: number };
  verdict?: string;
}

const IMPLEMENTATION = { name: "Valuation Card", version: "1.0.0" };

function ValuationCardApp() {
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
  const [data, setData] = useState<ValuationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Try to get data from host context (passed from tool result)
  useEffect(() => {
    const ctx = hostContext as any;
    const sc = ctx?.structuredContent ?? ctx?.toolResult?.structuredContent;
    if (sc && (sc.ticker || sc.current_price)) {
      setData(sc);
      setLoading(false);
      return;
    }
    // No data from host — show input form
    setLoading(false);
  }, [hostContext]);

  // Listen for data updates from the host
  useEffect(() => {
    if (app) {
      (app as any).ondatachanged = (params: any) => {
        if (params?.structuredContent) {
          setData(params.structuredContent);
        }
      };
    }
  }, [app]);

  const handleRun = async () => {
    if (!ticker.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await app.callServerTool({ name: "run-valuation", arguments: { ticker: ticker.trim().toUpperCase() } });
      const sc = result.structuredContent as any;
      if (sc) setData(sc);
      else setError("No data returned");
    } catch (e: any) {
      setError(e.message ?? "Valuation failed");
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className={styles.loading}>Running valuation...</div>;
  
  if (!data) return (
    <div className={styles.inputForm}>
      <h2 className={styles.formTitle}>📊 Run Valuation</h2>
      <div className={styles.formRow}>
        <input
          className={styles.tickerInput}
          type="text"
          placeholder="Enter ticker (e.g. AAPL)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && handleRun()}
        />
        <button className={styles.runBtn} onClick={handleRun}>Run</button>
      </div>
      {error && <div className={styles.error}>{error}</div>}
    </div>
  );

  return <Card data={data} hostContext={hostContext} onReset={() => { setData(null); setTicker(""); }} />;
}

function Card({ data, hostContext, onReset }: { data: ValuationData; hostContext?: McpUiHostContext; onReset?: () => void }) {
  const intrinsic = data.intrinsic_value ?? data.synthesized_value ?? 0;
  const current = data.current_price ?? 0;
  const diff = current > 0 ? ((intrinsic - current) / current) * 100 : 0;

  const verdict = useMemo(() => {
    if (data.verdict) return data.verdict;
    if (diff > 15) return "UNDERVALUED";
    if (diff < -15) return "OVERVALUED";
    return "FAIRLY VALUED";
  }, [data.verdict, diff]);

  const verdictClass = verdict.includes("UNDER") ? styles.verdictGreen
    : verdict.includes("OVER") ? styles.verdictRed
    : styles.verdictYellow;

  // Price bar visualization
  const maxVal = Math.max(intrinsic, current) * 1.2;
  const currentPct = (current / maxVal) * 100;
  const intrinsicPct = (intrinsic / maxVal) * 100;

  return (
    <main className={styles.container} style={{
      paddingTop: hostContext?.safeAreaInsets?.top,
      paddingRight: hostContext?.safeAreaInsets?.right,
      paddingBottom: hostContext?.safeAreaInsets?.bottom,
      paddingLeft: hostContext?.safeAreaInsets?.left,
    }}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.ticker}>{data.ticker ?? "???"}</h1>
          <span className={styles.company}>{data.company_name ?? ""}</span>
        </div>
        <div className={styles.headerRight}>
          <span className={styles.price}>${current.toFixed(2)}</span>
          <span className={`${styles.badge} ${verdictClass}`}>
            {verdict} ({diff >= 0 ? "+" : ""}{diff.toFixed(0)}%)
          </span>
        </div>
      </header>

      <div className={styles.priceBar}>
        <div className={styles.barLabel}>
          <span>Current: ${current.toFixed(2)}</span>
          <span>Intrinsic: ${intrinsic.toFixed(2)}</span>
        </div>
        <div className={styles.barTrack}>
          <div className={styles.barCurrent} style={{ width: `${currentPct}%` }} />
          <div className={styles.barIntrinsic} style={{ left: `${intrinsicPct}%` }} />
        </div>
      </div>

      <div className={styles.methods}>
        <h2 className={styles.sectionTitle}>Valuation Methods</h2>
        <div className={styles.methodGrid}>
          <div className={styles.method}>
            <span className={styles.methodLabel}>DCF</span>
            <span className={styles.methodValue}>${(data.dcf_value ?? 0).toFixed(2)}</span>
          </div>
          <div className={styles.method}>
            <span className={styles.methodLabel}>Relative</span>
            <span className={styles.methodValue}>${(data.relative_value ?? 0).toFixed(2)}</span>
          </div>
          <div className={styles.method}>
            <span className={styles.methodLabel}>Synthesized</span>
            <span className={`${styles.methodValue} ${styles.highlight}`}>${intrinsic.toFixed(2)}</span>
          </div>
        </div>
      </div>

      <div className={styles.metrics}>
        <h2 className={styles.sectionTitle}>Key Metrics</h2>
        <div className={styles.metricGrid}>
          {data.wacc != null && <div className={styles.metric}><span className={styles.metricLabel}>WACC</span><span className={styles.metricValue}>{(data.wacc * 100).toFixed(1)}%</span></div>}
          {data.growth_rate != null && <div className={styles.metric}><span className={styles.metricLabel}>Growth</span><span className={styles.metricValue}>{(data.growth_rate * 100).toFixed(1)}%</span></div>}
          {data.terminal_growth_rate != null && <div className={styles.metric}><span className={styles.metricLabel}>Terminal</span><span className={styles.metricValue}>{(data.terminal_growth_rate * 100).toFixed(1)}%</span></div>}
        </div>
      </div>

      {data.risk_flags && data.risk_flags.length > 0 && (
        <div className={styles.risks}>
          <h2 className={styles.sectionTitle}>⚠️ Risk Flags</h2>
          <ul className={styles.riskList}>
            {data.risk_flags.map((flag, i) => <li key={i}>{flag}</li>)}
          </ul>
        </div>
      )}

      {data.insider_signal && (
        <div className={styles.insider}>
          <h2 className={styles.sectionTitle}>Insider Signal</h2>
          <div className={styles.insiderInfo}>
            {data.insider_signal.cluster_detected && <span className={styles.clusterBadge}>🔥 Cluster</span>}
            {data.insider_signal.cluster_score != null && <span>Score: {data.insider_signal.cluster_score.toFixed(1)}</span>}
            {data.insider_signal.insider_count != null && <span>{data.insider_signal.insider_count} insiders</span>}
          </div>
        </div>
      )}

      {onReset && (
        <button className={styles.resetBtn} onClick={onReset}>← Run Another Valuation</button>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><ValuationCardApp /></StrictMode>,
);
