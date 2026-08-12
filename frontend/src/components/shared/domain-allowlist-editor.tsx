import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Plus, X } from "lucide-react";
import { toast } from "sonner";

import {
  getAccessAllowlist,
  getResearchPolicy,
  updateAccessAllowlist,
  updateResearchPolicy,
} from "@/api/settings";
import {
  accessAllowlistQueryKey,
  fetchPolicyQueryKey,
  getFetchPolicy,
  researchPolicyQueryKey,
  updateFetchPolicy,
  type DomainPolicy,
} from "@/components/shared/domain-policy";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

export function DomainAllowlistEditor({
  queryKey,
  getPolicy,
  updatePolicy,
  description,
  emptyLabel,
  placeholder,
}: {
  /** React Query cache key prefix, e.g. ["research-policy"]. */
  queryKey: readonly unknown[];
  getPolicy: () => Promise<DomainPolicy>;
  updatePolicy: (policy: DomainPolicy) => Promise<DomainPolicy>;
  description: string;
  emptyLabel: string;
  placeholder: string;
}) {
  const queryClient = useQueryClient();
  const policy = useQuery({ queryKey, queryFn: getPolicy });
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const update = useMutation({
    mutationFn: updatePolicy,
    onSuccess: (next) => {
      queryClient.setQueryData(queryKey, next);
      toast.success("白名单已更新");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "白名单更新失败"),
  });

  const domains = policy.data?.allowed_domains ?? [];
  const save = (next: string[], onSuccess?: () => void) => {
    update.mutate({ allowed_domains: next }, { onSuccess });
  };
  const add = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    save([...domains, value], () => setDraft(""));
  };
  const saveEdit = () => {
    if (!editing || !editDraft.trim()) return;
    save(
      domains.map((domain) => (domain === editing ? editDraft.trim() : domain)),
      () => {
        setEditing(null);
        setEditDraft("");
      },
    );
  };

  if (policy.isPending) {
    return <p className="text-sm text-muted-foreground">正在读取工作区白名单…</p>;
  }
  if (policy.isError) {
    return (
      <div className="flex items-center justify-between gap-3 text-sm text-destructive">
        <span>{policy.error.message || "工作区白名单读取失败"}</span>
        <Button onClick={() => void policy.refetch()} size="sm" variant="outline">
          重试
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted-foreground">{description}</p>
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={add}>
        <Input
          aria-label="添加来源域名"
          disabled={update.isPending}
          onChange={(event) => setDraft(event.currentTarget.value)}
          placeholder={placeholder}
          value={draft}
        />
        <Button disabled={update.isPending || !draft.trim()} type="submit" variant="outline">
          <Plus className="size-4" />
          添加
        </Button>
      </form>
      {domains.length ? (
        <div className="divide-y rounded-lg border">
          {domains.map((domain) => {
            const isEditing = editing === domain;
            return (
              <div className="flex min-h-12 items-center gap-2 px-3 py-2" key={domain}>
                {isEditing ? (
                  <Input
                    aria-label={`编辑 ${domain}`}
                    autoFocus
                    className="h-8"
                    disabled={update.isPending}
                    onChange={(event) => setEditDraft(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        saveEdit();
                      }
                    }}
                    value={editDraft}
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{domain}</span>
                )}
                {isEditing ? (
                  <>
                    <Button aria-label={`保存 ${domain}`} disabled={update.isPending || !editDraft.trim()} onClick={saveEdit} size="icon-sm" type="button" variant="ghost">
                      <Check className="size-4" />
                    </Button>
                    <Button aria-label={`取消编辑 ${domain}`} disabled={update.isPending} onClick={() => setEditing(null)} size="icon-sm" type="button" variant="ghost">
                      <X className="size-4" />
                    </Button>
                  </>
                ) : (
                  <>
                    <Button aria-label={`编辑 ${domain}`} disabled={update.isPending} onClick={() => { setEditing(domain); setEditDraft(domain); }} size="icon-sm" type="button" variant="ghost">
                      <Pencil className="size-4" />
                    </Button>
                    <Button aria-label={`删除 ${domain}`} className="text-muted-foreground hover:text-destructive" disabled={update.isPending} onClick={() => save(domains.filter((item) => item !== domain))} size="icon-sm" type="button" variant="ghost">
                      <X className="size-4" />
                    </Button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed px-3 py-5 text-center text-sm text-muted-foreground">
          {emptyLabel}
        </p>
      )}
    </div>
  );
}

/** 搜索与 Deep Research 来源白名单（research.policy，应用层）。 */
export function ResearchDomainAllowlistEditor() {
  return (
    <DomainAllowlistEditor
      description="搜索与 Deep Research 只能使用这里列出的精确公共 DNS 域名。空列表表示不设置工作区默认来源限制，仍受 Provider 与网络安全策略约束。此白名单只约束查询来源（应用层），不授予沙箱出站权限；沙箱联网需在「Egress 审批」中获批。"
      emptyLabel="尚未设置搜索与 Deep Research 来源白名单。"
      getPolicy={getResearchPolicy}
      placeholder="例如：arxiv.org"
      queryKey={researchPolicyQueryKey}
      updatePolicy={updateResearchPolicy}
    />
  );
}

/** 网页抓取白名单（web_fetch.policy，应用层抓取授权）。 */
export function FetchDomainAllowlistEditor() {
  return (
    <DomainAllowlistEditor
      description="网页抓取只能抓取这里列出的精确公共 DNS 域名（工作区级；聊天内「以后都允许」写入的是个人级列表，两者取并集）。空列表表示不限制工作区默认抓取域，仍按个人白名单与 Provider 能力约束。此授权只放行网页抓取操作（应用层），不授予沙箱出站权限。"
      emptyLabel="尚未设置网页抓取白名单（个人白名单仍可生效）。"
      getPolicy={getFetchPolicy}
      placeholder="例如：example.com"
      queryKey={fetchPolicyQueryKey}
      updatePolicy={updateFetchPolicy}
    />
  );
}

/**
 * 统一白名单（access.allowlist）：搜索 / Deep Research、网页抓取与沙箱
 * 出站共用一层。白名单内域名一律不拦截；「全放行」开关则不拦截全部公网域名。
 */
export function UnifiedAllowlistEditor() {
  const queryClient = useQueryClient();
  const policy = useQuery({
    queryKey: accessAllowlistQueryKey,
    queryFn: getAccessAllowlist,
  });
  const update = useMutation({
    mutationFn: updateAccessAllowlist,
    onSuccess: (next) => {
      queryClient.setQueryData(accessAllowlistQueryKey, next);
      toast.success("白名单已更新");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "白名单更新失败"),
  });
  if (policy.isPending) {
    return <p className="text-sm text-muted-foreground">正在读取白名单…</p>;
  }
  if (policy.isError) {
    return (
      <div className="flex items-center justify-between gap-3 text-sm text-destructive">
        <span>{policy.error.message || "白名单读取失败"}</span>
        <Button onClick={() => void policy.refetch()} size="sm" variant="outline">
          重试
        </Button>
      </div>
    );
  }
  const data = policy.data;
  const toggleAllowAll = (checked: boolean) =>
    update.mutate({ ...data, allow_all: checked });
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium">不拦截全放行</p>
          <p className="text-xs text-muted-foreground">
            开启后，所有公网域名在搜索、网页抓取与沙箱出站时均不拦截、无需审批
            （内网、环回与云元数据地址仍被拒绝）。
          </p>
        </div>
        <Switch
          checked={data.allow_all}
          disabled={update.isPending}
          onCheckedChange={toggleAllowAll}
        />
      </div>
      {data.allow_all ? (
        <p className="rounded-lg border border-dashed px-3 py-5 text-center text-sm text-muted-foreground">
          已开启全放行：白名单列表暂不生效，可随时关闭后继续使用。
        </p>
      ) : (
        <DomainListEditor
          disabled={update.isPending}
          domains={data.allowed_domains}
          onChange={(domains) => update.mutate({ ...data, allowed_domains: domains })}
          placeholder="例如：example.com"
          emptyLabel="尚未设置白名单域名。"
        />
      )}
    </div>
  );
}

/** 纯域名列表编辑器（添加 / 编辑 / 删除），供统一白名单复用。 */
function DomainListEditor({
  domains,
  onChange,
  disabled,
  placeholder,
  emptyLabel,
}: {
  domains: string[];
  onChange: (domains: string[]) => void;
  disabled: boolean;
  placeholder: string;
  emptyLabel: string;
}) {
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const add = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    onChange([...domains, value]);
    setDraft("");
  };
  const saveEdit = () => {
    if (!editing || !editDraft.trim()) return;
    onChange(domains.map((domain) => (domain === editing ? editDraft.trim() : domain)));
    setEditing(null);
    setEditDraft("");
  };
  return (
    <div className="space-y-4">
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={add}>
        <Input
          aria-label="添加白名单域名"
          disabled={disabled}
          onChange={(event) => setDraft(event.currentTarget.value)}
          placeholder={placeholder}
          value={draft}
        />
        <Button disabled={disabled || !draft.trim()} type="submit" variant="outline">
          <Plus className="size-4" />
          添加
        </Button>
      </form>
      {domains.length ? (
        <div className="divide-y rounded-lg border">
          {domains.map((domain) => {
            const isEditing = editing === domain;
            return (
              <div className="flex min-h-12 items-center gap-2 px-3 py-2" key={domain}>
                {isEditing ? (
                  <Input
                    aria-label={`编辑 ${domain}`}
                    autoFocus
                    className="h-8"
                    disabled={disabled}
                    onChange={(event) => setEditDraft(event.currentTarget.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        saveEdit();
                      }
                    }}
                    value={editDraft}
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">{domain}</span>
                )}
                {isEditing ? (
                  <>
                    <Button aria-label={`保存 ${domain}`} disabled={disabled || !editDraft.trim()} onClick={saveEdit} size="icon-sm" type="button" variant="ghost">
                      <Check className="size-4" />
                    </Button>
                    <Button aria-label={`取消编辑 ${domain}`} disabled={disabled} onClick={() => setEditing(null)} size="icon-sm" type="button" variant="ghost">
                      <X className="size-4" />
                    </Button>
                  </>
                ) : (
                  <>
                    <Button aria-label={`编辑 ${domain}`} disabled={disabled} onClick={() => { setEditing(domain); setEditDraft(domain); }} size="icon-sm" type="button" variant="ghost">
                      <Pencil className="size-4" />
                    </Button>
                    <Button aria-label={`删除 ${domain}`} className="text-muted-foreground hover:text-destructive" disabled={disabled} onClick={() => onChange(domains.filter((item) => item !== domain))} size="icon-sm" type="button" variant="ghost">
                      <X className="size-4" />
                    </Button>
                  </>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed px-3 py-5 text-center text-sm text-muted-foreground">
          {emptyLabel}
        </p>
      )}
    </div>
  );
}
