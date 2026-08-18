import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TerminalProvider, useTerminal, usePrice } from "./terminal";
import { ApiError, api } from "@/lib/api";
import type { PortfolioResponse } from "@/lib/types";
import { position, watched } from "@/test/harness";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      health: vi.fn(),
      portfolio: vi.fn(),
      history: vi.fn(),
      trade: vi.fn(),
      watchlist: vi.fn(),
      addTicker: vi.fn(),
      removeTicker: vi.fn(),
      chat: vi.fn(),
      chatHistory: vi.fn(),
    },
  };
});

const mocked = vi.mocked(api);

const PORTFOLIO: PortfolioResponse = {
  cash_balance: 10_000,
  positions: [position("AAPL", 10, 180, 185)],
  positions_value: 1_850,
  total_value: 11_850,
  total_cost_basis: 1_800,
  total_unrealized_pnl: 50,
  total_unrealized_pnl_percent: 2.78,
};

function Probe() {
  const terminal = useTerminal();
  const aapl = usePrice("AAPL");

  return (
    <div>
      <span data-testid="loading">{String(terminal.loading)}</span>
      <span data-testid="error">{terminal.error ?? "none"}</span>
      <span data-testid="cash">{terminal.portfolio?.cash_balance ?? "none"}</span>
      <span data-testid="watchlist">
        {terminal.watchlist.map((item) => item.ticker).join(",") || "none"}
      </span>
      <span data-testid="selected">{terminal.selected ?? "none"}</span>
      <span data-testid="aapl-price">{aapl ?? "none"}</span>
      <span data-testid="buffered">{Object.keys(terminal.buffers).sort().join(",") || "none"}</span>
      <button type="button" onClick={() => void terminal.addTicker("PYPL")}>
        add
      </button>
      <button type="button" onClick={() => void terminal.removeTicker("MSFT")}>
        remove
      </button>
      <button type="button" onClick={() => void terminal.trade("AAPL", 1, "buy")}>
        trade
      </button>
    </div>
  );
}

function mount() {
  return render(
    <TerminalProvider>
      <Probe />
    </TerminalProvider>,
  );
}

const text = (id: string) => screen.getByTestId(id).textContent;
const settled = () => waitFor(() => expect(text("loading")).toBe("false"));

beforeEach(() => {
  mocked.health.mockResolvedValue({ status: "ok", market_source: "simulator", llm_mock: true });
  mocked.portfolio.mockResolvedValue(PORTFOLIO);
  mocked.history.mockResolvedValue({ snapshots: [] });
  mocked.watchlist.mockResolvedValue({ tickers: [watched("AAPL", 185), watched("MSFT", 410)] });
  mocked.addTicker.mockResolvedValue(watched("PYPL", 62));
  mocked.removeTicker.mockResolvedValue(undefined);
  mocked.trade.mockResolvedValue({
    trade: {
      id: "t1",
      ticker: "AAPL",
      side: "buy",
      quantity: 1,
      price: 185,
      total: 185,
      executed_at: "2026-08-17T14:00:00.000Z",
    },
    cash_balance: 9_815,
    position: position("AAPL", 11, 180.45, 185),
    total_value: 11_850,
  });
});

