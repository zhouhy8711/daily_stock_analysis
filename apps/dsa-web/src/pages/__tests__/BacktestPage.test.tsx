import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../api/history';
import { rulesApi } from '../../api/rules';
import { stocksApi, type KLineData, type KLinePeriod } from '../../api/stocks';
import { systemConfigApi } from '../../api/systemConfig';
import type { StockIndexItem } from '../../types/stockIndex';
import BacktestPage from '../BacktestPage';

const stockIndexHookState = vi.hoisted(() => ({
  current: {
    index: [] as StockIndexItem[],
    loading: false,
    error: null as Error | null,
    fallback: false,
    loaded: true,
  },
}));

const rule = {
  id: 7,
  name: '放量观察',
  description: null,
  isActive: true,
  period: 'daily' as const,
  lookbackDays: 120,
  targetScope: 'watchlist',
  targetCodes: [],
  definition: {
    period: 'daily' as const,
    lookbackDays: 120,
    target: { scope: 'watchlist' as const, stockCodes: [] },
    groups: [{
      id: 'group-1',
      conditions: [
        {
          id: 'cond-1',
          left: { metric: 'volume', offset: 0 },
          operator: '>' as const,
          right: {
            type: 'aggregate' as const,
            metric: 'volume',
            method: 'avg' as const,
            window: 5,
            offset: 1,
            multiplier: 2,
          },
        },
        {
          id: 'cond-2',
          left: { metric: 'chip_concentration_90', offset: 0 },
          operator: '<' as const,
          right: { type: 'literal' as const, value: 15 },
        },
        {
          id: 'cond-3',
          left: { metric: 'profit_ratio', offset: 0 },
          operator: '>' as const,
          right: { type: 'literal' as const, value: 90 },
        },
      ],
    }],
  },
  lastMatchCount: 0,
};

const secondRule = {
  ...rule,
  id: 8,
  name: '低集中度观察',
  definition: {
    ...rule.definition,
    groups: [{
      id: 'group-2',
      conditions: [
        {
          id: 'cond-4',
          left: { metric: 'chip_concentration_90', offset: 0 },
          operator: '<' as const,
          right: { type: 'literal' as const, value: 12 },
        },
      ],
    }],
  },
};

const emptyRule = {
  ...rule,
  id: 9,
  name: '零命中观察',
  definition: {
    ...rule.definition,
    groups: [{
      id: 'group-3',
      conditions: [
        {
          id: 'cond-5',
          left: { metric: 'profit_ratio', offset: 0 },
          operator: '<' as const,
          right: { type: 'literal' as const, value: 5 },
        },
      ],
    }],
  },
};

const matches = [{
  stockCode: '300274.SZ',
  stockName: '阳光电源',
  matchedDates: ['2026-05-01'],
  matchedEvents: [{
    date: '2026-05-01',
    snapshot: { close: 137.41, volume: 123456, chip_concentration_90: 8.94, profit_ratio: 91.2 },
    matched_groups: [{
      id: 'group-1',
      conditions: [
        {
          id: 'cond-1',
          left_metric: 'volume',
          operator: '>',
          values: { left: 123456, right: 100000 },
          explanation: '命中：成交量 123,456 > 100,000',
        },
        {
          id: 'cond-2',
          left_metric: 'chip_concentration_90',
          operator: '<',
          values: { left: 8.94, right: 15 },
          explanation: '命中：90%筹码集中度 8.94 < 15',
        },
        {
          id: 'cond-3',
          left_metric: 'profit_ratio',
          operator: '>',
          values: { left: 91.2, right: 90 },
          explanation: '命中：收盘获利 91.2 > 90',
        },
      ],
    }],
    explanation: '成交量放大',
  }],
  matchedGroups: [],
  snapshot: {},
  explanation: '成交量放大',
}];

const multiEventMatches = [{
  ...matches[0],
  matchedDates: ['2026-05-01', '2026-05-02', '2026-05-03'],
  matchedEvents: ['2026-05-01', '2026-05-02', '2026-05-03'].map((date) => ({
    ...matches[0].matchedEvents[0],
    date,
  })),
}];

