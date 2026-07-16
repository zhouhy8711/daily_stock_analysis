import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { rulesApi } from '../rules';
import { setStoredTenantKey } from '../../utils/tenantStorage';

function getTenantHeader(config: InternalAxiosRequestConfig): unknown {
  const headers = config.headers;
  if (typeof headers.get === 'function') {
    return headers.get('X-DSA-Tenant');
  }
  return headers['X-DSA-Tenant'];
}

describe('apiClient tenant header', () => {
  const originalAdapter = apiClient.defaults.adapter;

  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter;
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it('does not overwrite an explicit tenant header', async () => {
    setStoredTenantKey('default');
    const adapter = vi.fn<AxiosAdapter>(async (config) => ({
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    } satisfies AxiosResponse));
    apiClient.defaults.adapter = adapter;

    await apiClient.get('/api/v1/rules/runs/202', {
      headers: { 'X-DSA-Tenant': 'quant_team' },
    });

    expect(adapter).toHaveBeenCalledTimes(1);
    expect(getTenantHeader(adapter.mock.calls[0][0])).toBe('quant_team');
  });

  it('keeps tenant context on normalized rule run history rows', async () => {
    const adapter = vi.fn<AxiosAdapter>(async (config) => ({
      data: {
        items: [{
          id: 202,
          rule_id: 7,
          rule_name: '放量观察',
          status: 'completed',
          target_count: 2,
          match_count: 1,
          event_count: 1,
        }],
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    } satisfies AxiosResponse));
    apiClient.defaults.adapter = adapter;

    const runs = await rulesApi.listRuns(30, 'quant_team');

    expect(adapter).toHaveBeenCalledTimes(1);
    expect(getTenantHeader(adapter.mock.calls[0][0])).toBe('quant_team');
    expect(runs[0].tenantRuns).toEqual([{ runId: 202, tenantKey: 'quant_team' }]);
  });
});
