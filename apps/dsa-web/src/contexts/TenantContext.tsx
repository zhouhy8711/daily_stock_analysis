import type React from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { tenantsApi } from '../api/tenants';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import type { TenantConfig, TenantItem } from '../types/tenants';
import { getStoredTenantKey, setStoredTenantKey } from '../utils/tenantStorage';

type TenantContextValue = {
  tenants: TenantItem[];
  currentTenant: TenantItem | null;
  tenantConfig: TenantConfig | null;
  isLoading: boolean;
  loadError: ParsedApiError | null;
  switchLocked: boolean;
  setCurrentTenantKey: (tenantKey: string) => void;
  setSwitchLocked: (locked: boolean) => void;
  reloadTenants: () => Promise<void>;
  reloadTenantConfig: () => Promise<TenantConfig | null>;
};

const TenantContext = createContext<TenantContextValue | null>(null);

function pickInitialTenant(tenants: TenantItem[], storedKey: string): TenantItem | null {
  if (!tenants.length) {
    return null;
  }
  return (
    tenants.find((tenant) => tenant.key === storedKey)
    ?? tenants.find((tenant) => tenant.isDefault)
    ?? tenants[0]
  );
}

export function TenantProvider({ children }: { children: React.ReactNode }) {
  const [tenants, setTenants] = useState<TenantItem[]>([]);
  const [currentTenantKey, setCurrentTenantKeyState] = useState(getStoredTenantKey());
  const [tenantConfig, setTenantConfig] = useState<TenantConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);
  const [switchLocked, setSwitchLocked] = useState(false);

  const currentTenant = useMemo(
    () => tenants.find((tenant) => tenant.key === currentTenantKey) ?? null,
    [currentTenantKey, tenants],
  );

  const reloadTenantConfig = useCallback(async (): Promise<TenantConfig | null> => {
    const tenantKey = getStoredTenantKey() || currentTenantKey;
    if (!tenantKey) {
      setTenantConfig(null);
      return null;
    }
    const config = await tenantsApi.getConfig(tenantKey);
    setTenantConfig(config);
    return config;
  }, [currentTenantKey]);

  const reloadTenants = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const items = await tenantsApi.list();
      const selected = pickInitialTenant(items, getStoredTenantKey() || currentTenantKey);
      setTenants(items);
      if (selected) {
        setStoredTenantKey(selected.key);
        setCurrentTenantKeyState(selected.key);
      }
    } catch (error: unknown) {
      setLoadError(getParsedApiError(error));
      setTenants([]);
      setTenantConfig(null);
    } finally {
      setIsLoading(false);
    }
  }, [currentTenantKey]);

  useEffect(() => {
    void reloadTenants();
  }, [reloadTenants]);

  useEffect(() => {
    if (!currentTenantKey) {
      setTenantConfig(null);
      return;
    }
    let cancelled = false;
    tenantsApi.getConfig(currentTenantKey)
      .then((config) => {
        if (!cancelled) {
          setTenantConfig(config);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(getParsedApiError(error));
          setTenantConfig(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentTenantKey]);

  const setCurrentTenantKey = useCallback((tenantKey: string) => {
    if (switchLocked) {
      return;
    }
    setStoredTenantKey(tenantKey);
    setCurrentTenantKeyState(tenantKey);
  }, [switchLocked]);

  const value = useMemo<TenantContextValue>(() => ({
    tenants,
    currentTenant,
    tenantConfig,
    isLoading,
    loadError,
    switchLocked,
    setCurrentTenantKey,
    setSwitchLocked,
    reloadTenants,
    reloadTenantConfig,
  }), [
    tenants,
    currentTenant,
    tenantConfig,
    isLoading,
    loadError,
    switchLocked,
    setCurrentTenantKey,
    setSwitchLocked,
    reloadTenants,
    reloadTenantConfig,
  ]);

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- hook lives with its provider
export function useTenant(): TenantContextValue {
  const ctx = useContext(TenantContext);
  if (!ctx) {
    throw new Error('useTenant must be used within TenantProvider');
  }
  return ctx;
}
