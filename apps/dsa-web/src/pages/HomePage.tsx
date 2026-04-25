import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { systemConfigApi } from '../api/systemConfig';
import { ApiErrorAlert, ConfirmDialog, Button, EmptyState, InlineAlert } from '../components/common';
import { useShellSidebarAction } from '../components/layout/ShellSidebarActionContext';
import { DashboardStateBlock } from '../components/dashboard';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { HistoryList } from '../components/history';
import { ReportMarkdown, ReportSummary } from '../components/report';
import { WatchlistBoard, type WatchlistItem } from '../components/watchlist';
import { useDashboardLifecycle, useHomeDashboardState } from '../hooks';
import { getReportText, normalizeReportLanguage } from '../utils/reportLanguage';

const normalizeWatchlistCode = (stockCode: string) => stockCode.trim().toUpperCase();

const getWatchlistLookupKeys = (stockCode: string): string[] => {
  const code = normalizeWatchlistCode(stockCode);
  const keys = new Set<string>([code]);
  const [base] = code.split('.');
  if (base) {
    keys.add(base);
  }
  if (code.startsWith('HK') && code.length > 2) {
    keys.add(code.slice(2));
  }
  if (/^\d{5}$/.test(code)) {
    keys.add(`HK${code}`);
    keys.add(`${code}.HK`);
  }
  return Array.from(keys).filter(Boolean);
};

