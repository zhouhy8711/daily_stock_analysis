import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi, type KLineData, type KLinePeriod } from '../../../api/stocks';
import { IndicatorAnalysisModal } from '../IndicatorAnalysisModal';

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    getHistory: vi.fn(),
    getQuote: vi.fn(),
    getIndicatorMetrics: vi.fn(),
  },
}));

function makeHistory(period: KLinePeriod): KLineData[] {
  const length = period === 'daily' ? 24 : 90;
  return Array.from({ length }, (_, index) => {
    const close = 120 + index * 0.1;
    return {
      date: period === 'daily'
        ? `2026-04-${String(index + 1).padStart(2, '0')}`
        : `2026-04-30 ${String(9 + Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}`,
      open: close - 0.1,
      high: close + 0.2,
      low: close - 0.2,
      close,
      volume: 10000 + index,
      amount: close * (10000 + index),
      changePercent: 0.1,
    };
  });
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('IndicatorAnalysisModal', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '阿里巴巴',
      period,
      data: makeHistory(period),
    }));
    vi.mocked(stocksApi.getQuote).mockResolvedValue({
      stockCode: 'BABA',
      stockName: '阿里巴巴',
      currentPrice: 132,
      change: 0.2,
      changePercent: 0.15,
      open: 131,
      high: 133,
      low: 130,
      prevClose: 131.8,
      volume: 1000000,
      amount: 132000000,
      updateTime: '2026-04-30T23:50:00',
    });
    vi.mocked(stocksApi.getIndicatorMetrics).mockResolvedValue({
      stockCode: 'BABA',
      stockName: '阿里巴巴',
      chipDistribution: null,
      majorHolders: [],
      majorHolderStatus: 'not_supported',
      sourceChain: [],
      errors: [],
      updateTime: '2026-04-30T23:50:00',
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('refreshes one-minute history and quote every 10 seconds only on 1m period', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="BABA" stockName="阿里巴巴" onClose={vi.fn()} />,
    );
    await flushPromises();

    fireEvent.click(screen.getByRole('tab', { name: '1分' }));
    await flushPromises();

    const historyCallsBeforeRefresh = vi.mocked(stocksApi.getHistory).mock.calls.length;
    const quoteCallsBeforeRefresh = vi.mocked(stocksApi.getQuote).mock.calls.length;
    const metricsCallsBeforeRefresh = vi.mocked(stocksApi.getIndicatorMetrics).mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stocksApi.getHistory).toHaveBeenCalledTimes(historyCallsBeforeRefresh + 1);
    expect(stocksApi.getHistory).toHaveBeenLastCalledWith('BABA', 3, '1m');
    expect(stocksApi.getQuote).toHaveBeenCalledTimes(quoteCallsBeforeRefresh + 1);
    expect(stocksApi.getIndicatorMetrics).toHaveBeenCalledTimes(metricsCallsBeforeRefresh);

    fireEvent.click(screen.getByRole('tab', { name: '5分' }));
    await flushPromises();
    const callsAfterFiveMinuteLoad = vi.mocked(stocksApi.getHistory).mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(20_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stocksApi.getHistory).toHaveBeenCalledTimes(callsAfterFiveMinuteLoad);
    unmount();
  });
});
