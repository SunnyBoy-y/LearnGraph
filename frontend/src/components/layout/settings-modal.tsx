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
} from "@/components/shared/settings-nav-data";

/**
 * ChatGPT-style secondary window for the settings area.
 *
 * The settings pages remain real routes (/w/:id/settings/*); this modal just
 * wraps the routed <Outlet/> so browser back/forward and deep links keep
 * working. The left column lists every settings category, the right column
 * shows the active page (scrollable), and closing the modal navigates back to
 * where the user came from.
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

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent
        aria-describedby={undefined}
        className={cn(
          "settings-modal grid h-[min(860px,86vh)] max-h-[86vh] w-full",
          "max-w-[calc(100vw-2rem)] grid-cols-1 gap-0 overflow-hidden p-0",
          "sm:max-w-[min(980px,94vw)] sm:grid-cols-[220px_minmax(0,1fr)]",
        )}
        showCloseButton={false}
      >
        <nav
          aria-label="设置导航"
          className="hidden min-h-0 flex-col border-r bg-muted/30 sm:flex"
        >
          <div className="flex h-12 shrink-0 items-center px-4">
            <DialogTitle className="text-sm font-semibold tracking-tight">
              设置
            </DialogTitle>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="flex flex-col gap-0.5 px-2 pb-3">
              {settingsNav.map((item) => {
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
          </ScrollArea>
        </nav>

        <section className="flex min-h-0 min-w-0 flex-col">
          <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
            <div className="min-w-0 flex-1 sm:hidden">
              <select
                aria-label="设置分类"
                className="h-9 w-full rounded-lg border bg-background px-2.5 text-sm outline-none"
                value={activeItem?.path ?? settingsNav[0]?.path}
                onChange={(event) =>
                  navigate(`/w/${workspaceId}/${event.target.value}`)
                }
              >
                {settingsNav.map((item) => (
                  <option key={item.path} value={item.path}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <p className="hidden min-w-0 flex-1 truncate text-sm font-medium sm:block">
              {activeItem?.label ?? "设置"}
            </p>
            <button
              type="button"
              onClick={() => handleOpenChange(false)}
              className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="关闭设置"
            >
              <X className="size-4" />
            </button>
          </header>
          <div
            className="settings-modal__body min-h-0 flex-1 overflow-y-auto"
            data-settings-modal-body=""
          >
            {children}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
