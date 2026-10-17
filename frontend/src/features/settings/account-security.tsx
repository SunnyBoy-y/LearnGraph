import { getAccountDeletionImpact } from "@/api";
import { ErrorState, LoadingState, Surface } from "@/components/shared/page-elements";
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/features/auth/auth-context-value";
import { useMutation, useQuery } from "@tanstack/react-query";
import { KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useSettingsDirty } from '@/lib/settings-dirty';
export function PasswordSecurity({ username }: {
    username: string;
}) {
    const auth = useAuth();
    const [currentPassword, setCurrentPassword] = useState("");
    const [newPassword, setNewPassword] = useState("");
    const [newPasswordConfirmation, setNewPasswordConfirmation] = useState("");
    useSettingsDirty("password", Boolean(currentPassword || newPassword || newPasswordConfirmation));
    const passwordChange = useMutation({
        mutationFn: () => auth.changePassword(currentPassword, newPassword),
        onError: (error) => toast.error(error.message),
        onSuccess: () => {
            setCurrentPassword("");
            setNewPassword("");
            setNewPasswordConfirmation("");
            toast.success("密码已修改，其他设备上的登录会话已撤销");
        },
    });
    const passwordReady = currentPassword.length > 0 &&
        newPassword.length >= 12 &&
        newPassword === newPasswordConfirmation;
    return (<Surface className="p-5">
      <div className="space-y-5">
        <div>
          <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <KeyRound className="size-5"/>
          </div>
          <h2 className="mt-4 text-base font-semibold">修改密码</h2>
          <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
            新密码至少 12 位，并同时包含字母、数字和足够多的不同字符。修改成功后，当前设备会保持登录，其他设备会退出。
          </p>
        </div>
        <form className="grid gap-4" onSubmit={(event) => {
            event.preventDefault();
            if (newPassword !== newPasswordConfirmation) {
                toast.error("两次输入的新密码不一致");
                return;
            }
            passwordChange.mutate();
        }}>
          <input aria-hidden="true" autoComplete="username" className="sr-only" readOnly tabIndex={-1} value={username}/>
          <Label className="grid gap-2">
            当前密码
            <Input autoComplete="current-password" onChange={(event) => setCurrentPassword(event.target.value)} required type="password" value={currentPassword}/>
          </Label>
          <div className="grid gap-4 sm:grid-cols-2">
            <Label className="grid gap-2">
              新密码
              <Input autoComplete="new-password" minLength={12} onChange={(event) => setNewPassword(event.target.value)} required type="password" value={newPassword}/>
            </Label>
            <Label className="grid gap-2">
              再次输入新密码
              <Input autoComplete="new-password" minLength={12} onChange={(event) => setNewPasswordConfirmation(event.target.value)} required type="password" value={newPasswordConfirmation}/>
            </Label>
          </div>
          {newPasswordConfirmation &&
            newPassword !== newPasswordConfirmation ? (<p className="text-xs text-destructive">两次输入的新密码不一致。</p>) : null}
          <Button className="w-fit" disabled={!passwordReady || passwordChange.isPending} type="submit">
            {passwordChange.isPending ? "正在修改…" : "修改密码"}
          </Button>
        </form>
      </div>
    </Surface>);
}
export function AccountDeletion({ username }: {
    username: string;
}) {
    const auth = useAuth();
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [deletePassword, setDeletePassword] = useState("");
    const [deleteConfirmation, setDeleteConfirmation] = useState("");
    const deletionImpact = useQuery({
        queryKey: ["account-deletion-impact"],
        queryFn: getAccountDeletionImpact,
        staleTime: 30000,
        enabled: deleteOpen,
    });
    const accountDeletion = useMutation({
        mutationFn: () => auth.deleteAccount(deletePassword, deleteConfirmation),
        onError: (error) => toast.error(error.message),
        onSuccess: () => toast.success("账户已删除"),
    });
    const deletionReady = deletionImpact.data?.can_delete === true &&
        deletePassword.length > 0 &&
        deleteConfirmation === username;
    function updateDeleteOpen(open: boolean) {
        if (accountDeletion.isPending)
            return;
        setDeleteOpen(open);
        if (!open) {
            setDeletePassword("");
            setDeleteConfirmation("");
        }
    }
    return (<>
      <div className="flex flex-col gap-3 rounded-xl border border-red-200/70 bg-background/60 p-4 sm:flex-row sm:items-center dark:border-red-900/60">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-destructive">删除账户</p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            删除会立即撤销全部登录和成员权限，并去除账户身份信息。为保留证据与审计完整性，个人工作区中的学习记录不会被伪装成从未发生。
          </p>
        </div>
        <Button onClick={() => setDeleteOpen(true)} variant="destructive">
          <Trash2 className="size-4"/>
          删除我的账户
        </Button>
      </div>

      <AlertDialog onOpenChange={updateDeleteOpen} open={deleteOpen}>
        <AlertDialogContent className="sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>永久删除账户？</AlertDialogTitle>
            <AlertDialogDescription>
              先核对服务端计算的影响范围，再输入当前密码和用户名完成二次确认。
            </AlertDialogDescription>
          </AlertDialogHeader>

          {deletionImpact.isPending ? (<LoadingState label="正在检查账户关联…"/>) : deletionImpact.isError ? (<ErrorState message={deletionImpact.error.message}/>) : deletionImpact.data ? (<div className="space-y-4">
              <div className="grid grid-cols-3 gap-3 rounded-xl border bg-muted/30 p-3 text-center">
                <div>
                  <p className="text-lg font-semibold">
                    {deletionImpact.data.active_session_count}
                  </p>
                  <p className="text-[11px] text-muted-foreground">登录会话</p>
                </div>
                <div>
                  <p className="text-lg font-semibold">
                    {deletionImpact.data.active_membership_count}
                  </p>
                  <p className="text-[11px] text-muted-foreground">成员关系</p>
                </div>
                <div>
                  <p className="text-lg font-semibold">
                    {deletionImpact.data.personal_workspace_count}
                  </p>
                  <p className="text-[11px] text-muted-foreground">个人工作区</p>
                </div>
              </div>
              {deletionImpact.data.blockers.length > 0 ? (<div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100">
                  <p className="font-medium">暂时无法删除</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {deletionImpact.data.blockers.map((blocker) => (<li key={blocker}>{blocker}</li>))}
                  </ul>
                </div>) : (<div className="grid gap-3">
                  <Label className="grid gap-2">
                    当前密码
                    <Input autoComplete="current-password" onChange={(event) => setDeletePassword(event.target.value)} type="password" value={deletePassword}/>
                  </Label>
                  <Label className="grid gap-2">
                    输入用户名 <span className="font-mono">{username}</span>
                    <Input autoComplete="off" onChange={(event) => setDeleteConfirmation(event.target.value)} value={deleteConfirmation}/>
                  </Label>
                </div>)}
            </div>) : null}

          <AlertDialogFooter>
            <AlertDialogCancel disabled={accountDeletion.isPending}>
              取消
            </AlertDialogCancel>
            <Button disabled={!deletionReady || accountDeletion.isPending} onClick={() => accountDeletion.mutate()} variant="destructive">
              {accountDeletion.isPending ? "正在删除…" : "永久删除账户"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>);
}