const secondMatches = [{
  stockCode: '688521.SH',
  stockName: '芯原股份',
  matchedDates: ['2026-04-20'],
  matchedEvents: [{
    date: '2026-04-20',
    snapshot: { close: 88.3, chip_concentration_90: 7.8 },
    matched_groups: [{
      id: 'group-2',
      conditions: [
        {
          id: 'cond-4',
          left_metric: 'chip_concentration_90',
          operator: '<',
          values: { left: 7.8, right: 12 },
          explanation: '命中：90%筹码集中度 7.8 < 12',
        },
      ],
    }],
    explanation: '筹码集中度较低',
  }],
  matchedGroups: [],
  snapshot: {},
  explanation: '筹码集中度较低',
}];

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((innerResolve, innerReject) => {
    resolve = innerResolve;
    reject = innerReject;
  });
  return { promise, resolve, reject };
}

function makeIndicatorHistory(period: KLinePeriod): KLineData[] {
  const length = period === 'daily' ? 31 : 40;
  return Array.from({ length }, (_, index) => {
    const close = 130 + index * 0.2;
    return {
      date: period === 'daily'
        ? `2026-05-${String(index + 1).padStart(2, '0')}`
        : `2026-05-01 09:${String(index).padStart(2, '0')}`,
      open: close - 0.2,
      high: close + 0.4,
      low: close - 0.4,
      close,
      volume: 10000 + index,
      amount: close * (10000 + index),
      changePercent: 0.1,
      turnoverRate: 1,
    };
  });
}

vi.mock('../../api/rules', () => ({
  rulesApi: {
    getMetrics: vi.fn(),
    list: vi.fn(),
    listRuns: vi.fn(),
    getRunMatches: vi.fn(),
    deleteRun: vi.fn(),
    run: vi.fn(),
    runBatch: vi.fn(),
  },
}));

vi.mock('../../api/history', () => ({
  historyApi: {
    getList: vi.fn(),
  },
}));

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: vi.fn(),
  },
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: {
    getHistory: vi.fn(),
    getQuote: vi.fn(),
    getIndicatorMetrics: vi.fn(),
  },
}));

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => stockIndexHookState.current,
}));

