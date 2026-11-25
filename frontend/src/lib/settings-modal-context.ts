import { createContext, useContext } from "react";

export type SettingsModalContextValue = {
  inSettingsModal: boolean;
  activeCategoryLabel?: string;
};

export const SettingsModalContext = createContext<SettingsModalContextValue>({
  inSettingsModal: false,
});

export function useSettingsModalContext() {
  return useContext(SettingsModalContext);
}
