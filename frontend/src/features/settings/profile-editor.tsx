import { useSettingsDirty } from '@/lib/settings-dirty';
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { LoaderCircle, Shuffle, Upload } from "lucide-react";
import { toast } from "sonner";

import { generateDefaultAvatar, updateProfile, uploadAvatar } from "@/api/auth";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/features/auth/auth-context-value";
import { defaultUserAvatar } from "@/lib/user-avatar";

type ProfileAction = { kind: "name"; name: string } | { kind: "upload"; file: File } | { kind: "generate" };

export function ProfileEditor() {
  const auth = useAuth();
  const defaultAvatar = defaultUserAvatar(auth.username);
  const [displayName, setDisplayName] = useState(auth.username);
  useSettingsDirty("profile-name", displayName !== auth.username);
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => setDisplayName(auth.username), [auth.username]);

  const save = useMutation({
    mutationFn: (action: ProfileAction) => {
      if (action.kind === "name") return updateProfile(action.name);
      if (action.kind === "upload") return uploadAvatar(action.file);
      return generateDefaultAvatar();
    },
    onSuccess: (profile, action) => {
      auth.updateProfile(profile);
      toast.success(action.kind === "name" ? "昵称已保存" : "头像已更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const busy = save.isPending;

  function selectImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast.error("头像图片不能超过 5 MB");
      return;
    }
    if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) {
      toast.error("请选择 PNG、JPG、WebP 或 GIF 图片");
      return;
    }
    save.mutate({ kind: "upload", file });
  }

  function submitName(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = displayName.trim();
    if (name && !busy && name !== auth.username) save.mutate({ kind: "name", name });
  }

  return (
    <div aria-busy={busy} className="space-y-5">
      <div className="flex flex-wrap items-center gap-4">
        <Avatar className="size-20">
          {auth.avatarUrl ? <AvatarImage alt={`${auth.username}的头像`} src={auth.avatarUrl} /> : null}
          <AvatarFallback className="sidebar-user-avatar text-2xl font-medium" style={defaultAvatar.style}>
            {defaultAvatar.initial}
          </AvatarFallback>
        </Avatar>
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} onClick={() => fileInput.current?.click()} size="sm" type="button" variant="outline">
              {busy && save.variables?.kind === "upload" ? <LoaderCircle className="size-4 animate-spin" /> : <Upload className="size-4" />}
              上传图片
            </Button>
            <Button disabled={busy} onClick={() => save.mutate({ kind: "generate" })} size="sm" type="button" variant="outline">
              {busy && save.variables?.kind === "generate" ? <LoaderCircle className="size-4 animate-spin" /> : <Shuffle className="size-4" />}
              生成默认头像
            </Button>
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">支持 JPG、PNG、WebP、GIF，最大 5 MB。图片将居中裁剪为正方形。</p>
          <input accept="image/png,image/jpeg,image/webp,image/gif" aria-label="选择头像图片" className="hidden" disabled={busy} onChange={selectImage} ref={fileInput} type="file" />
        </div>
      </div>
      <form className="space-y-2" onSubmit={submitName}>
        <Label htmlFor="profile-display-name">昵称</Label>
        <div className="flex flex-wrap gap-2">
          <Input autoComplete="nickname" className="min-w-40 flex-1" disabled={busy} id="profile-display-name" maxLength={160} onChange={(event) => setDisplayName(event.target.value)} required value={displayName} />
          <Button disabled={busy || !displayName.trim() || displayName.trim() === auth.username} size="sm" type="submit">
            {busy && save.variables?.kind === "name" ? <LoaderCircle className="size-4 animate-spin" /> : null}
            保存昵称
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">昵称用于界面展示，登录用户名保持不变。</p>
      </form>
    </div>
  );
}
