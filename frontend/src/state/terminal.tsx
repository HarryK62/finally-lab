"use client";

/**
 * Application state for the whole terminal: one SSE connection, one portfolio
 * poll, one watchlist copy, shared by every panel through context.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError } from "@/lib/api";
import { pruneBuffers } from "@/lib/priceBuffer";
import type {
  HealthResponse,
  PortfolioResponse,
  PriceMap,
  PricePoint,
  Snapshot,
  TradeResponse,
  TradeSide,
  WatchlistItem,
} from "@/lib/types";
import { usePriceStream, type ConnectionStatus } from "@/hooks/usePriceStream";

/** Contract §8: keep P&L tracking live prices. */
const PORTFOLIO_POLL_MS = 5_000;
/** Snapshots are written every 30s server-side; polling faster buys nothing. */
const HISTORY_POLL_MS = 30_000;

export interface TerminalState {
  prices: PriceMap;
  buffers: Record<string, PricePoint[]>;
  status: ConnectionStatus;
  health: HealthResponse | null;

  portfolio: PortfolioResponse | null;
  watchlist: WatchlistItem[];
  snapshots: Snapshot[];

  /** True until the first portfolio + watchlist fetch settles. */
  loading: boolean;
  /** Set when the backend is unreachable, so the UI can say so plainly. */
  error: string | null;

  selected: string | null;
  select: (ticker: string) => void;

  trade: (ticker: string, quantity: number, side: TradeSide) => Promise<TradeResponse>;
  addTicker: (ticker: string) => Promise<void>;
  removeTicker: (ticker: string) => Promise<void>;
  refresh: () => Promise<void>;
}

/** Exported so tests can mount panels against a hand-built state. */
export const TerminalContext = createContext<TerminalState | null>(null);

export function TerminalProvider({ children }: { children: ReactNode }) {
  const { prices, buffers, status } = usePriceStream();

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadPortfolio = useCallback(async () => {
    const data = await api.portfolio();
    if (mounted.current) setPortfolio(data);
  }, []);

  const loadWatchlist = useCallback(async () => {
    const data = await api.watchlist();
    if (mounted.current) setWatchlist(data.tickers);
  }, []);

  const loadHistory = useCallback(async () => {
    const data = await api.history();
    if (mounted.current) setSnapshots(data.snapshots);
  }, []);

  const refresh = useCallback(async () => {
    try {
      await Promise.all([loadPortfolio(), loadWatchlist(), loadHistory()]);
      if (mounted.current) setError(null);
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof ApiError ? err.message : "Cannot reach the server");
      }
    }
  }, [loadPortfolio, loadWatchlist, loadHistory]);

  // Initial load.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const info = await api.health();
        if (!cancelled) setHealth(info);
      } catch {
        /* health is decorative; a failure here shows up on the other calls */
      }
      await refresh();
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // Portfolio poll — P&L must move with the streamed prices.
  useEffect(() => {
    const id = window.setInterval(() => {
      void loadPortfolio().catch(() => undefined);
    }, PORTFOLIO_POLL_MS);
    return () => window.clearInterval(id);
  }, [loadPortfolio]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void loadHistory().catch(() => undefined);
    }, HISTORY_POLL_MS);
    return () => window.clearInterval(id);
  }, [loadHistory]);

  // Default the chart to the first watched ticker, and never leave the selection
  // pointing at a ticker that has been removed. Adjusted during render when the
  // watchlist changes, so no panel ever paints against a dead selection.
  const [seenWatchlist, setSeenWatchlist] = useState(watchlist);
  if (watchlist !== seenWatchlist) {
    setSeenWatchlist(watchlist);
    if (
      watchlist.length > 0 &&
      !(selected && watchlist.some((item) => item.ticker === selected))
    ) {
      setSelected(watchlist[0].ticker);
    }
  }

  const trade = useCallback(
    async (ticker: string, quantity: number, side: TradeSide) => {
      const result = await api.trade(ticker, quantity, side);
      // Contract §8: refetch after any successful mutation.
      await Promise.all([loadPortfolio(), loadWatchlist(), loadHistory()]);
      return result;
    },
    [loadPortfolio, loadWatchlist, loadHistory],
  );

  const addTicker = useCallback(
    async (ticker: string) => {
      await api.addTicker(ticker);
      await Promise.all([loadWatchlist(), loadPortfolio()]);
    },
    [loadWatchlist, loadPortfolio],
  );

  const removeTicker = useCallback(
    async (ticker: string) => {
      await api.removeTicker(ticker);
      await Promise.all([loadWatchlist(), loadPortfolio()]);
    },
    [loadWatchlist, loadPortfolio],
  );

  // Sparklines only matter for tickers we still display; drop the rest. Held
  // positions count as displayed even when unwatched — the positions table and
  // the heatmap can both select one, and the chart needs its buffer to draw.
  const liveTickers = useMemo(() => {
    const tickers = watchlist.map((item) => item.ticker);
    const seen = new Set(tickers);
    for (const position of portfolio?.positions ?? []) {
      if (!seen.has(position.ticker)) {
        seen.add(position.ticker);
        tickers.push(position.ticker);
      }
    }
    return tickers;
  }, [watchlist, portfolio]);
  const liveBuffers = useMemo(
    () => (liveTickers.length === 0 ? buffers : pruneBuffers(buffers, liveTickers)),
    [buffers, liveTickers],
  );

  const value = useMemo<TerminalState>(
    () => ({
      prices,
      buffers: liveBuffers,
      status,
      health,
      portfolio,
      watchlist,
      snapshots,
      loading,
      error,
      selected,
      select: setSelected,
      trade,
      addTicker,
      removeTicker,
      refresh,
    }),
    [
      prices,
      liveBuffers,
      status,
      health,
      portfolio,
      watchlist,
      snapshots,
      loading,
      error,
      selected,
      trade,
      addTicker,
      removeTicker,
      refresh,
    ],
  );

  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}

export function useTerminal(): TerminalState {
  const context = useContext(TerminalContext);
  if (!context) throw new Error("useTerminal must be used inside <TerminalProvider>");
  return context;
}

/**
 * The freshest price for a ticker: the SSE tick if one has arrived, otherwise
 * the snapshot the REST endpoints returned, otherwise `null`.
 */
export function usePrice(ticker: string | null): number | null {
  const { prices, watchlist, portfolio } = useTerminal();
  return useMemo(() => {
    if (!ticker) return null;
    const tick = prices[ticker];
    if (tick && Number.isFinite(tick.price)) return tick.price;
    const watched = watchlist.find((item) => item.ticker === ticker);
    if (watched?.price != null) return watched.price;
    const held = portfolio?.positions.find((item) => item.ticker === ticker);
    return held?.current_price ?? null;
  }, [ticker, prices, watchlist, portfolio]);
}
