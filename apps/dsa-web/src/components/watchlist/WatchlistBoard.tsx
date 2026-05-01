import type React from 'react';
import { useMemo, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BarChart3,
  Check,
  CheckSquare,
  Plus,
  RefreshCw,
  Search,
  Square,
} from 'lucide-react';
import { Badge, Button, EmptyState } from '../common';
import { DashboardStateBlock } from '../dashboard';
import { formatDateTime } from '../../utils/format';

export interface WatchlistItem {
  stockCode: string;
  watchlistCode?: string;
  stockName?: string;
  recordId?: number;
  currentPrice?: number;
  changePct?: number;
  sentimentScore?: number;
  operationAdvice?: string;
  createdAt?: string;
  source: 'config' | 'history' | 'index';
  isInWatchlist?: boolean;
}

interface WatchlistBoardProps {
  items: WatchlistItem[];
  allShareItems: WatchlistItem[];
  allShareTotal: number;
  allShareQuery: string;
  selectedCodes: Set<string>;
  isLoading: boolean;
  isLoadingAllShares: boolean;
  isLoadingAllShareQuotes: boolean;
  loadError?: string | null;
  allShareError?: string | null;
  addingWatchlistCode?: string | null;
  reanalyzeLabel: string;
  reanalyzeDisabled: boolean;
  onRefresh: () => void;
  onReanalyzeSelected: () => void;
  onOpenItem: (item: WatchlistItem) => void;
  onOpenIndicatorAnalysis: (item: WatchlistItem) => void;
  onAddToWatchlist: (item: WatchlistItem) => void;
  onShowAllShares: () => void;
  onToggleSelection: (stockCode: string) => void;
  onSelectVisible: (stockCodes: string[]) => void;
  onClearSelection: () => void;
}

type BoardTab = 'watchlist' | 'all-cn';
type MarketFilter = 'all' | 'cn' | 'hk' | 'us';
type SortField = 'currentPrice' | 'changePct';
type SortDirection = 'asc' | 'desc';
type SortState = { field: SortField; direction: SortDirection } | null;

const BOARD_TABS: Array<{ key: BoardTab; label: string }> = [
  { key: 'watchlist', label: '自选' },
  { key: 'all-cn', label: 'A股所有' },
];

const MARKET_TABS: Array<{ key: MarketFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'cn', label: 'A股' },
  { key: 'hk', label: '港股' },
  { key: 'us', label: '美股' },
];

const SORT_LABELS: Record<SortField, string> = {
  currentPrice: '最新价',
  changePct: '涨跌幅',
};

function getMarket(stockCode: string): MarketFilter {
  const code = stockCode.trim().toUpperCase();
  if (code.includes('.HK') || code.startsWith('HK') || /^\d{5}$/.test(code)) {
    return 'hk';
  }
  if (code.includes('.SH') || code.includes('.SZ') || /^\d{6}$/.test(code)) {
    return 'cn';
  }
  return 'us';
}

function formatPrice(value?: number): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return value >= 100 ? value.toFixed(2) : value.toFixed(3);
}

function formatChangePct(value?: number): string {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '--';
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function getSortableNumber(item: WatchlistItem, field: SortField): number | null {
  const value = item[field];
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return null;
  }
  return value;
}

function compareSortedItem(left: WatchlistItem, right: WatchlistItem, sortState: SortState): number {
  if (!sortState) {
    return 0;
  }

  const leftValue = getSortableNumber(left, sortState.field);
  const rightValue = getSortableNumber(right, sortState.field);
  if (leftValue === null && rightValue === null) {
    return 0;
  }
  if (leftValue === null) {
    return 1;
  }
  if (rightValue === null) {
    return -1;
  }

  const directionMultiplier = sortState.direction === 'asc' ? 1 : -1;
  return (leftValue - rightValue) * directionMultiplier;
}

function getAdviceVariant(advice?: string): 'success' | 'warning' | 'danger' | 'info' {
  const normalized = advice?.trim() ?? '';
  if (normalized.includes('卖') || normalized.includes('减')) {
    return 'danger';
  }
  if (normalized.includes('买')) {
    return 'success';
  }
  if (normalized.includes('观望') || normalized.includes('等待')) {
    return 'warning';
  }
  return 'info';
}

