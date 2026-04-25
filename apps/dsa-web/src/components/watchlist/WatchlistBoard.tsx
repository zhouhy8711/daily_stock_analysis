import type React from 'react';
import { useMemo, useState } from 'react';
import { CheckSquare, RefreshCw, Search, Square } from 'lucide-react';
import { Badge, Button, EmptyState } from '../common';
import { DashboardStateBlock } from '../dashboard';
import { formatDateTime } from '../../utils/format';

export interface WatchlistItem {
  stockCode: string;
  stockName?: string;
  recordId?: number;
  currentPrice?: number;
  changePct?: number;
  sentimentScore?: number;
  operationAdvice?: string;
  createdAt?: string;
  source: 'config' | 'history';
}

interface WatchlistBoardProps {
  items: WatchlistItem[];
  selectedCodes: Set<string>;
  isLoading: boolean;
  loadError?: string | null;
  reanalyzeLabel: string;
  reanalyzeDisabled: boolean;
  onRefresh: () => void;
  onReanalyzeSelected: () => void;
  onOpenItem: (item: WatchlistItem) => void;
  onToggleSelection: (stockCode: string) => void;
  onSelectVisible: (stockCodes: string[]) => void;
  onClearSelection: () => void;
}

type MarketFilter = 'all' | 'cn' | 'hk' | 'us';

const MARKET_TABS: Array<{ key: MarketFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'cn', label: 'A股' },
  { key: 'hk', label: '港股' },
  { key: 'us', label: '美股' },
];

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
  selectedCodes,
  isLoading,
  loadError,
  reanalyzeLabel,
  reanalyzeDisabled,
  onRefresh,
  onReanalyzeSelected,
  onOpenItem,
  onToggleSelection,
  onSelectVisible,
  onClearSelection,
}) => {
  const [activeMarket, setActiveMarket] = useState<MarketFilter>('all');
  const [selectionMode, setSelectionMode] = useState(false);

  const visibleItems = useMemo(() => (
    activeMarket === 'all'
      ? items
      : items.filter((item) => getMarket(item.stockCode) === activeMarket)
  ), [activeMarket, items]);

  const selectedVisibleCount = visibleItems.filter((item) => selectedCodes.has(item.stockCode)).length;
  const allVisibleSelected = visibleItems.length > 0 && selectedVisibleCount === visibleItems.length;

  const toggleSelectionMode = () => {
    setSelectionMode((current) => !current);
  };

  return (
      <section className="glass-card flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="border-b border-subtle px-4 py-4 md:px-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-4">
                <h1 className="text-2xl font-semibold tracking-normal text-foreground md:text-3xl">自选</h1>
                <span className="text-base text-secondary-text">市场</span>
                <span className="text-base text-secondary-text">资讯</span>
              </div>
              <p className="mt-1 text-xs text-muted-text">
                {items.length > 0 ? `共 ${items.length} 只监控股票` : '从自选股配置或最近历史生成监控列表'}
              </p>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              {selectedCodes.size > 0 ? (
                <Badge variant="info" size="sm" className="px-3">
                  已选 {selectedCodes.size}
                </Badge>
              ) : null}
              <Button
                variant="home-action-ai"
                size="sm"
                onClick={toggleSelectionMode}
                aria-pressed={selectionMode}
              >
                {selectionMode ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4" />}
                {selectionMode ? '完成' : '多选'}
              </Button>
              {selectionMode ? (
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
        </div>

        <div className="grid grid-cols-[minmax(13rem,1.7fr)_minmax(6rem,0.8fr)_minmax(6rem,0.8fr)_minmax(8rem,1fr)] items-center gap-4 border-b border-subtle px-4 py-2 text-xs text-muted-text md:px-6">
          <span>股票</span>
          <span className="text-right">最新价</span>
          <span className="text-right">涨跌幅</span>
          <span className="text-right">最近分析</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {isLoading ? (
            <DashboardStateBlock loading title="加载监控股票中..." className="m-6" />
          ) : loadError ? (
            <EmptyState
              title="监控股票加载失败"
              description={loadError}
              className="m-6"
              icon={<Search className="h-5 w-5" />}
            />
          ) : visibleItems.length === 0 ? (
            <EmptyState
              title="暂无监控股票"
              description="在设置里维护 STOCK_LIST，或先完成一次分析后这里会展示最近关注的股票。"
              className="m-6"
              icon={<Search className="h-5 w-5" />}
            />
          ) : (
            <div className="divide-y divide-subtle">
              {visibleItems.map((item) => {
                const stockName = item.stockName || item.stockCode;
                const selected = selectedCodes.has(item.stockCode);
                const changeClass = typeof item.changePct === 'number' && item.changePct < 0
                  ? 'text-danger'
                  : 'text-success';

                return (
                  <button
                    key={item.stockCode}
                    type="button"
                    aria-label={`查看 ${stockName} 报告`}
                    onClick={() => {
                      if (selectionMode) {
                        onToggleSelection(item.stockCode);
                        return;
                      }
                      onOpenItem(item);
                    }}
                    className={`grid min-h-[5.5rem] w-full grid-cols-[minmax(13rem,1.7fr)_minmax(6rem,0.8fr)_minmax(6rem,0.8fr)_minmax(8rem,1fr)] items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-hover md:px-6 ${
                      selected ? 'bg-primary/5' : ''
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-3">
                      {selectionMode ? (
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => onToggleSelection(item.stockCode)}
                          onClick={(event) => event.stopPropagation()}
                          aria-label={`选择 ${stockName} 监控股票`}
                          className="h-4 w-4 shrink-0 rounded border-subtle-hover bg-transparent accent-primary focus:ring-primary/30"
                        />
                      ) : null}
                      <span className="min-w-0">
                        <span className="block truncate text-lg font-semibold text-foreground">
                          {stockName}
                        </span>
                        <span className="mt-1 flex items-center gap-2 text-sm text-secondary-text">
                          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary">
                            {getMarket(item.stockCode).toUpperCase()}
                          </span>
                          <span className="font-mono">{item.stockCode}</span>
                        </span>
                      </span>
                    </span>

                    <span className={`text-right text-base font-semibold ${changeClass}`}>
                      {formatPrice(item.currentPrice)}
                    </span>
                    <span className={`text-right text-base font-semibold ${changeClass}`}>
                      {formatChangePct(item.changePct)}
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
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-t border-subtle px-4 py-3 text-center text-xs text-muted-text md:px-6">
          点击股票查看报告；进入多选后，自选区“重新分析”会批量分析已选股票。
        </div>
      </section>
  );
};
