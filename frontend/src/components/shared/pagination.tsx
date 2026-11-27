import { useId, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Pagination({ page, pageSize, total, onPageChange, onPageSizeChange, sizes = [12, 24, 48] }: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  sizes?: readonly number[];
}) {
  const last = Math.max(1, Math.ceil(total / pageSize));
  const start = Math.max(1, Math.min(page - 2, last - 4));
  const pages = [...new Set([1, ...Array.from({ length: Math.min(5, last) }, (_, i) => start + i), last])].sort((a, b) => a - b);
  const [jump, setJump] = useState("");
  const [invalid, setInvalid] = useState(false);
  const errorId = useId();
  const changePage = (next: number) => {
    setJump("");
    setInvalid(false);
    if (next !== page) onPageChange(next);
  };
  return (
    <nav aria-label="分页" className="@container min-w-0 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <p aria-live="polite" className="tabular-nums text-muted-foreground">
          第 {total ? (page - 1) * pageSize + 1 : 0}–{Math.min(total, page * pageSize)} 项 · 共 {total} 项
        </p>
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-2">
          <div className="flex items-center gap-0.5 tabular-nums">
            <Button type="button" aria-label="上一页" disabled={page <= 1} onClick={() => changePage(page - 1)} size="icon-sm" variant="ghost"><ChevronLeft /></Button>
            {pages.map((number, index) => (
              <span key={number} className={number === page || number === 1 || number === last ? "inline-flex items-center" : "hidden items-center @2xl:inline-flex"}>
                {index > 0 && number - pages[index - 1] > 1 && <span aria-hidden className="hidden px-1 text-muted-foreground @2xl:inline">…</span>}
                <Button type="button" aria-current={number === page ? "page" : undefined} aria-label={`第 ${number} 页`} className="w-auto min-w-7 px-1.5 text-xs" onClick={() => changePage(number)} size="icon-sm" variant={number === page ? "secondary" : "ghost"}>{number}</Button>
              </span>
            ))}
            <Button type="button" aria-label="下一页" disabled={page >= last} onClick={() => changePage(page + 1)} size="icon-sm" variant="ghost"><ChevronRight /></Button>
          </div>
          {last > 1 && <form className="relative flex items-center gap-1 text-muted-foreground" onSubmit={(event) => {
            event.preventDefault();
            const next = Number(jump.trim());
            if (!/^\d+$/.test(jump.trim()) || !Number.isSafeInteger(next) || next < 1 || next > last) {
              setInvalid(true);
              return;
            }
            changePage(next);
          }}>
            <span aria-hidden>前往</span>
            <input aria-label="跳转页码" aria-invalid={invalid || undefined} aria-describedby={invalid ? errorId : undefined} className="h-7 w-12 rounded-md border bg-background px-1 text-center tabular-nums" inputMode="numeric" placeholder={String(page)} value={jump} onChange={(event) => { setJump(event.target.value); setInvalid(false); }} />
            <span aria-hidden>页</span>
            <Button size="xs" type="submit" variant="outline">跳转</Button>
            {invalid && <span id={errorId} role="alert" className="absolute right-0 top-full mt-0.5 whitespace-nowrap">请输入 1–{last} 的整数页码</span>}
          </form>}
          {onPageSizeChange && <label className="flex items-center gap-1 whitespace-nowrap text-muted-foreground">每页 <select className="h-7 rounded-md border bg-background px-1" value={pageSize} onChange={(event) => { setJump(""); setInvalid(false); onPageSizeChange(Number(event.target.value)); }}>{sizes.map((size) => <option key={size} value={size}>{size}</option>)}</select> 项</label>}
        </div>
      </div>
    </nav>
  );
}