export const WatchlistBoard: React.FC<WatchlistBoardProps> = ({
  items,
  allShareItems,
  allShareTotal,
  allShareQuery,
  selectedCodes,
  isLoading,
  isLoadingAllShares,
  isLoadingAllShareQuotes,
  loadError,
  allShareError,
  addingWatchlistCode,
  reanalyzeLabel,
  reanalyzeDisabled,
  onRefresh,
  onReanalyzeSelected,
  onOpenItem,
  onOpenIndicatorAnalysis,
  onAddToWatchlist,
  onShowAllShares,
  onToggleSelection,
  onSelectVisible,
  onClearSelection,
}) => {
  const [activeBoardTab, setActiveBoardTab] = useState<BoardTab>('watchlist');
  const [activeMarket, setActiveMarket] = useState<MarketFilter>('all');
  const [selectionMode, setSelectionMode] = useState(false);
  const [sortByTab, setSortByTab] = useState<Record<BoardTab, SortState>>({
    watchlist: null,
    'all-cn': null,
  });
  const isAllShareView = activeBoardTab === 'all-cn';
  const activeSort = sortByTab[activeBoardTab];

  const filteredItems = useMemo(() => {
    if (isAllShareView) {
      return allShareItems;
    }

    return activeMarket === 'all'
      ? items
      : items.filter((item) => getMarket(item.stockCode) === activeMarket);
  }, [activeMarket, allShareItems, isAllShareView, items]);

  const visibleItems = useMemo(() => {
    if (!activeSort) {
      return filteredItems;
    }

    return filteredItems
      .map((item, index) => ({ item, index }))
      .sort((left, right) => (
        compareSortedItem(left.item, right.item, activeSort) || left.index - right.index
      ))
      .map(({ item }) => item);
  }, [activeSort, filteredItems]);

  const selectedVisibleCount = visibleItems.filter((item) => selectedCodes.has(item.stockCode)).length;
  const allVisibleSelected = !isAllShareView && visibleItems.length > 0 && selectedVisibleCount === visibleItems.length;
  const activeLoading = isAllShareView ? isLoadingAllShares : isLoading;
  const activeError = isAllShareView ? allShareError : loadError;
  const normalizedAllShareQuery = allShareQuery.trim();
  const gridTemplateClass = 'grid-cols-[minmax(12rem,1.6fr)_minmax(5.5rem,0.7fr)_minmax(5.5rem,0.7fr)_minmax(6rem,0.7fr)_minmax(8rem,1fr)]';
  const subtitle = isAllShareView
    ? normalizedAllShareQuery
      ? `匹配 ${visibleItems.length} / 共 ${allShareTotal} 只A股股票`
      : `共 ${allShareTotal} 只A股股票${isLoadingAllShareQuotes ? '，行情加载中' : ''}`
    : items.length > 0
      ? `共 ${items.length} 只监控股票`
      : '从自选股配置或最近历史生成监控列表';

  const toggleSelectionMode = () => {
    setSelectionMode((current) => !current);
  };

  const toggleSort = (field: SortField) => {
    setSortByTab((current) => {
      const tabSort = current[activeBoardTab];
      const nextSort: SortState = tabSort?.field === field
        ? { field, direction: tabSort.direction === 'desc' ? 'asc' : 'desc' }
        : { field, direction: 'desc' };
      return {
        ...current,
        [activeBoardTab]: nextSort,
      };
    });
  };

  const renderSortHeader = (field: SortField) => {
    const active = activeSort?.field === field;
    const direction = active ? activeSort.direction : undefined;
    const Icon = direction === 'asc'
      ? ArrowUp
      : direction === 'desc'
        ? ArrowDown
        : ArrowUpDown;
    const label = SORT_LABELS[field];

    return (
      <button
        type="button"
        className={`inline-flex min-w-0 items-center justify-end gap-1 rounded-md px-1 py-1 text-right transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
          active ? 'text-primary' : 'text-muted-text hover:text-foreground'
        }`}
        aria-label={`${label}${direction === 'asc' ? '升序' : direction === 'desc' ? '降序' : ''}排序`}
        aria-pressed={active}
        onClick={() => toggleSort(field)}
      >
        <span>{label}</span>
        <Icon className="h-3.5 w-3.5 shrink-0" />
      </button>
    );
  };

  return (
      <section className="glass-card flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="border-b border-subtle px-4 py-4 md:px-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-4">
                {BOARD_TABS.map((tab) => {
                  const active = activeBoardTab === tab.key;
                  return (
                    <button
                      key={tab.key}
                      type="button"
                      aria-pressed={active}
                      onClick={() => {
                        setActiveBoardTab(tab.key);
                        if (tab.key !== 'watchlist') {
                          setSelectionMode(false);
                          onShowAllShares();
                        }
                      }}
                      className={`rounded-lg text-left font-semibold tracking-normal transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
                        active
                          ? 'text-2xl text-foreground md:text-3xl'
                          : 'text-base text-secondary-text hover:text-foreground'
                      }`}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1 text-xs text-muted-text">
                {subtitle}
              </p>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              {!isAllShareView && selectedCodes.size > 0 ? (
                <Badge variant="info" size="sm" className="px-3">
                  已选 {selectedCodes.size}
                </Badge>
              ) : null}
              {!isAllShareView ? (
                <Button
                  variant="home-action-ai"
                  size="sm"
                  onClick={toggleSelectionMode}
                  aria-pressed={selectionMode}
                >
                  {selectionMode ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4" />}
                  {selectionMode ? '完成' : '多选'}
                </Button>
              ) : null}
              {!isAllShareView && selectionMode ? (
                <Button
                  variant="home-action-ai"
                  size="sm"
                  disabled={visibleItems.length === 0}
                  onClick={() => {
                    if (allVisibleSelected) {
                      onClearSelection();
                    } else {
                      onSelectVisible(visibleItems.map((item) => item.stockCode));
                    }
                  }}
                >
                  {allVisibleSelected ? '清空' : '全选当前'}
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" onClick={onRefresh} aria-label="刷新监控股票">
                <RefreshCw className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {!isAllShareView ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 overflow-x-auto">
                {MARKET_TABS.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    onClick={() => setActiveMarket(tab.key)}
                    className={`relative h-9 shrink-0 px-4 text-sm font-medium transition-colors ${
                      activeMarket === tab.key ? 'text-foreground' : 'text-secondary-text hover:text-foreground'
                    }`}
                  >
                    {tab.label}
                    {activeMarket === tab.key ? (
                      <span className="absolute bottom-0 left-1/2 h-0.5 w-7 -translate-x-1/2 rounded-full bg-danger" />
                    ) : null}
                  </button>
                ))}
              </div>
              <Button
                variant="home-action-ai"
                size="sm"
                disabled={reanalyzeDisabled}
                onClick={onReanalyzeSelected}
                className="min-w-[8.5rem] justify-center"
              >
                <RefreshCw className="h-4 w-4" />
                {reanalyzeLabel}
              </Button>
            </div>
          ) : null}
        </div>

        <div className={`grid ${gridTemplateClass} items-center gap-4 border-b border-subtle px-4 py-2 text-xs text-muted-text md:px-6`}>
          <span>股票</span>
          <span className="flex justify-end">{renderSortHeader('currentPrice')}</span>
          <span className="flex justify-end">{renderSortHeader('changePct')}</span>
          <span className="text-right">指标分析</span>
          <span className="text-right">最近分析</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {activeLoading ? (
            <DashboardStateBlock loading title={isAllShareView ? '加载A股股票中...' : '加载监控股票中...'} className="m-6" />
          ) : activeError ? (
            <EmptyState
              title={isAllShareView ? 'A股股票加载失败' : '监控股票加载失败'}
              description={activeError}
              className="m-6"
              icon={<Search className="h-5 w-5" />}
            />
          ) : visibleItems.length === 0 ? (
            <EmptyState
              title={isAllShareView ? '暂无匹配的A股股票' : '暂无监控股票'}
              description={
                isAllShareView
                  ? '换一个股票代码或名称试试。'
                  : '在设置里维护 STOCK_LIST，或先完成一次分析后这里会展示最近关注的股票。'
              }
              className="m-6"
              icon={<Search className="h-5 w-5" />}
            />
          ) : (
            <div className="divide-y divide-subtle">
              {visibleItems.map((item) => {
                const stockName = item.stockName || item.stockCode;
                const selected = !isAllShareView && selectedCodes.has(item.stockCode);
                const watchlistCode = item.watchlistCode || item.stockCode;
                const addingCurrent = addingWatchlistCode === watchlistCode;
                const changeClass = typeof item.changePct === 'number' && item.changePct < 0
                  ? 'text-danger'
                  : 'text-success';
                const openRow = () => {
                  if (!isAllShareView && selectionMode) {
                    onToggleSelection(item.stockCode);
                    return;
                  }
                  onOpenItem(item);
                };

                return (
                  <div
                    key={item.stockCode}
                    className={`grid min-h-[5.5rem] w-full ${gridTemplateClass} items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-hover md:px-6 ${
                      selected ? 'bg-primary/5' : ''
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-3">
                      {!isAllShareView && selectionMode ? (
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => onToggleSelection(item.stockCode)}
                          onClick={(event) => event.stopPropagation()}
                          aria-label={`选择 ${stockName} 监控股票`}
                          className="h-4 w-4 shrink-0 rounded border-subtle-hover bg-transparent accent-primary focus:ring-primary/30"
                        />
                      ) : null}
                      <button
                        type="button"
                        aria-label={`查看 ${stockName} 报告`}
                        onClick={openRow}
                        className="min-w-0 rounded-lg text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                      >
                        <span className="block truncate text-lg font-semibold text-foreground">
                          {stockName}
                        </span>
                        <span className="mt-1 flex items-center gap-2 text-sm text-secondary-text">
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary">
                            {getMarket(item.stockCode).toUpperCase()}
                          </span>
                          <span className="font-mono">{item.stockCode}</span>
                        </span>
                      </button>
                      {isAllShareView ? (
                        <Button
                          variant="home-action-ai"
                          size="sm"
                          className="h-8 w-8 shrink-0 p-0"
                          aria-label={item.isInWatchlist ? `${stockName} 已在自选` : `添加 ${stockName} 到自选`}
                          title={item.isInWatchlist ? '已在自选' : '添加到自选'}
                          disabled={item.isInWatchlist || addingCurrent}
                          isLoading={addingCurrent}
                          loadingText=""
                          onClick={(event) => {
                            event.stopPropagation();
                            onAddToWatchlist(item);
                          }}
                          onKeyDown={(event) => event.stopPropagation()}
                        >
                          {item.isInWatchlist ? <Check className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
                        </Button>
                      ) : null}
                    </span>

                    <span className={`text-right text-base font-semibold ${changeClass}`}>
                      {formatPrice(item.currentPrice)}
                    </span>
                    <span className={`text-right text-base font-semibold ${changeClass}`}>
                      {formatChangePct(item.changePct)}
                    </span>
                    <span className="flex justify-end">
                      <Button
                        variant="home-action-ai"
                        size="sm"
                        className="h-9 justify-center px-3"
                        aria-label={`查看 ${stockName} 指标分析`}
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenIndicatorAnalysis(item);
                        }}
                        onKeyDown={(event) => event.stopPropagation()}
                      >
                        <BarChart3 className="h-4 w-4" />
                        <span className="hidden xl:inline">指标</span>
                      </Button>
                    </span>
                    <span className="flex min-w-0 flex-col items-end gap-1">
                      <Badge variant={getAdviceVariant(item.operationAdvice)} size="sm" className="shadow-none">
                        {item.operationAdvice || '待分析'}
                        {typeof item.sentimentScore === 'number' ? ` ${item.sentimentScore}` : ''}
                      </Badge>
                      <span className="text-xs text-muted-text">
                        {item.createdAt ? formatDateTime(item.createdAt) : '暂无报告'}
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-t border-subtle px-4 py-3 text-center text-xs text-muted-text md:px-6">
          {isAllShareView
            ? '点击股票查看报告；未分析过的股票会直接发起分析；加号可添加到自选。'
            : '点击股票查看报告；指标分析可直接查看日 K 与实时行情指标；进入多选后，自选区“重新分析”会批量分析已选股票。'}
        </div>
      </section>
  );
};
