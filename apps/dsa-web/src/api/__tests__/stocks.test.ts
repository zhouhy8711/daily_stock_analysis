import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { stocksApi } from '../stocks';

vi.mock('../index', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

describe('stocksApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses an extended timeout for indicator metrics', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        stock_code: '603667',
        stock_name: '五洲新春',
        chip_distribution: null,
        major_holders: [],
        source_chain: [],
        errors: [],
      },
    });

    await stocksApi.getIndicatorMetrics('603667');

    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/v1/stocks/603667/indicator-metrics',
      { timeout: 75_000 },
    );
  });

  it('passes DB-only indicator metric options when provided', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        stock_code: '603667',
        stock_name: '五洲新春',
        chip_distribution: null,
        major_holders: [],
        source_chain: [],
        errors: [],
      },
    });

    await stocksApi.getIndicatorMetrics('603667', {
      dataPolicy: 'db_only',
      tradeDate: '2026-05-08',
      days: 365,
    });

    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/v1/stocks/603667/indicator-metrics',
      {
        params: {
          data_policy: 'db_only',
          trade_date: '2026-05-08',
          days: 365,
        },
        timeout: 75_000,
      },
    );
  });
});