describe('BacktestPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stockIndexHookState.current = {
      index: [],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    vi.mocked(rulesApi.getMetrics).mockResolvedValue([
      { key: 'close', label: '收盘价', category: 'K线图', valueType: 'number', unit: '元', periods: ['daily'] },
      { key: 'volume', label: '成交量', category: '成交量图', valueType: 'number', unit: '股', periods: ['daily'] },
      { key: 'chip_concentration_90', label: '90%筹码集中度', category: '筹码峰', valueType: 'number', unit: '%', periods: ['daily'] },
      { key: 'profit_ratio', label: '收盘获利', category: '筹码峰', valueType: 'number', unit: '%', periods: ['daily'] },
    ]);
    vi.mocked(rulesApi.list).mockResolvedValue([rule]);
    vi.mocked(rulesApi.listRuns).mockResolvedValue([]);
    vi.mocked(rulesApi.getRunMatches).mockResolvedValue(matches);
    vi.mocked(rulesApi.deleteRun).mockResolvedValue(undefined);
    vi.mocked(rulesApi.runBatch).mockResolvedValue({
      runId: 12,
      ruleId: 7,
      status: 'completed',
      targetCount: 2,
      matchCount: 1,
      eventCount: 1,
      mode: 'history',
      durationMs: 20,
      matches,
      errors: [],
    });
    vi.mocked(stocksApi.getHistory).mockImplementation(async (stockCode, _days, period = 'daily') => ({
      stockCode,
      stockName: '阳光电源',
      period,
      data: makeIndicatorHistory(period),
    }));
    vi.mocked(stocksApi.getQuote).mockImplementation(async (stockCode) => ({
      stockCode,
      stockName: '阳光电源',
      currentPrice: 137.41,
      change: 1.2,
      changePercent: 0.9,
      open: 136,
      high: 139,
      low: 135,
      prevClose: 136.21,
      volume: 100000,
      amount: 13741000,
      volumeRatio: 1.3,
      turnoverRate: 0.8,
      source: 'test',
      updateTime: '2026-05-01T15:00:00',
    }));
    vi.mocked(stocksApi.getIndicatorMetrics).mockImplementation(async (stockCode) => ({
      stockCode,
      stockName: '阳光电源',
      chipDistribution: {
        code: stockCode,
        date: '2026-05-01',
        source: 'test',
        profitRatio: 0.94,
        avgCost: 120,
        cost90Low: 110,
        cost90High: 140,
        concentration90: 0.12,
        cost70Low: 115,
        cost70High: 132,
        concentration70: 0.08,
        distribution: [{ price: 120, percent: 1 }],
        snapshots: [{
          code: stockCode,
          date: '2026-05-01',
          source: 'test',
          profitRatio: 0.94,
          avgCost: 120,
          cost90Low: 110,
          cost90High: 140,
          concentration90: 0.12,
          cost70Low: 115,
          cost70High: 132,
          concentration70: 0.08,
          distribution: [{ price: 120, percent: 1 }],
        }],
      },
      majorHolders: [],
      majorHolderStatus: 'not_supported',
      sourceChain: [],
      errors: [],
      updateTime: '2026-05-01T15:00:00',
    }));
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [{ key: 'STOCK_LIST', value: '300274.SZ,688521.SH', rawValueExists: true, isMasked: false }],
    });
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 2,
      page: 1,
      limit: 20,
      items: [
        { id: 1, queryId: 'q-1', stockCode: '300274.SZ', stockName: '阳光电源', createdAt: '2026-05-03T09:00:00' },
        { id: 2, queryId: 'q-2', stockCode: '688521.SH', stockName: '芯原股份', createdAt: '2026-05-03T09:00:00' },
      ],
    });
  });

  it('starts without old run history and displays rows after running', async () => {
    render(<BacktestPage />);

    expect(await screen.findByText('回测执行历史')).toBeInTheDocument();
    expect(screen.getByText('暂无执行记录')).toBeInTheDocument();
    expect(screen.getByText('暂无命中结果')).toBeInTheDocument();
    expect(screen.queryByLabelText('行业')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    expect(await screen.findByText('#12 放量观察')).toBeInTheDocument();
    expect(screen.getAllByText('300274.SZ').length).toBeGreaterThan(0);
    expect(screen.getByText('2026-05-01')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '成交量 当前值' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '成交量 当前值' }).querySelector('span')).toHaveClass('text-danger');
    expect(screen.getByRole('columnheader', { name: '前5期成交量均值*2' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '前5期成交量均值*2' }).querySelector('span')).not.toHaveClass('text-danger');
    expect(screen.getByRole('columnheader', { name: '90%筹码集中度 当前值' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '90%筹码集中度 当前值' }).querySelector('span')).toHaveClass('text-danger');
    expect(screen.getByRole('columnheader', { name: '90%筹码集中度 阈值' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '收盘获利 当前值' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '收盘获利 阈值' })).toBeInTheDocument();
    expect(screen.getByText('8.94%')).toBeInTheDocument();
    expect(screen.getByText('91.2%')).toBeInTheDocument();
    expect(screen.getAllByText(/成交量/).length).toBeGreaterThan(0);
  });

  it('groups multi-rule backtest results by rule and can collapse a rule group', async () => {
    vi.mocked(rulesApi.list).mockResolvedValue([rule, secondRule, emptyRule]);
    vi.mocked(rulesApi.runBatch).mockResolvedValue({
      runId: 12,
      ruleId: 7,
      ruleIds: [7, 8, 9],
      ruleNames: ['放量观察', '低集中度观察', '零命中观察'],
      status: 'completed',
      targetCount: 2,
      matchCount: 2,
      eventCount: 2,
      mode: 'history',
      durationMs: 53,
      matches: [
        { ...matches[0], ruleId: 7 },
        { ...secondMatches[0], ruleId: 8 },
      ],
      errors: [],
    });

    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByLabelText('选择回测规则'));
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    const volumeGroup = await screen.findByTestId('backtest-rule-group-7');
    const chipGroup = await screen.findByTestId('backtest-rule-group-8');
    const emptyGroup = await screen.findByTestId('backtest-rule-group-9');

    expect(within(volumeGroup).getByText('#7 放量观察')).toBeInTheDocument();
    expect(within(volumeGroup).getByText('300274.SZ')).toBeInTheDocument();
    expect(within(volumeGroup).getByRole('columnheader', { name: '成交量 当前值' })).toBeInTheDocument();
    expect(within(chipGroup).getByText('#8 低集中度观察')).toBeInTheDocument();
    expect(within(chipGroup).getByText('688521.SH')).toBeInTheDocument();
    expect(within(chipGroup).getByRole('columnheader', { name: '90%筹码集中度 当前值' })).toBeInTheDocument();
    expect(within(emptyGroup).getByText('#9 零命中观察')).toBeInTheDocument();
    expect(within(emptyGroup).getByText('命中 0 条')).toBeInTheDocument();
    expect(within(emptyGroup).getByText('该规则暂无命中记录')).toBeInTheDocument();

    fireEvent.click(within(volumeGroup).getByRole('button', { name: '收起规则 #7 放量观察' }));

    expect(within(volumeGroup).queryByText('300274.SZ')).not.toBeInTheDocument();
    expect(within(chipGroup).getByText('688521.SH')).toBeInTheDocument();
  });

  it('loads persisted run history and restores the latest run result', async () => {
    vi.mocked(rulesApi.getRunMatches).mockResolvedValueOnce(multiEventMatches);
    vi.mocked(rulesApi.listRuns).mockResolvedValue([{
      id: 11,
      ruleId: 7,
      ruleName: '放量观察',
      status: 'completed',
      targetCount: 2,
      matchCount: 1,
      eventCount: 3,
      startedAt: '2026-05-03T09:30:00',
      finishedAt: '2026-05-03T09:31:00',
      durationMs: 1000,
    }]);
    render(<BacktestPage />);

    expect(await screen.findByText('#11 放量观察')).toBeInTheDocument();
    expect(rulesApi.getRunMatches).toHaveBeenCalledWith(11);
    expect(screen.getByText('命中记录 3')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /运行结果/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('2026-05-01')).toBeInTheDocument();
    expect(screen.getByText('2026-05-03')).toBeInTheDocument();
    expect(screen.getAllByText('300274.SZ').length).toBeGreaterThan(0);
  });

  it('clears stale result rows when selecting a running persisted run', async () => {
    vi.mocked(rulesApi.listRuns).mockResolvedValue([
      {
        id: 12,
        ruleId: 7,
        ruleName: '放量观察',
        status: 'running',
        targetCount: 2,
        matchCount: 0,
        startedAt: '2026-05-04T09:30:00',
        finishedAt: null,
        durationMs: null,
      },
      {
        id: 11,
        ruleId: 7,
        ruleName: '放量观察',
        status: 'completed',
        targetCount: 2,
        matchCount: 1,
        startedAt: '2026-05-03T09:30:00',
        finishedAt: '2026-05-03T09:31:00',
        durationMs: 1000,
      },
    ]);
    render(<BacktestPage />);

    expect(await screen.findByText('#11 放量观察')).toBeInTheDocument();
    expect(screen.getByText('2026-05-01')).toBeInTheDocument();

    fireEvent.click(screen.getByText('#12 放量观察').closest('button')!);

    expect(screen.getByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '运行结果' })).toBeInTheDocument();
    expect(screen.getByText('#12 放量观察 仍在执行，暂未生成命中结果')).toBeInTheDocument();
    expect(screen.queryByText('2026-05-01')).not.toBeInTheDocument();
  });

  it('deletes a persisted backtest run from history and clears the selected result', async () => {
    vi.mocked(rulesApi.listRuns).mockResolvedValue([{
      id: 11,
      ruleId: 7,
      ruleName: '放量观察',
      status: 'completed',
      targetCount: 2,
      matchCount: 1,
      startedAt: '2026-05-03T09:30:00',
      finishedAt: '2026-05-03T09:31:00',
      durationMs: 1000,
    }]);
    render(<BacktestPage />);

    expect(await screen.findByText('#11 放量观察')).toBeInTheDocument();
    expect(screen.getByText('2026-05-01')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '删除回测记录 #11' }));
    expect(await screen.findByText('删除回测记录')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => {
      expect(rulesApi.deleteRun).toHaveBeenCalledWith(11);
    });
    await waitFor(() => {
      expect(screen.queryByText('#11 放量观察')).not.toBeInTheDocument();
    });
    expect(screen.getByText('暂无执行记录')).toBeInTheDocument();
    expect(screen.getByText('暂无命中结果')).toBeInTheDocument();
  });

  it('opens indicator analysis for the clicked hit stock and focuses the hit date', async () => {
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    const indicatorButton = await screen.findByRole('button', { name: '查看 阳光电源 2026-05-01 指标分析' });
    fireEvent.click(indicatorButton);

    expect(await screen.findByRole('dialog', { name: '指标分析' })).toBeInTheDocument();
    expect(screen.getByText('命中日 2026-05-01')).toBeInTheDocument();
    expect(screen.getByTestId('indicator-hit-highlight-2026-05-01')).toBeInTheDocument();
    expect(stocksApi.getHistory).toHaveBeenCalledWith('300274.SZ', expect.any(Number), 'daily');
  });

  it('runs selected rule with the current stock target override', async () => {
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    await waitFor(() => {
      expect(rulesApi.runBatch).toHaveBeenCalledWith({
        ruleIds: [7],
        mode: 'history',
        target: {
          scope: 'watchlist',
          stockCodes: ['300274.SZ', '688521.SH'],
        },
        startDate: expect.any(String),
        endDate: expect.any(String),
      });
    });
  });

  it('shows running history, progress, and logs while a run is in flight', async () => {
    const deferredRun = createDeferred<Awaited<ReturnType<typeof rulesApi.runBatch>>>();
    vi.mocked(rulesApi.runBatch).mockReturnValueOnce(deferredRun.promise);
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    expect(await screen.findByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);
    expect(screen.getAllByText('请求后端执行').length).toBeGreaterThan(0);
    expect(screen.getByText(/正在请求后端执行/)).toBeInTheDocument();

    await act(async () => {
      deferredRun.resolve({
        runId: 12,
        ruleId: 7,
        status: 'completed',
        targetCount: 2,
        matchCount: 1,
        eventCount: 1,
        mode: 'history',
        durationMs: 20,
        matches,
        errors: [],
      });
    });

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /运行结果/ })).toHaveAttribute('aria-selected', 'true');
    });
    expect(await screen.findByText('#12 放量观察')).toBeInTheDocument();
  });

  it('keeps the active run visible after leaving and returning to the page', async () => {
    const deferredRun = createDeferred<Awaited<ReturnType<typeof rulesApi.runBatch>>>();
    vi.mocked(rulesApi.runBatch).mockReturnValueOnce(deferredRun.promise);
    const view = render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    expect(await screen.findByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);

    view.unmount();
    render(<BacktestPage />);

    expect(await screen.findByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);
    expect(screen.getByText(/正在请求后端执行/)).toBeInTheDocument();

    await act(async () => {
      deferredRun.resolve({
        runId: 12,
        ruleId: 7,
        status: 'completed',
        targetCount: 2,
        matchCount: 1,
        eventCount: 1,
        mode: 'history',
        durationMs: 20,
        matches,
        errors: [],
      });
    });

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /运行结果/ })).toHaveAttribute('aria-selected', 'true');
    });
    expect(await screen.findByText('#12 放量观察')).toBeInTheDocument();
    expect(screen.getByText('2026-05-01')).toBeInTheDocument();
  });

  it('can run an industry backtest with explicit semiconductor stock codes', async () => {
    stockIndexHookState.current = {
      index: [
        {
          canonicalCode: '688521.SH',
          displayCode: '688521.SH',
          nameZh: '芯原股份',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '半导体',
        },
        {
          canonicalCode: '603375.SH',
          displayCode: '603375.SH',
          nameZh: '盛景微',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '半导体',
        },
        {
          canonicalCode: '000001.SZ',
          displayCode: '000001.SZ',
          nameZh: '平安银行',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '银行',
        },
      ],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    expect(screen.queryByLabelText('行业')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('股票范围'), { target: { value: 'industry' } });
    expect(screen.getByLabelText('行业')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    await waitFor(() => {
      expect(rulesApi.runBatch).toHaveBeenCalledWith(expect.objectContaining({
        ruleIds: [7],
        target: {
          scope: 'custom',
          stockCodes: ['603375.SH', '688521.SH'],
        },
      }));
    });
  });

  it('opens the indicator detail dialog when clicking a result row', async () => {
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    fireEvent.click(await screen.findByText('2026-05-01'));

    expect(await screen.findByRole('dialog', { name: '回测指标明细' })).toBeInTheDocument();
    expect(screen.getByText('全部指标')).toBeInTheDocument();
    expect(screen.getByText('命中条件值')).toBeInTheDocument();
    expect(screen.getAllByText('收盘获利').length).toBeGreaterThan(0);
  });
});
