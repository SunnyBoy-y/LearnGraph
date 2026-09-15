import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mic } from "lucide-react";
import { toast } from "sonner";

import {
  createProvider,
  listSettings,
  rotateProviderSecret,
  updateProvider,
  updateSetting,
} from "@/api";
import { SectionHeading, Surface } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { currentWorkspaceQueryKey } from "@/lib/query-keys";
import type { WorkspaceSetting } from "@/types/settings";
import type {
  Provider,
  ProviderTypeCatalogItem,
  SpeechModelPreset,
} from "@/types/providers";

import { speechModelsForRole } from "./speech-provider-config";

/**
 * 语音服务（全双工通话）专用的实时 ASR 常驻配置项。
 *
 * 与「语音转写」角色分家：卡位不可删除，只启停，用户只需要填一个地址和一个
 * API Key，模型名由预设固定。启用时除了把 Provider 行置为 enabled，还会写一条
 * 工作区级专用绑定 `models.functional_defaults.realtime_transcription`，让语音
 * 管线固定用这个模型，而不是「工作区里第一个可用的转写 Provider」。
 *
 * 模型白名单必须与 `backend/app/providers/speech_models.py` 的预设一一对应。
 */
const RESIDENT_ASR_MODEL_IDS: readonly string[] = ["qwen3-asr-flash-realtime"];

const FUNCTIONAL_DEFAULTS_KEY = "models.functional_defaults";
const VOICE_ASR_CAPABILITY = "realtime_transcription";

type AsrDraft = { address: string; key: string };
type VoiceAsrBinding = { providerId: string; modelId: string };

function purposeCapabilityKey(model: SpeechModelPreset): string {
  return model.purpose === "realtime"
    ? "default_realtime_transcription_model_id"
    : "default_transcription_model_id";
}

function addressLabel(model: SpeechModelPreset): string {
  return model.purpose === "realtime" ? "实时连接地址" : "服务地址";
}

function addressProtocols(model: SpeechModelPreset): string[] {
  return model.purpose === "realtime" ? ["wss:", "ws:"] : ["https:", "http:"];
}

