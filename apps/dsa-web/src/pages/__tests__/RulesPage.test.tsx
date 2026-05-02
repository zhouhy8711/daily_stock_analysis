import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../api/history';
import { rulesApi } from '../../api/rules';
import { systemConfigApi } from '../../api/systemConfig';
import RulesPage from '../RulesPage';
import type { StockIndexItem } from '../../types/stockIndex';

const stockIndexHookState = vi.hoisted(() => ({
  current: {
    index: [] as StockIndexItem[],
    loading: false,
    error: null as Error | null,
    fallback: false,
    loaded: true,
  },
}));

const metricItems = [
  {
    key: 'close',
    label: '收盘价',
    category: '基础行情',
    valueType: 'number',
    unit: '元',
    periods: ['daily'],
    description: '',
  },
];

vi.mock('../../api/rules', () => ({
  rulesApi: {
    getMetrics: vi.fn(),
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    run: vi.fn(),
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

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => stockIndexHookState.current,
}));

describe('RulesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    stockIndexHookState.current = {
      index: [
        {
          canonicalCode: '600519.SH',
          displayCode: '600519',
          nameZh: '贵州茅台',
          market: 'CN',
          assetType: 'stock',
          active: true,
        },
        {
          canonicalCode: '000001.SZ',
          displayCode: '000001',
          nameZh: '平安银行',
          market: 'CN',
          assetType: 'stock',
          active: true,
        },
        {
          canonicalCode: 'AAPL.US',
          displayCode: 'AAPL',
          nameZh: 'Apple',
          market: 'US',
          assetType: 'stock',
          active: true,
        },
        {
          canonicalCode: '000300.SH',
          displayCode: '000300',
          nameZh: '沪深300',
          market: 'INDEX',
          assetType: 'index',
          active: true,
        },
      ],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    vi.mocked(rulesApi.getMetrics).mockResolvedValue(metricItems);
    vi.mocked(rulesApi.list).mockResolvedValue([]);
    vi.mocked(rulesApi.create).mockResolvedValue({
      id: 1,
      name: '新规则',
      description: null,
      isActive: true,
      period: 'daily',
      lookbackDays: 120,
      targetScope: 'watchlist',
      targetCodes: ['300274.SZ', '688521.SH', 'BABA', '002439.SZ', '600126.SH', '002436.SZ'],
      definition: {
        period: 'daily',
        lookbackDays: 120,
        target: { scope: 'watchlist', stockCodes: ['300274.SZ', '688521.SH', 'BABA', '002439.SZ', '600126.SH', '002436.SZ'] },
        groups: [],
      },
      lastMatchCount: 0,
    });
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [{ key: 'STOCK_LIST', value: '', rawValueExists: false, isMasked: false }],
    });
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 6,
      page: 1,
      limit: 20,
      items: [
        {
          id: 1,
          queryId: 'q-1',
          stockCode: '300274.SZ',
          stockName: '阳光电源',
          currentPrice: 133.79,
          changePct: -0.53,
          createdAt: '2026-04-25T22:46:00',
        },
        {
          id: 2,
          queryId: 'q-2',
          stockCode: '688521.SH',
          stockName: '芯原股份',
          currentPrice: 228.71,
          changePct: -2.88,
          createdAt: '2026-04-25T22:36:00',
        },
        {
          id: 3,
          queryId: 'q-3',
          stockCode: 'BABA',
          stockName: '阿里巴巴',
          currentPrice: 135.82,
          createdAt: '2026-04-25T22:36:00',
        },
        {
          id: 4,
          queryId: 'q-4',
          stockCode: '002439.SZ',
          stockName: '启明星辰',
          currentPrice: 14.94,
          changePct: -0.86,
          createdAt: '2026-04-25T22:14:00',
        },
        {
          id: 5,
          queryId: 'q-5',
          stockCode: '600126.SH',
          stockName: '杭钢股份',
          currentPrice: 10.48,
          changePct: 0.48,
          createdAt: '2026-04-25T22:14:00',
        },
        {
          id: 6,
          queryId: 'q-6',
          stockCode: '002436.SZ',
          stockName: '兴森科技',
          currentPrice: 28.52,
          changePct: 0.42,
          createdAt: '2026-04-25T22:11:00',
        },
      ],
    });
  });

  it('fills the stock list from the same current watchlist shown on the home page', async () => {
    render(<RulesPage />);

    const textarea = await screen.findByLabelText('股票代码 / 股票名称');

    expect(textarea).toHaveValue('300274.SZ 阳光电源\n688521.SH 芯原股份\nBABA 阿里巴巴\n002439.SZ 启明星辰\n600126.SH 杭钢股份\n002436.SZ 兴森科技');
    expect(textarea).not.toBeDisabled();
    expect(textarea).toHaveAttribute('readonly');
    expect(textarea).toHaveClass('font-mono');
    expect(textarea).toHaveClass('text-foreground');

    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(rulesApi.create).toHaveBeenCalledWith(expect.objectContaining({
        definition: expect.objectContaining({
          target: {
            scope: 'watchlist',
            stockCodes: ['300274.SZ', '688521.SH', 'BABA', '002439.SZ', '600126.SH', '002436.SZ'],
          },
        }),
      }));
    });
  });

  it('refreshes the stock code list when switching back to watchlist scope', async () => {
    render(<RulesPage />);

    const textarea = await screen.findByLabelText('股票代码 / 股票名称');
    const scopeSelect = screen.getByLabelText('股票范围');

    fireEvent.change(scopeSelect, { target: { value: 'custom' } });
    fireEvent.change(textarea, { target: { value: 'TSLA' } });
    expect(textarea).toHaveValue('TSLA');
    expect(textarea).not.toHaveAttribute('readonly');

    fireEvent.change(scopeSelect, { target: { value: 'watchlist' } });

    expect(textarea).toHaveValue('300274.SZ 阳光电源\n688521.SH 芯原股份\nBABA 阿里巴巴\n002439.SZ 启明星辰\n600126.SH 杭钢股份\n002436.SZ 兴森科技');
    expect(textarea).toHaveAttribute('readonly');
  });

  it('opens an expanded stock list, filters by code or name, and removes one stock', async () => {
    render(<RulesPage />);

    await screen.findByLabelText('股票代码 / 股票名称');

    fireEvent.click(screen.getByRole('button', { name: '最大化股票列表' }));
    expect(screen.getByRole('dialog', { name: '股票列表最大化' })).toBeInTheDocument();

    const expandedList = screen.getByRole('list', { name: '最大化股票列表内容' });
    expect(within(expandedList).getByText('300274.SZ 阳光电源')).toBeInTheDocument();
    expect(within(expandedList).getByText('BABA 阿里巴巴')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('筛选股票列表'), { target: { value: '阿里' } });
    expect(within(expandedList).getByText('BABA 阿里巴巴')).toBeInTheDocument();
    expect(within(expandedList).queryByText('300274.SZ 阳光电源')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '移除 BABA 阿里巴巴' }));
    expect(within(expandedList).queryByText('BABA 阿里巴巴')).not.toBeInTheDocument();
    expect(screen.getByText('没有匹配的股票')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '收起股票列表' }));
    expect(screen.queryByRole('dialog', { name: '股票列表最大化' })).not.toBeInTheDocument();
    expect((screen.getByLabelText('股票代码 / 股票名称') as HTMLTextAreaElement).value).not.toContain('BABA');
  });

  it('fills all A-share stocks when selecting the all A-shares scope', async () => {
    render(<RulesPage />);

    const textarea = await screen.findByLabelText('股票代码 / 股票名称');
    const scopeSelect = screen.getByLabelText('股票范围');

    fireEvent.change(scopeSelect, { target: { value: 'all_a_shares' } });

    expect(textarea).toHaveValue('000001 平安银行\n600519 贵州茅台');
    expect(textarea).toHaveAttribute('readonly');

    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(rulesApi.create).toHaveBeenCalledWith(expect.objectContaining({
        definition: expect.objectContaining({
          target: {
            scope: 'all_a_shares',
            stockCodes: ['000001', '600519'],
          },
        }),
      }));
    });
  });

  it('replaces stale saved watchlist codes with the current home watchlist on selection', async () => {
    vi.mocked(rulesApi.list).mockResolvedValue([{
      id: 2,
      name: '旧规则',
      description: null,
      isActive: true,
      period: 'daily',
      lookbackDays: 120,
      targetScope: 'watchlist',
      targetCodes: ['OLD'],
      definition: {
        period: 'daily',
        lookbackDays: 120,
        target: { scope: 'watchlist', stockCodes: ['OLD'] },
        groups: [{
          id: 'group-1',
          conditions: [{
            id: 'cond-1',
            left: { metric: 'close', offset: 0 },
            operator: '>' as const,
            right: { type: 'literal' as const, value: 1 },
          }],
        }],
      },
      lastMatchCount: 0,
    }]);

    render(<RulesPage />);

    expect(await screen.findByLabelText('股票代码 / 股票名称')).toHaveValue('300274.SZ 阳光电源\n688521.SH 芯原股份\nBABA 阿里巴巴\n002439.SZ 启明星辰\n600126.SH 杭钢股份\n002436.SZ 兴森科技');
  });

  it('saves the current stock code list before running a saved rule', async () => {
    const existingRule = {
      id: 7,
      name: '放量观察',
      description: null,
      isActive: true,
      period: 'daily',
      lookbackDays: 120,
      targetScope: 'watchlist',
      targetCodes: [],
      definition: {
        period: 'daily' as const,
        lookbackDays: 120,
        target: { scope: 'watchlist' as const, stockCodes: [] },
        groups: [{
          id: 'group-1',
          conditions: [{
            id: 'cond-1',
            left: { metric: 'close', offset: 0 },
            operator: '>' as const,
            right: { type: 'literal' as const, value: 1 },
          }],
        }],
      },
      lastMatchCount: 0,
    };
    vi.mocked(rulesApi.list).mockResolvedValue([existingRule]);
    vi.mocked(rulesApi.update).mockResolvedValue({
      ...existingRule,
      targetCodes: ['000001', 'TSLA'],
      definition: {
        ...existingRule.definition,
        target: { scope: 'watchlist', stockCodes: ['000001', 'TSLA'] },
      },
    });
    vi.mocked(rulesApi.run).mockResolvedValue({
      runId: 11,
      ruleId: 7,
      status: 'completed',
      targetCount: 2,
      matchCount: 0,
      durationMs: 12,
      matches: [],
      errors: [],
    });

    render(<RulesPage />);

    const textarea = await screen.findByLabelText('股票代码 / 股票名称');
    const scopeSelect = screen.getByLabelText('股票范围');

    fireEvent.change(scopeSelect, { target: { value: 'custom' } });
    fireEvent.change(textarea, { target: { value: '000001 平安银行\nTSLA 特斯拉' } });
    fireEvent.click(screen.getByRole('button', { name: '运行' }));

    await waitFor(() => {
      expect(rulesApi.update).toHaveBeenCalledWith(7, expect.objectContaining({
        definition: expect.objectContaining({
          target: {
            scope: 'custom',
            stockCodes: ['000001', 'TSLA'],
          },
        }),
      }));
      expect(rulesApi.run).toHaveBeenCalledWith(7);
    });
    expect(vi.mocked(rulesApi.update).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(rulesApi.run).mock.invocationCallOrder[0]);
  });
});
