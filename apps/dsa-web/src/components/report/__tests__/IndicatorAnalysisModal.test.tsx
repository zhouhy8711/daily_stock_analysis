import { act, fireEvent, render, screen, within } from '@testing-library/react';
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
    vi.mocked(stocksApi.getQuote).mockImplementation(async (stockCode) => ({
      stockCode,
      stockName: stockCode === 'BABA' ? '阿里巴巴' : '贵州茅台',
      currentPrice: 132,
      change: 0.2,
      changePercent: 0.15,
      open: 131,
      high: 133,
      low: 130,
      prevClose: 131.8,
      volume: 1000000,
      amount: 132000000,
      volumeRatio: 1.2,
      turnoverRate: 0.8,
      source: stockCode === 'BABA' ? 'yfinance' : 'efinance',
      updateTime: '2026-04-30T23:50:00',
    }));
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

  it('refreshes quote every 10 seconds and refreshes one-minute history only on 1m period', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="BABA" stockName="阿里巴巴" onClose={vi.fn()} />,
    );
    await flushPromises();

    const dailyHistoryCallsBeforeRefresh = vi.mocked(stocksApi.getHistory).mock.calls.length;
    const dailyQuoteCallsBeforeRefresh = vi.mocked(stocksApi.getQuote).mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stocksApi.getHistory).toHaveBeenCalledTimes(dailyHistoryCallsBeforeRefresh);
    expect(stocksApi.getQuote).toHaveBeenCalledTimes(dailyQuoteCallsBeforeRefresh + 1);

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
    const quoteCallsAfterFiveMinuteLoad = vi.mocked(stocksApi.getQuote).mock.calls.length;

    await act(async () => {
      vi.advanceTimersByTime(20_000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(stocksApi.getHistory).toHaveBeenCalledTimes(callsAfterFiveMinuteLoad);
    expect(stocksApi.getQuote).toHaveBeenCalledTimes(quoteCallsAfterFiveMinuteLoad + 2);
    unmount();
  });

  it.each([
    ['600519', '贵州茅台'],
    ['BABA', '阿里巴巴'],
  ])('renders the three-zone indicator board, chip peak and order-flow monitor for %s', async (stockCode, stockName) => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode={stockCode} stockName={stockName} onClose={vi.fn()} />,
    );
    await flushPromises();

    expect(stocksApi.getHistory).toHaveBeenCalledWith(stockCode, 120, 'daily');
    expect(stocksApi.getQuote).toHaveBeenCalledWith(stockCode);
    expect(stocksApi.getIndicatorMetrics).toHaveBeenCalledWith(stockCode);

    expect(screen.getByRole('tablist', { name: 'K线周期' })).toBeInTheDocument();
    expect(screen.queryByText('K线、成交量、MACD、筹码峰与实时分单监控')).not.toBeInTheDocument();
    expect(screen.queryByText('K线相关指标')).not.toBeInTheDocument();
    expect(screen.getByText('成交量相关指标')).toBeInTheDocument();
    expect(screen.getByText('MACD等指标')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'K线图' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '成交量图' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'MACD指标图' })).toBeInTheDocument();
    expect(screen.getByTestId('chip-peak-panel')).toBeInTheDocument();
    expect(screen.getByTestId('order-flow-monitor')).toBeInTheDocument();
    expect(screen.getByText('筹码峰')).toBeInTheDocument();
    expect(screen.getByText('实时监控')).toBeInTheDocument();
    expect(screen.getByText('净特大单')).toBeInTheDocument();
    expect(screen.getByText('净大单')).toBeInTheDocument();
    expect(screen.getByText('净中单')).toBeInTheDocument();
    expect(screen.getByText('净小单')).toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByTestId('indicator-chart-bar-2026-04-24'));
    const priceTooltip = screen.getByTestId('indicator-chart-tooltip');
    expect(within(priceTooltip).getByText('2026-04-24')).toBeInTheDocument();
    expect(within(priceTooltip).getByText('收盘')).toBeInTheDocument();
    expect(within(screen.getByTestId('indicator-volume-tooltip')).getByText('2026-04-24')).toBeInTheDocument();
    expect(within(screen.getByTestId('indicator-momentum-tooltip')).getByText('2026-04-24')).toBeInTheDocument();
    expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-24')).toBeInTheDocument();
    fireEvent.mouseLeave(screen.getByTestId('indicator-chart-bar-2026-04-24'));

    fireEvent.mouseEnter(screen.getByTestId('indicator-volume-bar-2026-04-24'));
    const volumeTooltip = screen.getByTestId('indicator-volume-tooltip');
    expect(within(volumeTooltip).getByText('成交量')).toBeInTheDocument();
    expect(within(volumeTooltip).getByText('MAVOL5')).toBeInTheDocument();

    fireEvent.contextMenu(screen.getByRole('img', { name: '成交量图' }), { clientX: 80, clientY: 120 });
    expect(screen.getByRole('menu', { name: '图表缩放菜单' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '放大' }));
    expect(screen.queryByRole('menu', { name: '图表缩放菜单' })).not.toBeInTheDocument();

    unmount();
  });
});
