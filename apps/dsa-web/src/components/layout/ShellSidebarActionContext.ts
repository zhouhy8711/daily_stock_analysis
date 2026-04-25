import { createContext, useContext } from 'react';
import type React from 'react';

type ShellSidebarActionContextValue = {
  setSidebarAction: (action: React.ReactNode | null) => void;
};

export const ShellSidebarActionContext = createContext<ShellSidebarActionContextValue>({
  setSidebarAction: () => undefined,
});

export const ShellSidebarActionProvider = ShellSidebarActionContext.Provider;

export function useShellSidebarAction() {
  return useContext(ShellSidebarActionContext);
}
