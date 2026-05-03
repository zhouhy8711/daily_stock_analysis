import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../api/history';
import { rulesApi } from '../../api/rules';
import { systemConfigApi } from '../../api/systemConfig';
import type { StockIndexItem } from '../../types/stockIndex';
import RulesPage from '../RulesPage';

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
  { key: 'close', label: '收盘价', category: 'K线图', valueType: 'number', unit: '元', periods: ['daily'], description: '' },
  { key: 'volume_ma5', label: 'MAVOL5', category: '成交量图', valueType: 'number', unit: '股', periods: ['daily'], description: '' },
  { key: 'profit_ratio', label: '收盘获利', category: '筹码峰-全部筹码', valueType: 'number', unit: '%', periods: ['daily'], description: '' },
  { key: 'main_profit_ratio', label: '主力收盘获利', category: '筹码峰-主力筹码', valueType: 'number', unit: '%', periods: ['daily'], description: '' },
];

vi.mock('../../api/rules', () => ({
  rulesApi: {
    getMetrics: vi.fn(),
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
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
          industry: '白酒',
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
      targetCodes: ['300274.SZ', '688521.SH'],
      definition: {
        period: 'daily',
        lookbackDays: 120,
        target: { scope: 'watchlist', stockCodes: ['300274.SZ', '688521.SH'] },
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
      total: 2,
      page: 1,
      limit: 20,
      items: [
        { id: 1, queryId: 'q-1', stockCode: '300274.SZ', stockName: '阳光电源', createdAt: '2026-04-25T22:46:00' },
        { id: 2, queryId: 'q-2', stockCode: '688521.SH', stockName: '芯原股份', createdAt: '2026-04-25T22:36:00' },
      ],
    });
  });

  it('keeps rule editing focused on base settings and condition groups', async () => {
    render(<RulesPage />);

    expect(await screen.findByText('基础设置')).toBeInTheDocument();
    expect(screen.getByText('条件设置')).toBeInTheDocument();
    expect(screen.queryByLabelText('股票范围')).not.toBeInTheDocument();
    expect(screen.queryByText('运行结果')).not.toBeInTheDocument();
  });

  it('saves the current watchlist target without showing stock target controls', async () => {
    render(<RulesPage />);

    await screen.findByText('基础设置');
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => {
      expect(rulesApi.create).toHaveBeenCalledWith(expect.objectContaining({
        definition: expect.objectContaining({
          target: {
            scope: 'watchlist',
            stockCodes: ['300274.SZ', '688521.SH'],
          },
        }),
      }));
    });
  });

  it('labels trading-day offsets and shows help text from the question icon', async () => {
    render(<RulesPage />);

    await screen.findByText('基础设置');
    expect(screen.getAllByText('取值日偏移').length).toBeGreaterThan(0);

    fireEvent.mouseEnter(screen.getAllByRole('button', { name: '取值日偏移说明' })[0]);

    expect(await screen.findByRole('tooltip')).toHaveTextContent('偏移按交易日计算');
    expect(screen.getByRole('tooltip')).toHaveTextContent('窗口 5、偏移 1');
  });

  it('groups metric selectors by indicator chart category', async () => {
    render(<RulesPage />);

    await screen.findByText('基础设置');
    const metricSelect = screen.getByLabelText('指标 key') as HTMLSelectElement;
    const groupLabels = Array.from(metricSelect.querySelectorAll('optgroup')).map((group) => group.label);

    expect(groupLabels).toEqual(['K线图', '成交量图', '筹码峰-全部筹码', '筹码峰-主力筹码']);
    expect(metricSelect.querySelector('option[value="volume_ma5"]')?.textContent).toContain('MAVOL5');
    expect(metricSelect.querySelector('option[value="profit_ratio"]')?.textContent).toContain('收盘获利');
  });
});
