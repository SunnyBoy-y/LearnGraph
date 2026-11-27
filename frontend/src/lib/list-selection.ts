import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

export function applyRangeSelection(
  current: ReadonlySet<string>,
  orderedIds: readonly string[],
  anchorId: string | null,
  targetId: string,
  nextChecked: boolean,
): Set<string> {
  const next = new Set(current);
  const targetIndex = orderedIds.indexOf(targetId);
  const anchorIndex = anchorId ? orderedIds.indexOf(anchorId) : -1;
  if (anchorIndex >= 0 && targetIndex >= 0) {
    const start = Math.min(anchorIndex, targetIndex);
    const end = Math.max(anchorIndex, targetIndex);
    for (let index = start; index <= end; index += 1) {
      if (nextChecked) next.add(orderedIds[index]);
      else next.delete(orderedIds[index]);
    }
    return next;
  }
  if (nextChecked) next.add(targetId);
  else next.delete(targetId);
  return next;
}

export function useEscapeKey(
  enabled: boolean,
  onEscape: () => void,
  options?: { allowInsideDialog?: boolean },
) {
  const allowInsideDialog = options?.allowInsideDialog ?? false;
  useEffect(() => {
    if (!enabled) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (
        !allowInsideDialog &&
        document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]')
      ) {
        return;
      }
      const active = document.activeElement;
      if (
        active instanceof HTMLElement &&
        (active.tagName === "INPUT" ||
          active.tagName === "TEXTAREA" ||
          active.tagName === "SELECT" ||
          active.isContentEditable)
      ) {
        return;
      }
      onEscape();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [allowInsideDialog, enabled, onEscape]);
}

export interface UseListSelectionOptions {
  initialMultiSelect?: boolean;
  alwaysActive?: boolean;
  allowEscapeInsideDialog?: boolean;
  maxSelection?: number;
  onMaxExceeded?: (max: number) => void;
}