function presetCapability(model: SpeechModelPreset, key: string): string {
  const value = model.default_capabilities?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function defaultAddress(model: SpeechModelPreset): string {
  if (model.purpose === "realtime") {
    const ws = presetCapability(model, "realtime_ws_url");
    if (ws) return ws;
  }
  return model.default_base_url;
}

/**
 * 实时卡位只有一个地址框（用户填的是 WS 端点），但
 * `transcription_provider_for_workspace` 仍要求 `base_url` 非空才会选中该行，
 * 所以 HTTP base 由预设派生：主机与预设一致时沿用预设的 compatible-mode 路径，
 * 自建网关只保留 origin，避免把 DashScope 的路径写到别人的域名下。
 */
function httpBaseFor(model: SpeechModelPreset, address: string): string {
  if (model.purpose !== "realtime") return address;
  try {
    const next = new URL(address);
    const preset = new URL(model.default_base_url);
    if (next.hostname === preset.hostname) return model.default_base_url;
    return `${next.protocol === "wss:" ? "https:" : "http:"}//${next.host}`;
  } catch {
    return model.default_base_url;
  }
}

function rowAddress(row: Provider | undefined, model: SpeechModelPreset): string {
  if (row) {
    if (model.purpose === "realtime") {
      const ws = row.capabilities?.realtime_ws_url;
      if (typeof ws === "string" && ws.trim()) return ws.trim();
    }
    if (row.base_url?.trim()) return row.base_url.trim();
  }
  return defaultAddress(model);
}

/** 一行 ASR Provider 归属哪个卡位：用途键优先，语音配置向导写的标记兜底。 */
function residentRowFor(
  providers: Provider[],
  model: SpeechModelPreset,
  claimed: Set<string>,
): Provider | undefined {
  const purposeKey = purposeCapabilityKey(model);
  return providers.find((provider) => {
    if (claimed.has(provider.id)) return false;
    if (provider.provider_type !== model.provider_type) return false;
    const capabilities = provider.capabilities ?? {};
    const designated = capabilities[purposeKey];
    if (typeof designated === "string" && designated.trim() === model.id) {
      return true;
    }
    return capabilities.speech_model_id === model.id;
  });
}

function readVoiceAsrBinding(
  settings: WorkspaceSetting[] | undefined,
): VoiceAsrBinding | null {
  const raw = settings?.find((item) => item.key === FUNCTIONAL_DEFAULTS_KEY)?.value;
  if (!raw || typeof raw !== "object") return null;
  const target = (raw as Record<string, unknown>)[VOICE_ASR_CAPABILITY];
  if (!target || typeof target !== "object") return null;
  const providerId = (target as Record<string, unknown>).provider_id;
  const modelId = (target as Record<string, unknown>).model_id;
  if (typeof providerId !== "string" || !providerId) return null;
  if (typeof modelId !== "string" || !modelId) return null;
  return { providerId, modelId };
}

/**
 * 写入专用绑定。`models.functional_defaults` 是整值替换的 workspace setting，
 * 所以必须先把当前值读出来再合并，否则会抹掉其他能力位（chat/vision/...）。
 */
function writeVoiceAsrBinding(
  settings: WorkspaceSetting[] | undefined,
  target: VoiceAsrBinding | null,
) {
  const raw = settings?.find((item) => item.key === FUNCTIONAL_DEFAULTS_KEY)?.value;
  const next: Record<string, unknown> =
    raw && typeof raw === "object" ? { ...(raw as Record<string, unknown>) } : {};
  if (target) {
    next[VOICE_ASR_CAPABILITY] = {
      provider_id: target.providerId,
      model_id: target.modelId,
    };
  } else {
    delete next[VOICE_ASR_CAPABILITY];
  }
  return updateSetting(FUNCTIONAL_DEFAULTS_KEY, next);
}

function assertAddress(value: string, model: SpeechModelPreset): void {
  const protocols = addressProtocols(model);
  try {
    const parsed = new URL(value);
    if (
      !protocols.includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      throw new Error("invalid");
    }
  } catch {
    throw new Error(
      `${addressLabel(model)}格式不正确，请填写完整的 ${protocols.join(" / ")} 地址。`,
    );
  }
}

/** 建行或更新行，返回 Provider id；不改变启用状态与专用绑定。 */
async function upsertRow({
  model,
  row,
  address,
  key,
}: {
  model: SpeechModelPreset;
  row: Provider | undefined;
  address: string;
  key: string;
}): Promise<string> {
  const trimmed = address.trim();
  if (!trimmed) throw new Error(`请填写${addressLabel(model)}。`);
  assertAddress(trimmed, model);
  const isRealtime = model.purpose === "realtime";
  const designation = isRealtime
    ? { default_realtime_transcription_model_id: model.id }
    : { default_transcription_model_id: model.id };
  if (row) {
    await updateProvider(row.id, {
      base_url: httpBaseFor(model, trimmed),
      ...designation,
      ...(isRealtime ? { realtime_ws_url: trimmed } : {}),
    });
    if (key.trim()) await rotateProviderSecret(row.id, key.trim());
    return row.id;
  }
  if (!key.trim()) throw new Error("首次配置需要填写 API Key。");
  const capabilities: Record<string, unknown> = {
    ...model.default_capabilities,
    speech_model_id: model.id,
    default_model: model.id,
    ...designation,
  };
  if (isRealtime) capabilities.realtime_ws_url = trimmed;
  const created = await createProvider({
    display_name: model.label,
    provider_type: model.provider_type,
    base_url: httpBaseFor(model, trimmed),
    api_key: key.trim(),
    capabilities,
  });
  return created.id;
}

export function ResidentAsrSection({
  catalog,
  providers,
  providersPending,
  secretStoreAvailable,
}: {
  catalog: ProviderTypeCatalogItem[];
  providers: Provider[];
  providersPending: boolean;
  secretStoreAvailable: boolean;
}) {
  const queryClient = useQueryClient();
  const settingsQueryKey = currentWorkspaceQueryKey("settings");
  const settings = useQuery({
    queryKey: settingsQueryKey,
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const models = useMemo(() => {
    const byId = new Map(
      speechModelsForRole(catalog, "transcription").map((model) => [
        model.id,
        model,
      ]),
    );
    return RESIDENT_ASR_MODEL_IDS.flatMap((id) => {
      const model = byId.get(id);
      return model ? [model] : [];
    });
  }, [catalog]);
  const slots = useMemo(() => {
    const claimed = new Set<string>();
    return models.map((model) => {
      const row = residentRowFor(providers, model, claimed);
      if (row) claimed.add(row.id);
      return { model, row };
    });
  }, [models, providers]);
  const binding = useMemo(
    () => readVoiceAsrBinding(settings.data),
    [settings.data],
  );
  const [drafts, setDrafts] = useState<Record<string, AsrDraft>>({});
  // 只在 providers 首轮落地后水合草稿，避免把已有行的地址覆盖成预设值。
  useEffect(() => {
    if (providersPending) return;
    setDrafts((current) => {
      let changed = false;
      const next = { ...current };
      for (const { model, row } of slots) {
        if (next[model.id]) continue;
        next[model.id] = { address: rowAddress(row, model), key: "" };
        changed = true;
      }
      return changed ? next : current;
    });
  }, [providersPending, slots]);
  const cacheSetting = (setting: WorkspaceSetting) => {
    queryClient.setQueryData<WorkspaceSetting[]>(settingsQueryKey, (current) => [
      ...(current ?? []).filter((item) => item.key !== setting.key),
      setting,
    ]);
  };
  const save = useMutation({
    mutationFn: (input: {
      model: SpeechModelPreset;
      row: Provider | undefined;
      address: string;
      key: string;
    }) => upsertRow(input),
    onSuccess: (_rowId, variables) => {
      setDrafts((current) => ({
        ...current,
        [variables.model.id]: {
          address: variables.address,
          key: "",
        },
      }));
      toast.success(`${variables.model.label} 配置已保存`);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const toggle = useMutation({
    mutationFn: async (input: {
      model: SpeechModelPreset;
      row: Provider | undefined;
      address: string;
      key: string;
      enabled: boolean;
    }) => {
      const { model, row, enabled } = input;
      if (!enabled) {
        if (row) await updateProvider(row.id, { enabled: false });
        const setting = await writeVoiceAsrBinding(settings.data, null);
        return setting;
      }
      const rowId = await upsertRow(input);
      await updateProvider(rowId, { enabled: true });
      const setting = await writeVoiceAsrBinding(settings.data, {
        providerId: rowId,
        modelId: model.id,
      });
      return setting;
    },
    onSuccess: (setting, variables) => {
      cacheSetting(setting);
      setDrafts((current) => ({
        ...current,
        [variables.model.id]: { address: variables.address, key: "" },
      }));
      toast.success(
        variables.enabled
          ? `${variables.model.label} 已启用，语音服务将固定使用该模型`
          : `${variables.model.label} 已停用，语音服务回到默认转写解析`,
      );
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (error) => toast.error(error.message),
  });
  if (!models.length) return null;
  const locked = !secretStoreAvailable || settings.isPending;
  const busy = save.isPending || toggle.isPending;
  return (
    <Surface>
      <div className="border-b p-5">
        <SectionHeading title="语音服务 · 实时语音识别" />
        <p className="mt-1 text-xs text-muted-foreground">
          语音服务（全双工通话）专用的实时识别模型：模型名固定，只需要填地址和
          API Key，打开开关即启用。启用后会把这个模型固定绑定给语音管线，
          与「语音转写」分类下的文件/分段转写互不干扰。
        </p>
        {!secretStoreAvailable ? (
          <p className="mt-2 text-xs text-destructive">
            Secret Store 不可用，API Key 无法保存；请先在工作区设置里启用密钥存储。
          </p>
        ) : null}
      </div>
      <div className="divide-y">
        {slots.map(({ model, row }) => {
          const draft = drafts[model.id] ?? {
            address: defaultAddress(model),
            key: "",
          };
          const rowBusy = busy;
          const bound =
            Boolean(row) &&
            binding?.providerId === row?.id &&
            binding?.modelId === model.id;
          const active = Boolean(row?.enabled) && bound;
          const patchDraft = (patch: Partial<AsrDraft>) =>
            setDrafts((current) => ({
              ...current,
              [model.id]: { ...draft, ...patch },
            }));
          return (
            <div className="grid gap-3 px-5 py-4" key={model.id}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <Mic className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="font-mono text-sm">{model.id}</span>
                  <span className="text-xs text-muted-foreground">
                    {model.label}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    {active ? "已启用" : row ? "已配置未启用" : "未配置"}
                  </span>
                  <Switch
                    aria-label={`启用 ${model.label}`}
                    checked={active}
                    disabled={rowBusy || locked}
                    onCheckedChange={(checked) =>
                      toggle.mutate({
                        model,
                        row,
                        address: draft.address,
                        key: draft.key,
                        enabled: checked,
                      })
                    }
                  />
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="grid gap-1">
                  <Label className="text-[10px] text-muted-foreground">
                    {addressLabel(model)}
                  </Label>
                  <Input
                    aria-label={`${model.label} ${addressLabel(model)}`}
                    className="h-8 font-mono text-xs"
                    onChange={(event) =>
                      patchDraft({ address: event.target.value })
                    }
                    placeholder={defaultAddress(model)}
                    value={draft.address}
                  />
                </div>
                <div className="grid gap-1">
                  <Label className="text-[10px] text-muted-foreground">
                    API Key
                    {row?.api_key_masked ? `（已保存 ${row.api_key_masked}）` : ""}
                  </Label>
                  <Input
                    aria-label={`${model.label} API Key`}
                    autoComplete="off"
                    className="h-8 font-mono text-xs"
                    onChange={(event) => patchDraft({ key: event.target.value })}
                    placeholder={row ? "留空表示不修改" : "必填"}
                    type="password"
                    value={draft.key}
                  />
                </div>
              </div>
              <div>
                <Button
                  disabled={rowBusy || locked}
                  onClick={() =>
                    save.mutate({
                      model,
                      row,
                      address: draft.address,
                      key: draft.key,
                    })
                  }
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  {save.isPending ? "保存中…" : "保存"}
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </Surface>
  );
}
