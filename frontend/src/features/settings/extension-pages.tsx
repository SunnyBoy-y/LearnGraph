import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileSearch,
  Languages,
  Network,
  PackageCheck,
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
  createSkillPackage,
  deleteSkill,
  installSkill,
  invokeMcpTool,
  invokeSkill,
  listExtensionGrants,
  listExtensionInvocations,
  listMcpServers,
  listPlugins,
  listProviderCatalog,
  listProviders,
  listSkills,
  refreshMcpServer,
  registerMcpServer,
  revokeMcpServer,
  revokeSkill,
  togglePlugin,
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
import { RuntimeControlsBody } from "@/features/settings/control-pages";
import { SkillPackageEditor } from "@/features/settings/skill-package-editor";
import {
  DEFAULT_SKILL_MD_TEMPLATE,
  SkillTranslateDialog,
  SkillsHubExtras,
} from "@/features/settings/skills-hub-extras";
import type {
  MCPServerCreate,
  PermissionDecision,
  SkillCreate,
} from "@/types/extensions";
import type { ProviderRole } from "@/types/providers";

/** Hub tabs for D-076 unified Extensions Center. */
export type ExtensionsHubTab =
  | "overview"
  | "skills"
  | "mcp"
  | "components"
  | "plugins"
  | "audit";

const HUB_TABS: Array<{
  value: ExtensionsHubTab;
  label: string;
  description: string;
}> = [
  {
    value: "overview",
    label: "总览",
    description: "Provider、Plugin 与能力健康摘要",
  },
  {
    value: "skills",
    label: "Skills Hub",
    description: "已安装 Skill、声明式安装与授权",
  },
  {
    value: "mcp",
    label: "MCP",
    description: "注册、刷新能力、授权与调用",
  },
  {
    value: "components",
    label: "可信组件",
    description: "Manifest、授权、检查与 Artifact",
  },
  {
    value: "plugins",
    label: "插件",
    description: "工作区 Plugin 启停",
  },
  {
    value: "audit",
    label: "运行与审计",
    description: "MCP/Skill 修订、组件与 Sandbox 控制面",
  },
];

function normalizeHubTab(raw: string | null): ExtensionsHubTab {
  const value = (raw ?? "overview") as ExtensionsHubTab;
  if (HUB_TABS.some((tab) => tab.value === value)) return value;
  return "overview";
}

/** Unified Extensions Center (D-076): single entry for overview, Skills, MCP, components, plugins, runtime. */
export function ExtensionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = normalizeHubTab(searchParams.get("tab"));
  const activeMeta = HUB_TABS.find((item) => item.value === tab) ?? HUB_TABS[0];

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
        description="MCP、Skills、插件、可信组件与运行审计的统一入口。安装、秘密、权限、扫描与沙箱执行均由后端完成。"
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
        <p className="mt-3 text-xs text-muted-foreground">
          {activeMeta.description}
        </p>
        <TabsContent className="mt-5 space-y-5" value="overview">
          <ExtensionsOverviewPanel embedded />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="skills">
          <ToolsPage embedded focus="skills" />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="mcp">
          <ToolsPage embedded focus="mcp" />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="components">
          <RuntimeControlsBody defaultTab="components" />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="plugins">
          <ExtensionsOverviewPanel embedded pluginsOnly />
        </TabsContent>
        <TabsContent className="mt-5 space-y-5" value="audit">
          <RuntimeControlsBody defaultTab="mcp" />
        </TabsContent>
      </Tabs>
    </PageFrame>
  );
}

