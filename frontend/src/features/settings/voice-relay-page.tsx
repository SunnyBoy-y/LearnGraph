import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, ServerCog } from "lucide-react";
import { toast } from "sonner";

import { getCurrentUser } from "@/api/control";
import {
  clearVoiceRelay,
  getVoiceRelay,
  saveVoiceRelay,
  setVoiceRelayEnabled,
  testVoiceRelay,
  type VoiceRelayProbeWire,
  type VoiceRelaySaveWire,
  type VoiceRelayTestWire,
} from "@/api/voice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";

/**
 * Deployment-wide voice relay (Cloudflare TURN).
 *
 * Two things make this page different from the workspace-scoped settings pages:
 *
 * * it is **deployment administrator only** -- the configuration is a singleton
 *   shared by every workspace, so a workspace admin must not reach it;
 * * the transport choice is a *measurement*, not an opinion.  aiortc honours a
 *   single TURN entry, so the page states plainly which one the server will use
 *   and the "test" action probes every URL from inside the deployment, which is
 *   the only way to know which transport actually works here.
 */

type Transport = "udp" | "tcp" | "tls";

const STUN_URL = "stun:stun.cloudflare.com:3478";
const TRANSPORT_URL: Record<Transport, string> = {
  udp: "turn:turn.cloudflare.com:3478?transport=udp",
  tcp: "turn:turn.cloudflare.com:3478?transport=tcp",
  tls: "turns:turn.cloudflare.com:5349?transport=tcp",
};
const TRANSPORT_LABEL: Record<Transport, string> = {
  udp: "UDP（延迟最低）",
  tcp: "TCP（穿透性更好）",
  tls: "TLS / 443（最不容易被拦）",
};
const ALL_TRANSPORTS: Transport[] = ["udp", "tcp", "tls"];

function transportOf(url: string): Transport | null {
  if (url.startsWith("stun:") || url.startsWith("stuns:")) return null;
  if (url.startsWith("turns:")) return "tls";
  if (url.includes("transport=tcp")) return "tcp";
  return "udp";
}

/** Server order matters: aiortc uses the first TURN entry it is given. */
function buildUrls(useStun: boolean, transports: Transport[], preferred: Transport): string[] {
  const ordered = [preferred, ...transports.filter((item) => item !== preferred)];
  return [
    ...(useStun ? [STUN_URL] : []),
    ...ordered.map((item) => TRANSPORT_URL[item]),
  ];
}

function statusBadge(status: string, enabled: boolean) {
  if (!enabled) return <Badge variant="secondary">已停用</Badge>;
  if (status === "error") return <Badge variant="destructive">异常</Badge>;
  if (status === "configured") return <Badge>已配置</Badge>;
  return <Badge variant="secondary">未配置</Badge>;
}

