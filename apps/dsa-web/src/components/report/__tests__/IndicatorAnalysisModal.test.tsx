import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { rulesApi } from '../../../api/rules';
import { stocksApi, type KLineData, type KLinePeriod, type StockQuote } from '../../../api/stocks';
import { RULE_METRIC_DRAFT_STORAGE_KEY } from '../../../utils/ruleMetricDraft';
import { IndicatorAnalysisModal, IndicatorAnalysisView } from '../IndicatorAnalysisModal';

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    getHistory: vi.fn(),
    getQuote: vi.fn(),
    getIndicatorMetrics: vi.fn(),
    getRelatedNews: vi.fn(),
  },
}));

vi.mock('../../../api/rules', () => ({
  rulesApi: {
    getMetrics: vi.fn(),
  },
}));

const ruleMetricItems = [
  { key: 'current_price', label: '最新价', category: '核心行情', valueType: 'number', unit: '元', periods: ['daily'], description: '' },
  { key: 'volume_ratio', label: '量比', category: 'K线图', valueType: 'number', unit: '倍', periods: ['daily'], description: '' },
  { key: 'amplitude', label: '振幅', category: 'K线图', valueType: 'number', unit: '%', periods: ['daily'], description: '' },
  { key: 'prev_5d_return_pct', label: '前5日累计涨幅', category: '额外', valueType: 'number', unit: '%', periods: ['daily'], description: '' },
  { key: 'prev_20d_return_pct', label: '前20日累计涨幅', category: '额外', valueType: 'number', unit: '%', periods: ['daily'], description: '' },
  { key: 'profit_ratio', label: '收盘获利', category: '筹码峰-全部筹码', valueType: 'number', unit: '%', periods: ['daily'], description: '' },
];

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
      turnoverRate: 1 + index * 0.02,
    };
  });
}

function makeDailyHistory(length: number, startDate = '2026-01-01'): KLineData[] {
  const [year, month, day] = startDate.split('-').map(Number);
  const startTime = Date.UTC(year, month - 1, day);
  return Array.from({ length }, (_, index) => {
    const close = 120 + index * 0.1;
    const date = new Date(startTime + index * 86_400_000).toISOString().slice(0, 10);
    return {
      date,
      open: close - 0.1,
      high: close + 0.2,
      low: close - 0.2,
      close,
      volume: 10000 + index,
      amount: close * (10000 + index),
      changePercent: 0.1,
      turnoverRate: 1 + index * 0.02,
    };
  });
}

function makeChipSnapshot(date: string, avgCost: number, profitRatio: number) {
  return {
    code: '600519',
    date,
    source: 'local_chip_model:unit',
    profitRatio,
    avgCost,
    cost90Low: avgCost - 6,
    cost90High: avgCost + 8,
    concentration90: 0.055,
    cost70Low: avgCost - 3,
    cost70High: avgCost + 4,
    concentration70: 0.027,
    distribution: [
      { price: avgCost - 6, percent: 0.16 },
      { price: avgCost - 3, percent: 0.24 },
      { price: avgCost, percent: 0.36 },
      { price: avgCost + 4, percent: 0.16 },
      { price: avgCost + 8, percent: 0.08 },
    ],
  };
}