export function ExtensionsOverviewPanel({
  embedded = false,
  pluginsOnly = false,
}: {
  embedded?: boolean;
  pluginsOnly?: boolean;
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
      {!pluginsOnly ? (
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
              <Link to={`/w/${workspaceId}/settings/extensions?tab=audit`}>
                <TerminalSquare className="size-4" />
                运行与审计
              </Link>
            </Button>
          </div>
        </Surface>
      ) : null}
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

function CreatePackageDialog({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (payload: {
    skill_key: string;
    name: string;
    description: string;
    with_sample_script: boolean;
  }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [skillKey, setSkillKey] = useState("my-skill");
  const [name, setName] = useState("My skill");
  const [description, setDescription] = useState(
    "Use when the user needs this capability. Include trigger phrases so the agent matches correctly.",
  );
  const [withScript, setWithScript] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onCreate({
      skill_key: skillKey.trim(),
      name: name.trim(),
      description: description.trim(),
      with_sample_script: withScript,
    });
    setOpen(false);
  };
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">
          <TerminalSquare className="size-4" />
          创建文件包 Skill
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>创建 Agent Skill 文件包</DialogTitle>
          <DialogDescription>
            生成标准 SKILL.md 模板（触发条件 · 正文 · 步骤 · 示例）。scripts 仅可在 Docker 沙箱执行。
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              Skill Key
              <Input
                className="mt-2 font-mono text-xs"
                onChange={(event) => setSkillKey(event.currentTarget.value)}
                pattern="[a-z0-9][a-z0-9._-]{1,79}"
                placeholder="my-skill"
                required
                value={skillKey}
              />
            </Label>
            <Label>
              名称
              <Input
                className="mt-2"
                onChange={(event) => setName(event.currentTarget.value)}
                required
                value={name}
              />
            </Label>
          </div>
          <Label>
            描述 / 触发条件
            <Textarea
              className="mt-2"
              onChange={(event) => setDescription(event.currentTarget.value)}
              placeholder="Describe when the agent should use this skill…"
              rows={3}
              value={description}
            />
          </Label>
          <Label className="flex items-center gap-2 text-xs">
            <input
              checked={withScript}
              onChange={(event) => setWithScript(event.currentTarget.checked)}
              type="checkbox"
            />
            附带示例 scripts/hello.py（仅沙箱可运行）
          </Label>
          <button
            className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            onClick={() => setShowPreview((value) => !value)}
            type="button"
          >
            {showPreview ? "隐藏模板预览" : "查看 SKILL.md 模板结构"}
          </button>
          {showPreview ? (
            <pre className="max-h-48 overflow-auto rounded-lg bg-muted p-3 font-mono text-[10px] leading-4">
              {DEFAULT_SKILL_MD_TEMPLATE}
            </pre>
          ) : null}
          <DialogFooter>
            <Button disabled={busy} type="submit">
              {busy ? "创建中…" : "创建"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
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
  const [displayName, setDisplayName] = useState("");
  const [source, setSource] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [endpoint, setEndpoint] = useState("");
  const [bearerToken, setBearerToken] = useState("");
  const [tools, setTools] = useState("");
  const [permissions, setPermissions] = useState("network");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const requestedTools = tools
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const requiredPermissions = permissions
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    onInstall({
      server_key: serverKey.trim(),
      display_name: displayName.trim(),
      source: source.trim(),
      version: version.trim(),
      transport: "streamable_http",
      endpoint_url: endpoint.trim(),
      ...(bearerToken ? { bearer_token: bearerToken } : {}),
      manifest: {
        schema_version: "1.0",
        identity: serverKey.trim(),
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
      <DialogContent>
        <DialogHeader>
          <DialogTitle>注册 Streamable HTTP MCP</DialogTitle>
          <DialogDescription>
            注册后仍需刷新能力快照并明确授权，才可执行真实调用。
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              Server Key
              <Input
                onChange={(event) => setServerKey(event.currentTarget.value)}
                required
                value={serverKey}
              />
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
          调用工具
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
  const skills = useQuery({ queryKey: ["skills"], queryFn: listSkills });
  const grants = useQuery({
    queryKey: ["extension-grants"],
    queryFn: listExtensionGrants,
  });
  const invocations = useQuery({
    queryKey: ["extension-invocations"],
    queryFn: listExtensionInvocations,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    void queryClient.invalidateQueries({ queryKey: ["skills"] });
    void queryClient.invalidateQueries({ queryKey: ["extension-grants"] });
    void queryClient.invalidateQueries({
      queryKey: ["extension-invocations"],
    });
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
  const [translateSkillId, setTranslateSkillId] = useState<string | null>(null);
  const createPackageMutation = useMutation({
    mutationFn: createSkillPackage,
    onSuccess: (skill) => {
      toast.success(`文件包 Skill「${skill.name}」已创建`);
      setEditingSkillId(skill.id);
      refresh();
    },
    onError: (error) => toast.error(error.message),
  });
  const deleteSkillMutation = useMutation({
    mutationFn: deleteSkill,
    onSuccess: () => {
      toast.success("Skill 已删除");
      setEditingSkillId(null);
      setTranslateSkillId(null);
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
      toast.success(`权限决定已保存：${grant.decision}`);
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
  const queries = [servers, skills, grants, invocations];
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
          <InstallSkillDialog
            busy={installSkillMutation.isPending}
            onInstall={(payload) => installSkillMutation.mutate(payload)}
          />
          <CreatePackageDialog
            busy={createPackageMutation.isPending}
            onCreate={(payload) => createPackageMutation.mutate(payload)}
          />
        </>
      ) : null}
    </div>
  );

  const body = (
    <>
      {!embedded ? (
        <PageIntro
          actions={actions}
          description="安装、能力刷新、允许一次/总是/拒绝、真实调用、撤销和审计均由服务端工作区控制面执行。本页已并入扩展中心。"
          eyebrow="Tools & skills"
          title="MCP 与 Skills 管理"
        />
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {focus === "skills"
              ? "已安装 · 市场 · 创建与授权。高级工具（本机探测 / 沙箱）默认收起。"
              : focus === "mcp"
                ? "注册 Streamable HTTP MCP、刷新能力快照并授权调用。"
                : "MCP 与 Skills 安装与授权。"}
          </p>
          {actions}
        </div>
      )}
      {showMcp ? (
      <Surface className="overflow-hidden">
        <div className="border-b p-5">
          <SectionHeading
            description={`${servers.data?.length ?? 0} 个服务`}
            title="MCP 服务"
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
                      刷新能力
                    </Button>
                    {(["allow_once", "always", "deny"] as const).map(
                      (decision) => (
                        <Button
                          disabled={decide.isPending}
                          key={decision}
                          onClick={() =>
                            decide.mutate({
                              targetType: "mcp",
                              targetId: server.id,
                              decision,
                              permissions: server.required_permissions,
                            })
                          }
                          size="xs"
                          variant={decision === "deny" ? "ghost" : "outline"}
                        >
                          {decision === "allow_once"
                            ? "允许一次"
                            : decision === "always"
                              ? "总是允许"
                              : "拒绝"}
                        </Button>
                      ),
                    )}
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
                    <Button
                      disabled={revoke.isPending}
                      onClick={() =>
                        revoke.mutate({
                          targetType: "mcp",
                          targetId: server.id,
                        })
                      }
                      size="xs"
                      variant="ghost"
                    >
                      撤销
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="py-12 text-center text-sm text-muted-foreground">
            尚未注册 MCP 服务
          </p>
        )}
      </Surface>
      ) : null}
      {showSkills ? (
      <Surface className="overflow-hidden">
        <div className="border-b p-5">
          <SectionHeading
            description={`${skills.data?.length ?? 0} 个已安装 · 授权后可用`}
            title="已安装 Skills"
          />
        </div>
        {skills.data?.length ? (
          <div className="divide-y">
            {skills.data.map((skill) => (
              <div
                className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center"
                key={skill.id}
              >
                <TerminalSquare className="size-4 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold">{skill.name}</p>
                    <StatePill status={skill.status} />
                    {skill.kind === "agent_skill_package" ||
                    skill.package_format === "skill_md_v1" ? (
                      <Badge variant="secondary">文件包</Badge>
                    ) : (
                      <Badge variant="outline">声明式</Badge>
                    )}
                    {skill.has_scripts ? (
                      <Badge variant="secondary">scripts</Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                    {skill.skill_key} · v{skill.version} · {skill.source}
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(["allow_once", "always", "deny"] as const).map(
                    (decision) => (
                      <Button
                        disabled={decide.isPending}
                        key={decision}
                        onClick={() =>
                          decide.mutate({
                            targetType: "skill",
                            targetId: skill.id,
                            decision,
                            permissions: skill.required_permissions,
                          })
                        }
                        size="xs"
                        variant={decision === "deny" ? "ghost" : "outline"}
                      >
                        {decision === "allow_once"
                          ? "允许一次"
                          : decision === "always"
                            ? "总是允许"
                            : "拒绝"}
                      </Button>
                    ),
                  )}
                  {skill.kind === "agent_skill_package" ||
                  skill.package_format === "skill_md_v1" ? (
                    <Button
                      onClick={() =>
                        setEditingSkillId(
                          editingSkillId === skill.id ? null : skill.id,
                        )
                      }
                      size="xs"
                      variant="outline"
                    >
                      {editingSkillId === skill.id ? "收起" : "编辑"}
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
                  <Button
                    onClick={() => setTranslateSkillId(skill.id)}
                    size="xs"
                    variant="outline"
                  >
                    <Languages className="size-3" />
                    翻译
                  </Button>
                  <Button
                    disabled={revoke.isPending}
                    onClick={() =>
                      revoke.mutate({
                        targetType: "skill",
                        targetId: skill.id,
                      })
                    }
                    size="xs"
                    variant="ghost"
                  >
                    撤销
                  </Button>
                  <Button
                    disabled={deleteSkillMutation.isPending}
                    onClick={() => {
                      if (
                        window.confirm(
                          `确定永久删除 Skill「${skill.name}」？此操作不可恢复。`,
                        )
                      ) {
                        deleteSkillMutation.mutate(skill.id);
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
            ))}
          </div>
        ) : (
          <p className="py-12 text-center text-sm text-muted-foreground">
            尚未安装 Skill。可从市场安装，或创建文件包 / 声明式 Skill。
          </p>
        )}
      </Surface>
      ) : null}
      {showSkills && editingSkillId
        ? (() => {
            const skill = skills.data?.find((item) => item.id === editingSkillId);
            return skill ? <SkillPackageEditor skill={skill} /> : null;
          })()
        : null}
      {showSkills && translateSkillId
        ? (() => {
            const skill = skills.data?.find(
              (item) => item.id === translateSkillId,
            );
            return skill ? (
              <SkillTranslateDialog
                onOpenChange={(open) => {
                  if (!open) setTranslateSkillId(null);
                }}
                open
                skill={skill}
              />
            ) : null;
          })()
        : null}
      {showSkills ? (
        <SkillsHubExtras
          onInstalled={refresh}
          skills={skills.data ?? []}
        />
      ) : null}
      {focus === "all" || focus === "mcp" ? (
      <div className="grid gap-5 lg:grid-cols-2">
        <Surface className="p-5">
          <SectionHeading title="权限决定" />
          <div className="mt-4 space-y-2">
            {grants.data?.slice(0, 8).map((grant) => (
              <div
                className="flex items-center gap-3 rounded-lg border p-3 text-xs"
                key={grant.id}
              >
                <ShieldCheck className="size-4 text-primary" />
                <span className="min-w-0 flex-1 truncate">
                  {grant.subject_type}:{grant.subject_id}
                </span>
                <StatePill status={grant.decision} />
              </div>
            ))}
            {!grants.data?.length ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                尚无权限决定
              </p>
            ) : null}
          </div>
        </Surface>
        <Surface className="p-5">
          <SectionHeading title="最近调用" />
          <div className="mt-4 space-y-2">
            {invocations.data?.slice(0, 8).map((invocation) => (
              <details className="rounded-lg border p-3" key={invocation.id}>
                <summary className="flex cursor-pointer items-center gap-3 text-xs">
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {invocation.tool_name}
                  </span>
                  <StatePill status={invocation.status} />
                </summary>
                <pre className="mt-3 max-h-48 overflow-auto rounded bg-muted p-3 text-[10px]">
                  {JSON.stringify(
                    invocation.error_message
                      ? { error: invocation.error_message }
                      : invocation.result_json,
                    null,
                    2,
                  )}
                </pre>
              </details>
            ))}
            {!invocations.data?.length ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                尚无真实调用记录
              </p>
            ) : null}
          </div>
        </Surface>
      </div>
      ) : showSkills ? (
        <details className="rounded-xl border">
          <summary className="cursor-pointer px-4 py-3 text-sm text-muted-foreground">
            权限决定与最近调用（{grants.data?.length ?? 0} 条授权 ·{" "}
            {invocations.data?.length ?? 0} 次调用）
          </summary>
          <div className="grid gap-5 border-t p-4 lg:grid-cols-2">
            <div className="space-y-2">
              {grants.data?.slice(0, 6).map((grant) => (
                <div
                  className="flex items-center gap-3 rounded-lg border p-3 text-xs"
                  key={grant.id}
                >
                  <ShieldCheck className="size-4 text-primary" />
                  <span className="min-w-0 flex-1 truncate">
                    {grant.subject_type}:{grant.subject_id}
                  </span>
                  <StatePill status={grant.decision} />
                </div>
              ))}
              {!grants.data?.length ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  尚无权限决定
                </p>
              ) : null}
            </div>
            <div className="space-y-2">
              {invocations.data?.slice(0, 6).map((invocation) => (
                <div
                  className="flex items-center gap-3 rounded-lg border p-3 text-xs"
                  key={invocation.id}
                >
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {invocation.tool_name}
                  </span>
                  <StatePill status={invocation.status} />
                </div>
              ))}
              {!invocations.data?.length ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  尚无真实调用记录
                </p>
              ) : null}
            </div>
          </div>
        </details>
      ) : null}
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
