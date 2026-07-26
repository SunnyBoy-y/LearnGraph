import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCcw, FlaskConical } from "lucide-react";
import { toast } from "sonner";

import { updateProviderBalanceQueryConfig } from "@/api";
import { LoadingState, StatePill } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  BALANCE_QUERY_PRESETS,
  formatBalanceAmount,
  materializeScript,
  parseVariablesInput,
  runCustomBalanceQuery,
  stringifyVariables,
  type CustomBalanceQueryOutcome,
} from "@/lib/balance-query";
import {
  providerBalanceQueryConfig,
  type BalanceExtractorResult,
  type Provider,
  type ProviderBalanceQueryConfig,
} from "@/types/providers";

/* ------------------------------------------------------------------ */
/* Result rendering                                                    */
/* ------------------------------------------------------------------ */

function BalanceResultList({ results }: { results: BalanceExtractorResult[] }) {
  return (
    <div className="overflow-hidden rounded-xl border">
      {results.map((entry, index) => {
        const usedPercent =
          entry.total !== undefined &&
          entry.total > 0 &&
          entry.used !== undefined
            ? Math.min(100, Math.max(0, (entry.used / entry.total) * 100))
            : null;
        return (
          <section className="border-b p-4 last:border-b-0" key={index}>
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-sm font-medium">
                {entry.planName ?? "账户余额"}
              </p>
              {entry.remaining !== undefined ? (
                <p className="font-mono text-base font-semibold">
                  剩余 {formatBalanceAmount(entry.remaining)}
                  {entry.unit ? ` ${entry.unit}` : ""}
                </p>
              ) : null}
            </div>
            {entry.isValid === false ? (
              <p
                className="mt-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive"
                role="alert"
              >
                {entry.invalidMessage ?? "套餐已失效"}
              </p>
            ) : null}
            {usedPercent !== null ? (
              <>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className={`h-full rounded-full ${
                      usedPercent >= 90 ? "bg-destructive" : "bg-primary"
                    }`}
                    style={{ width: `${usedPercent}%` }}
                  />
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  已用 {formatBalanceAmount(entry.used ?? 0)} / 总额{" "}
                  {entry.total === -1
                    ? "∞"
                    : formatBalanceAmount(entry.total ?? 0)}
                  {entry.unit ? ` ${entry.unit}` : ""}（
                  {usedPercent.toFixed(0)}%）
                </p>
              </>
            ) : entry.total !== undefined || entry.used !== undefined ? (
              <p className="mt-2 text-xs text-muted-foreground">
                {entry.total !== undefined
                  ? `总额 ${entry.total === -1 ? "∞" : formatBalanceAmount(entry.total)} `
                  : ""}
                {entry.used !== undefined
                  ? `已用 ${formatBalanceAmount(entry.used)}`
                  : ""}
              </p>
            ) : null}
            {entry.extra ? (
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {entry.extra}
              </p>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Custom balance dialog (query result)                                */
/* ------------------------------------------------------------------ */

export function CustomBalanceDialog({
  error,
  isPending,
  onClose,
  onRetry,
  result,
  target,
}: {
  error: Error | null;
  isPending: boolean;
  onClose: () => void;
  onRetry: () => void;
  result: CustomBalanceQueryOutcome | undefined;
  target: Provider | null;
}) {
  const visibleResult =
    result?.providerId === target?.id ? result : undefined;
  const loading = isPending || (!error && !visibleResult);
  const hasInvalidEntry = visibleResult?.results.some(
    (entry) => entry.isValid === false,
  );

  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={Boolean(target)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>账户余额（自定义查询）</DialogTitle>
          <DialogDescription>
            {target?.display_name ?? "Provider"}
            。使用自定义脚本查询；提取器在本地沙箱中运行。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          {loading ? (
            <div className="py-3">
              <LoadingState label="正在执行自定义余额查询…" />
            </div>
          ) : error ? (
            <div
              className="rounded-xl border border-destructive/30 bg-destructive/5 p-4"
              role="alert"
            >
              <p className="font-medium text-destructive">余额查询未完成</p>
              <p className="mt-1 break-all text-sm leading-6 text-muted-foreground">
                {error.message}
              </p>
            </div>
          ) : visibleResult ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-muted/20 px-4 py-3">
                <StatePill
                  label={hasInvalidEntry ? "套餐存在异常" : "查询成功"}
                  status={hasInvalidEntry ? "failed" : "healthy"}
                />
                <p className="text-xs text-muted-foreground">
                  查询于{" "}
                  {new Intl.DateTimeFormat("zh-CN", {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }).format(new Date(visibleResult.queriedAt))}
                </p>
              </div>
              <BalanceResultList results={visibleResult.results} />
            </>
          ) : null}
        </div>
        <DialogFooter>
          <Button onClick={onClose} type="button" variant="outline">
            关闭
          </Button>
          {!loading ? (
            <Button onClick={onRetry} type="button">
              <RefreshCcw className="size-4" />
              {error ? "重试查询" : "刷新余额"}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Config dialog                                                       */
/* ------------------------------------------------------------------ */

type TestState =
  | { status: "idle" }
  | { status: "running" }
  | { status: "success"; results: BalanceExtractorResult[] }
  | { status: "error"; message: string };

export function BalanceQueryConfigDialog({
  onClose,
  target,
}: {
  onClose: () => void;
  target: Provider | null;
}) {
  const queryClient = useQueryClient();
  const stored = useMemo(
    () => (target ? providerBalanceQueryConfig(target) : null),
    [target],
  );
  const [enabled, setEnabled] = useState(false);
  const [templateId, setTemplateId] = useState("general");
  const [script, setScript] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("10");
  const [intervalMinutes, setIntervalMinutes] = useState("0");
  const [variablesText, setVariablesText] = useState("");
  const [testState, setTestState] = useState<TestState>({ status: "idle" });

  useEffect(() => {
    if (!target) return;
    setEnabled(stored?.enabled ?? false);
    setTemplateId(stored?.template_id ?? "general");
    setScript(
      stored?.script ??
        materializeScript(
          BALANCE_QUERY_PRESETS.find((preset) => preset.id === "general")!
            .script,
          target.base_url,
        ),
    );
    setTimeoutSeconds(String(stored?.timeout_seconds ?? 10));
    setIntervalMinutes(String(stored?.auto_query_interval_minutes ?? 0));
    setVariablesText(stringifyVariables(stored?.variables ?? {}));
    setTestState({ status: "idle" });
  }, [target, stored]);

  const buildConfig = (): ProviderBalanceQueryConfig => {
    if (!script.trim()) {
      throw new Error("提取器代码不能为空");
    }
    const timeout = Number(timeoutSeconds);
    if (!Number.isFinite(timeout) || timeout < 1 || timeout > 60) {
      throw new Error("超时时间需在 1–60 秒之间");
    }
    const interval = Number(intervalMinutes);
    if (
      !Number.isFinite(interval) ||
      !Number.isInteger(interval) ||
      interval < 0 ||
      interval > 1440
    ) {
      throw new Error("自动查询间隔需为 0–1440 的整数分钟");
    }
    return {
      enabled,
      template_id: templateId,
      script,
      timeout_seconds: timeout,
      auto_query_interval_minutes: interval,
      variables: parseVariablesInput(variablesText),
    };
  };

  const save = useMutation({
    mutationFn: (config: ProviderBalanceQueryConfig) =>
      updateProviderBalanceQueryConfig(target!.id, config),
    onSuccess: (view) => {
      toast.success(
        view.config?.enabled
          ? "自定义余额查询已启用"
          : "余额查询配置已保存（当前使用官方内置方式）",
      );
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      onClose();
    },
    onError: (error) => toast.error(error.message),
  });

  const runTest = async () => {
    if (!target) return;
    let config: ProviderBalanceQueryConfig;
    try {
      config = buildConfig();
    } catch (error) {
      setTestState({
        status: "error",
        message: error instanceof Error ? error.message : String(error),
      });
      return;
    }
    setTestState({ status: "running" });
    try {
      const outcome = await runCustomBalanceQuery(target.id, config);
      setTestState({ status: "success", results: outcome.results });
    } catch (error) {
      setTestState({
        status: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  };

  const applyPreset = (presetId: string) => {
    setTemplateId(presetId);
    const preset = BALANCE_QUERY_PRESETS.find((item) => item.id === presetId);
    if (preset) setScript(materializeScript(preset.script, target?.base_url));
  };

  const activePreset = BALANCE_QUERY_PRESETS.find(
    (item) => item.id === templateId,
  );

  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={Boolean(target)}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>余额查询配置</DialogTitle>
          <DialogDescription>
            {target?.display_name ?? "Provider"}
            。不启用时使用官方内置查询方式；启用后按下方脚本查询（参考 cc-switch）。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-1">
          <div className="flex items-center justify-between rounded-xl border bg-muted/20 px-4 py-3">
            <div>
              <p className="text-sm font-medium">启用自定义余额查询</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                关闭时按官方 / 内置中转站惯例查询余额
              </p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-2">
              <Label>预设模板</Label>
              <Select onValueChange={applyPreset} value={templateId}>
                <SelectTrigger>
                  <SelectValue placeholder="选择模板" />
                </SelectTrigger>
                <SelectContent>
                  {BALANCE_QUERY_PRESETS.map((preset) => (
                    <SelectItem key={preset.id} value={preset.id}>
                      {preset.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="balance-query-timeout">超时时间（秒）</Label>
              <Input
                id="balance-query-timeout"
                inputMode="numeric"
                onChange={(event) => setTimeoutSeconds(event.target.value)}
                value={timeoutSeconds}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="balance-query-interval">
                自动查询间隔（分钟）
              </Label>
              <Input
                id="balance-query-interval"
                inputMode="numeric"
                onChange={(event) => setIntervalMinutes(event.target.value)}
                value={intervalMinutes}
              />
              <p className="text-[11px] leading-4 text-muted-foreground">
                0 表示不自动查询；仅在应用打开时轮询
              </p>
            </div>
          </div>
          {activePreset?.note ? (
            <p className="rounded-lg border border-dashed px-3 py-2 text-xs leading-5 text-muted-foreground">
              {activePreset.note}
            </p>
          ) : null}
          <div className="space-y-2">
            <Label htmlFor="balance-query-script">提取器代码</Label>
            <Textarea
              className="min-h-64 font-mono text-xs leading-5"
              id="balance-query-script"
              onChange={(event) => {
                setScript(event.currentTarget.value);
                setTemplateId("custom");
              }}
              spellCheck={false}
              value={script}
            />
            <p className="text-xs leading-5 text-muted-foreground">
              整个配置须用 () 包裹为对象字面量；已默认填入该供应商的 Base URL（
              {target?.base_url ?? "未配置"}），也可写{" "}
              <code className="rounded bg-muted px-1">{"{{baseUrl}}"}</code>{" "}
              占位符自动跟随。
              <code className="rounded bg-muted px-1">{"{{apiKey}}"}</code>{" "}
              即已保存密钥
              {target?.api_key_masked ? `（${target.api_key_masked}）` : ""}
              ，由服务端注入，无需手填。extractor 返回{" "}
              <code className="rounded bg-muted px-1">
                {"{ isValid, invalidMessage, remaining, unit, planName, total, used, extra }"}
              </code>
              ，或它们的数组。
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="balance-query-variables">
              自定义变量（每行一个，格式：名称=值）
            </Label>
            <Textarea
              className="min-h-16 font-mono text-xs"
              id="balance-query-variables"
              onChange={(event) => setVariablesText(event.currentTarget.value)}
              placeholder={"accessToken=xxxx\nuserId=1"}
              spellCheck={false}
              value={variablesText}
            />
          </div>
          {testState.status === "running" ? (
            <div className="py-1">
              <LoadingState label="正在测试脚本…" />
            </div>
          ) : testState.status === "error" ? (
            <div
              className="rounded-xl border border-destructive/30 bg-destructive/5 p-3"
              role="alert"
            >
              <p className="text-sm font-medium text-destructive">测试失败</p>
              <p className="mt-1 break-all text-xs leading-5 text-muted-foreground">
                {testState.message}
              </p>
            </div>
          ) : testState.status === "success" ? (
            <div className="space-y-2">
              <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
                测试成功
              </p>
              <BalanceResultList results={testState.results} />
            </div>
          ) : null}
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            disabled={testState.status === "running" || save.isPending}
            onClick={() => void runTest()}
            type="button"
            variant="secondary"
          >
            <FlaskConical className="size-4" />
            测试脚本
          </Button>
          <div className="flex gap-2">
            <Button
              disabled={save.isPending}
              onClick={onClose}
              type="button"
              variant="outline"
            >
              取消
            </Button>
            <Button
              disabled={save.isPending || testState.status === "running"}
              onClick={() => {
                try {
                  save.mutate(buildConfig());
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : String(error),
                  );
                }
              }}
              type="button"
            >
              {save.isPending ? "保存中…" : "保存配置"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
