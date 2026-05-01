import type React from 'react';
import { useEffect, useMemo } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { EmptyState } from '../components/common';
import { IndicatorAnalysisView } from '../components/report';

function parseSearchNumber(value: string | null): number | undefined {
  if (!value) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

const IndicatorAnalysisPage: React.FC = () => {
  const navigate = useNavigate();
  const params = useParams<{ stockCode: string }>();
  const [searchParams] = useSearchParams();
  const stockCode = params.stockCode ? decodeURIComponent(params.stockCode) : '';
  const stockName = searchParams.get('name') || stockCode;
  const reportCurrentPrice = useMemo(
    () => parseSearchNumber(searchParams.get('price')),
    [searchParams],
  );
  const reportChangePct = useMemo(
    () => parseSearchNumber(searchParams.get('changePct')),
    [searchParams],
  );

  useEffect(() => {
    document.title = stockCode ? `${stockName} 指标分析 - DSA` : '指标分析 - DSA';
  }, [stockCode, stockName]);

  if (!stockCode) {
    return (
      <div className="flex h-[calc(100vh-5rem)] items-center justify-center px-4 md:h-[calc(100vh-2rem)]">
        <EmptyState
          title="缺少股票代码"
          description="请从自选列表进入指标分析页。"
          className="max-w-xl border-dashed"
        />
      </div>
    );
  }

  return (
    <IndicatorAnalysisView
      stockCode={stockCode}
      stockName={stockName}
      reportCurrentPrice={reportCurrentPrice}
      reportChangePct={reportChangePct}
      onClose={() => navigate(-1)}
      variant="page"
    />
  );
};

export default IndicatorAnalysisPage;
