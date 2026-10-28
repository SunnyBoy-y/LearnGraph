import { DropdownMenuCheckboxItem } from "@/components/ui/dropdown-menu";

export function VoiceLowLatencyToggle({
  enabled,
  pending = false,
  onCheckedChange,
}: {
  enabled: boolean;
  pending?: boolean;
  onCheckedChange: (enabled: boolean) => void;
}) {
  return (
    <DropdownMenuCheckboxItem
      aria-label="低延迟模式"
      checked={enabled}
      disabled={pending}
      onCheckedChange={(checked) => onCheckedChange(checked === true)}
      onSelect={(event) => event.preventDefault()}
      className="gap-3 py-2 pr-1.5 [&>[data-slot=dropdown-menu-checkbox-item-indicator]]:hidden"
      title="下一轮生效：复杂任务先简短应声，每轮一次；仍可随时打断。简单问答与原样复述不受影响。"
    >
      <span className="flex min-w-0 flex-col">
        <span>低延迟模式</span>
        <span className="text-[10px] text-muted-foreground">复杂任务先简短应声</span>
      </span>
      <span aria-hidden="true" className="ml-auto flex shrink-0 items-center gap-1.5 text-[10px] text-muted-foreground">
        {pending ? "保存中…" : enabled ? "已开启" : "已关闭"}
        <span className={`flex h-4 w-7 items-center rounded-full p-0.5 ${enabled ? "bg-foreground" : "bg-muted-foreground/25"}`}>
          <span className={`size-3 rounded-full bg-background transition-transform ${enabled ? "translate-x-3" : "translate-x-0"}`} />
        </span>
      </span>
    </DropdownMenuCheckboxItem>
  );
}
