import { useRef, type InputHTMLAttributes } from "react";
import { Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface ClearableSearchInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "value"> {
  value: string;
  onChange: (value: string) => void;
  clearLabel?: string;
  containerClassName?: string;
  inputClassName?: string;
}

export function ClearableSearchInput({
  value,
  onChange,
  clearLabel = "清空搜索",
  containerClassName,
  inputClassName,
  className,
  placeholder = "搜索…",
  ...rest
}: ClearableSearchInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className={cn("relative", containerClassName, className)}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        ref={inputRef}
        className={cn("pl-9 [&::-webkit-search-cancel-button]:hidden", value ? "pr-8" : "", inputClassName)}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type="text"
        value={value}
        {...rest}
      />
      {value ? (
        <button
          aria-label={clearLabel}
          className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          onClick={() => {
            onChange("");
            inputRef.current?.focus();
          }}
          title={clearLabel}
          type="button"
        >
          <X className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}