function makeQuote(stockCode: string, overrides: Partial<StockQuote> = {}): StockQuote {
  return {
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
    afterHoursVolume: 104,
    afterHoursAmount: 1429064,
    volumeRatio: 1.2,
    turnoverRate: 0.8,
    peRatio: 23.89,
    totalMv: 264000000000,
    circMv: 211200000000,
    totalShares: 2000000000,
    floatShares: 1600000000,
    limitUpPrice: 145.2,
    limitDownPrice: 118.8,
    priceSpeed: 0.36,
    entrustRatio: 18.5,
    source: stockCode === 'BABA' ? 'yfinance' : 'efinance',
    updateTime: '2026-04-30T23:50:00',
    ...overrides,
  };
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function expectIndicatorHeadersToShow(date: string) {
  const priceHeader = screen.getByTestId('indicator-price-header');
  const volumeHeader = screen.getByTestId('indicator-volume-header');
  const momentumHeader = screen.getByTestId('indicator-momentum-header');

  expect(within(priceHeader).getByText(date)).toBeInTheDocument();
  expect(within(priceHeader).getByText(/^MA5:/)).toBeInTheDocument();
  expect(within(priceHeader).getByRole('button', { name: '更多K线指标' })).toBeInTheDocument();
  expect(within(priceHeader).queryByText(/^振幅:/)).not.toBeInTheDocument();
  expect(within(priceHeader).queryByText(/^收盘:/)).not.toBeInTheDocument();
  expect(within(priceHeader).queryByText(/^总市值:/)).not.toBeInTheDocument();
  expect(within(priceHeader).queryByText(/^总量:/)).not.toBeInTheDocument();
  expect(within(priceHeader).queryByText(/^量均5日:/)).not.toBeInTheDocument();
  expect(within(priceHeader).queryByText(/^总金额:/)).not.toBeInTheDocument();
  expect(within(volumeHeader).getByText(date)).toBeInTheDocument();
  expect(within(volumeHeader).getByText(/^量:/)).toBeInTheDocument();
  expect(within(volumeHeader).getByText(/^盘后:/)).toBeInTheDocument();
  expect(within(volumeHeader).getByText(/^MA5:/)).toBeInTheDocument();
  expect(within(volumeHeader).getByText(/^10:/)).toBeInTheDocument();
  expect(within(volumeHeader).getByText(/^换手:/)).toBeInTheDocument();
  expect(within(volumeHeader).queryByText(/^MAVOL5:/)).not.toBeInTheDocument();
  expect(within(volumeHeader).queryByText(/^成交额:/)).not.toBeInTheDocument();
  expect(within(momentumHeader).getByText(date)).toBeInTheDocument();
  expect(within(momentumHeader).getByText(/^DIF:/)).toBeInTheDocument();
}

describe('IndicatorAnalysisModal', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(rulesApi.getMetrics).mockResolvedValue(ruleMetricItems);
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '阿里巴巴',
      period,
      data: makeHistory(period),
    }));
    vi.mocked(stocksApi.getQuote).mockImplementation(async (stockCode) => makeQuote(stockCode));
    vi.mocked(stocksApi.getIndicatorMetrics).mockImplementation(async (stockCode) => ({
      stockCode,
      stockName: stockCode === 'BABA' ? '阿里巴巴' : '贵州茅台',
      chipDistribution: stockCode === 'BABA'
        ? {
          code: 'BABA',
          date: '2026-04-30',
          source: 'tushare_cyq_chips',
          profitRatio: 0.62,
          avgCost: 131.2,
          cost90Low: 125.1,
          cost90High: 138.6,
          concentration90: 0.051,
          cost70Low: 128.4,
          cost70High: 135.2,
          concentration70: 0.026,
          distribution: [
            { price: 125.1, percent: 0.12 },
            { price: 128.4, percent: 0.2 },
            { price: 131.2, percent: 0.36 },
            { price: 135.2, percent: 0.22 },
            { price: 138.6, percent: 0.1 },
          ],
        }
        : {
          ...makeChipSnapshot('2026-04-30', 122.3, 0.66),
          snapshots: [
            makeChipSnapshot('2026-04-23', 121.8, 0.58),
            makeChipSnapshot('2026-04-24', 121.9, 0.61),
            makeChipSnapshot('2026-04-30', 122.3, 0.66),
          ],
        },
      majorHolders: [],
      majorHolderStatus: 'not_supported',
      capitalFlow: {
        status: stockCode === 'BABA' ? 'not_supported' : 'ok',
        mainNetInflow: stockCode === 'BABA' ? null : 52000000,
        mainNetInflowRatio: stockCode === 'BABA' ? null : 2.46,
        inflow5d: stockCode === 'BABA' ? null : 120000000,
        inflow10d: stockCode === 'BABA' ? null : 180000000,
      },
      sourceChain: [],
      errors: [],
      updateTime: '2026-04-30T23:50:00',
    }));
    vi.mocked(stocksApi.getRelatedNews).mockResolvedValue({
      total: 1,
      items: [
        {
          title: '平安银行发布最新经营动态',
          snippet: '公司近期披露经营数据，市场关注息差和资产质量变化。',
          url: 'https://example.com/news/pab',
        },
      ],
    });
  });

  afterEach(() => {
    localStorage.clear();
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
    const coreMetrics = screen.getByTestId('indicator-core-metrics');
    expect(within(coreMetrics).getByText('132.00')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('+0.20')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('+0.15%')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('最高价')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('最低价')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('开盘价')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('总市值')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('流通市值')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('市盈TTM')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('量比')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('换手率')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('成交额')).toBeInTheDocument();
    expect(within(coreMetrics).getByRole('button', { name: '添加 最高价 到规则' })).toBeInTheDocument();
    expect(within(coreMetrics).getByRole('button', { name: '添加 换手率 到规则' })).toBeInTheDocument();
    expect(within(coreMetrics).getByRole('button', { name: '添加 成交额 到规则' })).toBeInTheDocument();
    expect(within(coreMetrics).getByText('2640.00亿')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('2112.00亿')).toBeInTheDocument();
    expect(within(coreMetrics).getByText('23.89')).toBeInTheDocument();
    const latestPriceHeader = screen.getByTestId('indicator-price-header');
    expect(within(latestPriceHeader).queryByText(/^流通市值:/)).not.toBeInTheDocument();
    expect(within(latestPriceHeader).queryByText(/^总市值:/)).not.toBeInTheDocument();
    expect(within(latestPriceHeader).queryByText(/^流通股本:/)).not.toBeInTheDocument();
    fireEvent.click(within(latestPriceHeader).getByRole('button', { name: '更多K线指标' }));
    const latestMoreMetrics = screen.getByRole('dialog', { name: '更多K线指标' });
    expect(within(latestMoreMetrics).getByText(/^流通股本:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^总股本:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^涨幅限价:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^涨速:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^主力净量:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^主力净流入:/)).toBeInTheDocument();
    expect(within(latestPriceHeader).queryByText(/^换手:/)).not.toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^委比:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^前5日累计涨幅:/)).toBeInTheDocument();
    expect(within(latestMoreMetrics).getByText(/^前20日累计涨幅:/)).toBeInTheDocument();
    expect(within(latestPriceHeader).queryByText(/^总量:/)).not.toBeInTheDocument();
    expect(within(latestPriceHeader).queryByText(/^总金额:/)).not.toBeInTheDocument();
    fireEvent.click(within(latestPriceHeader).getByRole('button', { name: '更多K线指标' }));
    const latestVolumeHeader = screen.getByTestId('indicator-volume-header');
    expect(within(latestVolumeHeader).getByText(/^量:/)).toBeInTheDocument();
    expect(within(latestVolumeHeader).getByText('盘后:104')).toBeInTheDocument();
    expect(within(latestVolumeHeader).getByText(/^MA5:/)).toBeInTheDocument();
    expect(within(latestVolumeHeader).getByText(/^10:/)).toBeInTheDocument();
    expect(within(latestVolumeHeader).getByText('换手:0.80%')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '成交量图' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'MACD指标图' })).toBeInTheDocument();
    const sidePanel = screen.getByTestId('indicator-side-panel');
    expect(within(sidePanel).getByRole('tab', { name: '筹码峰' })).toHaveAttribute('aria-selected', 'true');
    const chipPeakPanel = screen.getByTestId('chip-peak-panel');
    expect(chipPeakPanel).toBeInTheDocument();
    expect(screen.queryByTestId('order-flow-monitor')).not.toBeInTheDocument();
    expect(within(chipPeakPanel).getByRole('tab', { name: '全部筹码' })).toHaveAttribute('aria-selected', 'true');
    expect(within(chipPeakPanel).getByText('收盘获利')).toBeInTheDocument();
    expect(within(chipPeakPanel).getByText('套牢盘')).toBeInTheDocument();
    expect(within(chipPeakPanel).getAllByText('平均成本').length).toBeGreaterThan(0);
    expect(within(chipPeakPanel).getByText('价格区间')).toBeInTheDocument();
    expect(within(chipPeakPanel).getByText('集中度')).toBeInTheDocument();
    expect(within(chipPeakPanel).queryByText('筹码来源')).not.toBeInTheDocument();
    expect(within(chipPeakPanel).queryByText('筹码分布说明')).not.toBeInTheDocument();
    expect(within(chipPeakPanel).queryByText(/local_chip_model/)).not.toBeInTheDocument();
    fireEvent.click(within(chipPeakPanel).getByRole('tab', { name: '主力筹码' }));
    expect(within(screen.getByTestId('chip-peak-panel')).getByRole('tab', { name: '主力筹码' })).toHaveAttribute('aria-selected', 'true');
    expect(within(screen.getByTestId('chip-peak-panel')).getByText('暂无同源主力筹码峰明细')).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId('chip-peak-panel')).getByRole('tab', { name: '全部筹码' }));

    fireEvent.click(within(sidePanel).getByRole('tab', { name: '实时监控' }));
    expect(screen.getByTestId('order-flow-monitor')).toBeInTheDocument();
    expect(screen.getByText('净特大单')).toBeInTheDocument();
    expect(screen.getByText('净大单')).toBeInTheDocument();
    expect(screen.getByText('净中单')).toBeInTheDocument();
    expect(screen.getByText('净小单')).toBeInTheDocument();
    fireEvent.click(within(sidePanel).getByRole('tab', { name: '筹码峰' }));

    fireEvent.mouseEnter(screen.getByTestId('indicator-chart-bar-2026-04-24'));
    expect(screen.queryByTestId('indicator-chart-tooltip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('indicator-volume-tooltip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('indicator-momentum-tooltip')).not.toBeInTheDocument();
    expectIndicatorHeadersToShow('2026-04-24');
    if (stockCode === '600519') {
      expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-24')).toBeInTheDocument();
      expect(screen.getByRole('img', { name: '筹码峰分布图' })).toBeInTheDocument();
    } else {
      expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-30')).toBeInTheDocument();
      expect(screen.getByRole('img', { name: '筹码峰分布图' })).toBeInTheDocument();
    }
    fireEvent.mouseLeave(screen.getByTestId('indicator-chart-bar-2026-04-24'));

    fireEvent.mouseEnter(screen.getByTestId('indicator-volume-bar-2026-04-24'));
    expectIndicatorHeadersToShow('2026-04-24');

    fireEvent.contextMenu(screen.getByRole('img', { name: '成交量图' }), { clientX: 80, clientY: 120 });
    expect(screen.getByRole('menu', { name: '图表缩放菜单' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '放大' }));
    expect(screen.queryByRole('menu', { name: '图表缩放菜单' })).not.toBeInTheDocument();

    unmount();
  });

  it('adds visible indicator metrics to a reusable rule draft', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    fireEvent.click(screen.getByRole('button', { name: '添加 最新价 到规则' }));
    fireEvent.click(screen.getByRole('button', { name: '添加 量比 到规则' }));
    fireEvent.click(within(screen.getByTestId('indicator-price-header')).getByRole('button', { name: '更多K线指标' }));
    fireEvent.click(screen.getByRole('button', { name: '添加 前5日累计涨幅 到规则' }));

    expect(screen.getByText('已选 3')).toBeInTheDocument();
    expect(screen.getByText(/多个指标会在规则页放入同一条件组/)).toBeInTheDocument();

    const rawDraft = localStorage.getItem(RULE_METRIC_DRAFT_STORAGE_KEY);
    expect(rawDraft).not.toBeNull();
    const draft = JSON.parse(rawDraft ?? '{}') as { stockCode?: string; stockName?: string; items?: Array<{ key: string; value: number | null }> };
    expect(draft.stockCode).toBe('600519');
    expect(draft.stockName).toBe('贵州茅台');
    expect(draft.items?.map((item) => item.key)).toEqual(['current_price', 'volume_ratio', 'prev_5d_return_pct']);
    expect(draft.items?.[0]?.value).toBe(132);
    expect(draft.items?.[1]?.value).toBe(1.2);
    expect(typeof draft.items?.[2]?.value).toBe('number');

    unmount();
  });

  it('toggles selected metrics and edits the reusable rule draft inline', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    fireEvent.click(screen.getByRole('button', { name: '添加 最新价 到规则' }));
    expect(screen.getByRole('button', { name: '移除 最新价 从规则' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '编辑已选规则条件' }));
    const editor = screen.getByRole('dialog', { name: '已选规则条件编辑' });
    expect(within(editor).getByText('规则条件草稿')).toBeInTheDocument();
    expect(within(editor).getByText(/多个子条件按“且”关系判断/)).toBeInTheDocument();

    fireEvent.change(within(editor).getByLabelText('关系'), { target: { value: '<=' } });
    fireEvent.change(within(editor).getByLabelText('数值'), { target: { value: '130' } });

    const editedDraft = JSON.parse(localStorage.getItem(RULE_METRIC_DRAFT_STORAGE_KEY) ?? '{}') as {
      items?: Array<{ key: string; operator?: string; right?: { type?: string; value?: number } }>;
    };
    expect(editedDraft.items?.[0]?.key).toBe('current_price');
    expect(editedDraft.items?.[0]?.operator).toBe('<=');
    expect(editedDraft.items?.[0]?.right).toEqual({ type: 'literal', value: 130 });

    fireEvent.click(screen.getByRole('button', { name: '移除 最新价 从规则' }));
    expect(localStorage.getItem(RULE_METRIC_DRAFT_STORAGE_KEY)).toBeNull();
    expect(screen.queryByText('已选 1')).not.toBeInTheDocument();

    unmount();
  });

  it('pins the shared indicator cursor and moves it with left and right keys', async () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={onClose} />,
    );
    await flushPromises();

    const pinnedBar = screen.getByTestId('indicator-chart-bar-2026-04-24');
    fireEvent.click(pinnedBar);
    fireEvent.mouseLeave(pinnedBar);

    expectIndicatorHeadersToShow('2026-04-24');
    expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-24')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expectIndicatorHeadersToShow('2026-04-23');
    expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-23')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'ArrowRight' });
    expectIndicatorHeadersToShow('2026-04-24');
    expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-24')).toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByTestId('indicator-chart-bar-2026-04-23'));
    expectIndicatorHeadersToShow('2026-04-23');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByTestId('indicator-chart-tooltip')).not.toBeInTheDocument();

    unmount();
  });

  it('opens on an initial hit date and highlights it across the indicator charts', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal
        stockCode="600519"
        stockName="贵州茅台"
        initialDate="2026-04-24"
        initialHistoryDays={365}
        onClose={vi.fn()}
      />,
    );
    await flushPromises();

    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 365, 'daily');
    expect(screen.getByText('命中日 2026-04-24')).toBeInTheDocument();
    expect(screen.getByTestId('indicator-hit-highlight-2026-04-24')).toBeInTheDocument();
    expect(screen.getByTestId('indicator-volume-hit-highlight-2026-04-24')).toBeInTheDocument();
    expect(screen.getByTestId('indicator-momentum-hit-highlight-2026-04-24')).toBeInTheDocument();
    expectIndicatorHeadersToShow('2026-04-24');
    expect(screen.queryByTestId('indicator-chart-tooltip')).not.toBeInTheDocument();

    unmount();
  });

  it('keeps indicator headers live when the selected candle changes by mouse', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    fireEvent.click(screen.getByTestId('indicator-chart-bar-2026-04-24'));
    expectIndicatorHeadersToShow('2026-04-24');
    expect(screen.queryByTestId('indicator-chart-tooltip')).not.toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByTestId('indicator-chart-bar-2026-04-23'));
    expectIndicatorHeadersToShow('2026-04-23');
    const historicalCoreMetrics = within(screen.getByTestId('indicator-core-metrics'));
    expect(historicalCoreMetrics.getByText('122.20')).toBeInTheDocument();
    expect(historicalCoreMetrics.getByText('+0.10')).toBeInTheDocument();
    expect(historicalCoreMetrics.getByText('+0.10%')).toBeInTheDocument();
    const historicalPriceHeader = screen.getByTestId('indicator-price-header');
    fireEvent.click(within(historicalPriceHeader).getByRole('button', { name: '更多K线指标' }));
    const historicalPriceHeaderText = screen.getByRole('dialog', { name: '更多K线指标' }).textContent ?? '';
    expect(historicalPriceHeaderText).toContain('流通股本:16.00亿股');
    expect(historicalPriceHeaderText).toContain('总股本:20.00亿股');
    expect(historicalPriceHeaderText).toMatch(/涨幅限价:\d/);
    expect(historicalPriceHeaderText).toMatch(/跌幅限价:\d/);
    expect(historicalPriceHeaderText).toContain('涨速:+0.10%');
    expect(historicalPriceHeaderText).toMatch(/主力净量:[+-]?\d/);
    expect(historicalPriceHeaderText).toMatch(/主力净流入:[+-]?\d/);
    expect(historicalPriceHeaderText).toMatch(/委比:[+-]?\d/);
    expect(historicalPriceHeaderText).not.toContain('流通股本:--');
    expect(historicalPriceHeaderText).not.toContain('总股本:--');
    expect(historicalPriceHeaderText).not.toContain('涨幅限价:--');
    expect(historicalPriceHeaderText).not.toContain('跌幅限价:--');
    expect(historicalPriceHeaderText).not.toContain('涨速:--');
    expect(historicalPriceHeaderText).not.toContain('主力净量:--');
    expect(historicalPriceHeaderText).not.toContain('主力净流入:--');
    expect(historicalPriceHeaderText).not.toContain('委比:--');
    fireEvent.click(within(historicalPriceHeader).getByRole('button', { name: '更多K线指标' }));

    fireEvent.mouseEnter(screen.getByTestId('indicator-volume-bar-2026-04-24'));
    expectIndicatorHeadersToShow('2026-04-24');
    expect(within(screen.getByTestId('indicator-core-metrics')).getByText('132.00')).toBeInTheDocument();

    unmount();
  });

  it('uses post-market amount when the volume chart switches to amount mode', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    fireEvent.click(screen.getByRole('button', { name: '成交额' }));

    const volumeHeader = within(screen.getByTestId('indicator-volume-header'));
    expect(volumeHeader.getByText(/^额:/)).toBeInTheDocument();
    expect(volumeHeader.queryByText(/^量:/)).not.toBeInTheDocument();
    expect(volumeHeader.getByText('盘后:142.91万')).toBeInTheDocument();

    unmount();
  });

  it('backs fills post-market volume from post-market amount and latest price', async () => {
    vi.mocked(stocksApi.getQuote).mockImplementation(async (stockCode) => makeQuote(stockCode, {
      currentPrice: 137.41,
      afterHoursVolume: null,
      afterHoursAmount: 1429064,
    }));

    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    const volumeHeader = within(screen.getByTestId('indicator-volume-header'));
    expect(volumeHeader.getByText('盘后:104')).toBeInTheDocument();

    unmount();
  });

  it('derives core market values from share counts when quote market values are missing', async () => {
    vi.mocked(stocksApi.getQuote).mockImplementation(async (stockCode) => makeQuote(stockCode, {
      totalMv: null,
      circMv: null,
    }));

    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    const coreMetrics = within(screen.getByTestId('indicator-core-metrics'));
    expect(coreMetrics.getByText('2640.00亿')).toBeInTheDocument();
    expect(coreMetrics.getByText('2112.00亿')).toBeInTheDocument();
    expect(coreMetrics.getByText('23.89')).toBeInTheDocument();

    unmount();
  });

  it('derives turnover rate for historical candles when K-line history omits it', async () => {
    const historyWithoutTurnover = makeHistory('daily').map((point) => ({
      date: point.date,
      open: point.open,
      high: point.high,
      low: point.low,
      close: point.close,
      volume: point.volume,
      amount: point.amount,
      changePercent: point.changePercent,
    }));
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '贵州茅台',
      period,
      data: period === 'daily' ? historyWithoutTurnover : makeHistory(period),
    }));

    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    expect(within(screen.getByTestId('indicator-volume-header')).getByText('换手:0.80%')).toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByTestId('indicator-chart-bar-2026-04-14'));

    expect(within(screen.getByTestId('indicator-volume-header')).getByText('换手:0.06%')).toBeInTheDocument();
    expect(within(screen.getByTestId('indicator-core-metrics')).getByText('0.06%')).toBeInTheDocument();

    unmount();
  });

  it('requests a wider history range for slower intraday periods', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 3, '1m');
    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 3, '5m');
    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 30, '15m');
    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 30, '30m');
    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 30, '60m');

    unmount();
  });

  it('keeps the time window axis visible when intraday periods cannot pan', async () => {
    const shortIntradayPeriods = new Set<KLinePeriod>(['15m', '30m', '60m']);
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '贵州茅台',
      period,
      data: shortIntradayPeriods.has(period) ? makeHistory(period).slice(0, 8) : makeHistory(period),
    }));

    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    for (const label of ['15分', '30分', '60分']) {
      fireEvent.click(screen.getByRole('tab', { name: label }));
      await flushPromises();

      const slider = screen.getByRole('slider', { name: 'K线时间窗口' });

      expect(screen.getByTestId('indicator-window-track')).toBeInTheDocument();
      expect(screen.getByTestId('indicator-window-thumb')).toBeInTheDocument();
      expect(slider).toBeDisabled();
      expect(slider).toHaveAttribute('max', '1');
      expect(slider).toHaveValue('1');
    }

    unmount();
  });

  it('syncs the chip peak date with the current visible K-line window', async () => {
    const longHistory = makeDailyHistory(120);
    const windowSnapshotDate = longHistory[89].date;
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '贵州茅台',
      period,
      data: period === 'daily' ? longHistory : makeHistory(period),
    }));
    vi.mocked(stocksApi.getIndicatorMetrics).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      chipDistribution: {
        ...makeChipSnapshot('2026-04-30', 122.3, 0.66),
        snapshots: [
          makeChipSnapshot(windowSnapshotDate, 121.4, 0.57),
          makeChipSnapshot('2026-04-30', 122.3, 0.66),
        ],
      },
      majorHolders: [],
      majorHolderStatus: 'not_supported',
      sourceChain: [],
      errors: [],
      updateTime: '2026-04-30T23:50:00',
    });

    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    expect(within(screen.getByTestId('chip-peak-panel')).getByText('2026-04-30')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('slider', { name: 'K线时间窗口' }), { target: { value: '10' } });

    expect(within(screen.getByTestId('chip-peak-panel')).getByText(windowSnapshotDate)).toBeInTheDocument();
    expect(within(screen.getByTestId('chip-peak-panel')).queryByText('2026-04-30')).not.toBeInTheDocument();

    unmount();
  });

  it('pans and zooms the K-line window from the header control strip without covering the chart', async () => {
    const longHistory = makeDailyHistory(120);
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '贵州茅台',
      period,
      data: period === 'daily' ? longHistory : makeHistory(period),
    }));

    const { unmount, container } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    const selectedDate = longHistory[89].date;
    fireEvent.mouseEnter(screen.getByTestId(`indicator-chart-bar-${selectedDate}`));
    expectIndicatorHeadersToShow(selectedDate);

    const timeAxis = screen.getByTestId('indicator-chart-time-axis');
    const chartShell = screen.getByRole('img', { name: 'K线图' }).parentElement as HTMLElement;
    expect(within(chartShell).queryByRole('button', { name: '放大选中K线日期' })).not.toBeInTheDocument();
    expect(within(timeAxis).getByRole('slider', { name: 'K线时间窗口' })).toBeInTheDocument();
    expect(screen.queryByText(/最近 \d+ 个交易日/)).not.toBeInTheDocument();
    expect(screen.getByTestId('indicator-kline-y-axis-zero')).toHaveTextContent('0.00');
    expect(screen.getAllByTestId('indicator-kline-x-axis-label').length).toBeGreaterThan(1);

    fireEvent.click(within(timeAxis).getByRole('button', { name: '放大选中K线日期' }));
    expect(container.querySelectorAll('[data-testid^="indicator-chart-bar-"]')).toHaveLength(55);
    expectIndicatorHeadersToShow(selectedDate);

    fireEvent.click(within(timeAxis).getByRole('button', { name: '缩小选中K线日期' }));
    expect(container.querySelectorAll('[data-testid^="indicator-chart-bar-"]')).toHaveLength(80);
    expectIndicatorHeadersToShow(selectedDate);

    fireEvent.click(within(timeAxis).getByRole('button', { name: '向左平移K线时间' }));
    expect(screen.getByTestId(`indicator-chart-bar-${longHistory[0].date}`)).toBeInTheDocument();

    fireEvent.click(within(timeAxis).getByRole('button', { name: '向右平移K线时间' }));
    expect(screen.getByTestId(`indicator-chart-bar-${longHistory.at(-1)?.date}`)).toBeInTheDocument();

    unmount();
  });

  it('shows secondary K-line metrics in a transparent more popover', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    const priceHeader = screen.getByTestId('indicator-price-header');
    const moreButton = within(priceHeader).getByRole('button', { name: '更多K线指标' });

    expect(within(priceHeader).queryByText(/^振幅:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '更多K线指标' })).not.toBeInTheDocument();

    fireEvent.click(moreButton);
    const morePopover = screen.getByRole('dialog', { name: '更多K线指标' });
    expect(within(morePopover).getByText(/^振幅:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^流通股本:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^总股本:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^涨幅限价:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^跌幅限价:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^涨速:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^主力净量:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^主力净流入:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^委比:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^前5日累计涨幅:/)).toBeInTheDocument();
    expect(within(morePopover).getByText(/^前20日累计涨幅:/)).toBeInTheDocument();

    fireEvent.click(moreButton);
    expect(screen.queryByRole('dialog', { name: '更多K线指标' })).not.toBeInTheDocument();

    unmount();
  });

  it('maximizes each indicator chart and restores it with Escape', async () => {
    const { unmount } = render(
      <IndicatorAnalysisModal stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} />,
    );
    await flushPromises();

    fireEvent.click(screen.getByRole('button', { name: '最大化K线图' }));
    expect(screen.getByRole('dialog', { name: 'K线图最大化' })).toBeInTheDocument();
    expect(within(screen.getByRole('dialog', { name: 'K线图最大化' })).getByRole('img', { name: 'K线图' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'K线图最大化' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '最大化成交量图' }));
    expect(screen.getByRole('dialog', { name: '成交量图最大化' })).toBeInTheDocument();
    expect(within(screen.getByRole('dialog', { name: '成交量图最大化' })).getByRole('img', { name: '成交量图' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '成交量图最大化' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '最大化MACD指标图' }));
    expect(screen.getByRole('dialog', { name: 'MACD指标图最大化' })).toBeInTheDocument();
    expect(within(screen.getByRole('dialog', { name: 'MACD指标图最大化' })).getByRole('img', { name: 'MACD指标图' })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'MACD指标图最大化' })).not.toBeInTheDocument();

    unmount();
  });

  it('loads related news on the indicator page and refreshes it on demand', async () => {
    const { unmount } = render(
      <IndicatorAnalysisView stockCode="600519" stockName="贵州茅台" onClose={vi.fn()} variant="page" />,
    );
    await flushPromises();

    expect(screen.getByTestId('indicator-related-news')).toBeInTheDocument();
    expect(screen.getByText('平安银行发布最新经营动态')).toBeInTheDocument();
    expect(stocksApi.getRelatedNews).toHaveBeenCalledWith('600519', 8, false);

    fireEvent.click(screen.getByRole('button', { name: '刷新相关资讯' }));
    await flushPromises();

    expect(stocksApi.getRelatedNews).toHaveBeenLastCalledWith('600519', 8, true);

    unmount();
  });
});
