import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Plus, X } from "lucide-react";
import { toast } from "sonner";

import {
  getResearchPolicy,
  updateResearchPolicy,
} from "@/api/settings";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export const researchPolicyQueryKey = ["research-policy"] as const;

export function DomainAllowlistEditor() {
  const queryClient = useQueryClient();
  const policy = useQuery({
    queryKey: researchPolicyQueryKey,
    queryFn: getResearchPolicy,
  });
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const update = useMutation({
    mutationFn: updateResearchPolicy,
    onSuccess: (next) => {
      queryClient.setQueryData(researchPolicyQueryKey, next);
      toast.success("工作区来源白名单已更新");
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
      <p className="text-xs leading-5 text-muted-foreground">
        搜索与 Deep Research 只能使用这里列出的精确公共 DNS 域名。空列表表示不设置工作区默认来源限制，仍受 Provider 与网络安全策略约束。
      </p>
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={add}>
        <Input
          aria-label="添加工作区来源域名"
          disabled={update.isPending}
          onChange={(event) => setDraft(event.currentTarget.value)}
          placeholder="例如：arxiv.org"
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
          尚未设置搜索与 Deep Research 来源白名单。
        </p>
      )}
    </div>
  );
}
