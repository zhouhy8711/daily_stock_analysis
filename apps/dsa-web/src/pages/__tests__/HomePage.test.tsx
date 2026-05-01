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
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import HomePage from '../HomePage';

const navigateMock = vi.fn();

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
  },
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: {
    getQuote: vi.fn(),
    getHistory: vi.fn(),
    getIndicatorMetrics: vi.fn(),
  },
}));

vi.mock('../../hooks/useTaskStream', () => ({
  useTaskStream: vi.fn(),
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
    useStockPoolStore.getState().resetDashboardState();
    vi.mocked(systemConfigApi.getConfig).mockResolvedValue({
      configVersion: 'v1',
      maskToken: '******',
      items: [{ key: 'STOCK_LIST', value: '', rawValueExists: false, isMasked: false }],
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

  it('opens indicator analysis from the watchlist action and removes the report overlay entry', async () => {
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

    expect(await screen.findByTestId('indicator-analysis-modal')).toBeInTheDocument();
    expect(stocksApi.getHistory).toHaveBeenCalledWith('600519', 120, 'daily');
    expect(stocksApi.getQuote).toHaveBeenCalledWith('600519');
    expect(stocksApi.getIndicatorMetrics).toHaveBeenCalledWith('600519');
    expect(screen.getByRole('tab', { name: '日K' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '5分' })).toBeInTheDocument();
    expect(await screen.findByText('MA5 / MA10 / MA20')).toBeInTheDocument();
    expect(await screen.findByText('昨收 / 涨跌额')).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'K线时间窗口' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '1分' }));
    await waitFor(() => {
      expect(stocksApi.getHistory).toHaveBeenLastCalledWith('600519', 3, '1m');
    });
    expect(await screen.findByText('数据周期')).toBeInTheDocument();
    expect(screen.getByText('1分 · 北京时间')).toBeInTheDocument();
    expect(await screen.findByText('主力持仓与筹码分布')).toBeInTheDocument();
    const marketStructureToggle = await screen.findByTestId('market-structure-toggle');
    expect(marketStructureToggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('平均成本')).not.toBeInTheDocument();
    expect(screen.queryByText('摩根士丹利')).not.toBeInTheDocument();

    fireEvent.click(marketStructureToggle);
    expect(marketStructureToggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('img', { name: '主力筹码趋势图' })).toBeInTheDocument();
    const holderSelect = screen.getByRole('combobox', { name: '选择主力名称' });
    expect(holderSelect).toHaveValue('all');
    expect(within(holderSelect).getByRole('option', { name: '所有主力合计' })).toBeInTheDocument();
    expect(within(holderSelect).getByRole('option', { name: '摩根士丹利 2.35%' })).toBeInTheDocument();
    expect(screen.getByText('主力判断')).toBeInTheDocument();
    expect(screen.getByText('主力指标')).toBeInTheDocument();
    expect(screen.getByText('基础指标')).toBeInTheDocument();
    expect(screen.getByText('当前主力')).toBeInTheDocument();
    expect(screen.getByTestId('selected-holder-label')).toHaveTextContent('所有主力合计');
    expect(screen.getByText('平均成本')).toBeInTheDocument();
    expect(screen.getAllByText('118.50').length).toBeGreaterThan(0);
    fireEvent.mouseEnter(await screen.findByTestId('market-structure-trend-point-2026-03-24'));
    const structureTooltip = await screen.findByTestId('market-structure-trend-tooltip');
    expect(within(structureTooltip).getByText('2026-03-24')).toBeInTheDocument();
    expect(within(structureTooltip).getByText('收盘价')).toBeInTheDocument();
    expect(within(structureTooltip).getByText('主力动能')).toBeInTheDocument();
    fireEvent.mouseLeave(screen.getByTestId('market-structure-trend-point-2026-03-24'));
    await waitFor(() => {
      expect(screen.queryByTestId('market-structure-trend-tooltip')).not.toBeInTheDocument();
    });
    fireEvent.change(holderSelect, { target: { value: 'holder-0' } });
    expect(holderSelect).toHaveValue('holder-0');
    expect(screen.getByTestId('selected-holder-label')).toHaveTextContent('摩根士丹利 2.35%');
    const klineToggle = screen.getByTestId('kline-chart-toggle');
    expect(klineToggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: '收起K线图' })).toBeInTheDocument();
    expect(screen.queryByTestId('report-overlay')).not.toBeInTheDocument();

    fireEvent.mouseEnter(await screen.findByTestId('indicator-chart-bar-2026-03-24'));
    const tooltip = await screen.findByTestId('indicator-chart-tooltip');
    expect(within(tooltip).getByText('2026-03-24')).toBeInTheDocument();
    expect(within(tooltip).getByText('收盘')).toBeInTheDocument();
    expect(within(tooltip).getByText('123.00')).toBeInTheDocument();
    expect(within(tooltip).getByText('量比')).toBeInTheDocument();

    fireEvent.mouseLeave(screen.getByTestId('indicator-chart-bar-2026-03-24'));
    await waitFor(() => {
      expect(screen.queryByTestId('indicator-chart-tooltip')).not.toBeInTheDocument();
    });

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByTestId('indicator-analysis-modal')).not.toBeInTheDocument();
    });

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
    expect(await screen.findByTestId('indicator-analysis-modal')).toBeInTheDocument();
    expect(stocksApi.getHistory).toHaveBeenLastCalledWith('00700.HK', 120, 'daily');

    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => {
      expect(screen.queryByTestId('indicator-analysis-modal')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '美股' }));
    fireEvent.click(screen.getByRole('button', { name: '查看 阿里巴巴 指标分析' }));
    expect(await screen.findByTestId('indicator-analysis-modal')).toBeInTheDocument();
    expect(stocksApi.getHistory).toHaveBeenLastCalledWith('BABA', 120, 'daily');
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
    const watchlistPanel = screen.getByRole('heading', { name: '自选' }).closest('section');
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
