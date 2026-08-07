import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileSearch,
  Network,
  PackageCheck,
  Pencil,
  Puzzle,
  RefreshCcw,
  Search,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Trash2,
} from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  authorizeMcpServer,
  authorizeSkill,
  browseMcpRegistry,
  checkSkillUpdate,
  confirmSkillDeletion,
  deleteMcpServer,
  installSkill,
  invokeMcpTool,
  invokeSkill,
  listBuiltinMcpTools,
  listMcpServers,
  listPlugins,
  listProviderCatalog,
  listProviders,
  listSkills,
  refreshMcpServer,
  registerMcpServer,
  requestSkillDeletion,
  revokeMcpServer,
  revokeSkill,
  togglePlugin,
  updateMcpServer,
  upgradeSkill,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { DomainAllowlistEditor } from "@/components/shared/domain-allowlist-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  ComponentAdministration,
  McpRevisionPanel,
  SandboxAdministration,
  SkillRevisionPanel,
} from "@/features/settings/control-pages";
import { SkillPackageEditor } from "@/features/settings/skill-package-editor";
import { AddSkillDialog } from "@/features/settings/skills-hub-extras";
import type {
  BuiltinMcpTool,
  MCPServer,
  MCPServerCreate,
  McpRegistrySearchItem,
  PermissionDecision,
  Skill,
  SkillCreate,
  SkillDeleteRequest,
} from "@/types/extensions";
import type { ProviderRole } from "@/types/providers";

/** Hub tabs for D-076 unified Extensions Center. */
export type ExtensionsHubTab =
  | "overview"
  | "skills"
  | "mcp"
  | "components"
  | "sandbox";

const HUB_TABS: Array<{
  value: ExtensionsHubTab;
  label: string;
}> = [
  { value: "overview", label: "总览" },
  { value: "skills", label: "Skills Hub" },
  { value: "mcp", label: "MCP" },
  { value: "components", label: "可信组件" },
  { value: "sandbox", label: "沙箱" },
];

/** Legacy tab params from removed tabs redirect to their merged destination. */
const LEGACY_TAB_ALIASES: Record<string, ExtensionsHubTab> = {
  plugins: "overview",
  audit: "mcp",
};

function normalizeHubTab(raw: string | null): ExtensionsHubTab {
  if (raw && raw in LEGACY_TAB_ALIASES) return LEGACY_TAB_ALIASES[raw];
  const value = (raw ?? "overview") as ExtensionsHubTab;
  if (HUB_TABS.some((tab) => tab.value === value)) return value;
  return "overview";
}

