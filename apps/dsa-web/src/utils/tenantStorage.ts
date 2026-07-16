const TENANT_STORAGE_KEY = 'dsa.currentTenantKey';

export function getStoredTenantKey(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  return window.localStorage.getItem(TENANT_STORAGE_KEY) || '';
}

export function setStoredTenantKey(value: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  const key = value.trim();
  if (key) {
    window.localStorage.setItem(TENANT_STORAGE_KEY, key);
  } else {
    window.localStorage.removeItem(TENANT_STORAGE_KEY);
  }
}
