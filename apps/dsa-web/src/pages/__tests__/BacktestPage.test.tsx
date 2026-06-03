import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createApiError, createParsedApiError } from '../../api/error';
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

const multiGroupRule = {
  ...rule,
  id: 5,
  name: '缩量后的放倍量',
  definition: {
    ...rule.definition,
    groups: [
      {
        id: 'group-volume-after-shrink-with-range-chip-30d',
        conditions: [
          {
            id: 'cond-after-shrink-volume-gt-prev5avg-30d',
            left: { metric: 'volume', offset: 0 },
            operator: '>' as const,
            right: {
              type: 'aggregate' as const,
              metric: 'volume',
              method: 'avg' as const,
              window: 5,
              offset: 1,
              multiplier: 1.2,
            },
          },
          {
            id: 'cond-price-range-30d-between-20-30',
            left: { metric: 'price_range_30d_pct', offset: 0 },
            operator: 'between' as const,
            right: {
              type: 'range' as const,
              min: { type: 'literal' as const, value: 20 },
              max: { type: 'literal' as const, value: 30 },
            },
          },
        ],
      },
      {
        id: 'group-volume-after-shrink-with-range-chip-60d',
        conditions: [
          {
            id: 'cond-after-shrink-volume-gt-prev5avg-60d',
            left: { metric: 'volume', offset: 0 },
            operator: '>' as const,
            right: {
              type: 'aggregate' as const,
              metric: 'volume',
              method: 'avg' as const,
              window: 5,
              offset: 1,
              multiplier: 1.2,
            },
          },
          {
            id: 'cond-price-range-60d-between-20-30',
            left: { metric: 'price_range_60d_pct', offset: 0 },
            operator: 'between' as const,
            right: {
              type: 'range' as const,
              min: { type: 'literal' as const, value: 20 },
              max: { type: 'literal' as const, value: 30 },
            },
          },
        ],
      },
    ],
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

const multiGroupMatches = [{
  stockCode: '600143',
  stockName: '金发科技',
  matchedDates: ['2026-05-08'],
  matchedEvents: [{
    date: '2026-05-08',
    snapshot: { volume: 1070148, price_range_60d_pct: 26.5436 },
    matched_groups: [{
      id: 'group-volume-after-shrink-with-range-chip-60d',
      conditions: [
        {
          id: 'cond-after-shrink-volume-gt-prev5avg-60d',
          left_metric: 'volume',
          operator: '>',
          values: { left: 1070148, right: 606511.44 },
          explanation: '命中：成交量 1,070,148 > 606,511.44',
        },
        {
          id: 'cond-price-range-60d-between-20-30',
          left_metric: 'price_range_60d_pct',
          operator: 'between',
          values: { left: 26.5436, min: 20, max: 30 },
          explanation: '命中：近60日最高最低振幅 26.54 between [20, 30]',
        },
      ],
    }],
    explanation: '近60日条件组命中',
  }],
  matchedGroups: [],
  snapshot: {},
  explanation: '近60日条件组命中',
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

function makeBacktestMatch(stockCode: string, stockName: string, dates: string[]) {
  return {
    ...matches[0],
    stockCode,
    stockName,
    matchedDates: dates,
    matchedEvents: dates.map((date) => ({
      ...matches[0].matchedEvents[0],
      date,
    })),
  };
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((innerResolve, innerReject) => {
    resolve = innerResolve;
    reject = innerReject;
  });
  return { promise, resolve, reject };
}

function createRequestTimeoutError() {
  return createApiError(createParsedApiError({
    title: '请求本地服务超时',
    message: '本地服务响应超时，后台任务可能仍在执行，请稍后刷新或检查服务负载。',
    category: 'request_timeout',
  }), { code: 'ECONNABORTED' });
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
    getRun: vi.fn(),
    getRunMatches: vi.fn(),
    deleteRun: vi.fn(),
    notifyRunMatches: vi.fn(),
    run: vi.fn(),
    runBatch: vi.fn(),
    runBatchAsync: vi.fn(),
    clearLiveCache: vi.fn(),
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
    getRealtimeCacheStats: vi.fn(),
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
    window.localStorage.clear();
    stockIndexHookState.current = {
      index: [],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    vi.mocked(rulesApi.getMetrics).mockResolvedValue([
      { key: 'close', label: '收盘价', category: 'K线图', valueType: 'number', unit: '元', periods: ['daily'] },
      { key: 'volume', label: '成交量', category: '成交量图', valueType: 'number', unit: '手', periods: ['daily'] },
      { key: 'chip_concentration_90', label: '90%筹码集中度', category: '筹码峰', valueType: 'number', unit: '%', periods: ['daily'] },
      { key: 'profit_ratio', label: '收盘获利', category: '筹码峰', valueType: 'number', unit: '%', periods: ['daily'] },
    ]);
    vi.mocked(rulesApi.list).mockResolvedValue([rule]);
    vi.mocked(rulesApi.listRuns).mockResolvedValue([]);
    vi.mocked(rulesApi.getRunMatches).mockResolvedValue(matches);
    vi.mocked(rulesApi.deleteRun).mockResolvedValue(undefined);
    vi.mocked(rulesApi.notifyRunMatches).mockResolvedValue({
      sent: true,
      message: '实测命中通知已发送',
      matchCount: 1,
      eventCount: 1,
    });
    vi.mocked(rulesApi.clearLiveCache).mockResolvedValue(undefined);
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
    vi.mocked(rulesApi.runBatchAsync).mockResolvedValue({
      runId: 12,
      ruleId: 7,
      ruleIds: [7],
      ruleNames: ['放量观察'],
      status: 'running',
      targetCount: 2,
      completedCount: 0,
      matchCount: 0,
      eventCount: 0,
      mode: 'history',
      durationMs: 0,
      matches: [],
      errors: [],
    });
    vi.mocked(rulesApi.getRun).mockResolvedValue({
      id: 12,
      runIds: [12],
      ruleId: 7,
      ruleIds: [7],
      ruleName: '放量观察',
      ruleNames: ['放量观察'],
      status: 'completed',
      targetCount: 2,
      completedCount: 2,
      matchCount: 1,
      eventCount: 1,
      startedAt: '2026-05-10T08:00:00',
      finishedAt: '2026-05-10T08:00:20',
      durationMs: 20,
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
    vi.mocked(systemConfigApi.getRealtimeCacheStats).mockResolvedValue({
      totalMemoryBytes: 0,
      totalMemoryMb: 0,
      quoteCacheItems: 0,
      quoteCacheMemoryBytes: 0,
      quoteCacheMemoryMb: 0,
      providerCacheMemoryBytes: 0,
      providerCacheMemoryMb: 0,
      bucketStart: null,
      snapshotId: '20260507104500',
      snapshotTime: '2026-05-07T10:45:00',
      snapshotAgeSeconds: 0,
      quoteSnapshotItems: 2,
      snapshotRequestedCount: 2,
      snapshotHitCount: 2,
      snapshotMissCount: 0,
      providerBreakdown: [],
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
    stockIndexHookState.current = {
      index: [{
        canonicalCode: '300274.SZ',
        displayCode: '300274.SZ',
        nameZh: '阳光电源',
        market: 'CN',
        assetType: 'stock',
        active: true,
        industry: '电力设备',
      }],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    render(<BacktestPage />);

    expect(await screen.findByText('回测执行历史')).toBeInTheDocument();
    expect(screen.getByText('暂无执行记录')).toBeInTheDocument();
    expect(screen.getByText('暂无命中结果')).toBeInTheDocument();
    expect(screen.queryByLabelText('行业')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    expect(await screen.findByText('#12 放量观察')).toBeInTheDocument();
    expect(screen.getAllByText('300274.SZ').length).toBeGreaterThan(0);
    expect(screen.getByText('2026-05-01')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '行业' })).toBeInTheDocument();
    expect(screen.getByText('电力设备')).toBeInTheDocument();
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
    expect(screen.getByText('12.35万')).toBeInTheDocument();
    expect(screen.getByText('10万')).toBeInTheDocument();
    expect(screen.getAllByText(/成交量/).length).toBeGreaterThan(0);
  });

  it.each([
    { mode: undefined, action: '回测' },
    { mode: 'live' as const, action: '实测' },
  ])('hides inactive rules from the $action rule selector', async ({ mode, action }) => {
    vi.mocked(rulesApi.list).mockResolvedValue([
      { ...rule, id: 6, name: '夹板数', isActive: false },
      { ...secondRule, id: 8, name: '净利润断层', isActive: false },
      { ...emptyRule, id: 9, name: '启用观察', isActive: true },
    ]);

    render(<BacktestPage mode={mode} />);

    expect(await screen.findByText('1 / 1')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(`选择${action}规则`));

    expect(screen.getByRole('checkbox', { name: /#9 启用观察/ })).toBeChecked();
    expect(screen.queryByRole('checkbox', { name: /#6 夹板数/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /#8 净利润断层/ })).not.toBeInTheDocument();
  });

  it('runs live test without showing backtest run history', async () => {
    stockIndexHookState.current = {
      index: [{
        canonicalCode: '300274.SZ',
        displayCode: '300274.SZ',
        nameZh: '阳光电源',
        market: 'CN',
        assetType: 'stock',
        active: true,
        industry: '电力设备',
      }],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    expect(screen.queryByText('回测执行历史')).not.toBeInTheDocument();
    expect(screen.queryByText('开始日期')).not.toBeInTheDocument();
    expect(screen.queryByText('结束日期')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '运行实测' })).toBeInTheDocument();
    expect(rulesApi.listRuns).not.toHaveBeenCalled();

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T00:45:00Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(rulesApi.runBatchAsync).toHaveBeenCalledWith({
        ruleIds: [7],
        mode: 'latest',
        dataPolicy: 'db_only',
        liveCacheKey: expect.stringMatching(/^live-\d+$/),
        target: {
          scope: 'watchlist',
          stockCodes: ['300274.SZ', '688521.SH'],
        },
      });
      expect(rulesApi.notifyRunMatches).toHaveBeenCalledWith(12, {
        executionTime: expect.any(String),
        ruleIds: [7],
        ruleNames: ['放量观察'],
        compact: true,
      });
      expect(screen.getByRole('checkbox', { name: /精简模式/ })).toBeChecked();
      const executionGroupButton = screen.getByRole('button', {
        name: /展开执行时间 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}，命中 1 条，完成时间 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/,
      });
      expect(within(executionGroupButton).getByText(/完成时间 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
      expect(screen.queryByTestId('live-rule-group-7')).not.toBeInTheDocument();
      fireEvent.click(executionGroupButton);

      const liveRuleGroup = screen.getByTestId('live-rule-group-7');
      expect(within(liveRuleGroup).getByText('#7 放量观察')).toBeInTheDocument();
      expect(within(liveRuleGroup).getByText('300274.SZ')).toBeInTheDocument();
      expect(within(liveRuleGroup).getByRole('columnheader', { name: '行业' })).toBeInTheDocument();
      expect(within(liveRuleGroup).getByText('电力设备')).toBeInTheDocument();
      expect(within(liveRuleGroup).queryByRole('columnheader', { name: '日期' })).not.toBeInTheDocument();
      expect(within(liveRuleGroup).getByText(/执行 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '停止实测' }));
    } finally {
      vi.useRealTimers();
    }
    await waitFor(() => {
      expect(rulesApi.clearLiveCache).toHaveBeenCalledWith(expect.stringMatching(/^live-\d+$/));
    });
  });

  it('treats preopen live test response as history prewarm only', async () => {
    vi.mocked(rulesApi.runBatchAsync).mockResolvedValueOnce({
      runId: 88,
      ruleId: 7,
      ruleIds: [7],
      ruleNames: ['放量观察'],
      status: 'completed',
      targetCount: 2,
      completedCount: 2,
      matchCount: 0,
      eventCount: 0,
      mode: 'latest',
      durationMs: 12,
      matches: [],
      errors: [],
      prewarmOnly: true,
      prewarmHitCount: 2,
      prewarmMissCount: 0,
    });

    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T00:45:00Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
    } finally {
      vi.useRealTimers();
    }

    expect(rulesApi.getRun).not.toHaveBeenCalled();
    expect(rulesApi.getRunMatches).not.toHaveBeenCalled();
    expect(rulesApi.notifyRunMatches).not.toHaveBeenCalled();
    expect(screen.getByText(/09:30 前仅预热历史数据/)).toBeInTheDocument();
    expect(screen.getByText(/历史缓存命中 2\/2/)).toBeInTheDocument();
  });

  it('polls async preopen history prewarm until it completes', async () => {
    vi.mocked(rulesApi.runBatchAsync).mockResolvedValueOnce({
      runId: 88,
      ruleId: 7,
      ruleIds: [7],
      ruleNames: ['放量观察'],
      status: 'running',
      targetCount: 2,
      completedCount: 0,
      matchCount: 0,
      eventCount: 0,
      mode: 'latest',
      durationMs: 0,
      matches: [],
      errors: [],
      prewarmOnly: true,
      prewarmHitCount: 0,
      prewarmMissCount: 0,
    });
    vi.mocked(rulesApi.getRun).mockResolvedValueOnce({
      id: 88,
      runIds: [88],
      ruleId: 7,
      ruleIds: [7],
      ruleName: '放量观察',
      ruleNames: ['放量观察'],
      status: 'completed',
      targetCount: 2,
      completedCount: 2,
      matchCount: 0,
      eventCount: 0,
      startedAt: '2026-05-07T00:45:00Z',
      finishedAt: '2026-05-07T00:45:20Z',
      durationMs: 20,
      prewarmOnly: true,
      prewarmHitCount: 2,
      prewarmMissCount: 0,
    });

    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T00:45:00Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
    } finally {
      vi.useRealTimers();
    }

    expect(rulesApi.getRun).toHaveBeenCalledWith(88);
    expect(rulesApi.getRunMatches).not.toHaveBeenCalled();
    expect(rulesApi.notifyRunMatches).not.toHaveBeenCalled();
    expect(screen.getByText(/开盘前历史缓存预热已启动：0\/2/)).toBeInTheDocument();
    expect(screen.getByText(/历史缓存命中 2\/2/)).toBeInTheDocument();
  });

  it('keeps an active live run retryable when progress polling times out', async () => {
    vi.mocked(rulesApi.getRun)
      .mockRejectedValueOnce(createRequestTimeoutError())
      .mockResolvedValueOnce({
        id: 12,
        runIds: [12],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105510',
        startedAt: '2026-05-07T02:55:10Z',
        finishedAt: '2026-05-07T02:55:20Z',
        durationMs: 20,
      });

    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T02:55:10Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByText(/进度轮询暂时超时/)).toBeInTheDocument();
      expect(screen.queryByText(/实测失败/)).not.toBeInTheDocument();

      vi.setSystemTime(new Date('2026-05-07T02:55:40Z'));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(rulesApi.getRun).toHaveBeenCalledTimes(2);
      expect(rulesApi.getRunMatches).toHaveBeenCalledWith(12);
      expect(screen.getByRole('tab', { name: /运行结果/ })).toHaveAttribute('aria-selected', 'true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps a completed live run retryable when match loading times out', async () => {
    vi.mocked(rulesApi.getRun).mockResolvedValue({
      id: 12,
      runIds: [12],
      ruleId: 7,
      ruleIds: [7],
      ruleName: '放量观察',
      ruleNames: ['放量观察'],
      status: 'completed',
      targetCount: 2,
      completedCount: 2,
      matchCount: 1,
      eventCount: 1,
      snapshotId: '20260507105510',
      startedAt: '2026-05-07T02:55:10Z',
      finishedAt: '2026-05-07T02:55:20Z',
      durationMs: 20,
    });
    vi.mocked(rulesApi.getRunMatches)
      .mockRejectedValueOnce(createRequestTimeoutError())
      .mockResolvedValueOnce(matches);

    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T02:55:10Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByText(/命中明细读取暂时超时/)).toBeInTheDocument();
      expect(screen.queryByText(/实测失败/)).not.toBeInTheDocument();
      expect(rulesApi.runBatchAsync).toHaveBeenCalledTimes(1);

      vi.setSystemTime(new Date('2026-05-07T02:55:40Z'));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(rulesApi.getRunMatches).toHaveBeenCalledTimes(2);
      expect(rulesApi.runBatchAsync).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('tab', { name: /运行结果/ })).toHaveAttribute('aria-selected', 'true');
    } finally {
      vi.useRealTimers();
    }
  });

  it('skips realtime snapshot coverage log before A-share open', async () => {
    stockIndexHookState.current = {
      index: [
        {
          canonicalCode: '300274.SZ',
          displayCode: '300274.SZ',
          nameZh: '阳光电源',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '电力设备',
        },
        {
          canonicalCode: '688521.SH',
          displayCode: '688521.SH',
          nameZh: '芯原股份',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '半导体',
        },
      ],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    vi.mocked(rulesApi.runBatchAsync).mockResolvedValueOnce({
      runId: 88,
      ruleId: 7,
      ruleIds: [7],
      ruleNames: ['放量观察'],
      status: 'completed',
      targetCount: 2,
      completedCount: 2,
      matchCount: 0,
      eventCount: 0,
      mode: 'latest',
      durationMs: 12,
      matches: [],
      errors: [],
      prewarmOnly: true,
      prewarmHitCount: 2,
      prewarmMissCount: 0,
    });

    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    await screen.findByText('1 / 1');
    fireEvent.change(screen.getByLabelText('股票范围'), { target: { value: 'all_a_shares' } });

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T00:45:00Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(systemConfigApi.getRealtimeCacheStats).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('tab', { name: /执行日志/ }));
      expect(screen.getByText(/开盘前实时行情快照尚未生成/)).toBeInTheDocument();
      expect(screen.queryByText(/实时行情快照覆盖/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '停止实测' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('logs realtime snapshot coverage before full-market live test starts', async () => {
    stockIndexHookState.current = {
      index: [
        {
          canonicalCode: '300274.SZ',
          displayCode: '300274.SZ',
          nameZh: '阳光电源',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '电力设备',
        },
        {
          canonicalCode: '688521.SH',
          displayCode: '688521.SH',
          nameZh: '芯原股份',
          market: 'CN',
          assetType: 'stock',
          active: true,
          industry: '半导体',
        },
      ],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    vi.mocked(systemConfigApi.getRealtimeCacheStats).mockResolvedValueOnce({
      totalMemoryBytes: 0,
      totalMemoryMb: 0,
      quoteCacheItems: 0,
      quoteCacheMemoryBytes: 0,
      quoteCacheMemoryMb: 0,
      providerCacheMemoryBytes: 0,
      providerCacheMemoryMb: 0,
      bucketStart: null,
      snapshotId: null,
      snapshotTime: null,
      snapshotAgeSeconds: null,
      quoteSnapshotItems: 1,
      snapshotRequestedCount: 2,
      snapshotHitCount: 1,
      snapshotMissCount: 1,
      providerBreakdown: [],
    });

    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    await screen.findByText('1 / 1');
    fireEvent.change(screen.getByLabelText('股票范围'), { target: { value: 'all_a_shares' } });
    expect(screen.getAllByText(/2 只股票/).length).toBeGreaterThan(0);

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T02:45:00Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(systemConfigApi.getRealtimeCacheStats).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole('tab', { name: /执行日志/ }));
      expect(screen.getByText(/实时行情快照覆盖：1\/2，快照 未就绪/)).toBeInTheDocument();
      expect(rulesApi.runBatchAsync).toHaveBeenCalledWith(expect.objectContaining({
        target: {
          scope: 'all_a_shares',
          stockCodes: ['300274.SZ', '688521.SH'],
        },
      }));

      fireEvent.click(screen.getByRole('button', { name: '停止实测' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not start live test after A-share market closes', async () => {
    render(<BacktestPage mode="live" />);

    expect(await screen.findByText('实测结果')).toBeInTheDocument();
    await screen.findByText('1 / 1');

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-11T13:05:00Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    } finally {
      vi.useRealTimers();
    }

    expect(rulesApi.runBatchAsync).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '运行实测' })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    });
    expect(screen.getByText(/当前已超过实测时间，未触发实时扫描/)).toBeInTheDocument();
  });

  it('keeps each live test cycle as a collapsed execution group', async () => {
    render(<BacktestPage mode="live" />);

    await screen.findByText('1 / 1');
    vi.mocked(rulesApi.runBatchAsync)
      .mockResolvedValueOnce({
        runId: 12,
        ruleId: 7,
        ruleIds: [7],
        ruleNames: ['放量观察'],
        status: 'running',
        targetCount: 2,
        completedCount: 0,
        matchCount: 0,
        eventCount: 0,
        mode: 'latest',
        durationMs: 0,
        matches: [],
        errors: [],
      })
      .mockResolvedValueOnce({
        runId: 13,
        ruleId: 7,
        ruleIds: [7],
        ruleNames: ['放量观察'],
        status: 'running',
        targetCount: 2,
        completedCount: 0,
        matchCount: 0,
        eventCount: 0,
        mode: 'latest',
        durationMs: 0,
        matches: [],
        errors: [],
      });
    vi.mocked(rulesApi.getRun)
      .mockResolvedValueOnce({
        id: 12,
        runIds: [12],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105510',
        startedAt: '2026-05-07T02:55:10Z',
        finishedAt: '2026-05-07T02:55:10Z',
        durationMs: 20,
      })
      .mockResolvedValueOnce({
        id: 13,
        runIds: [13],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105610',
        startedAt: '2026-05-07T02:56:10Z',
        finishedAt: '2026-05-07T02:56:10Z',
        durationMs: 18,
      });
    vi.mocked(rulesApi.getRunMatches)
      .mockResolvedValueOnce(matches)
      .mockResolvedValueOnce(secondMatches);

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T02:55:10Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByRole('button', {
        name: '展开执行时间 2026-05-07 10:55:10，命中 1 条，完成时间 2026-05-07 10:55:10',
      })).toBeInTheDocument();

      vi.setSystemTime(new Date('2026-05-07T02:55:40Z'));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByRole('button', {
        name: '展开执行时间 2026-05-07 10:56:10，命中 1 条，完成时间 2026-05-07 10:56:10',
      })).toBeInTheDocument();
      expect(screen.getAllByTestId('live-execution-group')).toHaveLength(2);
      expect(screen.queryByTestId('live-rule-group-7')).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: '停止实测' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows one same-day live row per rule and stock while compact mode is enabled', async () => {
    render(<BacktestPage mode="live" />);

    await screen.findByText('1 / 1');
    vi.mocked(rulesApi.runBatchAsync)
      .mockResolvedValueOnce({
        runId: 12,
        ruleId: 7,
        ruleIds: [7],
        ruleNames: ['放量观察'],
        status: 'running',
        targetCount: 2,
        completedCount: 0,
        matchCount: 0,
        eventCount: 0,
        mode: 'latest',
        durationMs: 0,
        matches: [],
        errors: [],
      })
      .mockResolvedValueOnce({
        runId: 13,
        ruleId: 7,
        ruleIds: [7],
        ruleNames: ['放量观察'],
        status: 'running',
        targetCount: 2,
        completedCount: 0,
        matchCount: 0,
        eventCount: 0,
        mode: 'latest',
        durationMs: 0,
        matches: [],
        errors: [],
      });
    vi.mocked(rulesApi.getRun)
      .mockResolvedValueOnce({
        id: 12,
        runIds: [12],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105510',
        startedAt: '2026-05-07T02:55:10Z',
        finishedAt: '2026-05-07T02:55:10Z',
        durationMs: 20,
      })
      .mockResolvedValueOnce({
        id: 13,
        runIds: [13],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105610',
        startedAt: '2026-05-07T02:56:10Z',
        finishedAt: '2026-05-07T02:56:10Z',
        durationMs: 18,
      });
    vi.mocked(rulesApi.getRunMatches)
      .mockResolvedValueOnce(matches)
      .mockResolvedValueOnce(matches);

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T02:55:10Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      vi.setSystemTime(new Date('2026-05-07T02:55:40Z'));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getAllByTestId('live-execution-group')).toHaveLength(1);
      expect(screen.getByRole('button', {
        name: '展开执行时间 2026-05-07 10:56:10，命中 1 条，完成时间 2026-05-07 10:56:10',
      })).toBeInTheDocument();
      expect(screen.queryByRole('button', {
        name: '展开执行时间 2026-05-07 10:55:10，命中 1 条，完成时间 2026-05-07 10:55:10',
      })).not.toBeInTheDocument();
      expect(rulesApi.notifyRunMatches).toHaveBeenLastCalledWith(13, expect.objectContaining({ compact: true }));

      fireEvent.click(screen.getByRole('button', { name: '停止实测' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps current repeated live rows when compact mode is disabled', async () => {
    render(<BacktestPage mode="live" />);

    await screen.findByText('1 / 1');
    fireEvent.click(screen.getByRole('checkbox', { name: /精简模式/ }));
    expect(screen.getByRole('checkbox', { name: /精简模式/ })).not.toBeChecked();

    vi.mocked(rulesApi.runBatchAsync)
      .mockResolvedValueOnce({
        runId: 12,
        ruleId: 7,
        ruleIds: [7],
        ruleNames: ['放量观察'],
        status: 'running',
        targetCount: 2,
        completedCount: 0,
        matchCount: 0,
        eventCount: 0,
        mode: 'latest',
        durationMs: 0,
        matches: [],
        errors: [],
      })
      .mockResolvedValueOnce({
        runId: 13,
        ruleId: 7,
        ruleIds: [7],
        ruleNames: ['放量观察'],
        status: 'running',
        targetCount: 2,
        completedCount: 0,
        matchCount: 0,
        eventCount: 0,
        mode: 'latest',
        durationMs: 0,
        matches: [],
        errors: [],
      });
    vi.mocked(rulesApi.getRun)
      .mockResolvedValueOnce({
        id: 12,
        runIds: [12],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105510',
        startedAt: '2026-05-07T02:55:10Z',
        finishedAt: '2026-05-07T02:55:10Z',
        durationMs: 20,
      })
      .mockResolvedValueOnce({
        id: 13,
        runIds: [13],
        ruleId: 7,
        ruleIds: [7],
        ruleName: '放量观察',
        ruleNames: ['放量观察'],
        status: 'completed',
        targetCount: 2,
        completedCount: 2,
        matchCount: 1,
        eventCount: 1,
        snapshotId: '20260507105610',
        startedAt: '2026-05-07T02:56:10Z',
        finishedAt: '2026-05-07T02:56:10Z',
        durationMs: 18,
      });
    vi.mocked(rulesApi.getRunMatches)
      .mockResolvedValueOnce(matches)
      .mockResolvedValueOnce(matches);

    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-07T02:55:10Z'));
      fireEvent.click(screen.getByRole('button', { name: '运行实测' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      vi.setSystemTime(new Date('2026-05-07T02:55:40Z'));
      await act(async () => {
        vi.advanceTimersByTime(30_000);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getAllByTestId('live-execution-group')).toHaveLength(2);
      expect(rulesApi.notifyRunMatches).toHaveBeenLastCalledWith(13, expect.objectContaining({ compact: false }));

      fireEvent.click(screen.getByRole('button', { name: '停止实测' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('groups multi-rule backtest results by rule and can collapse a rule group', async () => {
    vi.mocked(rulesApi.list).mockResolvedValue([rule, secondRule, emptyRule]);
    vi.mocked(rulesApi.runBatchAsync).mockResolvedValue({
      runId: 12,
      ruleId: 7,
      ruleIds: [7, 8, 9],
      ruleNames: ['放量观察', '低集中度观察', '零命中观察'],
      status: 'running',
      targetCount: 2,
      completedCount: 0,
      matchCount: 0,
      eventCount: 0,
      mode: 'history',
      durationMs: 0,
      matches: [],
      errors: [],
    });
    vi.mocked(rulesApi.getRun).mockResolvedValue({
      id: 12,
      runIds: [12],
      ruleId: 7,
      ruleIds: [7, 8, 9],
      ruleName: '多规则回测（3 条）',
      ruleNames: ['放量观察', '低集中度观察', '零命中观察'],
      status: 'completed',
      targetCount: 2,
      completedCount: 2,
      matchCount: 2,
      eventCount: 2,
      startedAt: '2026-05-10T08:00:00',
      finishedAt: '2026-05-10T08:00:53',
      durationMs: 53,
    });
    vi.mocked(rulesApi.getRunMatches).mockResolvedValue([
      { ...matches[0], ruleId: 7 },
      { ...secondMatches[0], ruleId: 8 },
    ]);

    render(<BacktestPage />);

    await screen.findByText('1 / 3');
    fireEvent.click(screen.getByLabelText('选择回测规则'));
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    await waitFor(() => {
      expect(screen.getByText('3 / 3')).toBeInTheDocument();
    });
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

  it('uses the matched condition group columns for multi-group rule results', async () => {
    vi.mocked(rulesApi.getMetrics).mockResolvedValue([
      { key: 'volume', label: '成交量', category: '成交量图', valueType: 'number', unit: '手', periods: ['daily'] },
      { key: 'price_range_30d_pct', label: '近30日振幅', category: '价格', valueType: 'number', unit: '%', periods: ['daily'] },
      { key: 'price_range_60d_pct', label: '近60日振幅', category: '价格', valueType: 'number', unit: '%', periods: ['daily'] },
    ]);
    vi.mocked(rulesApi.list).mockResolvedValue([multiGroupRule]);
    vi.mocked(rulesApi.listRuns).mockResolvedValue([{
      id: 21,
      ruleId: 5,
      ruleName: '缩量后的放倍量',
      status: 'completed',
      targetCount: 1,
      matchCount: 1,
      eventCount: 1,
      startedAt: '2026-05-10T08:00:00',
      finishedAt: '2026-05-10T08:00:10',
      durationMs: 10,
    }]);
    vi.mocked(rulesApi.getRunMatches).mockResolvedValueOnce(multiGroupMatches);

    render(<BacktestPage />);

    const resultGroup = await screen.findByTestId(
      'backtest-rule-group-5-group-volume-after-shrink-with-range-chip-60d',
    );
    expect(within(resultGroup).getByText('#5 缩量后的放倍量 · 近60日条件组')).toBeInTheDocument();
    expect(within(resultGroup).getByRole('columnheader', { name: '近60日振幅 当前值' })).toBeInTheDocument();
    expect(within(resultGroup).queryByRole('columnheader', { name: '近30日振幅 当前值' })).not.toBeInTheDocument();

    const dataRow = within(resultGroup).getAllByRole('row')[1];
    expect(within(dataRow).getByText('600143')).toBeInTheDocument();
    expect(within(dataRow).getByText('26.54%')).toBeInTheDocument();
    expect(within(dataRow).queryByText('--')).not.toBeInTheDocument();
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

  it('sorts backtest result rows by date descending and stock id ascending', async () => {
    vi.mocked(rulesApi.getRunMatches).mockResolvedValueOnce([
      makeBacktestMatch('603375', '盛景微', ['2025-11-17', '2026-04-30']),
      makeBacktestMatch('600126.SH', '杭钢股份', ['2026-03-12']),
      makeBacktestMatch('000333.SZ', '美的集团', ['2025-11-17']),
    ]);
    vi.mocked(rulesApi.listRuns).mockResolvedValue([{
      id: 13,
      ruleId: 7,
      ruleName: '放量观察',
      status: 'completed',
      targetCount: 3,
      matchCount: 3,
      eventCount: 4,
      startedAt: '2026-05-03T09:30:00',
      finishedAt: '2026-05-03T09:31:00',
      durationMs: 1000,
    }]);

    render(<BacktestPage />);

    const group = await screen.findByTestId('backtest-rule-group-7');
    const rows = within(group).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(4);

    expect(within(rows[0]).getByText('603375')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2026-04-30')).toBeInTheDocument();
    expect(within(rows[1]).getByText('600126.SH')).toBeInTheDocument();
    expect(within(rows[1]).getByText('2026-03-12')).toBeInTheDocument();
    expect(within(rows[2]).getByText('000333.SZ')).toBeInTheDocument();
    expect(within(rows[2]).getByText('2025-11-17')).toBeInTheDocument();
    expect(within(rows[3]).getByText('603375')).toBeInTheDocument();
    expect(within(rows[3]).getByText('2025-11-17')).toBeInTheDocument();
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
    expect(stocksApi.getHistory).toHaveBeenCalledWith('300274.SZ', expect.any(Number), 'daily', 'db_only');
    expect(stocksApi.getIndicatorMetrics).toHaveBeenCalledWith(
      '300274.SZ',
      expect.objectContaining({ dataPolicy: 'db_only', tradeDate: '2026-05-01' }),
    );
    expect(screen.queryByRole('tab', { name: '实时监控' })).not.toBeInTheDocument();
  });

  it('runs selected rule with the current stock target override', async () => {
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    await waitFor(() => {
      expect(rulesApi.runBatchAsync).toHaveBeenCalledWith({
        ruleIds: [7],
        mode: 'history',
        dataPolicy: 'db_only',
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
    const deferredRun = createDeferred<Awaited<ReturnType<typeof rulesApi.runBatchAsync>>>();
    vi.mocked(rulesApi.runBatchAsync).mockReturnValueOnce(deferredRun.promise);
    render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    expect(await screen.findByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);
    expect(screen.getAllByText('请求后端启动后台任务').length).toBeGreaterThan(0);
    expect(screen.getByText(/正在启动后台回测任务/)).toBeInTheDocument();

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
    const deferredRun = createDeferred<Awaited<ReturnType<typeof rulesApi.runBatchAsync>>>();
    vi.mocked(rulesApi.runBatchAsync).mockReturnValueOnce(deferredRun.promise);
    const view = render(<BacktestPage />);

    await screen.findByText('回测执行历史');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    expect(await screen.findByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);

    view.unmount();
    render(<BacktestPage />);

    expect(await screen.findByRole('tab', { name: /执行日志/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);
    expect(screen.getByText(/正在启动后台回测任务/)).toBeInTheDocument();

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

  it('can run an industry backtest with multiple selected industries', async () => {
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
    const industryButton = screen.getByLabelText('行业');
    expect(industryButton).toHaveTextContent('半导体');
    fireEvent.click(industryButton);
    expect(screen.getByLabelText('选择行业 半导体')).toBeChecked();
    fireEvent.click(screen.getByLabelText('选择行业 银行'));
    expect(industryButton).toHaveTextContent('2 个行业');
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));

    await waitFor(() => {
      expect(rulesApi.runBatchAsync).toHaveBeenCalledWith(expect.objectContaining({
        ruleIds: [7],
        target: {
          scope: 'custom',
          stockCodes: ['000001.SZ', '603375.SH', '688521.SH'],
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
