import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { ReadResourceResult } from "@modelcontextprotocol/sdk/types.js";
import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import {
  RESOURCE_MIME_TYPE,
  registerAppResource,
  registerAppTool,
} from "@modelcontextprotocol/ext-apps/server";

const DIST_DIR = import.meta.filename.endsWith(".ts")
  ? path.join(import.meta.dirname, "dist")
  : import.meta.dirname;

const API_BASE = process.env.API_BASE ?? "http://localhost:8502";

// ── Helpers ──

async function apiFetch(endpoint: string): Promise<any> {
  const res = await fetch(`${API_BASE}${endpoint}`);
  if (!res.ok) throw new Error(`API ${endpoint}: ${res.status} ${res.statusText}`);
  return res.json();
}

function readView(filename: string): Promise<string> {
  // Vite outputs to dist/views/, try both locations
  const viewsPath = path.join(DIST_DIR, "views", filename);
  const directPath = path.join(DIST_DIR, filename);
  return fs.readFile(viewsPath, "utf-8").catch(() => fs.readFile(directPath, "utf-8"));
}

// ── Schemas ──

const SignalScannerInputSchema = z.object({
  limit: z.number().min(1).max(500).optional().default(50),
  sector: z.string().optional(),
  cluster_only: z.boolean().optional().default(false),
  min_score: z.number().optional(),
});

const ClusterDetailInputSchema = z.object({
  ticker: z.string(),
});

const ValuationInputSchema = z.object({
  ticker: z.string(),
});

const BuybackInputSchema = z.object({
  ticker: z.string(),
});

// ── Server factory ──

