import type React from 'react';
import { useCallback, useMemo, useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { analysisApi, DuplicateTaskError } from '../../api/analysis';
import { historyApi } from '../../api/history';
import { stocksApi } from '../../api/stocks';
import { systemConfigApi } from '../../api/systemConfig';
import { ShellSidebarActionProvider } from '../../components/layout/ShellSidebarActionContext';
import { useStockPoolStore } from '../../stores';
import type { StockIndexItem } from '../../types/stockIndex';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import HomePage from '../HomePage';

const navigateMock = vi.fn();
const stockIndexHookState = vi.hoisted(() => ({
  current: {
    index: [] as StockIndexItem[],
    loading: false,
    error: null as Error | null,
    fallback: false,
    loaded: true,
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../api/history', () => ({
  historyApi: {
    getList: vi.fn(),
    getDetail: vi.fn(),
    deleteRecords: vi.fn(),
    getNews: vi.fn().mockResolvedValue({ total: 0, items: [] }),
    getMarkdown: vi.fn().mockResolvedValue('# report'),
  },
}));

vi.mock('../../api/analysis', async () => {
  const actual = await vi.importActual<typeof import('../../api/analysis')>('../../api/analysis');
  return {
    ...actual,
    analysisApi: {
      analyzeAsync: vi.fn(),
    },
  };
});

vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    getConfig: vi.fn(),
    update: vi.fn(),
  },
  SystemConfigConflictError: class SystemConfigConflictError extends Error {},
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: {
    getQuote: vi.fn(),
    getQuotes: vi.fn(),
    getHistory: vi.fn(),
    getIndicatorMetrics: vi.fn(),
  },
}));

vi.mock('../../hooks/useTaskStream', () => ({
  useTaskStream: vi.fn(),
}));

vi.mock('../../hooks/useStockIndex', () => ({
  useStockIndex: () => stockIndexHookState.current,
}));

const historyItem = {
  id: 1,
  queryId: 'q-1',
  stockCode: '600519',
  stockName: '贵州茅台',
  sentimentScore: 82,
  operationAdvice: '买入',
  createdAt: '2026-03-18T08:00:00Z',
};

const historyReport = {
  meta: {
    id: 1,
    queryId: 'q-1',
    stockCode: '600519',
    stockName: '贵州茅台',
    reportType: 'detailed' as const,
    reportLanguage: 'zh' as const,
    createdAt: '2026-03-18T08:00:00Z',
  },
  summary: {
    analysisSummary: '趋势维持强势',
    operationAdvice: '继续观察买点',
    trendPrediction: '短线震荡偏强',
    sentimentScore: 78,
  },
};

const SidebarActionHarness: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [sidebarAction, setSidebarActionState] = useState<React.ReactNode | null>(null);
  const setSidebarAction = useCallback((action: React.ReactNode | null) => {
    setSidebarActionState(action);
  }, []);
  const value = useMemo(() => ({ setSidebarAction }), [setSidebarAction]);

  return (
    <ShellSidebarActionProvider value={value}>
      <div data-testid="sidebar-action-host">{sidebarAction}</div>
      {children}
    </ShellSidebarActionProvider>
  );
};

const renderHomePageWithSidebarAction = () => render(
  <MemoryRouter>
    <SidebarActionHarness>
      <HomePage />
    </SidebarActionHarness>
  </MemoryRouter>,
);

