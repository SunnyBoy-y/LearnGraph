import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { X } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import {
  isSettingsNavActive,
  settingsNav,
  type SettingsNavItem,
} from "@/components/shared/settings-nav-data";

/**
 * Settings secondary surface.
 *
 * Desktop (≥sm): ChatGPT-style centered modal with a left category rail.
 * Mobile (<sm): full-screen page that fills the viewport — closer to native
 * phone settings than a floating card with side margins.
 *
 * Routes stay real (/w/:id/settings/*); this just wraps the routed <Outlet/>
 * so browser history and deep links keep working. Closing navigates back to
 * the pre-settings location.
 */
export function SettingsModal({
  open,
  onClose,
  children,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { workspaceId = "" } = useParams();
  // Radix fires onOpenChange(false) on Esc/overlay click. Guard against a
  // second dismiss while navigation away from /settings/* is already in flight.
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!open) setDismissed(false);
  }, [open]);

  const handleOpenChange = (next: boolean) => {
    if (next) return;
    if (dismissed) return;
    setDismissed(true);
    onClose();
  };

  const activeItem = settingsNav.find((item) =>
    isSettingsNavActive(pathname, item),
  );
  const dialogLabel = activeItem
    ? `设置 · ${activeItem.label}`
    : "设置";

  // Group consecutive items that share a `section` so the sidebar can render
  // section headers; ungrouped items fall under a null title.
  const sections: Array<{ title: string | null; items: SettingsNavItem[] }> =
    [];
  for (const item of settingsNav) {
    const last = sections[sections.length - 1];
    if (last && last.title === (item.section ?? null)) {
      last.items.push(item);
    } else {
      sections.push({ title: item.section ?? null, items: [item] });
    }
  }

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent
        aria-describedby={undefined}
        className={cn(
          "settings-modal grid grid-cols-1 gap-0 overflow-hidden p-0",
          // Mobile: full-screen page — fill the viewport, no card chrome.
          // Override DialogContent's default top-1/2 left-1/2 -translate centering.
          "fixed inset-0 top-0 left-0 h-dvh max-h-dvh w-full max-w-none",
          "translate-x-0 translate-y-0 rounded-none border-0 shadow-none ring-0",
          // ≥sm: ChatGPT-style centered secondary window.
          "sm:inset-auto sm:top-1/2 sm:left-1/2 sm:right-auto sm:bottom-auto",
          "sm:h-[min(860px,86vh)] sm:max-h-[86vh]",
          "sm:w-full sm:max-w-[min(980px,94vw)]",
          "sm:-translate-x-1/2 sm:-translate-y-1/2",
          "sm:grid-cols-[220px_minmax(0,1fr)] sm:rounded-[18px]",
          "sm:border sm:shadow-2xl sm:ring-1 sm:ring-black/10",
        )}
        showCloseButton={false}
      >
        {/* Single always-present title for Radix a11y; visual titles below. */}
        <DialogTitle className="sr-only">{dialogLabel}</DialogTitle>

        <nav
          aria-label="设置导航"
          className="hidden min-h-0 flex-col border-r bg-muted/30 sm:flex"
        >
          <div className="flex h-12 shrink-0 items-center px-4">
            <p className="text-sm font-semibold tracking-tight">设置</p>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="flex flex-col gap-0.5 px-2 pb-3">
              {sections.map((section, sectionIndex) => (
                <li key={section.title ?? sectionIndex}>
                  {section.title ? (
                    <p className="px-2.5 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60 first:pt-2">
                      {section.title}
                    </p>
                  ) : (
                    <div className="pt-2" />
                  )}
                  <ul className="flex flex-col gap-0.5">
                    {section.items.map((item) => {
                      const Icon = item.icon;
                      const active = isSettingsNavActive(pathname, item);
                      return (
                        <li key={item.path}>
                          <button
                            type="button"
                            onClick={() =>
                              navigate(`/w/${workspaceId}/${item.path}`)
                            }
                            className={cn(
                              "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
                              active
                                ? "bg-background font-medium text-foreground shadow-sm ring-1 ring-black/5 dark:ring-white/10"
                                : "text-muted-foreground hover:bg-background/70 hover:text-foreground",
                            )}
                          >
                            <Icon className="size-4 shrink-0" />
                            <span className="truncate">{item.label}</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))}
            </ul>
          </ScrollArea>
        </nav>

        <section className="flex min-h-0 min-w-0 flex-col">
          <header
            className={cn(
              "flex shrink-0 items-center gap-2 border-b bg-background/95 px-3 backdrop-blur-xl",
              // Mobile: room for category select + safe-area for notch.
              "min-h-14 gap-y-1 py-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:h-12 sm:min-h-0 sm:py-0 sm:pt-0",
            )}
          >
            <div className="min-w-0 flex-1 sm:hidden">
              <p className="mb-1 text-[11px] font-medium leading-none text-muted-foreground">
                设置
              </p>
              <select
                aria-label="设置分类"
                className="h-10 w-full rounded-xl border bg-background px-2.5 text-sm outline-none"
                value={activeItem?.path ?? settingsNav[0]?.path}
                onChange={(event) =>
                  navigate(`/w/${workspaceId}/${event.target.value}`)
                }
              >
                {sections.map((section, sectionIndex) => (
                  <optgroup
                    key={section.title ?? sectionIndex}
                    label={section.title ?? "设置"}
                  >
                    {section.items.map((item) => (
                      <option key={item.path} value={item.path}>
                        {item.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
            <p className="hidden min-w-0 flex-1 truncate text-sm font-medium sm:block">
              {activeItem?.label ?? "设置"}
            </p>
            <button
              type="button"
              onClick={() => handleOpenChange(false)}
              className="grid size-11 shrink-0 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:bg-muted sm:size-8 sm:rounded-lg"
              aria-label="关闭设置"
            >
              <X className="size-5 sm:size-4" />
            </button>
          </header>
          <div
            className="settings-modal__body min-h-0 flex-1 overflow-y-auto overscroll-contain pb-[env(safe-area-inset-bottom)]"
            data-settings-modal-body=""
          >
            {children}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