export function createServer(): McpServer {
  const server = new McpServer(
    {
      name: "Insider Signal MCP",
      version: "0.2.0",
    },
    {
      instructions:
        "Per-ticker signals from a local insider-transaction + shares-outstanding database. " +
        "Exposes TWO orthogonal signal families: (1) insider open-market BUYING (Form 4 P-code) " +
        "with cluster detection, and (2) share BUYBACKS inferred from declining shares outstanding. " +
        "Use get-cluster-detail for a full per-ticker view (both signals + transaction list + UI card). " +
        "Use get-buyback-status when you only need a yes/no + magnitude answer about buybacks. " +
        "Use get-signal-scanner to rank tickers by the composite of both signals. " +
        "Use run-valuation for a DCF + relative-valuation view that also folds in the insider signal.",
    },
  );

  // ── Tool 1: Signal Scanner ──

  const scannerUri = "ui://get-signal-scanner/signal-scanner.html";

  registerAppTool(
    server,
    "get-signal-scanner",
    {
      title: "Scan tickers — top insider-buying + buyback signals",
      description:
        "Ranks tickers by a composite of (a) insider open-market buying clusters and " +
        "(b) share-buyback intensity (declining shares outstanding). Returns a ranked list. " +
        "For yes/no per-ticker buyback or insider-buy lookups use get-buyback-status or get-cluster-detail.",
      inputSchema: SignalScannerInputSchema.shape,
      _meta: { ui: { resourceUri: scannerUri } },
    },
    async ({ limit, sector, cluster_only, min_score }) => {
      const params = new URLSearchParams();
      params.set("limit", String(limit));
      if (sector) params.set("sector", sector);
      if (cluster_only) params.set("cluster_only", "true");
      if (min_score !== undefined) params.set("min_score", String(min_score));

      const data = await apiFetch(`/signals?${params}`);
      const signals = Array.isArray(data) ? data : data.signals ?? [];
      const clusters = signals.filter((s: any) => s.has_cluster || s.cluster_detected).length;

      // Tier distribution to give the LLM size context, since the new composite is size-adjusted.
      const tierCounts: Record<string, number> = {};
      for (const s of signals) {
        const t = s.tier ?? "unknown";
        tierCounts[t] = (tierCounts[t] ?? 0) + 1;
      }
      const tierLine = Object.entries(tierCounts)
        .sort(([, a], [, b]) => (b as number) - (a as number))
        .map(([t, n]) => `${t}=${n}`)
        .join(", ");

      const top = signals
        .slice(0, 10)
        .map(
          (s: any) =>
            `  ${s.ticker} [${s.tier ?? "?"}] composite=${(s.composite_score ?? 0).toFixed(2)} (insider ${(s.cluster_adjusted ?? 0).toFixed(2)}, buyback ${(s.buyback_adjusted ?? 0).toFixed(2)})`,
        )
        .join("\n");

      const text =
        `Top ${signals.length} signals (ranked by SIZE-ADJUSTED composite — bucket-percentile × tier weight; ` +
        `mid-cap is the sweet spot, micro-cap is down-weighted to suppress noise). ` +
        `${clusters} have an insider-buying cluster.\n` +
        `Tier mix: ${tierLine}\n` +
        (top ? `Top:\n${top}` : "");

      return {
        content: [{ type: "text", text }],
        structuredContent: { signals },
      };
    },
  );

  registerAppResource(
    server,
    scannerUri,
    scannerUri,
    { mimeType: RESOURCE_MIME_TYPE },
    async (): Promise<ReadResourceResult> => ({
      contents: [{ uri: scannerUri, mimeType: RESOURCE_MIME_TYPE, text: await readView("signal-scanner.html") }],
    }),
  );

  // ── Tool 2: Cluster Detail ──

  const clusterUri = "ui://get-cluster-detail/cluster-detail.html";

  registerAppTool(
    server,
    "get-cluster-detail",
    {
      title: "Per-ticker insider buying + share-buyback report",
      description:
        "For a given stock ticker, returns BOTH (a) insider open-market buying activity " +
        "(Form 4 P-code transactions, cluster detection, score, individual trades) AND " +
        "(b) share-buyback status (trailing-4Q and QoQ shares-outstanding delta, trend label, " +
        "whether ANY buyback is occurring regardless of intensity). " +
        "Use this whenever you want to know if a ticker has insider buying OR buybacks. " +
        "Renders a UI card; the text body always summarizes both signals so the LLM sees them in context.",
      inputSchema: ClusterDetailInputSchema.shape,
      _meta: { ui: { resourceUri: clusterUri } },
    },
    async ({ ticker }) => {
      const upper = ticker.toUpperCase();
      const [cluster, activity] = await Promise.all([
        apiFetch(`/cluster/${upper}`),
        apiFetch(`/insider_activity/${upper}`).catch(() => ({ summary: {}, purchases: [] })),
      ]);

      const score = cluster.cluster_score ?? cluster.score ?? 0;
      const summary = activity?.summary ?? {};
      const insiders = summary.unique_insiders ?? 0;
      const totalValue = summary.total_value ?? 0;
      const detected = cluster.cluster_detected ?? score > 0;

      const bb = cluster.buyback ?? {};
      const bbFlag = bb.is_buyback ? "yes" : "no";
      const fmtPct = (v: any) => (typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "n/a");

      const tier = cluster.tier ?? bb.tier ?? "unknown";
      const mcap = cluster.market_cap ?? bb.market_cap;
      const mcapStr = typeof mcap === "number" && mcap > 0 ? `$${(mcap / 1e9).toFixed(1)}B` : "n/a";
      const clusterRelevance = cluster.relevance_score ?? 0;
      const clusterPct = cluster.tier_percentile ?? 0;
      const bbRelevance = bb.relevance_score ?? 0;
      const bbPct = bb.tier_percentile ?? 0;

      const sizeLine = `Market cap tier: ${tier} (${mcapStr}). Scores below are SIZE-ADJUSTED — bucket-percentile × tier weight, so a 3% buyback at a mega-cap can outrank a 20% buyback at a micro-cap.`;
      const buybackLine = bb.data_points
        ? `Buyback: ${bbFlag} (trend=${bb.trend ?? "n/a"}, QoQ ${fmtPct(bb.delta_qoq)}, 4Q ${fmtPct(bb.delta_4q)}, ` +
          `tier-percentile ${(bbPct * 100).toFixed(0)}, relevance=${bbRelevance.toFixed(2)})`
        : "Buyback: no data";

      const insiderLine =
        `Insider cluster: ${detected ? "yes" : "no"} (raw score ${score.toFixed(1)}, ` +
        `tier-percentile ${(clusterPct * 100).toFixed(0)}, relevance=${clusterRelevance.toFixed(2)}), ` +
        `${insiders} unique insider buyers, $${(totalValue / 1000).toFixed(0)}K total open-market purchases`;

      const salient =
        bb.is_buyback || detected || clusterRelevance >= 0.5 || bbRelevance >= 0.5;
      const text = [
        `${upper} signal summary${salient ? " (relevant signal present)" : ""}:`,
        `- ${sizeLine}`,
        `- ${insiderLine}`,
        `- ${buybackLine}`,
      ].join("\n");

      return {
        content: [{ type: "text", text }],
        structuredContent: { cluster, activity },
      };
    },
  );

  registerAppResource(
    server,
    clusterUri,
    clusterUri,
    { mimeType: RESOURCE_MIME_TYPE },
    async (): Promise<ReadResourceResult> => ({
      contents: [{ uri: clusterUri, mimeType: RESOURCE_MIME_TYPE, text: await readView("cluster-detail.html") }],
    }),
  );

  // ── Tool 3: Valuation ──

  const valuationUri = "ui://run-valuation/valuation-card.html";

  registerAppTool(
    server,
    "run-valuation",
    {
      title: "Run DCF + relative valuation for a ticker",
      description:
        "Damodaran-style DCF + relative-multiples valuation for a stock ticker. Returns " +
        "intrinsic value, current price, verdict (UNDERVALUED/FAIRLY VALUED/OVERVALUED), DCF " +
        "assumptions, and folds in the insider-buying signal. For buyback or insider data " +
        "without a valuation, use get-buyback-status or get-cluster-detail instead.",
      inputSchema: ValuationInputSchema.shape,
      _meta: { ui: { resourceUri: valuationUri } },
    },
    async ({ ticker }) => {
      const upper = ticker.toUpperCase();
      const data = await apiFetch(`/valuation/${upper}`);

      const intrinsic = data.intrinsic_value ?? data.synthesized_value ?? 0;
      const current = data.current_price ?? 0;
      const diff = current > 0 ? ((intrinsic - current) / current) * 100 : 0;
      const verdict =
        data.verdict ??
        (diff > 15 ? "UNDERVALUED" : diff < -15 ? "OVERVALUED" : "FAIRLY VALUED");

      const fallback = `${upper}: Intrinsic $${intrinsic.toFixed(2)} vs Current $${current.toFixed(2)} — ${verdict} (${diff >= 0 ? "+" : ""}${diff.toFixed(0)}%)`;
      const text = data.summary_text ?? fallback;

      return {
        content: [{ type: "text", text }],
        structuredContent: data,
      };
    },
  );

  registerAppResource(
    server,
    valuationUri,
    valuationUri,
    { mimeType: RESOURCE_MIME_TYPE },
    async (): Promise<ReadResourceResult> => ({
      contents: [{ uri: valuationUri, mimeType: RESOURCE_MIME_TYPE, text: await readView("valuation-card.html") }],
    }),
  );

  // ── Tool 4: Buyback status (no UI; plain text for the LLM) ──

  server.registerTool(
    "get-buyback-status",
    {
      title: "Is a ticker buying back its shares?",
      description:
        "Yes/no + magnitude buyback check for one ticker, INDEPENDENT of the scanner's intensity " +
        "threshold. Returns the trailing-4-quarter and QoQ share-count delta and a trend label " +
        "(buyback / dilution / stable) inferred from shares-outstanding history. Use this when " +
        "the user asks 'has BKNG been doing buybacks' or 'does TICKER repurchase shares' — " +
        "this is the dedicated buyback lookup. For combined insider+buyback view, use get-cluster-detail.",
      inputSchema: BuybackInputSchema.shape,
    },
    async ({ ticker }) => {
      const upper = ticker.toUpperCase();
      const bb = await apiFetch(`/buyback/${upper}`);

      const fmtPct = (v: any) =>
        typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "n/a";
      const fmtShares = (v: any) =>
        typeof v === "number" ? `${(v / 1e6).toFixed(2)}M` : "n/a";

      let text: string;
      if (!bb.data_points) {
        text = `${upper}: no shares-outstanding data available — cannot determine buyback status.`;
      } else {
        const verdict = bb.is_buyback
          ? "yes — actively buying back stock"
          : bb.trend === "dilution"
            ? "no — shares outstanding are increasing (dilution)"
            : "no — share count is roughly stable";
        const mcapStr =
          typeof bb.market_cap === "number" && bb.market_cap > 0
            ? `$${(bb.market_cap / 1e9).toFixed(1)}B`
            : "n/a";
        const pctStr =
          typeof bb.tier_percentile === "number" ? `${(bb.tier_percentile * 100).toFixed(0)}` : "n/a";
        const relScore =
          typeof bb.relevance_score === "number" ? bb.relevance_score.toFixed(3) : "n/a";
        const rawScore =
          typeof bb.intensity_score_raw === "number" ? bb.intensity_score_raw.toFixed(3) : "n/a";
        text =
          `${upper} buyback status: ${verdict}\n` +
          `- Market cap tier: ${bb.tier ?? "unknown"} (${mcapStr})\n` +
          `- Trend: ${bb.trend ?? "n/a"}\n` +
          `- QoQ shares delta: ${fmtPct(bb.delta_qoq)}\n` +
          `- Trailing-4Q shares delta: ${fmtPct(bb.delta_4q)}\n` +
          `- Latest shares outstanding: ${fmtShares(bb.latest_shares)} as of ${bb.latest_date ?? "n/a"}\n` +
          `- Raw intensity (% scale): ${rawScore}\n` +
          `- Tier percentile (vs. ${bb.tier ?? "?"}-cap peers): ${pctStr}\n` +
          `- Relevance score (size-adjusted, 0–1): ${relScore} — use this to compare across cap tiers\n` +
          `- Quarterly data points: ${bb.data_points}`;
      }

      return {
        content: [{ type: "text", text }],
        structuredContent: bb,
      };
    },
  );

  return server;
}