export function useListSelection(
  orderedVisibleIds: readonly string[],
  options: UseListSelectionOptions = {},
) {
  const {
    initialMultiSelect = false,
    alwaysActive = false,
    allowEscapeInsideDialog = false,
    maxSelection,
    onMaxExceeded,
  } = options;

  const [multiSelect, setMultiSelect] = useState(initialMultiSelect || alwaysActive);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const anchorRef = useRef<string | null>(null);
  const shiftRef = useRef(false);

  const clampSelection = useCallback(
    (candidate: Set<string>, fallback: ReadonlySet<string>) => {
      if (maxSelection != null && candidate.size > maxSelection) {
        onMaxExceeded?.(maxSelection);
        return new Set(fallback);
      }
      return candidate;
    },
    [maxSelection, onMaxExceeded],
  );

  const toggleItem = useCallback(
    (id: string, opts?: { checked?: boolean; shiftKey?: boolean }) => {
      const useShift = opts?.shiftKey ?? shiftRef.current;
      const anchorId = anchorRef.current;
      shiftRef.current = false;
      setSelectedIds((current) => {
        const nextChecked = opts?.checked ?? !current.has(id);
        const candidate = useShift
          ? applyRangeSelection(current, orderedVisibleIds, anchorId, id, nextChecked)
          : (() => {
              const next = new Set(current);
              if (nextChecked) next.add(id);
              else next.delete(id);
              return next;
            })();
        return clampSelection(candidate, current);
      });
      anchorRef.current = id;
    },
    [clampSelection, orderedVisibleIds],
  );

  const toggleVisible = useCallback(
    (checked?: boolean) => {
      setSelectedIds((current) => {
        const allSelected =
          orderedVisibleIds.length > 0 && orderedVisibleIds.every((id) => current.has(id));
        const nextChecked = checked ?? !allSelected;
        const candidate = new Set(current);
        for (const id of orderedVisibleIds) {
          if (nextChecked) candidate.add(id);
          else candidate.delete(id);
        }
        return clampSelection(candidate, current);
      });
    },
    [clampSelection, orderedVisibleIds],
  );

  const selectAll = useCallback(
    (allIds: readonly string[]) => {
      setSelectedIds((current) => {
        const candidate = new Set(allIds);
        return clampSelection(candidate, current);
      });
    },
    [clampSelection],
  );

  const clear = useCallback(() => {
    setSelectedIds(new Set());
    anchorRef.current = null;
  }, []);

  const enterMultiSelect = useCallback((initialId?: string) => {
    setMultiSelect(true);
    if (initialId) {
      setSelectedIds(new Set([initialId]));
      anchorRef.current = initialId;
    }
  }, []);

  const exitMultiSelect = useCallback(() => {
    if (!alwaysActive) setMultiSelect(false);
    setSelectedIds(new Set());
    anchorRef.current = null;
  }, [alwaysActive]);

  const toggleMultiSelect = useCallback(() => {
    setMultiSelect((current) => {
      if (current && !alwaysActive) {
        setSelectedIds(new Set());
        anchorRef.current = null;
        return false;
      }
      return true;
    });
  }, [alwaysActive]);

  const removeIds = useCallback((ids: readonly string[]) => {
    if (!ids.length) return;
    const drop = new Set(ids);
    setSelectedIds((current) => {
      let changed = false;
      const next = new Set<string>();
      for (const id of current) {
        if (drop.has(id)) changed = true;
        else next.add(id);
      }
      return changed ? next : current;
    });
  }, []);

  const prune = useCallback((validIds: ReadonlySet<string>) => {
    setSelectedIds((current) => {
      let changed = false;
      const next = new Set<string>();
      for (const id of current) {
        if (validIds.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : current;
    });
  }, []);

  const escapeActive = alwaysActive ? selectedIds.size > 0 : multiSelect;
  useEscapeKey(escapeActive, exitMultiSelect, { allowInsideDialog: allowEscapeInsideDialog });

  const recordShift = useCallback((event: ReactMouseEvent | { shiftKey: boolean }) => {
    shiftRef.current = Boolean(event.shiftKey);
  }, []);

  const checkboxProps = useCallback(
    (id: string) => ({
      checked: selectedIds.has(id),
      onClickCapture: (event: ReactMouseEvent) => {
        shiftRef.current = event.shiftKey;
      },
      onCheckedChange: (value: boolean | "indeterminate") => {
        toggleItem(id, { checked: value === true });
      },
    }),
    [selectedIds, toggleItem],
  );

  const stats = useMemo(() => {
    let selectedVisibleCount = 0;
    for (const id of orderedVisibleIds) {
      if (selectedIds.has(id)) selectedVisibleCount += 1;
    }
    const selectedCount = selectedIds.size;
    const otherSelectedCount = Math.max(0, selectedCount - selectedVisibleCount);
    const allVisibleSelected =
      orderedVisibleIds.length > 0 && selectedVisibleCount === orderedVisibleIds.length;
    const someVisibleSelected = selectedVisibleCount > 0 && !allVisibleSelected;
    const visibleCheckboxState: boolean | "indeterminate" = allVisibleSelected
      ? true
      : someVisibleSelected
        ? "indeterminate"
        : false;
    return {
      selectedCount,
      selectedVisibleCount,
      otherSelectedCount,
      allVisibleSelected,
      someVisibleSelected,
      visibleCheckboxState,
    };
  }, [orderedVisibleIds, selectedIds]);

  return {
    multiSelect,
    setMultiSelect,
    selectedIds,
    setSelectedIds,
    selectedList: useMemo(() => Array.from(selectedIds), [selectedIds]),
    ...stats,
    toggleItem,
    toggleVisible,
    selectAll,
    clear,
    enterMultiSelect,
    exitMultiSelect,
    toggleMultiSelect,
    removeIds,
    prune,
    recordShift,
    checkboxProps,
  };
}
