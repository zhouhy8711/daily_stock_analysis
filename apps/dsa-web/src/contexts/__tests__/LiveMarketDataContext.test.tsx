import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../../api/stocks';
import { systemConfigApi } from '../../api/systemConfig';
import { findLiveQuote, LiveMarketDataProvider, useLiveQuotes } from '../LiveMarketDataContext';

vi.mock('../../api/stocks', () => ({
  stocksApi: {
    getQuotes: vi.fn(),
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: vi.fn(),
  },
}));

function QuoteProbe({ codes }: { codes: string[] }) {
  const { quotesByCode, refreshIntervalSeconds } = useLiveQuotes(codes);
  const quote = findLiveQuote(quotesByCode, codes[0]);
  return (
    <div>
      <span data-testid="live-price">{quote?.currentPrice ?? '--'}</span>
      <span data-testid="live-refresh">{refreshIntervalSeconds}</span>
    </div>
  );
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('LiveMarketDataContext', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [
        {
          key: 'INDICATOR_INTRADAY_REFRESH_SECONDS',
          value: '60',
          rawValueExists: true,
          isMasked: false,
        },
      ],
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('polls subscribed watchlist quotes every configured interval and stops after unmount', async () => {
    let price = 123;
    vi.mocked(stocksApi.getQuotes).mockImplementation(async (stockCodes, freshnessSeconds) => ({
      items: stockCodes.map((stockCode) => ({
        stockCode,
        stockName: stockCode === '600519' ? '贵州茅台' : stockCode,
        currentPrice: price++,
        change: 1,
        changePercent: 0.8,
        open: 122,
        high: 125,
        low: 121,
        prevClose: 122,
        volume: 1000000,
        amount: 123000000,
        updateTime: '2026-04-30T10:30:00',
      })),
      failedCodes: [],
      updateTime: `fresh-${freshnessSeconds}`,
    }));

    const { unmount } = render(
      <LiveMarketDataProvider>
        <QuoteProbe codes={['600519']} />
      </LiveMarketDataProvider>,
    );
    await flushPromises();

    expect(systemConfigApi.getConfig).toHaveBeenCalledWith(false);
    expect(stocksApi.getQuotes).toHaveBeenCalledWith(['600519'], 60);
    expect(screen.getByTestId('live-refresh')).toHaveTextContent('60');
    expect(screen.getByTestId('live-price')).toHaveTextContent('123');

    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stocksApi.getQuotes).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('live-price')).toHaveTextContent('124');

    unmount();
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stocksApi.getQuotes).toHaveBeenCalledTimes(2);
  });

  it('matches equivalent A-share code formats in the live quote store', () => {
    const quote = {
      stockCode: 'SZ300274',
      stockName: '阳光电源',
      currentPrice: 140.53,
      updateTime: '2026-05-06T10:07:00',
    };

    expect(findLiveQuote({ SZ300274: quote }, '300274.SZ')).toBe(quote);
    expect(findLiveQuote({ '300274.SZ': quote }, 'SZ300274')).toBe(quote);
    expect(findLiveQuote({ 300274: quote }, '300274.SZ')).toBe(quote);
  });
});
