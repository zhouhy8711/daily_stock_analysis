import axios from 'axios';
import { API_BASE_URL } from '../utils/constants';
import { getStoredTenantKey } from '../utils/tenantStorage';
import { attachParsedApiError } from './error';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use((config) => {
  const tenantKey = getStoredTenantKey();
  if (tenantKey) {
    config.headers = config.headers ?? {};
    const existingTenantHeader = typeof config.headers.get === 'function'
      ? config.headers.get('X-DSA-Tenant')
      : config.headers['X-DSA-Tenant'] ?? config.headers['x-dsa-tenant'];
    if (!existingTenantHeader) {
      if (typeof config.headers.set === 'function') {
        config.headers.set('X-DSA-Tenant', tenantKey);
      } else {
        config.headers['X-DSA-Tenant'] = tenantKey;
      }
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const path = window.location.pathname + window.location.search;
      if (!path.startsWith('/login')) {
        const redirect = encodeURIComponent(path);
        window.location.assign(`/login?redirect=${redirect}`);
      }
    }
    attachParsedApiError(error);
    return Promise.reject(error);
  }
);

export default apiClient;
