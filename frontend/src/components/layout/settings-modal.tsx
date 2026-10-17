import { startTransition, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useBlocker, useLocation, useNavigate, useParams } from "react-router-dom";
import { Check, ChevronDown, FilePenLine, Info, Settings, X } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { SettingsDirtyContext } from "@/lib/settings-dirty";
import "@/features/settings/settings-center.css";
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
  const dirtyForms = useRef(new Set<string>());
  const registerDirty = useCallback((id: string, dirty: boolean) => {
    if (dirty) dirtyForms.current.add(id);
    else dirtyForms.current.delete(id);
  }, []);
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    currentLocation.pathname !== nextLocation.pathname && dirtyForms.current.size > 0,
  );
  const keepEditing = () => {
    if (blocker.state !== "blocked") return;
    setDismissed(false);
    blocker.reset();
  };

  useEffect(() => {
    if (!open) setDismissed(false);
  }, [open]);

  const handleOpenChange = (next: boolean) => {
    if (next) return;
    if (dismissed || blocker.state === "blocked") return;
    setDismissed(true);
    onClose();
  };

  const activeItem = settingsNav.find((item) =>
    isSettingsNavActive(pathname, item),
  );
  const dialogLabel = activeItem
    ? `设置 · ${activeItem.label}`
    : "设置";

  // Route pages are lazy-loaded. Treat category changes as a transition so
  // React keeps the already visible settings page mounted until the next
  // page is ready instead of falling through to the app-wide route loader.
  const navigateToSetting = (path: string) => {
    const target = `/w/${workspaceId}/${path}`;
    if (pathname === target) return;
    startTransition(() => {
      navigate(target);
    });
  };

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
    <SettingsDirtyContext.Provider value={registerDirty}>
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
          "sm:w-full sm:max-w-[min(1260px,94vw)]",
          "sm:-translate-x-1/2 sm:-translate-y-1/2",
          "sm:grid-cols-[256px_minmax(0,1fr)] sm:rounded-[16px]",
          "sm:border sm:shadow-lg sm:ring-1 sm:ring-black/5",
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
              {sections.filter(section => section.title).map((section, sectionIndex) => (
                <li key={section.title ?? sectionIndex} className={!section.title ? "mt-6 border-t pt-2" : undefined}>
                  {section.title ? (
                    <p className="px-2.5 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground first:pt-2">
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
                            aria-current={active ? "page" : undefined}
                            onClick={() => navigateToSetting(item.path)}
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
          <div className="shrink-0 border-t p-2"><button type="button" onClick={() => navigateToSetting('settings/about')} aria-current={pathname.endsWith('/settings/about') ? 'page' : undefined} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-muted-foreground hover:bg-background hover:text-foreground"><Info className="size-4"/>关于</button></div>
        </nav>

        <section className="flex min-h-0 min-w-0 flex-col">
          <header
            className={cn(
              "settings-modal__topbar flex shrink-0 items-center gap-2 border-b bg-background/90 px-3 backdrop-blur-xl",
              // Mobile: polished app-bar with notch-safe spacing and a large touch target.
              "min-h-[4.5rem] py-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:h-12 sm:min-h-0 sm:py-0 sm:pt-0",
            )}
          >
            <div className="flex min-w-0 flex-1 items-center gap-2.5 sm:hidden">
              <div className="grid size-10 shrink-0 place-items-center rounded-2xl bg-foreground text-background shadow-sm">
                <Settings className="size-[18px]" />
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="group flex h-11 min-w-0 flex-1 items-center gap-2 rounded-2xl px-2.5 text-left outline-none transition-colors hover:bg-muted/70 focus-visible:ring-2 focus-visible:ring-ring/40 data-[state=open]:bg-muted"
                    aria-label={`切换设置分类，当前为${activeItem?.label ?? "设置"}`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-[10px] font-semibold uppercase leading-none tracking-[0.16em] text-muted-foreground/70">
                        设置
                      </span>
                      <span className="mt-1 block truncate text-[15px] font-semibold leading-none tracking-tight">
                        {activeItem?.label ?? "选择分类"}
                      </span>
                    </span>
                    <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  sideOffset={8}
                  collisionPadding={12}
                  className="settings-mobile-menu max-h-[min(68dvh,34rem)] w-[calc(100vw-1.5rem)] min-w-0 overflow-y-auto !rounded-[1.25rem] border bg-popover/98 !p-2 !shadow-2xl ring-1 ring-foreground/5 backdrop-blur-xl"
                >
                  <DropdownMenuLabel className="px-2.5 pb-2 pt-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
                    设置分类
                  </DropdownMenuLabel>
                  {sections.map((section, sectionIndex) => (
                    <div key={section.title ?? sectionIndex}>
                      {sectionIndex > 0 ? (
                        <DropdownMenuSeparator className="mx-2 my-2" />
                      ) : null}
                      {section.title ? (
                        <DropdownMenuLabel className="px-2.5 pb-1 pt-1 text-[10px] uppercase tracking-wider text-muted-foreground/60">
                          {section.title}
                        </DropdownMenuLabel>
                      ) : null}
                      {section.items.map((item) => {
                        const Icon = item.icon;
                        const active = isSettingsNavActive(pathname, item);
                        return (
                          <DropdownMenuItem
                            key={item.path}
                            onSelect={() => navigateToSetting(item.path)}
                            className={cn(
                              "mb-0.5 min-h-12 gap-3 rounded-xl px-2.5 py-2.5 text-sm",
                              active &&
                                "bg-foreground text-background focus:bg-foreground focus:text-background [&_svg]:text-background",
                            )}
                          >
                            <span
                              className={cn(
                                "grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground",
                                active && "bg-background/15 text-background",
                              )}
                            >
                              <Icon className="size-4" />
                            </span>
                            <span className="min-w-0 flex-1 truncate font-medium">
                              {item.label}
                            </span>
                            {active ? <Check className="size-4 shrink-0" /> : null}
                          </DropdownMenuItem>
                        );
                      })}
                    </div>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <p className="hidden min-w-0 flex-1 truncate text-sm font-medium sm:block">
              {activeItem?.label ?? "设置"}
            </p>
            <button
              type="button"
              onClick={() => handleOpenChange(false)}
              className="grid size-11 shrink-0 place-items-center rounded-2xl border border-border/70 bg-background/80 text-muted-foreground shadow-sm transition-all hover:bg-muted hover:text-foreground active:scale-95 sm:size-8 sm:rounded-lg sm:border-0 sm:bg-transparent sm:shadow-none"
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
        <AlertDialog
          open={blocker.state === "blocked"}
          onOpenChange={(next) => { if (!next) keepEditing(); }}
        >
          <AlertDialogContent className="w-[calc(100%-2rem)] gap-5 rounded-2xl p-6 shadow-xl sm:max-w-[420px] motion-reduce:animate-none">
            <AlertDialogHeader className="flex flex-col items-start gap-3 text-left">
              <AlertDialogMedia className="mb-1 size-11 rounded-xl bg-primary/8 text-primary">
                <FilePenLine className="size-5" aria-hidden="true" />
              </AlertDialogMedia>
              <AlertDialogTitle className="text-lg font-semibold">
                放弃未保存的修改？
              </AlertDialogTitle>
              <AlertDialogDescription className="leading-relaxed">
                当前页面有尚未保存的修改。离开后，这些修改将丢失。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="m-0 rounded-none border-0 bg-transparent p-0 pt-1">
              <AlertDialogCancel className="h-10 px-4">继续编辑</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                className="h-10 px-4"
                onClick={() => { if (blocker.state === "blocked") blocker.proceed(); }}
              >
                放弃修改并离开
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
    </SettingsDirtyContext.Provider>
  );
}