describe("TerminalProvider", () => {
  it("loads portfolio, watchlist and history on mount", async () => {
    mount();
    await settled();

    expect(text("cash")).toBe("10000");
    expect(text("watchlist")).toBe("AAPL,MSFT");
    expect(mocked.portfolio).toHaveBeenCalledTimes(1);
    expect(mocked.watchlist).toHaveBeenCalledTimes(1);
    expect(mocked.history).toHaveBeenCalledTimes(1);
  });

  it("defaults the chart selection to the first watched ticker", async () => {
    mount();
    // The default lands an effect after the watchlist commits, so it can trail
    // `loading` by a render.
    await waitFor(() => expect(text("selected")).toBe("AAPL"));
  });

  it("reports an unreachable backend rather than rendering a blank void", async () => {
    mocked.portfolio.mockRejectedValue(new ApiError("Cannot reach the server", 0));
    mount();
    await settled();

    expect(text("error")).toBe("Cannot reach the server");
  });

  it("survives a failed health check, which is decorative", async () => {
    mocked.health.mockRejectedValue(new ApiError("Not Found", 404));
    mount();
    await settled();

    expect(text("error")).toBe("none");
    expect(text("cash")).toBe("10000");
  });

  it("adds a ticker through the api and refetches the watchlist", async () => {
    const user = userEvent.setup();
    mount();
    await settled();

    mocked.watchlist.mockResolvedValue({
      tickers: [watched("AAPL", 185), watched("MSFT", 410), watched("PYPL", 62)],
    });
    await user.click(screen.getByRole("button", { name: "add" }));

    expect(mocked.addTicker).toHaveBeenCalledWith("PYPL");
    await waitFor(() => expect(text("watchlist")).toBe("AAPL,MSFT,PYPL"));
    expect(mocked.portfolio).toHaveBeenCalledTimes(2);
  });

  it("removes a ticker through the api and refetches the watchlist", async () => {
    const user = userEvent.setup();
    mount();
    await settled();

    mocked.watchlist.mockResolvedValue({ tickers: [watched("AAPL", 185)] });
    await user.click(screen.getByRole("button", { name: "remove" }));

    expect(mocked.removeTicker).toHaveBeenCalledWith("MSFT");
    await waitFor(() => expect(text("watchlist")).toBe("AAPL"));
  });

  it("refetches portfolio, watchlist and history after a trade (contract §8)", async () => {
    const user = userEvent.setup();
    mount();
    await settled();

    await user.click(screen.getByRole("button", { name: "trade" }));

    expect(mocked.trade).toHaveBeenCalledWith("AAPL", 1, "buy");
    await waitFor(() => expect(mocked.portfolio).toHaveBeenCalledTimes(2));
    expect(mocked.watchlist).toHaveBeenCalledTimes(2);
    expect(mocked.history).toHaveBeenCalledTimes(2);
  });

  it("keeps the selection off a ticker that has been removed", async () => {
    const user = userEvent.setup();
    mount();
    await waitFor(() => expect(text("selected")).toBe("AAPL"));

    mocked.watchlist.mockResolvedValue({ tickers: [watched("MSFT", 410)] });
    await user.click(screen.getByRole("button", { name: "remove" }));

    await waitFor(() => expect(text("selected")).toBe("MSFT"));
  });
});

describe("usePrice", () => {
  it("prefers the watchlist price when no tick has streamed", async () => {
    mount();
    await settled();
    expect(text("aapl-price")).toBe("185");
  });

  it("falls back to the position's price when the ticker is unwatched", async () => {
    mocked.watchlist.mockResolvedValue({ tickers: [watched("MSFT", 410)] });
    mount();
    await settled();
    expect(text("aapl-price")).toBe("185");
  });

  it("reports nothing for a ticker with no price anywhere", async () => {
    mocked.watchlist.mockResolvedValue({ tickers: [] });
    mocked.portfolio.mockResolvedValue({ ...PORTFOLIO, positions: [] });
    mount();
    await settled();
    expect(text("aapl-price")).toBe("none");
  });
});

/**
 * A hand-driven EventSource so a test can stream a tick and inspect which
 * sparkline buffers survive the prune.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readyState = 1;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.readyState = 2;
  }

  emit(payload: unknown) {
    act(() => this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) })));
  }
}

function streamTick(ticker: string, price: number) {
  FakeEventSource.instances.at(-1)!.emit({
    [ticker]: {
      ticker,
      price,
      previous_price: price,
      timestamp: 1_755_439_402.48,
      change: 0,
      change_percent: 0,
      direction: "flat",
    },
  });
}

describe("sparkline buffer pruning", () => {
  let original: unknown;

  beforeEach(() => {
    FakeEventSource.instances = [];
    original = (globalThis as Record<string, unknown>).EventSource;
    (globalThis as Record<string, unknown>).EventSource = FakeEventSource;
  });

  afterEach(() => {
    (globalThis as Record<string, unknown>).EventSource = original;
  });

  it("keeps a held ticker's buffer even when it is off the watchlist", async () => {
    // The positions table and the heatmap can both select an unwatched holding;
    // pruning to the watchlist alone left its chart stuck on "accumulating".
    mocked.watchlist.mockResolvedValue({ tickers: [watched("AAPL", 185)] });
    mocked.portfolio.mockResolvedValue({
      ...PORTFOLIO,
      positions: [position("AAPL", 10, 180, 185), position("TSLA", 5, 250, 260)],
    });
    mount();
    await settled();

    streamTick("AAPL", 185);
    streamTick("TSLA", 260);
    streamTick("ZZZZ", 1); // neither watched nor held — this is the one to drop

    await waitFor(() => expect(text("buffered")).toBe("AAPL,TSLA"));
  });
});

describe("useTerminal outside a provider", () => {
  it("fails loudly rather than rendering half a terminal", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Probe />)).toThrow(/useTerminal must be used inside/);
    error.mockRestore();
  });
});