/** Unified Extensions Center (D-076): single entry for overview (incl. plugins), Skills, MCP, components, sandbox. */
export function ExtensionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = normalizeHubTab(searchParams.get("tab"));

  const setTab = (next: string) => {
    const normalized = normalizeHubTab(next);
    setSearchParams(
      normalized === "overview" ? {} : { tab: normalized },
      { replace: true },
    );
  };

  return (
    <PageFrame>
      <PageIntro
        description="MCP、Skills、插件、可信组件与沙箱的统一入口。安装、秘密、权限、扫描与沙箱执行均由后端完成。"
        eyebrow="Extensions hub"
        title="扩展中心"
      />
      <Tabs onValueChange={setTab} value={tab}>
        <TabsList
          aria-label="扩展中心分区"
          className="h-auto w-full flex-wrap justify-start gap-1"
        >
          {HUB_TABS.map((item) => (
            <TabsTrigger key={item.value} value={item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent className="mt-5 space-y-5" value="overview">
          <ExtensionsOverviewPanel embedded />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="skills">
          <ToolsPage embedded focus="skills" />
          <details>
            <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
              高级 · Skill 详情与修订（版本、来源与 Manifest）
            </summary>
            <div className="mt-4">
              <SkillRevisionPanel />
            </div>
          </details>
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="mcp">
          <ToolsPage embedded focus="mcp" />
          <details>
            <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
              高级 · Transport 能力、Server 详情与快照
            </summary>
            <div className="mt-4">
              <McpRevisionPanel />
            </div>
          </details>
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="components">
          <ComponentAdministration />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="sandbox">
          <SandboxAdministration />
        </TabsContent>
      </Tabs>
    </PageFrame>
  );
}

export function ExtensionsOverviewPanel({
  embedded = false,
}: {
  embedded?: boolean;
}) {
  const { workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: listPlugins });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      togglePlugin(id, { enabled }),
    onSuccess: () => {
      toast.success("插件状态已更新");
      void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    },
    onError: (error) => toast.error(error.message),
  });
  if (plugins.isPending || providers.isPending)
    return embedded ? (
      <LoadingState />
    ) : (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (plugins.isError || providers.isError)
    return embedded ? (
      <ErrorState
        message={
          (plugins.error ?? providers.error)?.message ?? "扩展状态读取失败"
        }
      />
    ) : (
      <PageFrame>
        <ErrorState
          message={
            (plugins.error ?? providers.error)?.message ?? "扩展状态读取失败"
          }
        />
      </PageFrame>
    );
  const capabilityRows = [
    ...providers.data.map((provider) => ({
      detail: `${provider.provider_type} · ${provider.remote_capability ? "远程能力已验证" : "未声明远程能力"}`,
      icon: Sparkles,
      key: `provider:${provider.id}`,
      name: provider.display_name,
      status: provider.enabled ? provider.status : "disabled",
      target: `/w/${workspaceId}/settings/providers`,
    })),
    ...plugins.data.map((plugin) => ({
      detail: `${plugin.plugin_type} · v${plugin.version} · ${plugin.capabilities.join(" / ") || "未声明 capability"}`,
      icon: PackageCheck,
      key: `plugin:${plugin.id}`,
      name: plugin.name,
      status: plugin.enabled ? plugin.status : "disabled",
      target: `#installed-plugins`,
    })),
  ];
  const body = (
    <>
      <Surface className="p-5">
          <SectionHeading
            description="状态、版本和远程能力均来自服务端"
            title="实际能力记录"
          />
          {capabilityRows.length ? (
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {capabilityRows.map((item) => {
                const Icon = item.icon;
                return (
                  <div className="rounded-xl border p-4" key={item.key}>
                    <Icon className="size-4 text-primary" />
                    <p className="mt-3 text-sm font-semibold">{item.name}</p>
                    <div className="mt-2">
                      <StatePill status={item.status} />
                    </div>
                    <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
                      {item.detail}
                    </p>
                    <Button asChild className="mt-3" size="xs" variant="outline">
                      <Link to={item.target}>查看记录</Link>
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="py-12 text-center text-sm text-muted-foreground">
              当前工作区没有 Provider 或 Plugin 记录。
            </p>
          )}
          <div className="mt-5 flex flex-wrap gap-2">
            <Button asChild size="sm" variant="outline">
              <Link to={`/w/${workspaceId}/settings/extensions?tab=skills`}>
                <Puzzle className="size-4" />
                Skills 安装与授权
              </Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to={`/w/${workspaceId}/settings/extensions?tab=mcp`}>
                <Network className="size-4" />
                MCP 管理
              </Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to={`/w/${workspaceId}/settings/extensions?tab=sandbox`}>
                <TerminalSquare className="size-4" />
                沙箱管理
              </Link>
            </Button>
          </div>
        </Surface>
      <Surface className="overflow-hidden" id="installed-plugins">
        <div className="border-b p-5">
          <SectionHeading title="已安装插件" />
        </div>
        {plugins.data.length ? (
          <div className="divide-y">
            {plugins.data.map((plugin) => (
              <div
                className="flex flex-col gap-4 px-5 py-4 sm:flex-row sm:items-center"
                key={plugin.id}
              >
                <span className="grid size-9 place-items-center rounded-xl bg-muted">
                  <Puzzle className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold">{plugin.name}</p>
                    <StatePill status={plugin.status} />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {plugin.plugin_key} · v{plugin.version} ·{" "}
                    {plugin.plugin_type}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {plugin.permissions.map((permission) => (
                      <Badge
                        className="font-mono text-[10px]"
                        key={permission}
                        variant="secondary"
                      >
                        {permission}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Label className="text-xs" htmlFor={`plugin-${plugin.id}`}>
                    {plugin.enabled ? "启用" : "停用"}
                  </Label>
                  <Switch
                    checked={plugin.enabled}
                    disabled={toggle.isPending}
                    id={`plugin-${plugin.id}`}
                    onCheckedChange={(enabled) =>
                      toggle.mutate({ id: plugin.id, enabled })
                    }
                  />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-12 text-center text-sm text-muted-foreground">
            没有已安装插件。
          </p>
        )}
      </Surface>
    </>
  );
  if (embedded) return body;
  return (
    <PageFrame>
      <PageIntro
        description="展示当前工作区后端返回的 Provider 与 Plugin 记录。"
        eyebrow="Extension registry"
        title="扩展与可信组件中心"
      />
      {body}
    </PageFrame>
  );
}

function InstallMcpDialog({
  busy,
  onInstall,
}: {
  busy: boolean;
  onInstall: (payload: MCPServerCreate) => void;
}) {
  const [open, setOpen] = useState(false);
  const [serverKey, setServerKey] = useState("");
  const [serverKeyError, setServerKeyError] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [source, setSource] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [endpoint, setEndpoint] = useState("");
  const [bearerToken, setBearerToken] = useState("");
  const [tools, setTools] = useState("");
  const [permissions, setPermissions] = useState("network");
  const [registryQuery, setRegistryQuery] = useState("");
  const [registryItems, setRegistryItems] = useState<McpRegistrySearchItem[]>(
    [],
  );
  const [registryCursor, setRegistryCursor] = useState<string | null>(null);
  const [registrySearching, setRegistrySearching] = useState(false);
  const loadRegistry = async (options: { reset: boolean; q?: string }) => {
    setRegistrySearching(true);
    try {
      const result = await browseMcpRegistry({
        q: (options.q ?? registryQuery).trim() || undefined,
        cursor: options.reset ? undefined : (registryCursor ?? undefined),
        limit: 8,
      });
      setRegistryItems((previous) =>
        options.reset ? result.items : [...previous, ...result.items],
      );
      setRegistryCursor(result.next_cursor);
      if (options.reset && !result.items.length) {
        toast.info("MCP Registry 未找到匹配的服务器");
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "MCP Registry 加载失败",
      );
    } finally {
      setRegistrySearching(false);
    }
  };
  useEffect(() => {
    if (open) {
      void loadRegistry({ reset: true, q: "" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per open
  }, [open]);
  const applyRegistryItem = (item: McpRegistrySearchItem) => {
    const shortName =
      item.title || (item.name.split("/").pop() ?? item.name);
    setServerKey(
      shortName
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80),
    );
    setServerKeyError("");
    setDisplayName(shortName);
    setSource(item.repository_url || item.website_url || item.name);
    if (item.version) setVersion(item.version);
    if (item.endpoint_url) setEndpoint(item.endpoint_url);
    if (item.env_hints.length) {
      toast.message(`该服务器声明了环境变量：${item.env_hints.join(", ")}`);
    }
    toast.success(
      item.supported
        ? "已从 Registry 一键预填；请确认工具与权限后注册"
        : item.endpoint_url
          ? `已预填；注意：${item.unsupported_reason || "远程类型可能不兼容"}`
          : "已预填基础信息；该服务器未提供远程端点，需要自行填写 Endpoint",
    );
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const normalizedServerKey = serverKey.trim();
    if (!/^[a-z0-9][a-z0-9._-]{0,79}$/.test(normalizedServerKey)) {
      setServerKeyError(
        "请输入 1–80 个字符：以小写字母或数字开头，仅使用小写字母、数字、点、下划线或连字符。",
      );
      return;
    }
    setServerKeyError("");
    const requestedTools = tools
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const requiredPermissions = permissions
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    onInstall({
      server_key: normalizedServerKey,
      display_name: displayName.trim(),
      source: source.trim(),
      version: version.trim(),
      transport: "streamable_http",
      endpoint_url: endpoint.trim(),
      ...(bearerToken ? { bearer_token: bearerToken } : {}),
      manifest: {
        schema_version: "1.0",
        identity: normalizedServerKey,
        requested_tools: requestedTools,
        permissions: requiredPermissions,
        requested_resources: [],
        requested_prompts: [],
      },
    });
  };
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Network className="size-4" />
          注册 MCP
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>注册 Streamable HTTP MCP</DialogTitle>
          <DialogDescription>
            注册后仍需刷新能力快照并明确授权，才可执行真实调用。
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border bg-muted/30 p-3">
          <p className="text-xs font-medium text-muted-foreground">
            MCP 市场 · 官方 Registry（registry.modelcontextprotocol.io）——
            可注册的远程服务器支持一键预填
          </p>
          <div className="mt-2 flex gap-2">
            <Input
              onChange={(event) => setRegistryQuery(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void loadRegistry({ reset: true });
                }
              }}
              placeholder="搜索，例如 github / postgres / browser；留空浏览全部"
              value={registryQuery}
            />
            <Button
              disabled={registrySearching}
              onClick={() => void loadRegistry({ reset: true })}
              size="sm"
              type="button"
              variant="outline"
            >
              {registrySearching ? "加载中…" : "搜索"}
            </Button>
          </div>
          {registryItems.length ? (
            <div className="mt-2 max-h-64 space-y-1 overflow-auto">
              {registryItems.map((item) => (
                <button
                  className="flex w-full items-center gap-2 rounded border bg-background p-2 text-left text-xs enabled:hover:border-primary disabled:opacity-60"
                  disabled={!item.supported && !item.endpoint_url}
                  key={item.name}
                  onClick={() => applyRegistryItem(item)}
                  title={item.supported ? undefined : item.unsupported_reason}
                  type="button"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">
                      <span className="font-medium">
                        {item.title || item.name.split("/").pop()}
                      </span>
                      <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                        {item.name}
                      </span>
                    </span>
                    <span className="block truncate text-muted-foreground">
                      {item.description || item.repository_url}
                    </span>
                    {!item.supported ? (
                      <span className="block truncate text-[10px] text-amber-700 dark:text-amber-300">
                        {item.unsupported_reason}
                      </span>
                    ) : null}
                  </span>
                  {item.env_hints.length ? (
                    <Badge variant="outline">需配置密钥</Badge>
                  ) : null}
                  {item.supported ? (
                    <Badge variant="secondary">一键填入</Badge>
                  ) : item.endpoint_url ? (
                    <Badge variant="outline">{item.transport}</Badge>
                  ) : (
                    <Badge variant="outline">仅本地包</Badge>
                  )}
                </button>
              ))}
              {registryCursor ? (
                <Button
                  className="w-full"
                  disabled={registrySearching}
                  onClick={() => void loadRegistry({ reset: false })}
                  size="sm"
                  type="button"
                  variant="ghost"
                >
                  {registrySearching ? "加载中…" : "加载更多"}
                </Button>
              ) : null}
            </div>
          ) : registrySearching ? (
            <p className="mt-2 text-xs text-muted-foreground">加载中…</p>
          ) : null}
        </div>
        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              Server Key
              <Input
                aria-describedby={serverKeyError ? "mcp-server-key-error" : undefined}
                aria-invalid={serverKeyError ? true : undefined}
                autoCapitalize="none"
                maxLength={80}
                onChange={(event) => {
                  setServerKey(event.currentTarget.value);
                  if (serverKeyError) setServerKeyError("");
                }}
                pattern="[a-z0-9][a-z0-9._-]{0,79}"
                placeholder="例如 box-mcp"
                required
                spellCheck={false}
                value={serverKey}
              />
              {serverKeyError ? (
                <span
                  className="text-xs font-normal text-destructive"
                  id="mcp-server-key-error"
                >
                  {serverKeyError}
                </span>
              ) : (
                <span className="text-xs font-normal text-muted-foreground">
                  1–80 个字符，仅限小写字母、数字、点、下划线和连字符。
                </span>
              )}
            </Label>
            <Label>
              显示名称
              <Input
                onChange={(event) => setDisplayName(event.currentTarget.value)}
                required
                value={displayName}
              />
            </Label>
            <Label>
              来源
              <Input
                onChange={(event) => setSource(event.currentTarget.value)}
                placeholder="https://provider.example"
                required
                value={source}
              />
            </Label>
            <Label>
              版本
              <Input
                onChange={(event) => setVersion(event.currentTarget.value)}
                required
                value={version}
              />
            </Label>
          </div>
          <Label>
            Endpoint URL
            <Input
              onChange={(event) => setEndpoint(event.currentTarget.value)}
              placeholder="https://mcp.example/mcp"
              required
              type="url"
              value={endpoint}
            />
          </Label>
          <Label>
            请求工具（英文逗号分隔）
            <Input
              onChange={(event) => setTools(event.currentTarget.value)}
              placeholder="search,fetch"
              required
              value={tools}
            />
          </Label>
          <Label>
            权限（英文逗号分隔）
            <Input
              onChange={(event) => setPermissions(event.currentTarget.value)}
              value={permissions}
            />
          </Label>
          <Label>
            Bearer Token（可选，仅本次提交）
            <Input
              autoComplete="off"
              onChange={(event) => setBearerToken(event.currentTarget.value)}
              type="password"
              value={bearerToken}
            />
          </Label>
          <DialogFooter>
            <Button disabled={busy} type="submit">
              {busy ? "注册中…" : "注册"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function InstallSkillDialog({
  busy,
  onInstall,
}: {
  busy: boolean;
  onInstall: (payload: SkillCreate) => void;
}) {
  const [skillKey, setSkillKey] = useState("");
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [instructions, setInstructions] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onInstall({
      skill_key: skillKey.trim(),
      name: name.trim(),
      source: source.trim(),
      version: "1.0.0",
      generated_by: "user_import",
      manifest: {
        schema_version: "1.0",
        kind: "declarative_review",
        instructions_markdown: instructions.trim(),
        required_tools: ["builtin.review.list_due"],
        permissions: ["mastery.read"],
        allowed_components: [],
        input_schema: { type: "object", additionalProperties: false },
        steps: [{ tool: "builtin.review.list_due", arguments: {} }],
      },
    });
  };
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <TerminalSquare className="size-4" />
          安装声明式 Skill
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>安装复习 Skill</DialogTitle>
          <DialogDescription>
            首版只执行白名单声明式工具，不会在宿主进程执行脚本。
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              Skill Key
              <Input
                onChange={(event) => setSkillKey(event.currentTarget.value)}
                required
                value={skillKey}
              />
            </Label>
            <Label>
              名称
              <Input
                onChange={(event) => setName(event.currentTarget.value)}
                required
                value={name}
              />
            </Label>
          </div>
          <Label>
            来源
            <Input
              onChange={(event) => setSource(event.currentTarget.value)}
              required
              value={source}
            />
          </Label>
          <Label>
            指令 Markdown
            <Textarea
              onChange={(event) => setInstructions(event.currentTarget.value)}
              required
              rows={6}
              value={instructions}
            />
          </Label>
          <DialogFooter>
            <Button disabled={busy} type="submit">
              {busy ? "安装中…" : "安装"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function InvokeMcpDialog({
  busy,
  serverName,
  tools,
  onInvoke,
}: {
  busy: boolean;
  serverName: string;
  tools: string[];
  onInvoke: (tool: string, argumentsJson: Record<string, unknown>) => void;
}) {
  const [tool, setTool] = useState(tools[0] ?? "");
  const [argumentsText, setArgumentsText] = useState("{}");
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button disabled={!tools.length} size="xs" variant="outline">
          手动测试
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>调用 {serverName}</DialogTitle>
          <DialogDescription>
            参数会按服务端快照 Schema 校验，调用结果与 Hash 进入审计记录。
          </DialogDescription>
        </DialogHeader>
        <Label>
          工具
          <select
            className="mt-2 h-9 w-full rounded-md border bg-background px-3 text-sm"
            onChange={(event) => setTool(event.currentTarget.value)}
            value={tool}
          >
            {tools.map((item) => (
              <option key={item}>{item}</option>
            ))}
          </select>
        </Label>
        <Label>
          JSON 参数
          <Textarea
            className="font-mono"
            onChange={(event) => setArgumentsText(event.currentTarget.value)}
            rows={8}
            value={argumentsText}
          />
        </Label>
        <DialogFooter>
          <Button
            disabled={busy || !tool}
            onClick={() => {
              try {
                const parsed = JSON.parse(argumentsText) as unknown;
                if (
                  !parsed ||
                  Array.isArray(parsed) ||
                  typeof parsed !== "object"
                )
                  throw new Error("参数必须是 JSON 对象");
                onInvoke(tool, parsed as Record<string, unknown>);
              } catch (error) {
                toast.error(
                  error instanceof Error ? error.message : "JSON 参数无效",
                );
              }
            }}
          >
            {busy ? "调用中…" : "确认调用"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditMcpDialog({
  busy,
  server,
  onSave,
}: {
  busy: boolean;
  server: MCPServer;
  onSave: (payload: Parameters<typeof updateMcpServer>[1]) => void;
}) {
  const manifest = server.manifest_json as Partial<{
    schema_version: "1.0";
    identity: string;
    requested_tools: string[];
    permissions: string[];
    requested_resources: string[];
    requested_prompts: string[];
  }>;
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState(server.display_name);
  const [source, setSource] = useState(server.source);
  const [version, setVersion] = useState(server.version);
  const [endpoint, setEndpoint] = useState(server.endpoint_url ?? "");
  const [bearerToken, setBearerToken] = useState("");
  const [clearBearerToken, setClearBearerToken] = useState(false);
  const [tools, setTools] = useState(server.requested_tools.join(", "));
  const [permissions, setPermissions] = useState(
    server.required_permissions.join(", "),
  );

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="xs" variant="outline">
          <Pencil className="size-3" />
          编辑
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>编辑 {server.display_name}</DialogTitle>
          <DialogDescription>
            修改连接或能力声明后请重新探测。Server Key 与传输类型不可修改。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            onSave({
              display_name: displayName.trim(),
              source: source.trim(),
              version: version.trim(),
              endpoint_url: endpoint.trim() || null,
              ...(bearerToken ? { bearer_token: bearerToken } : {}),
              clear_bearer_token: clearBearerToken,
              manifest: {
                schema_version: "1.0",
                identity: manifest.identity ?? server.server_key,
                requested_tools: tools.split(",").map((item) => item.trim()).filter(Boolean),
                permissions: permissions.split(",").map((item) => item.trim()).filter(Boolean),
                requested_resources: manifest.requested_resources ?? [],
                requested_prompts: manifest.requested_prompts ?? [],
              },
            });
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              显示名称
              <Input required value={displayName} onChange={(event) => setDisplayName(event.currentTarget.value)} />
            </Label>
            <Label>
              版本
              <Input required value={version} onChange={(event) => setVersion(event.currentTarget.value)} />
            </Label>
          </div>
          <Label>
            来源
            <Input required value={source} onChange={(event) => setSource(event.currentTarget.value)} />
          </Label>
          <Label>
            Endpoint URL
            <Input required={server.transport === "streamable_http"} value={endpoint} onChange={(event) => setEndpoint(event.currentTarget.value)} />
          </Label>
          <Label>
            工具（逗号分隔）
            <Input value={tools} onChange={(event) => setTools(event.currentTarget.value)} />
          </Label>
          <Label>
            权限（逗号分隔）
            <Input value={permissions} onChange={(event) => setPermissions(event.currentTarget.value)} />
          </Label>
          <Label>
            新 Bearer Token（留空则保持不变）
            <Input disabled={clearBearerToken} type="password" value={bearerToken} onChange={(event) => setBearerToken(event.currentTarget.value)} />
          </Label>
          {server.auth_configured ? (
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Switch checked={clearBearerToken} onCheckedChange={setClearBearerToken} />
              清除现有 Bearer Token
            </label>
          ) : null}
          <DialogFooter>
            <Button disabled={busy} type="submit">
              {busy ? "保存中…" : "保存配置"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function BuiltinToolDetailDialog({
  tool,
  onOpenChange,
}: {
  tool: BuiltinMcpTool | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [locale, setLocale] = useState<"zh" | "en">("zh");
  const [copied, setCopied] = useState(false);
  const parametersText = tool
    ? JSON.stringify(tool.parameters, null, 2)
    : "";
  const zhAvailable = Boolean(tool?.description_zh);
  const description =
    locale === "zh" && tool?.description_zh
      ? tool.description_zh
      : tool?.description ?? "";
  const descriptionLabel =
    locale === "zh"
      ? zhAvailable
        ? "中文"
        : "中文（暂无译文，显示英文原文）"
      : "English";

  return (
    <Dialog onOpenChange={onOpenChange} open={tool !== null}>
      <DialogContent
        aria-describedby={undefined}
        className="max-h-[90vh] overflow-y-auto p-0 sm:max-w-2xl"
      >
        <DialogHeader>
          <DialogTitle className="sr-only">
            {tool ? `系统工具 · ${tool.tool}` : "系统工具详情"}
          </DialogTitle>
        </DialogHeader>
        {tool ? (
          <div className="space-y-4 p-5">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-mono text-sm font-semibold">{tool.tool}</p>
                  <Badge variant="secondary">系统</Badge>
                </div>
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  function: {tool.function_name}
                </p>
              </div>
            </div>

            <div>
              <Tabs
                onValueChange={(value) => setLocale(value as "zh" | "en")}
                value={locale}
              >
                <TabsList>
                  <TabsTrigger value="zh">{descriptionLabel}</TabsTrigger>
                  <TabsTrigger value="en">English</TabsTrigger>
                </TabsList>
                <TabsContent className="mt-3" value="zh">
                  <p className="text-sm leading-relaxed">{description}</p>
                </TabsContent>
                <TabsContent className="mt-3" value="en">
                  <p className="text-sm leading-relaxed">{tool.description}</p>
                </TabsContent>
              </Tabs>
            </div>

            <div>
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-muted-foreground">
                  parameters（协议内容）
                </p>
                <Button
                  onClick={() => {
                    void navigator.clipboard
                      .writeText(parametersText)
                      .then(() => {
                        setCopied(true);
                        toast.success("已复制 parameters JSON");
                        window.setTimeout(() => setCopied(false), 1500);
                      })
                      .catch(() => toast.error("复制失败"));
                  }}
                  size="xs"
                  variant="ghost"
                >
                  {copied ? "已复制" : "复制"}
                </Button>
              </div>
              <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-muted/60 p-3 font-mono text-xs leading-relaxed">
                {parametersText}
              </pre>
            </div>

            <div>
              <p className="text-xs font-medium text-muted-foreground">权限</p>
              {tool.permissions.length ? (
                <div className="mt-2 flex flex-wrap gap-1">
                  {tool.permissions.map((permission) => (
                    <Badge key={permission} variant="outline">{permission}</Badge>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-xs text-muted-foreground">无需额外权限</p>
              )}
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export function ToolsPage({
  embedded = false,
  focus = "all",
}: {
  embedded?: boolean;
  /** When embedded in the hub, show only skills, only mcp, or both. */
  focus?: "all" | "skills" | "mcp";
}) {
  const queryClient = useQueryClient();
  const showMcp = focus === "all" || focus === "mcp";
  const showSkills = focus === "all" || focus === "skills";
  const servers = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: listMcpServers,
  });
  const builtinTools = useQuery({
    queryKey: ["builtin-mcp-tools"],
    queryFn: listBuiltinMcpTools,
    enabled: showMcp,
  });
  const skills = useQuery({ queryKey: ["skills"], queryFn: listSkills });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    void queryClient.invalidateQueries({ queryKey: ["skills"] });
    void queryClient.invalidateQueries({ queryKey: ["builtin-mcp-tools"] });
  };
  const installMcp = useMutation({
    mutationFn: registerMcpServer,
    onSuccess: () => {
      toast.success("MCP 已注册，请刷新能力并授权");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const installSkillMutation = useMutation({
    mutationFn: installSkill,
    onSuccess: () => {
      toast.success("Skill 已安装，请明确授权后使用");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const [editingSkillId, setEditingSkillId] = useState<string | null>(null);
  const [skillSearch, setSkillSearch] = useState("");
  const [viewingBuiltinTool, setViewingBuiltinTool] = useState<BuiltinMcpTool | null>(
    null,
  );
  const [deleteTarget, setDeleteTarget] = useState<Skill | null>(null);
  const [deleteRequest, setDeleteRequest] =
    useState<SkillDeleteRequest | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deletePassword, setDeletePassword] = useState("");
  const requestSkillDeleteMutation = useMutation({
    mutationFn: (skill: Skill) => requestSkillDeletion(skill.id),
    onSuccess: (request, skill) => {
      setDeleteTarget(skill);
      setDeleteRequest(request);
      setDeleteConfirmation("");
      setDeletePassword("");
    },
    onError: (error) => toast.error(error.message),
  });
  const confirmSkillDeleteMutation = useMutation({
    mutationFn: () => {
      if (!deleteRequest) throw new Error("删除确认请求不存在或已过期");
      return confirmSkillDeletion(
        deleteRequest.id,
        deleteConfirmation,
        deletePassword,
      );
    },
    onSuccess: () => {
      toast.success("Skill 已删除");
      setEditingSkillId(null);
      setDeleteTarget(null);
      setDeleteRequest(null);
      setDeleteConfirmation("");
      setDeletePassword("");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const updateCheckMutation = useMutation({
    mutationFn: checkSkillUpdate,
    onSuccess: (result) => {
      if (!result.supported) {
        toast.info(result.message || "此 Skill 不支持上游更新检查");
        return;
      }
      if (!result.update_available) {
        toast.success(
          `已是最新（${result.current_commit.slice(0, 12)} @ ${result.checked_ref}）`,
        );
        return;
      }
      if (
        window.confirm(
          `发现上游更新：${result.current_commit.slice(0, 12)} → ${result.latest_commit.slice(0, 12)}。\n升级会替换包内容并需要重新授权，继续？`,
        )
      ) {
        upgradeSkillMutation.mutate(result.skill_id);
      }
    },
    onError: (error) => toast.error(error.message),
  });
  const upgradeSkillMutation = useMutation({
    mutationFn: upgradeSkill,
    onSuccess: (skill) => {
      toast.success(`已升级到 ${skill.version}，请重新授权`);
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const capabilityRefresh = useMutation({
    mutationFn: refreshMcpServer,
    onSuccess: (result) => {
      toast.success(
        result.snapshot.changed
          ? "能力快照已更新，需要重新授权"
          : "能力快照已核验",
      );
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const updateMcpMutation = useMutation({
    mutationFn: ({
      serverId,
      payload,
    }: {
      serverId: string;
      payload: Parameters<typeof updateMcpServer>[1];
    }) => updateMcpServer(serverId, payload),
    onSuccess: () => {
      toast.success("MCP 配置已保存，请重新探测能力");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const deleteMcpMutation = useMutation({
    mutationFn: deleteMcpServer,
    onSuccess: () => {
      toast.success("MCP Server 已删除");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const decide = useMutation({
    mutationFn: ({
      targetType,
      targetId,
      decision,
      permissions,
    }: {
      targetType: "mcp" | "skill";
      targetId: string;
      decision: PermissionDecision;
      permissions: string[];
    }) =>
      targetType === "mcp"
        ? authorizeMcpServer(targetId, decision, permissions)
        : authorizeSkill(targetId, decision, permissions),
    onSuccess: (grant) => {
      const decisionLabel =
        grant.decision === "always"
          ? "已启用（总是允许）"
          : grant.decision === "allow_once"
            ? "允许一次"
            : "已禁用";
      toast.success(`权限决定已保存：${decisionLabel}`);
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const invokeMcp = useMutation({
    mutationFn: ({
      serverId,
      tool,
      argumentsJson,
    }: {
      serverId: string;
      tool: string;
      argumentsJson: Record<string, unknown>;
    }) => invokeMcpTool(serverId, tool, argumentsJson),
    onSuccess: (invocation) => {
      toast.success(`调用已记录：${invocation.status}`);
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const invokeSkillMutation = useMutation({
    mutationFn: invokeSkill,
    onSuccess: (invocation) => {
      toast.success(`Skill 调用已记录：${invocation.status}`);
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const revoke = useMutation({
    mutationFn: ({
      targetType,
      targetId,
    }: {
      targetType: "mcp" | "skill";
      targetId: string;
    }) =>
      targetType === "mcp"
        ? revokeMcpServer(targetId).then(() => undefined)
        : revokeSkill(targetId).then(() => undefined),
    onSuccess: () => {
      toast.success("授权与启用状态已撤销");
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const queries = [servers, skills, ...(showMcp ? [builtinTools] : [])];
  if (queries.some((query) => query.isPending))
    return embedded ? (
      <LoadingState />
    ) : (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  const queryError = queries.find((query) => query.isError)?.error;
  if (queryError)
    return embedded ? (
      <ErrorState message={queryError.message} />
    ) : (
      <PageFrame>
        <ErrorState message={queryError.message} />
      </PageFrame>
    );

  const actions = (
    <div className="flex flex-wrap gap-2">
      {showMcp ? (
        <InstallMcpDialog
          busy={installMcp.isPending}
          onInstall={(payload) => installMcp.mutate(payload)}
        />
      ) : null}
      {showSkills ? (
        <>
          <AddSkillDialog
            onCreatedPackage={(skill) => {
              setEditingSkillId(skill.id);
              refresh();
            }}
            onInstalled={refresh}
          />
          <InstallSkillDialog
            busy={installSkillMutation.isPending}
            onInstall={(payload) => installSkillMutation.mutate(payload)}
          />
        </>
      ) : null}
    </div>
  );

  const body = (
    <>
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !confirmSkillDeleteMutation.isPending) {
            setDeleteTarget(null);
            setDeleteRequest(null);
            setDeleteConfirmation("");
            setDeletePassword("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>永久删除 Skill</DialogTitle>
            <DialogDescription>
              此操作不可恢复，并可能影响依赖该 Skill 的智能体或工作流。
              必须由你本人完成二次确认；智能体不能代替你点击确认。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <Label>
              输入 Skill 名称“{deleteTarget?.name ?? ""}”
              <Input
                autoComplete="off"
                onChange={(event) =>
                  setDeleteConfirmation(event.currentTarget.value)
                }
                value={deleteConfirmation}
              />
            </Label>
            <Label>
              输入当前账户密码
              <Input
                autoComplete="current-password"
                onChange={(event) => setDeletePassword(event.currentTarget.value)}
                type="password"
                value={deletePassword}
              />
            </Label>
          </div>
          <DialogFooter>
            <Button
              disabled={
                confirmSkillDeleteMutation.isPending ||
                deleteConfirmation !== (deleteTarget?.name ?? "") ||
                deletePassword.length === 0
              }
              onClick={(event) => {
                if (!event.isTrusted) return;
                confirmSkillDeleteMutation.mutate();
              }}
              variant="destructive"
            >
              {confirmSkillDeleteMutation.isPending
                ? "正在删除…"
                : "由我本人确认永久删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {!embedded ? (
        <PageIntro
          actions={actions}
          description="安装、能力刷新、允许一次/总是/拒绝、真实调用、撤销和审计均由服务端工作区控制面执行。本页已并入扩展中心。"
          eyebrow="Tools & skills"
          title="MCP 与 Skills 管理"
        />
      ) : (
        <div className="flex flex-wrap items-center justify-end gap-3">
          {actions}
        </div>
      )}
      {showMcp ? (
      <>
      <Surface className="overflow-hidden">
        <div className="border-b p-5">
          <SectionHeading
            description={`${builtinTools.data?.length ?? 0} 个第一方工具 · 随系统提供，只读`}
            title="系统自带 MCP"
          />
        </div>
        {builtinTools.data?.length ? (
          <div className="grid gap-px bg-border sm:grid-cols-2 lg:grid-cols-3">
            {builtinTools.data.map((tool) => (
              <button
                className="group bg-background p-4 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                key={tool.tool}
                onClick={() => setViewingBuiltinTool(tool)}
                type="button"
              >
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate font-mono text-sm font-semibold">{tool.tool}</p>
                      <Badge variant="secondary">系统</Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {tool.description_zh ?? tool.description}
                    </p>
                    {tool.permissions.length ? (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {tool.permissions.map((permission) => (
                          <Badge key={permission} variant="outline">{permission}</Badge>
                        ))}
                      </div>
                    ) : null}
                    <p className="mt-2 text-[11px] text-muted-foreground/70 opacity-0 transition-opacity group-hover:opacity-100">
                      点击查看协议内容 →
                    </p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <p className="py-10 text-center text-sm text-muted-foreground">暂无系统自带 MCP 工具</p>
        )}
      </Surface>
      <Surface className="overflow-hidden">
        <div className="border-b p-5">
          <SectionHeading
            description={`${servers.data?.length ?? 0} 个用户配置`}
            title="用户配置的 MCP"
          />
        </div>
        {servers.data?.length ? (
          <div className="divide-y">
            {servers.data.map((server) => (
              <div className="space-y-3 p-5" key={server.id}>
                <div className="flex flex-wrap items-start gap-3">
                  <Network className="mt-1 size-4 text-primary" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-semibold">{server.display_name}</p>
                      <StatePill status={server.status} />
                    </div>
                    <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                      {server.endpoint_url ?? "stdio runner 未配置"}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {server.requested_tools.map((tool) => (
                        <Badge key={tool} variant="secondary">
                          {tool}
                        </Badge>
                      ))}
                    </div>
                    {server.last_error ? (
                      <p className="mt-2 text-xs text-destructive">
                        {server.last_error}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      disabled={capabilityRefresh.isPending}
                      onClick={() => capabilityRefresh.mutate(server.id)}
                      size="xs"
                      variant="outline"
                    >
                      <RefreshCcw className="size-3" />
                      探测
                    </Button>
                    <InvokeMcpDialog
                      busy={invokeMcp.isPending}
                      onInvoke={(tool, argumentsJson) =>
                        invokeMcp.mutate({
                          serverId: server.id,
                          tool,
                          argumentsJson,
                        })
                      }
                      serverName={server.display_name}
                      tools={server.requested_tools}
                    />
                    <EditMcpDialog
                      busy={updateMcpMutation.isPending}
                      onSave={(payload) =>
                        updateMcpMutation.mutate({ serverId: server.id, payload })
                      }
                      server={server}
                    />
                    <label className="flex cursor-pointer items-center gap-1.5 px-1 text-xs text-muted-foreground">
                      <Switch
                        aria-label={`启用或禁用 ${server.display_name}`}
                        checked={server.enabled}
                        disabled={decide.isPending || revoke.isPending}
                        onCheckedChange={(checked) => {
                          if (checked) {
                            decide.mutate({
                              targetType: "mcp",
                              targetId: server.id,
                              decision: "always",
                              permissions: server.required_permissions,
                            });
                          } else {
                            revoke.mutate({ targetType: "mcp", targetId: server.id });
                          }
                        }}
                      />
                      {server.enabled ? "已启用" : "已停用"}
                    </label>
                    <Button
                      className="text-destructive hover:text-destructive"
                      disabled={deleteMcpMutation.isPending}
                      onClick={() => {
                        if (
                          window.confirm(
                            `删除 MCP Server「${server.display_name}」？将同时移除其凭据、能力快照与授权记录。`,
                          )
                        ) {
                          deleteMcpMutation.mutate(server.id);
                        }
                      }}
                      size="xs"
                      variant="ghost"
                    >
                      <Trash2 className="size-3" />
                      删除
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-12 text-center text-sm text-muted-foreground">
            尚未配置用户 MCP 服务
          </p>
        )}
      </Surface>
      </>
      ) : null}
      {showSkills ? (
      <Surface className="overflow-hidden">
        <div className="border-b p-5">
          <SectionHeading
            description={`${skills.data?.length ?? 0} 个已安装 · 授权后可用`}
            title="已安装 Skills"
          />
          {(skills.data?.length ?? 0) > 0 ? (
            <div className="relative mt-3">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="pl-9"
                onChange={(event) => setSkillSearch(event.currentTarget.value)}
                placeholder="搜索名称、skill key、来源…"
                value={skillSearch}
              />
            </div>
          ) : null}
        </div>
        {skills.data?.length ? (
          (() => {
            const keyword = skillSearch.trim().toLowerCase();
            const matchesSearch = (skill: Skill) =>
              !keyword ||
              `${skill.name} ${skill.skill_key} ${skill.source}`
                .toLowerCase()
                .includes(keyword);
            const officialSkills = skills.data.filter(
              (skill) => skill.is_official && matchesSearch(skill),
            );
            const userSkills = skills.data.filter(
              (skill) => !skill.is_official && matchesSearch(skill),
            );
            const renderSkillRow = (skill: Skill) => (
              <div
                className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center"
                key={skill.id}
              >
                <TerminalSquare className="size-4 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold">{skill.name}</p>
                    <StatePill status={skill.status} />
                    {skill.is_official ? (
                      <Badge className="gap-1" variant="default">
                        <ShieldCheck className="size-3" />
                        官方
                      </Badge>
                    ) : null}
                    {skill.kind === "agent_skill_package" ||
                    skill.package_format === "skill_md_v1" ? (
                      <Badge variant="secondary">文件包</Badge>
                    ) : (
                      <Badge variant="outline">声明式</Badge>
                    )}
                    {skill.has_scripts ? (
                      <Badge variant="secondary">scripts</Badge>
                    ) : null}
                    {(() => {
                      const scan = (
                        skill.validation_report as {
                          security_scan?: { risk_level?: string };
                        }
                      )?.security_scan;
                      const risk = scan?.risk_level;
                      if (risk !== "high" && risk !== "medium") return null;
                      return (
                        <Badge
                          variant={risk === "high" ? "destructive" : "outline"}
                        >
                          风险{risk === "high" ? "高" : "中"}
                        </Badge>
                      );
                    })()}
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                    {skill.skill_key} · v{skill.version} · {skill.source}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  {skill.is_official ? (
                    <span className="text-xs text-muted-foreground">
                      系统管理 · 自动启用
                    </span>
                  ) : (
                    <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
                      <Switch
                        aria-label={`启用或禁用 ${skill.name}`}
                        checked={skill.status === "enabled"}
                        disabled={decide.isPending}
                        onCheckedChange={(checked) =>
                          decide.mutate({
                            targetType: "skill",
                            targetId: skill.id,
                            decision: checked ? "always" : "deny",
                            permissions: checked
                              ? skill.required_permissions
                              : [],
                          })
                        }
                      />
                      {skill.status === "enabled" ? "启用" : "禁用"}
                    </label>
                  )}
                  {skill.kind === "agent_skill_package" ||
                  skill.package_format === "skill_md_v1" ? (
                    <Button
                      onClick={() => setEditingSkillId(skill.id)}
                      size="xs"
                      variant="outline"
                    >
                      {skill.is_official ? "查看" : "编辑"}
                    </Button>
                  ) : (
                    <Button
                      disabled={invokeSkillMutation.isPending}
                      onClick={() => invokeSkillMutation.mutate(skill.id)}
                      size="xs"
                      variant="outline"
                    >
                      运行
                    </Button>
                  )}
                  {skill.origin_type === "github_import" ? (
                    <Button
                      disabled={
                        updateCheckMutation.isPending ||
                        upgradeSkillMutation.isPending
                      }
                      onClick={() => updateCheckMutation.mutate(skill.id)}
                      size="xs"
                      variant="outline"
                    >
                      <RefreshCcw className="size-3" />
                      {upgradeSkillMutation.isPending ? "升级中…" : "更新"}
                    </Button>
                  ) : null}
                  {!skill.is_official ? (
                    <Button
                      disabled={requestSkillDeleteMutation.isPending}
                      onClick={() => requestSkillDeleteMutation.mutate(skill)}
                      size="xs"
                      variant="ghost"
                    >
                      <Trash2 className="size-3" />
                      删除
                    </Button>
                  ) : null}
                </div>
              </div>
            );
            if (!officialSkills.length && !userSkills.length) {
              return (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  没有匹配「{skillSearch.trim()}」的 Skill。
                </p>
              );
            }
            return (
              <div className="max-h-[420px] overflow-y-auto">
                {officialSkills.length ? (
                  <div>
                    <p className="border-b bg-muted/40 px-4 py-2 text-xs font-medium text-muted-foreground">
                      官方 Skills · LearnGraph 内置工作流（图谱生成、路线规划、复习等，系统管理）
                    </p>
                    <div className="divide-y">
                      {officialSkills.map(renderSkillRow)}
                    </div>
                  </div>
                ) : null}
                {userSkills.length ? (
                  <div>
                    <p className="border-y bg-muted/40 px-4 py-2 text-xs font-medium text-muted-foreground">
                      用户 Skills · 市场安装 / 导入 / 自建
                    </p>
                    <div className="divide-y">{userSkills.map(renderSkillRow)}</div>
                  </div>
                ) : null}
              </div>
            );
          })()
        ) : (
          <p className="py-12 text-center text-sm text-muted-foreground">
            尚未安装 Skill。可从市场安装，或创建文件包 / 声明式 Skill。
          </p>
        )}
      </Surface>
      ) : null}
      {showSkills
        ? (() => {
            const skill = skills.data?.find(
              (item) => item.id === editingSkillId,
            );
            return (
              <Dialog
                onOpenChange={(nextOpen) => {
                  if (!nextOpen) setEditingSkillId(null);
                }}
                open={Boolean(skill)}
              >
                <DialogContent
                  aria-describedby={undefined}
                  className="max-h-[90vh] overflow-y-auto p-0 sm:max-w-4xl [&>.surface]:rounded-none [&>.surface]:border-0 [&>.surface]:shadow-none"
                >
                  <DialogTitle className="sr-only">
                    {skill ? `文件包 · ${skill.name}` : "Skill 详情"}
                  </DialogTitle>
                  {skill ? (
                    <SkillPackageEditor
                      key={skill.id}
                      skill={skill}
                    />
                  ) : null}
                </DialogContent>
              </Dialog>
            );
          })()
        : null}
      <BuiltinToolDetailDialog
        onOpenChange={(open) => {
          if (!open) setViewingBuiltinTool(null);
        }}
        tool={viewingBuiltinTool}
      />
    </>
  );
  if (embedded) return <div className="space-y-5">{body}</div>;
  return <PageFrame>{body}</PageFrame>;
}

export function ResearchSettingsPage() {
  const { workspaceId = "" } = useParams();
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
  const providerCatalog = useQuery({
    queryKey: ["provider-catalog"],
    queryFn: listProviderCatalog,
  });
  if (providers.isPending || providerCatalog.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (providers.isError || providerCatalog.isError)
    return (
      <PageFrame>
        <ErrorState
          message={
            (providers.error ?? providerCatalog.error)?.message ??
            "Provider 目录读取失败"
          }
        />
      </PageFrame>
    );
  const rolesByType = new Map(
    providerCatalog.data.map((item) => [item.provider_type, item.role]),
  );
  const groups = [
    { icon: Search, title: "普通搜索 Provider", role: "search" },
    { icon: Network, title: "正文抓取 FetchProvider", role: "fetch" },
    { icon: FileSearch, title: "DeepResearch Provider", role: "deep_research" },
  ] satisfies {
    icon: typeof Search;
    title: string;
    role: Extract<ProviderRole, "search" | "fetch" | "deep_research">;
  }[];
  const groupedProviders = groups.map((group) => ({
    ...group,
    providers: providers.data.filter((provider) =>
      rolesByType.get(provider.provider_type) === group.role,
    ),
  }));
  return (
    <PageFrame>
      <PageIntro
        description="SearchProvider、FetchProvider 与 DeepResearchProvider 独立记录；缺失的远程能力明确显示为未配置。"
        eyebrow="Research providers"
        title="搜索与 Deep Research 设置"
      />
      <Surface className="p-5">
        <SectionHeading
          description="实例和状态均来自当前工作区 Provider API"
          title="Provider 分层"
        />
        <div className="mt-5 grid gap-4 lg:grid-cols-3">
          {groupedProviders.map((group) => {
            const Icon = group.icon;
            return (
              <div className="rounded-xl border p-4" key={group.title}>
                <div className="flex items-center gap-2">
                  <Icon className="size-4 text-primary" />
                  <p className="text-sm font-semibold">{group.title}</p>
                </div>
                <div className="mt-4 space-y-2">
                  {group.providers.length ? (
                    group.providers.map((provider) => (
                      <div
                        className="rounded-lg border bg-muted/20 px-3 py-2 text-xs"
                        key={provider.id}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-medium">
                            {provider.display_name}
                          </span>
                          <StatePill
                            status={
                              provider.enabled ? provider.status : "disabled"
                            }
                          />
                        </div>
                        <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                          {provider.base_url ?? "未设置 Base URL"}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-lg border border-dashed px-3 py-5 text-center text-xs text-muted-foreground">
                      未配置
                    </p>
                  )}
                </div>
                <Button asChild className="mt-4" size="xs" variant="outline">
                  <Link to={`/w/${workspaceId}/settings/providers`}>
                    管理 Provider
                  </Link>
                </Button>
              </div>
            );
          })}
        </div>
      </Surface>
      <Surface className="space-y-4 p-5">
        <SectionHeading
          description="统一管理普通联网搜索与 Deep Research 可使用的工作区来源域名。"
          title="搜索与 Deep Research 来源白名单"
        />
        <DomainAllowlistEditor />
      </Surface>
      <Surface className="p-5">
        <SectionHeading title="当前配置边界" />
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {groupedProviders.map((group) => (
            <div className="rounded-xl border p-4" key={group.title}>
              <p className="text-xs text-muted-foreground">{group.title}</p>
              <p className="mt-2 text-2xl font-semibold">
                {group.providers.length}
              </p>
              <p className="mt-1 text-[10px] text-muted-foreground">
                其中
                {
                  group.providers.filter(
                    (provider) =>
                      provider.enabled && provider.remote_capability,
                  ).length
                }{" "}
                个已声明远程能力
              </p>
            </div>
          ))}
        </div>
      </Surface>
    </PageFrame>
  );
}