describe('HomePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigateMock.mockReset();
    stockIndexHookState.current = {
      index: [],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    useStockPoolStore.getState().resetDashboardState();
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [{ key: 'STOCK_LIST', value: '', rawValueExists: false, isMasked: false }],
    });
    vi.mocked(systemConfigApi.update).mockResolvedValue({
      success: true,
      configVersion: 'v2',
      appliedCount: 1,
      skippedMaskedCount: 0,
      reloadTriggered: true,
      updatedKeys: ['STOCK_LIST'],
      warnings: [],
    });
    vi.mocked(stocksApi.getHistory).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      period: 'daily',
      data: [],
    });
    vi.mocked(stocksApi.getQuote).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      currentPrice: 123,
      change: 1.2,
      changePercent: 1,
      open: 121,
      high: 125,
      low: 120,
      prevClose: 121.8,
      volume: 1000000,
      amount: 120000000,
      volumeRatio: 1.1,
      turnoverRate: 0.8,
      updateTime: '2026-04-25T15:00:00',
    });
    vi.mocked(stocksApi.getQuotes).mockResolvedValue({
      items: [],
      failedCodes: [],
      updateTime: '2026-04-25T15:00:00',
    });
    vi.mocked(stocksApi.getIndicatorMetrics).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      chipDistribution: null,
      majorHolders: [],
      majorHolderStatus: 'not_supported',
      sourceChain: [],
      errors: [],
      updateTime: '2026-04-25T15:00:00',
    });
  });

  it('renders the watchlist workspace and opens the selected report as an overlay', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);
    vi.mocked(analysisApi.analyzeAsync).mockResolvedValue({
      taskId: 'task-1',
      status: 'pending',
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const dashboard = await screen.findByTestId('home-dashboard');
    expect(dashboard).toBeInTheDocument();
    expect(dashboard.className).toContain('h-[calc(100vh-5rem)]');
    expect(dashboard.className).toContain('lg:h-[calc(100vh-2rem)]');
    expect(dashboard.firstElementChild?.className).toContain('min-h-0');
    expect(dashboard.firstElementChild?.className).not.toContain('lg:max-w-6xl');
    expect(dashboard.firstElementChild?.className).not.toContain('mx-auto');
    expect(screen.getByPlaceholderText('输入股票代码或名称，如 600519、贵州茅台、AAPL')).toBeInTheDocument();
    expect(await screen.findByText('自选')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 贵州茅台 报告' })).toBeInTheDocument();
    expect(screen.queryByTestId('report-overlay')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 报告' }));

    expect(await screen.findByTestId('report-overlay')).toBeInTheDocument();
    expect(await screen.findByText('趋势维持强势')).toBeInTheDocument();
    expect(
      screen.getByRole('button', {
        name: getReportText(normalizeReportLanguage(historyReport.meta.reportLanguage)).fullReport,
      }),
    ).toBeInTheDocument();
  });

  it('sorts the watchlist by latest price and change percentage', async () => {
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [{ key: 'STOCK_LIST', value: '600519,000001', rawValueExists: true, isMasked: false }],
    });
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 2,
      page: 1,
      limit: 20,
      items: [
        {
          ...historyItem,
          id: 1,
          stockCode: '600519',
          stockName: '贵州茅台',
          currentPrice: 1688.5,
          changePct: -1.25,
        },
        {
          ...historyItem,
          id: 2,
          stockCode: '000001',
          stockName: '平安银行',
          currentPrice: 11.23,
          changePct: 0.42,
        },
      ],
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const watchlistTab = await screen.findByRole('button', { name: '自选' });
    const board = watchlistTab.closest('section') as HTMLElement;
    const getStockLabels = () => within(board)
      .getAllByRole('button', { name: /查看 .* 报告/ })
      .map((button) => button.getAttribute('aria-label'));

    expect(getStockLabels()).toEqual([
      '查看 贵州茅台 报告',
      '查看 平安银行 报告',
    ]);

    fireEvent.click(within(board).getByRole('button', { name: '最新价排序' }));
    expect(getStockLabels()).toEqual([
      '查看 贵州茅台 报告',
      '查看 平安银行 报告',
    ]);

    fireEvent.click(within(board).getByRole('button', { name: '最新价降序排序' }));
    expect(getStockLabels()).toEqual([
      '查看 平安银行 报告',
      '查看 贵州茅台 报告',
    ]);

    fireEvent.click(within(board).getByRole('button', { name: '涨跌幅排序' }));
    expect(getStockLabels()).toEqual([
      '查看 平安银行 报告',
      '查看 贵州茅台 报告',
    ]);
  });

  it('closes the report overlay when pressing Escape', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 报告' }));
    expect(await screen.findByTestId('report-overlay')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByTestId('report-overlay')).not.toBeInTheDocument();
    });
  });

  it('navigates to the standalone indicator analysis page from the watchlist action', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);
    vi.mocked(stocksApi.getHistory).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      period: 'daily',
      data: Array.from({ length: 24 }, (_, index) => {
        const close = 100 + index;
        return {
          date: `2026-03-${String(index + 1).padStart(2, '0')}`,
          open: close - 1,
          high: close + 2,
          low: close - 3,
          close,
          volume: 1000000 + index * 10000,
          amount: 100000000 + index * 1000000,
          changePercent: 1,
        };
      }),
    });
    vi.mocked(stocksApi.getQuote).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      currentPrice: 124,
      change: 1,
      changePercent: 0.8,
      open: 122,
      high: 126,
      low: 121,
      prevClose: 123,
      volume: 1240000,
      amount: 152000000,
      volumeRatio: 1.3,
      turnoverRate: 0.72,
      updateTime: '2026-04-25T15:00:00',
    });
    vi.mocked(stocksApi.getIndicatorMetrics).mockResolvedValue({
      stockCode: '600519',
      stockName: '贵州茅台',
      chipDistribution: {
        code: '600519',
        date: '2026-04-24',
        source: 'akshare',
        profitRatio: 0.68,
        avgCost: 118.5,
        cost90Low: 110.2,
        cost90High: 130.8,
        concentration90: 0.12,
        cost70Low: 114.1,
        cost70High: 126.2,
        concentration70: 0.09,
      },
      majorHolders: [
        { name: '摩根士丹利', holdingRatio: 2.35, holderType: 'QFII', reportDate: '2026-03-31' },
        { name: '香港中央结算有限公司', holdingRatio: 6.18, holderType: '机构', reportDate: '2026-03-31' },
      ],
      majorHolderStatus: 'ok',
      sourceChain: [],
      errors: [],
      updateTime: '2026-04-25T15:00:00',
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const indicatorButton = await screen.findByRole('button', { name: '查看 贵州茅台 指标分析' });
    fireEvent.click(indicatorButton);

    expect(navigateMock).toHaveBeenCalledWith('/indicators/600519?name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0');
    expect(screen.queryByTestId('report-overlay')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 报告' }));
    const overlay = await screen.findByTestId('report-overlay');
    expect(within(overlay).queryByRole('button', { name: '指标分析' })).not.toBeInTheDocument();
  });

  it('keeps the indicator action available across all market filters', async () => {
    const marketItems = [
      historyItem,
      {
        ...historyItem,
        id: 2,
        queryId: 'q-hk',
        stockCode: '00700.HK',
        stockName: '腾讯控股',
      },
      {
        ...historyItem,
        id: 3,
        queryId: 'q-us',
        stockCode: 'BABA',
        stockName: '阿里巴巴',
      },
    ];

    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 3,
      page: 1,
      limit: 20,
      items: marketItems,
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: '查看 贵州茅台 指标分析' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 腾讯控股 指标分析' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 阿里巴巴 指标分析' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '港股' }));
    expect(screen.getByRole('button', { name: '查看 腾讯控股 指标分析' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 贵州茅台 指标分析' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看 腾讯控股 指标分析' }));
    expect(navigateMock).toHaveBeenLastCalledWith('/indicators/00700.HK?name=%E8%85%BE%E8%AE%AF%E6%8E%A7%E8%82%A1');

    fireEvent.click(screen.getByRole('button', { name: '美股' }));
    fireEvent.click(screen.getByRole('button', { name: '查看 阿里巴巴 指标分析' }));
    expect(navigateMock).toHaveBeenLastCalledWith('/indicators/BABA?name=%E9%98%BF%E9%87%8C%E5%B7%B4%E5%B7%B4');
  });

  it('shows the empty report workspace when history is empty', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 0,
      page: 1,
      limit: 20,
      items: [],
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('暂无监控股票')).toBeInTheDocument();
    expect(screen.getByText('在设置里维护 STOCK_LIST，或先完成一次分析后这里会展示最近关注的股票。')).toBeInTheDocument();
  });

  it('shows all A-share stocks sorted by id, filters with the home search, and adds to watchlist', async () => {
    stockIndexHookState.current = {
      index: [
        {
          canonicalCode: '600519.SH',
          displayCode: '600519',
          nameZh: '贵州茅台',
          pinyinFull: 'guizhoumaotai',
          pinyinAbbr: 'gzmt',
          aliases: ['茅台'],
          market: 'CN',
          assetType: 'stock',
          active: true,
          popularity: 100,
        },
        {
          canonicalCode: '000001.SZ',
          displayCode: '000001',
          nameZh: '平安银行',
          pinyinFull: 'pinganyinhang',
          pinyinAbbr: 'payh',
          aliases: [],
          market: 'CN',
          assetType: 'stock',
          active: true,
          popularity: 90,
        },
        {
          canonicalCode: 'BABA',
          displayCode: 'BABA',
          nameZh: '阿里巴巴',
          market: 'US',
          assetType: 'stock',
          active: true,
          popularity: 95,
        },
      ],
      loading: false,
      error: null,
      fallback: false,
      loaded: true,
    };
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 0,
      page: 1,
      limit: 20,
      items: [],
    });
    vi.mocked(analysisApi.analyzeAsync).mockResolvedValue({
      taskId: 'task-all-share',
      status: 'pending',
    });
    vi.mocked(stocksApi.getQuotes).mockImplementation(async (stockCodes: string[]) => ({
      items: stockCodes
        .filter((code) => code === '000001' || code === '600519')
        .map((code) => ({
          stockCode: code,
          stockName: code === '000001' ? '平安银行' : '贵州茅台',
          currentPrice: code === '000001' ? 11.23 : 1688.5,
          changePercent: code === '000001' ? 0.42 : -1.25,
          updateTime: '2026-04-25T15:00:00',
        })),
      failedCodes: [],
      updateTime: '2026-04-25T15:00:00',
    }));

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const allShareTab = await screen.findByRole('button', { name: 'A股所有' });
    fireEvent.click(allShareTab);

    const board = allShareTab.closest('section') as HTMLElement;
    const stockButtons = within(board).getAllByRole('button', { name: /查看 .* 报告/ });
    expect(stockButtons.map((button) => button.getAttribute('aria-label'))).toEqual([
      '查看 平安银行 报告',
      '查看 贵州茅台 报告',
    ]);
    expect(screen.queryByRole('button', { name: '查看 阿里巴巴 报告' })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(stocksApi.getQuotes).toHaveBeenCalledWith(['000001', '600519']);
    });
    expect(await screen.findByText('11.230')).toBeInTheDocument();
    expect(screen.getByText('+0.42%')).toBeInTheDocument();
    expect(screen.getByText('1688.50')).toBeInTheDocument();
    expect(screen.getByText('-1.25%')).toBeInTheDocument();

    fireEvent.click(within(board).getByRole('button', { name: '最新价排序' }));
    expect(within(board).getAllByRole('button', { name: /查看 .* 报告/ })
      .map((button) => button.getAttribute('aria-label'))).toEqual([
      '查看 贵州茅台 报告',
      '查看 平安银行 报告',
    ]);

    fireEvent.click(within(board).getByRole('button', { name: '涨跌幅排序' }));
    expect(within(board).getAllByRole('button', { name: /查看 .* 报告/ })
      .map((button) => button.getAttribute('aria-label'))).toEqual([
      '查看 平安银行 报告',
      '查看 贵州茅台 报告',
    ]);

    const input = screen.getByPlaceholderText('输入股票代码或名称，如 600519、贵州茅台、AAPL');
    fireEvent.change(input, { target: { value: '茅台' } });

    expect(screen.getByRole('button', { name: '查看 贵州茅台 报告' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看 平安银行 报告' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '添加 贵州茅台 到自选' }));

    await waitFor(() => {
      expect(systemConfigApi.update).toHaveBeenCalledWith(expect.objectContaining({
        configVersion: 'v1',
        maskToken: '******',
        reloadNow: true,
        items: [{ key: 'STOCK_LIST', value: '600519' }],
      }));
    });
    expect(analysisApi.analyzeAsync).not.toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: '贵州茅台 已在自选' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看 贵州茅台 报告' }));

    await waitFor(() => {
      expect(analysisApi.analyzeAsync).toHaveBeenCalledWith(expect.objectContaining({
        stockCode: '600519.SH',
        stockName: '贵州茅台',
        originalQuery: '600519.SH',
      }));
    });
  });

  it('surfaces duplicate task warnings from dashboard submission', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 0,
      page: 1,
      limit: 20,
      items: [],
    });
    vi.mocked(analysisApi.analyzeAsync).mockRejectedValue(
      new DuplicateTaskError('600519', 'task-1', '股票 600519 正在分析中'),
    );

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByPlaceholderText('输入股票代码或名称，如 600519、贵州茅台、AAPL');
    fireEvent.change(input, { target: { value: '600519' } });
    fireEvent.click(screen.getByRole('button', { name: '分析' }));

    await waitFor(() => {
      expect(screen.getByText(/股票 600519 正在分析中/)).toBeInTheDocument();
    });
    expect(screen.getByText(/股票 600519 正在分析中/).closest('[role="alert"]')).toBeInTheDocument();
  });

  it('navigates to chat with report context when asking a follow-up question', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 报告' }));

    const followUpButton = await screen.findByRole('button', { name: '追问 AI' });
    fireEvent.click(followUpButton);

    expect(navigateMock).toHaveBeenCalledWith(
      '/chat?stock=600519&name=%E8%B4%B5%E5%B7%9E%E8%8C%85%E5%8F%B0&recordId=1',
    );
  });

  it('confirms and deletes selected history from the dashboard state flow', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);
    vi.mocked(historyApi.deleteRecords).mockResolvedValue({ deleted: 1 });

    useStockPoolStore.setState({
      historyItems: [historyItem],
      selectedHistoryIds: [1],
      selectedReport: historyReport,
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 报告' }));
    fireEvent.click(await screen.findByRole('button', { name: '删除' }));

    expect(
      await screen.findByText('确认删除这条历史记录吗？删除后将不可恢复。'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => {
      expect(historyApi.deleteRecords).toHaveBeenCalledWith([1]);
    });
  });

  it('opens and closes the report overlay history drawer without changing dashboard styles', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);

    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 报告' }));

    const trigger = await screen.findByRole('button', { name: '历史记录' });
    fireEvent.click(trigger);

    expect(container.querySelector('.page-drawer-overlay')).toBeTruthy();
    expect(container.querySelector('.dashboard-card')).toBeTruthy();

    fireEvent.click(container.querySelector('.page-drawer-overlay')?.parentElement as HTMLElement);

    await waitFor(() => {
      expect(container.querySelector('.page-drawer-overlay')).toBeFalsy();
    });
  });

  it('renders active task progress outside the report history sidebar', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 0,
      page: 1,
      limit: 20,
      items: [],
    });

    useStockPoolStore.setState({
      activeTasks: [
        {
          taskId: 'task-1',
          stockCode: '600519',
          stockName: '贵州茅台',
          status: 'processing',
          progress: 45,
          message: '正在抓取最新行情',
          reportType: 'detailed',
          createdAt: '2026-03-18T08:00:00Z',
        },
      ],
    });

    renderHomePageWithSidebarAction();

    const sidebarAction = screen.getByTestId('sidebar-action-host');
    expect(await within(sidebarAction).findByText('分析进度')).toBeInTheDocument();
    expect(within(sidebarAction).getByText('贵州茅台')).toBeInTheDocument();
    expect(within(sidebarAction).getByText('45%')).toBeInTheDocument();
  });

  it('triggers reanalyze from the watchlist panel action even if the search input has other text', async () => {
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 1,
      page: 1,
      limit: 20,
      items: [historyItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);
    vi.mocked(analysisApi.analyzeAsync).mockResolvedValue({
      taskId: 'task-re-1',
      status: 'pending',
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    await screen.findByRole('button', { name: '查看 贵州茅台 报告' });
    const input = screen.getByPlaceholderText('输入股票代码或名称，如 600519、贵州茅台、AAPL');
    fireEvent.change(input, { target: { value: 'AAPL' } });

    fireEvent.click(screen.getByRole('button', { name: '多选' }));
    fireEvent.click(screen.getByLabelText('选择 贵州茅台 监控股票'));
    const reanalyzeButton = screen.getByRole('button', { name: '重新分析' });
    fireEvent.click(reanalyzeButton);

    expect(analysisApi.analyzeAsync).toHaveBeenCalledWith(expect.objectContaining({
      stockCode: '600519',
      originalQuery: '600519',
      forceRefresh: true,
    }));
  });

  it('filters the report sidebar history to the stock opened from the watchlist', async () => {
    const otherHistoryItem = {
      ...historyItem,
      id: 2,
      queryId: 'q-2',
      stockCode: '300274.SZ',
      stockName: '阳光电源',
      sentimentScore: 62,
    };
    const olderSameStockItem = {
      ...historyItem,
      id: 3,
      queryId: 'q-3',
      createdAt: '2026-03-17T08:00:00Z',
      sentimentScore: 76,
    };

    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 3,
      page: 1,
      limit: 20,
      items: [historyItem, otherHistoryItem, olderSameStockItem],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '查看 贵州茅台 报告' }));
    const overlay = await screen.findByTestId('report-overlay');
    await within(overlay).findByText('趋势维持强势');

    expect(within(overlay).getAllByLabelText('选择 贵州茅台 历史记录')).toHaveLength(2);
    expect(within(overlay).queryByLabelText('选择 阳光电源 历史记录')).not.toBeInTheDocument();
  });

  it('reanalyzes all stocks selected from the watchlist panel action', async () => {
    const selectedHistoryItems = [
      {
        ...historyItem,
        id: 2,
        queryId: 'q-2',
        stockCode: '300274.SZ',
        stockName: '阳光电源',
        sentimentScore: 62,
      },
      {
        ...historyItem,
        id: 3,
        queryId: 'q-3',
        stockCode: '600126.SH',
        stockName: '杭钢股份',
        sentimentScore: 66,
      },
    ];

    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 3,
      page: 1,
      limit: 20,
      items: [historyItem, ...selectedHistoryItems],
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(historyReport);
    vi.mocked(analysisApi.analyzeAsync).mockResolvedValue({
      taskId: 'task-re-batch',
      status: 'pending',
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    await screen.findByRole('button', { name: '查看 贵州茅台 报告' });
    fireEvent.click(screen.getByRole('button', { name: '多选' }));
    fireEvent.click(screen.getByLabelText('选择 阳光电源 监控股票'));
    fireEvent.click(screen.getByLabelText('选择 杭钢股份 监控股票'));
    fireEvent.click(screen.getByRole('button', { name: '重新分析 (2)' }));

    await waitFor(() => {
      expect(analysisApi.analyzeAsync).toHaveBeenCalledTimes(2);
    });

    const submittedCodes = vi.mocked(analysisApi.analyzeAsync).mock.calls.map(([payload]) => payload.stockCode);
    expect(submittedCodes).toEqual(['300274.SZ', '600126.SH']);
    expect(analysisApi.analyzeAsync).toHaveBeenNthCalledWith(1, expect.objectContaining({
      stockCode: '300274.SZ',
      stockName: '阳光电源',
      originalQuery: '300274.SZ',
      forceRefresh: true,
    }));
    expect(analysisApi.analyzeAsync).toHaveBeenNthCalledWith(2, expect.objectContaining({
      stockCode: '600126.SH',
      stockName: '杭钢股份',
      originalQuery: '600126.SH',
      forceRefresh: true,
    }));
  });

  it('links watchlist multi-selection to the watchlist panel reanalyze action', async () => {
    const selectedHistoryItems = [
      {
        ...historyItem,
        id: 2,
        queryId: 'q-2',
        stockCode: '300274.SZ',
        stockName: '阳光电源',
        sentimentScore: 62,
      },
      {
        ...historyItem,
        id: 3,
        queryId: 'q-3',
        stockCode: '600126.SH',
        stockName: '杭钢股份',
        sentimentScore: 66,
      },
    ];
    const sunshineReport = {
      ...historyReport,
      meta: {
        ...historyReport.meta,
        id: 2,
        queryId: 'q-2',
        stockCode: '300274.SZ',
        stockName: '阳光电源',
      },
    };

    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [{ key: 'STOCK_LIST', value: '300274,600126', rawValueExists: true, isMasked: false }],
    });
    vi.mocked(historyApi.getList).mockResolvedValue({
      total: 2,
      page: 1,
      limit: 20,
      items: selectedHistoryItems,
    });
    vi.mocked(historyApi.getDetail).mockResolvedValue(sunshineReport);
    vi.mocked(analysisApi.analyzeAsync).mockResolvedValue({
      taskId: 'task-watchlist-batch',
      status: 'pending',
    });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('button', { name: '查看 阳光电源 报告' })).toBeInTheDocument();
    const watchlistPanel = screen.getByRole('button', { name: '自选' }).closest('section');
    expect(watchlistPanel).not.toBeNull();
    expect(within(watchlistPanel as HTMLElement).getByRole('button', { name: '重新分析' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '多选' }));
    fireEvent.click(screen.getByLabelText('选择 阳光电源 监控股票'));
    fireEvent.click(screen.getByLabelText('选择 杭钢股份 监控股票'));
    expect(within(watchlistPanel as HTMLElement).getByText('已选 2')).toBeInTheDocument();
    fireEvent.click(within(watchlistPanel as HTMLElement).getByRole('button', { name: '重新分析 (2)' }));

    await waitFor(() => {
      expect(analysisApi.analyzeAsync).toHaveBeenCalledTimes(2);
    });
    expect(analysisApi.analyzeAsync).toHaveBeenNthCalledWith(1, expect.objectContaining({
      stockCode: '300274.SZ',
      stockName: '阳光电源',
      originalQuery: '300274.SZ',
      forceRefresh: true,
    }));
    expect(analysisApi.analyzeAsync).toHaveBeenNthCalledWith(2, expect.objectContaining({
      stockCode: '600126.SH',
      stockName: '杭钢股份',
      originalQuery: '600126.SH',
      forceRefresh: true,
    }));
  });
});
