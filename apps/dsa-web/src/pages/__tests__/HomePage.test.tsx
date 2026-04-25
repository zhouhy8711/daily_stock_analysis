import type React from 'react';
import { useCallback, useMemo, useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { analysisApi, DuplicateTaskError } from '../../api/analysis';
import { historyApi } from '../../api/history';
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