const parseWatchlistValue = (value: string): string[] => {
  const seen = new Set<string>();
  return value
    .split(/[,\n\r\t ]+/)
    .map(normalizeWatchlistCode)
    .filter((code) => {
      if (!code || seen.has(code)) {
        return false;
      }
      seen.add(code);
      return true;
    });
};

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const { setSidebarAction } = useShellSidebarAction();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [reportOverlayOpen, setReportOverlayOpen] = useState(false);
  const [watchlistCodes, setWatchlistCodes] = useState<string[]>([]);
  const [selectedWatchlistCodes, setSelectedWatchlistCodes] = useState<string[]>([]);
  const [isLoadingWatchlist, setIsLoadingWatchlist] = useState(false);
  const [watchlistError, setWatchlistError] = useState<string | null>(null);
  const [reportHistoryStockCode, setReportHistoryStockCode] = useState<string | null>(null);

  const {
    query,
    inputError,
    duplicateError,
    error,
    isAnalyzing,
    historyItems,
    selectedHistoryIds,
    isDeletingHistory,
    isLoadingHistory,
    isLoadingMore,
    hasMore,
    selectedReport,
    isLoadingReport,
    activeTasks,
    markdownDrawerOpen,
    setQuery,
    clearError,
    loadInitialHistory,
    refreshHistory,
    loadMoreHistory,
    selectHistoryItem,
    toggleHistorySelection,
    deleteSelectedHistory,
    submitAnalysis,
    notify,
    setNotify,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
    openMarkdownDrawer,
    closeMarkdownDrawer,
    selectedIds,
  } = useHomeDashboardState();

  useEffect(() => {
    document.title = '每日选股分析 - DSA';
  }, []);
  const reportLanguage = normalizeReportLanguage(selectedReport?.meta.reportLanguage);
  const reportText = getReportText(reportLanguage);

  const loadWatchlist = useCallback(async () => {
    setIsLoadingWatchlist(true);
    setWatchlistError(null);
    try {
      const config = await systemConfigApi.getConfig(false);
      const stockList = config.items.find((item) => item.key === 'STOCK_LIST')?.value ?? '';
      setWatchlistCodes(parseWatchlistValue(stockList));
    } catch (error) {
      setWatchlistError(error instanceof Error ? error.message : '自选股配置加载失败');
    } finally {
      setIsLoadingWatchlist(false);
    }
  }, []);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  const latestHistoryByCode = useMemo(() => {
    const map = new Map<string, typeof historyItems[number]>();
    for (const item of historyItems) {
      for (const key of getWatchlistLookupKeys(item.stockCode)) {
        if (!key || map.has(key)) {
          continue;
        }
        map.set(key, item);
      }
    }
    return map;
  }, [historyItems]);

  const watchlistItems = useMemo<WatchlistItem[]>(() => {
    const sourceCodes = watchlistCodes.length > 0
      ? watchlistCodes
      : historyItems.reduce<string[]>((codes, item) => {
        const code = normalizeWatchlistCode(item.stockCode);
        if (code && !codes.includes(code)) {
          codes.push(code);
        }
        return codes;
      }, []);

    return sourceCodes.map((code) => {
      const history = getWatchlistLookupKeys(code)
        .map((key) => latestHistoryByCode.get(key))
        .find((item) => item !== undefined);
      return {
        stockCode: history?.stockCode || code,
        stockName: history?.stockName,
        recordId: history?.id,
        currentPrice: history?.currentPrice,
        changePct: history?.changePct,
        sentimentScore: history?.sentimentScore,
        operationAdvice: history?.operationAdvice,
        createdAt: history?.createdAt,
        source: watchlistCodes.length > 0 ? 'config' : 'history',
      };
    });
  }, [historyItems, latestHistoryByCode, watchlistCodes]);

  const selectedWatchlistSet = useMemo(
    () => new Set(selectedWatchlistCodes),
    [selectedWatchlistCodes],
  );

  const selectedWatchlistTargets = useMemo(
    () => watchlistItems
      .filter((item) => selectedWatchlistSet.has(item.stockCode))
      .map((item) => ({
        stockCode: item.stockCode,
        stockName: item.stockName,
      })),
    [selectedWatchlistSet, watchlistItems],
  );

  const reanalyzeButtonText = selectedWatchlistTargets.length > 1
    ? `${reportText.reanalyze} (${selectedWatchlistTargets.length})`
    : reportText.reanalyze;

  const reportHistoryFilterCode = reportHistoryStockCode || selectedReport?.meta.stockCode || '';
  const reportHistoryItems = useMemo(() => {
    const filterKeys = new Set(getWatchlistLookupKeys(reportHistoryFilterCode));
    if (filterKeys.size === 0) {
      return [];
    }
    return historyItems.filter((item) => (
      getWatchlistLookupKeys(item.stockCode).some((key) => filterKeys.has(key))
    ));
  }, [historyItems, reportHistoryFilterCode]);

  useDashboardLifecycle({
    loadInitialHistory,
    refreshHistory,
    syncTaskCreated,
    syncTaskUpdated,
    syncTaskFailed,
    removeTask,
  });

  const handleHistoryItemClick = useCallback((recordId: number) => {
    const item = historyItems.find((historyItem) => historyItem.id === recordId);
    if (item) {
      setReportHistoryStockCode(item.stockCode);
    }
    setReportOverlayOpen(true);
    void selectHistoryItem(recordId);
    setSidebarOpen(false);
  }, [historyItems, selectHistoryItem]);

  const handleCloseReportOverlay = useCallback(() => {
    setReportOverlayOpen(false);
    setSidebarOpen(false);
    closeMarkdownDrawer();
  }, [closeMarkdownDrawer]);

  useEffect(() => {
    if (!reportOverlayOpen) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return;
      }
      event.preventDefault();
      handleCloseReportOverlay();
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleCloseReportOverlay, reportOverlayOpen]);

  const handleWatchlistItemOpen = useCallback((item: WatchlistItem) => {
    if (item.recordId === undefined) {
      void submitAnalysis({
        stockCode: item.stockCode,
        stockName: item.stockName,
        originalQuery: item.stockCode,
        selectionSource: 'manual',
      });
      return;
    }

    setReportHistoryStockCode(item.stockCode);
    setReportOverlayOpen(true);
    void selectHistoryItem(item.recordId);
  }, [selectHistoryItem, submitAnalysis]);

  const toggleWatchlistSelection = useCallback((stockCode: string) => {
    setSelectedWatchlistCodes((current) => {
      if (current.includes(stockCode)) {
        return current.filter((code) => code !== stockCode);
      }
      return [...current, stockCode];
    });
  }, []);

  const selectVisibleWatchlist = useCallback((stockCodes: string[]) => {
    setSelectedWatchlistCodes((current) => Array.from(new Set([...current, ...stockCodes])));
  }, []);

  const clearWatchlistSelection = useCallback(() => {
    setSelectedWatchlistCodes([]);
  }, []);

  const handleSubmitAnalysis = useCallback(
    (
      stockCode?: string,
      stockName?: string,
      selectionSource?: 'manual' | 'autocomplete' | 'import' | 'image',
    ) => {
      void submitAnalysis({
        stockCode,
        stockName,
        originalQuery: query,
        selectionSource: selectionSource ?? 'manual',
      });
    },
    [query, submitAnalysis],
  );

  const handleAskFollowUp = useCallback(() => {
    if (selectedReport?.meta.id === undefined) {
      return;
    }

    const code = selectedReport.meta.stockCode;
    const name = selectedReport.meta.stockName;
    const rid = selectedReport.meta.id;
    navigate(`/chat?stock=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}&recordId=${rid}`);
  }, [navigate, selectedReport]);

  const handleReanalyzeWatchlist = useCallback(() => {
    if (selectedWatchlistTargets.length === 0) {
      return;
    }

    selectedWatchlistTargets.forEach((target) => {
      void submitAnalysis({
        stockCode: target.stockCode,
        stockName: target.stockName,
        originalQuery: target.stockCode,
        selectionSource: 'manual',
        forceRefresh: true,
      });
    });
  }, [selectedWatchlistTargets, submitAnalysis]);

  const sidebarProgressTasks = useMemo(
    () => activeTasks.filter((task) => task.status === 'pending' || task.status === 'processing'),
    [activeTasks],
  );

  const sidebarProgressAction = useMemo(() => {
    if (sidebarProgressTasks.length === 0) {
      return null;
    }

    return (
      <div className="space-y-2" data-testid="home-sidebar-progress">
        <div className="flex items-center justify-between gap-1 text-[11px] font-semibold text-foreground">
          <span className="inline-flex min-w-0 items-center gap-1">
            <RefreshCw className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span className="truncate">分析进度</span>
          </span>
          <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
            {sidebarProgressTasks.length}
          </span>
        </div>
        <div className="max-h-[24rem] space-y-2 overflow-y-auto">
          {sidebarProgressTasks.map((task) => {
            const progress = Math.max(0, Math.min(100, task.progress || 0));
            const statusLabel = task.status === 'processing' ? '分析中' : '等待中';
            return (
              <div key={task.taskId} className="rounded-xl border border-subtle bg-surface/70 p-2">
                <div className="truncate text-[11px] font-semibold text-foreground">
                  {task.stockName || task.stockCode}
                </div>
                <div className="mt-0.5 truncate font-mono text-[10px] text-muted-text">
                  {task.stockCode}
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/8">
                  <div
                    className="h-full rounded-full bg-cyan transition-[width] duration-300 ease-out"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <div className="mt-1 flex items-center justify-between gap-1 text-[10px] text-muted-text">
                  <span className="truncate">{statusLabel}</span>
                  <span className="shrink-0 tabular-nums">{progress}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }, [sidebarProgressTasks]);

  useEffect(() => {
    setSidebarAction(sidebarProgressAction);
    return () => setSidebarAction(null);
  }, [setSidebarAction, sidebarProgressAction]);

  const handleToggleReportHistorySelectAll = useCallback(() => {
    const visibleIds = reportHistoryItems.map((item) => item.id);
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
    visibleIds.forEach((id) => {
      if (allVisibleSelected || !selectedIds.has(id)) {
        toggleHistorySelection(id);
      }
    });
  }, [reportHistoryItems, selectedIds, toggleHistorySelection]);

  useEffect(() => {
    if (!reportOverlayOpen || !reportHistoryFilterCode) {
      return;
    }

    const visibleIds = new Set(reportHistoryItems.map((item) => item.id));
    Array.from(selectedIds)
      .filter((id) => !visibleIds.has(id))
      .forEach((id) => toggleHistorySelection(id));
  }, [reportHistoryFilterCode, reportHistoryItems, reportOverlayOpen, selectedIds, toggleHistorySelection]);

  const handleDeleteSelectedHistory = useCallback(() => {
    void deleteSelectedHistory();
    setShowDeleteConfirm(false);
  }, [deleteSelectedHistory]);

  const sidebarContent = useMemo(
    () => (
      <div className="flex min-h-0 h-full flex-col gap-3 overflow-hidden">
        <HistoryList
          items={reportHistoryItems}
          isLoading={isLoadingHistory}
          isLoadingMore={isLoadingMore}
          hasMore={hasMore}
          selectedId={selectedReport?.meta.id}
          selectedIds={selectedIds}
          isDeleting={isDeletingHistory}
          onItemClick={handleHistoryItemClick}
          onLoadMore={() => void loadMoreHistory()}
          onToggleItemSelection={toggleHistorySelection}
          onToggleSelectAll={handleToggleReportHistorySelectAll}
          onDeleteSelected={() => setShowDeleteConfirm(true)}
          className="flex-1 overflow-hidden"
        />
      </div>
    ),
    [
      hasMore,
      isDeletingHistory,
      isLoadingHistory,
      isLoadingMore,
      handleHistoryItemClick,
      handleToggleReportHistorySelectAll,
      loadMoreHistory,
      reportHistoryItems,
      selectedIds,
      selectedReport?.meta.id,
      toggleHistorySelection,
    ],
  );

  return (
    <div
      data-testid="home-dashboard"
      className="flex h-[calc(100vh-5rem)] w-full flex-col overflow-hidden md:flex-row sm:h-[calc(100vh-5.5rem)] lg:h-[calc(100vh-2rem)]"
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col w-full">
        <header className="flex min-w-0 flex-shrink-0 items-center overflow-hidden px-3 py-3 md:px-4 md:py-4">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2.5 md:flex-nowrap">
            <div className="relative min-w-0 flex-1">
              <StockAutocomplete
                value={query}
                onChange={setQuery}
                onSubmit={(stockCode, stockName, selectionSource) => {
                  handleSubmitAnalysis(stockCode, stockName, selectionSource);
                }}
                placeholder="输入股票代码或名称，如 600519、贵州茅台、AAPL"
                disabled={isAnalyzing}
                className={inputError ? 'border-danger/50' : undefined}
              />
            </div>
            <label className="flex h-10 flex-shrink-0 cursor-pointer items-center gap-1.5 rounded-xl border border-subtle bg-surface/60 px-3 text-xs text-secondary-text select-none transition-colors hover:border-subtle-hover hover:text-foreground">
              <input
                type="checkbox"
                checked={notify}
                onChange={(e) => setNotify(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-border accent-primary"
              />
              推送通知
            </label>
            <button
              type="button"
              onClick={() => handleSubmitAnalysis()}
              disabled={!query || isAnalyzing}
              className="btn-primary flex h-10 flex-shrink-0 items-center gap-1.5 whitespace-nowrap"
            >
              {isAnalyzing ? (
                <>
                  <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  分析中
                </>
              ) : (
                '分析'
              )}
            </button>
          </div>
        </header>

        {inputError || duplicateError ? (
          <div className="px-3 pb-2 md:px-4">
            {inputError ? (
              <InlineAlert
                variant="danger"
                title="输入有误"
                message={inputError}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
            {!inputError && duplicateError ? (
              <InlineAlert
                variant="warning"
                title="任务已存在"
                message={duplicateError}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
          </div>
        ) : null}

        <section className="flex-1 min-w-0 min-h-0 overflow-hidden px-3 pb-4 md:px-6 touch-pan-y">
          {error ? (
            <ApiErrorAlert
              error={error}
              className="mb-3"
              onDismiss={clearError}
            />
          ) : null}
          <div className="flex h-full min-h-0 flex-col gap-4">
            <WatchlistBoard
              items={watchlistItems}
              selectedCodes={selectedWatchlistSet}
              isLoading={isLoadingWatchlist || isLoadingHistory}
              loadError={watchlistError}
              reanalyzeLabel={reanalyzeButtonText}
              reanalyzeDisabled={selectedWatchlistTargets.length === 0}
              onRefresh={() => {
                void loadWatchlist();
                void refreshHistory(true);
              }}
              onReanalyzeSelected={handleReanalyzeWatchlist}
              onOpenItem={handleWatchlistItemOpen}
              onToggleSelection={toggleWatchlistSelection}
              onSelectVisible={selectVisibleWatchlist}
              onClearSelection={clearWatchlistSelection}
            />
          </div>
        </section>
      </div>

      {reportOverlayOpen ? (
        <div
          data-testid="report-overlay"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-background/70 p-2 backdrop-blur-sm md:p-5"
        >
          <div className="glass-card flex h-full w-full max-w-7xl flex-col overflow-hidden shadow-2xl">
            <div className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-subtle px-3 py-3 md:px-4">
              <div className="flex min-w-0 items-center gap-2">
                <button
                  onClick={() => setSidebarOpen(true)}
                  className="md:hidden flex-shrink-0 rounded-lg p-1.5 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                  aria-label="历史记录"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                  </svg>
                </button>
                <span className="truncate text-sm font-semibold text-foreground">分析报告</span>
              </div>
              <Button variant="ghost" size="sm" onClick={handleCloseReportOverlay} aria-label="关闭报告浮窗">
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="flex min-h-0 flex-1 overflow-hidden">
              <div className="hidden min-h-0 w-64 shrink-0 flex-col overflow-hidden border-r border-subtle p-3 md:flex lg:w-72">
                {sidebarContent}
              </div>

              {sidebarOpen ? (
                <div className="fixed inset-0 z-[60] md:hidden" onClick={() => setSidebarOpen(false)}>
                  <div className="page-drawer-overlay absolute inset-0" />
                  <div
                    className="dashboard-card absolute bottom-0 left-0 top-0 flex w-72 flex-col overflow-hidden !rounded-none !rounded-r-xl p-3 shadow-2xl"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {sidebarContent}
                  </div>
                </div>
              ) : null}

              <section className="min-h-0 min-w-0 flex-1 overflow-x-auto overflow-y-auto px-3 pb-4 pt-3 md:px-6">
                {isLoadingReport ? (
                  <div className="flex h-full flex-col items-center justify-center">
                    <DashboardStateBlock title="加载报告中..." loading />
                  </div>
                ) : selectedReport ? (
                  <div className="mx-auto max-w-4xl space-y-4 pb-8">
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <Button
                        variant="home-action-ai"
                        size="sm"
                        disabled={selectedReport.meta.id === undefined}
                        onClick={handleAskFollowUp}
                      >
                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                        </svg>
                        追问 AI
                      </Button>
                      <Button
                        variant="home-action-ai"
                        size="sm"
                        disabled={selectedReport.meta.id === undefined}
                        onClick={openMarkdownDrawer}
                      >
                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        {reportText.fullReport}
                      </Button>
                    </div>
                    <ReportSummary data={selectedReport} isHistory />
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center">
                    <EmptyState
                      title="暂无报告"
                      description="选择有历史记录的监控股票查看报告，或先发起一次分析。"
                      className="max-w-xl border-dashed"
                    />
                  </div>
                )}
              </section>
            </div>
          </div>
        </div>
      ) : null}

      {reportOverlayOpen && markdownDrawerOpen && selectedReport?.meta.id ? (
        <ReportMarkdown
          recordId={selectedReport.meta.id}
          stockName={selectedReport.meta.stockName || ''}
          stockCode={selectedReport.meta.stockCode}
          reportLanguage={reportLanguage}
          onClose={closeMarkdownDrawer}
        />
      ) : null}

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="删除历史记录"
        message={
          selectedHistoryIds.length === 1
            ? '确认删除这条历史记录吗？删除后将不可恢复。'
            : `确认删除选中的 ${selectedHistoryIds.length} 条历史记录吗？删除后将不可恢复。`
        }
        confirmText={isDeletingHistory ? '删除中...' : '确认删除'}
        cancelText="取消"
        isDanger={true}
        onConfirm={handleDeleteSelectedHistory}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
};

export default HomePage;