export function VoiceRelayPage() {
  const queryClient = useQueryClient();
  const currentUser = useQuery({ queryKey: ["auth-me"], queryFn: getCurrentUser });
  const isAdmin = Boolean(currentUser.data?.is_system_admin);

  const relay = useQuery({
    queryKey: ["voice-relay"],
    queryFn: getVoiceRelay,
    // Non-admins must not even issue the request.
    enabled: isAdmin,
  });

  const [keyId, setKeyId] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [token, setToken] = useState("");
  const [ttlHours, setTtlHours] = useState(24);
  const [useStun, setUseStun] = useState(true);
  const [transports, setTransports] = useState<Transport[]>(["udp", "tcp", "tls"]);
  const [preferred, setPreferred] = useState<Transport>("udp");
  const [result, setResult] = useState<VoiceRelayTestWire | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    const data = relay.data;
    if (!data) return;
    setKeyId(data.key_id ?? "");
    setApiBase(data.api_base || data.defaults.api_base);
    setTtlHours(Math.max(1, Math.round((data.credential_ttl_seconds || 86400) / 3600)));
    const urls = data.urls?.length ? data.urls : data.defaults.urls;
    setUseStun(urls.some((url) => url.startsWith("stun:") || url.startsWith("stuns:")));
    const found = urls
      .map(transportOf)
      .filter((item): item is Transport => item !== null);
    setTransports(found.length ? found : ["udp"]);
    const firstTurn = urls.find((url) => transportOf(url) !== null);
    setPreferred((firstTurn && transportOf(firstTurn)) || "udp");
  }, [relay.data]);

  const payload = useMemo<VoiceRelaySaveWire>(
    () => ({
      mode: "cloudflare",
      key_id: keyId.trim(),
      api_base: apiBase.trim(),
      urls: buildUrls(useStun, transports, preferred),
      credential_ttl_seconds: Math.round(ttlHours * 3600),
      ...(token.trim() ? { secret: token.trim() } : {}),
    }),
    [apiBase, keyId, preferred, token, transports, ttlHours, useStun],
  );

  const canSubmit =
    keyId.trim().length > 0 &&
    transports.length > 0 &&
    (token.trim().length > 0 || Boolean(relay.data?.secret_configured));

  const saveAndTest = useMutation({
    mutationFn: async () => {
      await saveVoiceRelay(payload);
      return testVoiceRelay(payload);
    },
    onSuccess: (tested) => {
      setResult(tested);
      setToken("");
      void queryClient.invalidateQueries({ queryKey: ["voice-relay"] });
      if (tested.ok) {
        toast.success("语音中继已保存，并已对所有用户生效");
      } else {
        toast.warning(`配置已保存，但中继暂不可用：${tested.detail}`);
      }
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const runTest = useMutation({
    mutationFn: () => testVoiceRelay(payload),
    onSuccess: (tested) => {
      setResult(tested);
      void queryClient.invalidateQueries({ queryKey: ["voice-relay"] });
      toast[tested.ok ? "success" : "warning"](tested.detail);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const toggleEnabled = useMutation({
    mutationFn: (next: boolean) => setVoiceRelayEnabled(next),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["voice-relay"] });
      toast.success(data.enabled ? "已启用语音中继" : "已停用语音中继（配置保留）");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const removeConfig = useMutation({
    mutationFn: () => clearVoiceRelay(),
    onSuccess: () => {
      setConfirmClear(false);
      setResult(null);
      void queryClient.invalidateQueries({ queryKey: ["voice-relay"] });
      toast.success("已删除语音中继配置");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  if (!currentUser.isLoading && !isAdmin) {
    return (
      <div className="space-y-2">
        <h2 className="text-lg font-medium">语音中继</h2>
        <p className="text-xs text-muted-foreground">
          仅终端管理员（系统管理员）可配置全系统通用的语音中继。
        </p>
      </div>
    );
  }

  const data = relay.data;
  const busy = saveAndTest.isPending || runTest.isPending;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <ServerCog className="h-4 w-4" />
          <h2 className="text-lg font-medium">语音中继</h2>
          {data ? statusBadge(data.status, data.enabled) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          配置一次，全系统所有用户的语音通话都使用它。未配置时通话只能尝试直连（`host`
          候选），跨网络通常无法接通。
        </p>
      </div>

      {data?.status === "error" && data.status_detail ? (
        <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs">
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 text-destructive" />
          <div className="space-y-1">
            <p className="font-medium text-destructive">最近一次解析失败</p>
            <p className="break-all text-muted-foreground">{data.status_detail}</p>
          </div>
        </div>
      ) : null}

      <section className="space-y-4 rounded-md border p-4">
        <div className="space-y-2">
          <Label htmlFor="voice-relay-key-id">Cloudflare TURN Key ID</Label>
          <Input
            id="voice-relay-key-id"
            onChange={(event) => setKeyId(event.target.value)}
            placeholder="例如 2f0b1c…（可明文保存）"
            value={keyId}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="voice-relay-token">Cloudflare API Token</Label>
          <Input
            id="voice-relay-token"
            onChange={(event) => setToken(event.target.value)}
            placeholder={
              data?.secret_configured
                ? `已配置（${data.secret_masked ?? "••••"}）— 留空表示不修改`
                : "粘贴 Cloudflare 生成的 TURN API Token"
            }
            type="password"
            value={token}
          />
          <p className="text-xs text-muted-foreground">
            Token 以密文入库（主密钥保护），接口只回显掩码；换 Token 时覆盖填入即可。
          </p>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <Label htmlFor="voice-relay-stun">同时下发 STUN</Label>
              <p className="text-xs text-muted-foreground">
                免费且无需凭据，能多一条 NAT 打洞机会，减少走中继的流量。
              </p>
            </div>
            <Switch
              checked={useStun}
              id="voice-relay-stun"
              onCheckedChange={setUseStun}
            />
          </div>

          <div className="space-y-2">
            <Label>中继传输方式</Label>
            {ALL_TRANSPORTS.map((item) => (
              <div className="flex items-center gap-2" key={item}>
                <input
                  checked={transports.includes(item)}
                  className="h-4 w-4 accent-foreground"
                  id={`voice-relay-transport-${item}`}
                  onChange={(event) => {
                    setTransports((current) => {
                      const next = event.target.checked
                        ? [...current, item]
                        : current.filter((value) => value !== item);
                      if (next.length === 0) return current;
                      if (!next.includes(preferred)) setPreferred(next[0]);
                      return next;
                    });
                  }}
                  type="checkbox"
                />
                <Label
                  className="cursor-pointer font-normal"
                  htmlFor={`voice-relay-transport-${item}`}
                >
                  {TRANSPORT_LABEL[item]}
                </Label>
              </div>
            ))}
          </div>

          <div className="space-y-2">
            <Label>服务端首选传输</Label>
            <p className="text-xs text-muted-foreground">
              服务端（aiortc）只能使用一个中继地址，因此列表中的第一个 TURN
              就是它实际会用的那条；浏览器侧会把全部地址都拿到并按优先级自行回退。
            </p>
            <RadioGroup
              className="gap-2"
              onValueChange={(value) => setPreferred(value as Transport)}
              value={preferred}
            >
              {transports.map((item) => (
                <div className="flex items-center gap-2" key={item}>
                  <RadioGroupItem
                    id={`voice-relay-preferred-${item}`}
                    value={item}
                  />
                  <Label
                    className="cursor-pointer font-normal"
                    htmlFor={`voice-relay-preferred-${item}`}
                  >
                    {TRANSPORT_LABEL[item]}
                  </Label>
                </div>
              ))}
            </RadioGroup>
          </div>
        </div>

        <details className="rounded-md border p-3">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            高级设置（凭据有效期 / 接口基址）
          </summary>
          <div className="mt-3 space-y-3">
            <div className="space-y-2">
              <Label htmlFor="voice-relay-ttl">凭据有效期（小时）</Label>
              <Input
                id="voice-relay-ttl"
                max={24}
                min={1}
                onChange={(event) => setTtlHours(Number(event.target.value) || 24)}
                type="number"
                value={ttlHours}
              />
              <p className="text-xs text-muted-foreground">
                Cloudflare 短期凭据有效期上限为 24 小时，服务端会在到期前自动换新。
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="voice-relay-api-base">Cloudflare 接口基址</Label>
              <Input
                id="voice-relay-api-base"
                onChange={(event) => setApiBase(event.target.value)}
                value={apiBase}
              />
              <p className="text-xs text-muted-foreground">
                默认 `https://rtc.live.cloudflare.com/v1`，仅在 Cloudflare
                调整端点时修改。
              </p>
            </div>
          </div>
        </details>

        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!canSubmit || busy} onClick={() => saveAndTest.mutate()} type="button">
            {saveAndTest.isPending ? "正在保存并实测…" : "保存并测试连接"}
          </Button>
          <Button
            disabled={!canSubmit || busy}
            onClick={() => runTest.mutate()}
            type="button"
            variant="outline"
          >
            {runTest.isPending ? "正在实测…" : "仅测试"}
          </Button>
          {data?.configured ? (
            <Button
              onClick={() => toggleEnabled.mutate(!data.enabled)}
              type="button"
              variant="outline"
            >
              {data.enabled ? "紧急停用" : "重新启用"}
            </Button>
          ) : null}
          {data?.configured ? (
            confirmClear ? (
              <Button
                onClick={() => removeConfig.mutate()}
                type="button"
                variant="destructive"
              >
                确认删除配置？
              </Button>
            ) : (
              <Button onClick={() => setConfirmClear(true)} type="button" variant="ghost">
                删除配置
              </Button>
            )
          ) : null}
        </div>
      </section>

      {result ? (
        <section className="space-y-3 rounded-md border p-4">
          <div className="flex items-center gap-2">
            {result.ok ? (
              <Check className="h-4 w-4 text-emerald-600" />
            ) : (
              <CircleAlert className="h-4 w-4 text-destructive" />
            )}
            <span className="text-sm font-medium">{result.detail}</span>
          </div>
          <p className="text-xs text-muted-foreground">
            凭据（掩码）：{result.credential_masked}
          </p>
          <div className="space-y-1">
            {result.probes.map((probe: VoiceRelayProbeWire) => (
              <div
                className="flex items-center justify-between gap-3 rounded border px-2 py-1 text-xs"
                key={probe.url}
              >
                <span className="break-all font-mono">{probe.url}</span>
                <span className={probe.ok ? "text-emerald-600" : "text-destructive"}>
                  {probe.ok ? `可用 ${probe.elapsed_ms}ms` : probe.detail || "不可用"}
                </span>
              </div>
            ))}
          </div>
          {result.cloudflare_urls.length &&
          result.cloudflare_urls.join("|") !== result.configured_urls.join("|") ? (
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>Cloudflare 本次下发的地址（与配置不同时仅记录，不会自动改写）：</p>
              {result.cloudflare_urls.map((url) => (
                <p className="break-all font-mono" key={url}>
                  {url}
                </p>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      <details className="rounded-md border p-4">
        <summary className="cursor-pointer text-sm font-medium">
          如何获取 Cloudflare TURN（首次配置请看这里）
        </summary>
        <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-muted-foreground">
          <li>
            登录 Cloudflare 控制台，进入 <strong>Realtime</strong>（Realtime / Calls）
            下的 <strong>TURN</strong> 页面，创建一个 TURN App / TURN Key。
          </li>
          <li>
            记下 <strong>TURN Key ID</strong>（可明文保存）与{" "}
            <strong>API Token</strong>（通常只显示一次，请立即保存）。
          </li>
          <li>
            回到本页：把 Key ID 与 API Token 填入上方；传输方式建议至少勾选 UDP，若所在环境
            对 UDP 出向有限制，再加选 TCP / TLS。
          </li>
          <li>
            点击「保存并测试连接」：页面会真实调用 Cloudflare 签发一次短期凭据，并从
            <strong>本机</strong>逐条实测每个地址能否拿到中继候选——哪条可用就是服务端该用的那条。
          </li>
          <li>
            看到「已配置」即生效：此后所有用户拨号都会自动带上中继，无需重启服务。
          </li>
        </ol>
        <p className="mt-3 text-xs text-muted-foreground">
          说明：Cloudflare 的 TURN 凭据是短期有效的，本系统保存的是长期凭据（Key ID + API
          Token），短期凭据由服务端按需签发并缓存，到期自动续期——因此不需要人工定期更换。
        </p>
      </details>
    </div>
  );
}
