import { useState, type FormEvent } from "react";
import { ArrowRight, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "@/api/client";
import { KnowledgeGraph } from "@/components/graph/knowledge-graph";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/features/auth/auth-context-value";

function errorMessage(reason: unknown): string {
  if (reason instanceof ApiError) return reason.message;
  if (reason instanceof Error) return reason.message;
  return "无法连接 LearnGraph 后端，请确认 API 已启动。";
}

export function ChangePasswordPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const initialCurrentPassword =
    (location.state as { currentPassword?: string } | null)?.currentPassword ?? "";
  const [currentPassword, setCurrentPassword] = useState(initialCurrentPassword);
  const [newPassword, setNewPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!auth.authenticated) {
    return <Navigate replace to="/auth/login" />;
  }
  if (auth.mustChangePassword === null) {
    return (
      <main aria-live="polite" className="grid min-h-svh place-items-center bg-background">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin" />
          正在检查登录状态…
        </div>
      </main>
    );
  }
  if (!auth.mustChangePassword) {
    return <Navigate replace to={`/w/${auth.workspaceId}/home`} />;
  }

  async function submitPasswordChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (newPassword !== passwordConfirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    try {
      await auth.changePassword(currentPassword, newPassword);
      navigate(`/w/${auth.workspaceId}/home`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-backdrop" aria-hidden="true">
        <KnowledgeGraph compact interactive={false} />
      </div>
      <section className="auth-modal" aria-labelledby="auth-title">
        <div className="auth-brand">
          <span className="brand__mark">L</span>
          <strong>LearnGraph</strong>
        </div>
        <form onSubmit={submitPasswordChange}>
          <div className="eyebrow">
            <ShieldCheck /> 首次登录保护
          </div>
          <h1 id="auth-title">设置新密码</h1>
          <p className="form-intro">
            引导密码只能使用一次。修改后，其他已登录会话会由服务端立即撤销。
          </p>
          <div className="field-stack">
            <Label htmlFor="current-password">当前密码</Label>
            <div className="field-with-icon">
              <KeyRound />
              <Input
                autoComplete="current-password"
                id="current-password"
                onChange={(event) => setCurrentPassword(event.target.value)}
                required
                type="password"
                value={currentPassword}
              />
            </div>
          </div>
          <div className="field-stack">
            <Label htmlFor="new-password">新密码</Label>
            <div className="field-with-icon">
              <KeyRound />
              <Input
                autoComplete="new-password"
                id="new-password"
                minLength={12}
                onChange={(event) => setNewPassword(event.target.value)}
                required
                type="password"
                value={newPassword}
              />
            </div>
          </div>
          <div className="field-stack">
            <Label htmlFor="password-confirmation">再次输入新密码</Label>
            <div className="field-with-icon">
              <KeyRound />
              <Input
                autoComplete="new-password"
                id="password-confirmation"
                minLength={12}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                required
                type="password"
                value={passwordConfirmation}
              />
            </div>
          </div>
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
          <Button className="auth-submit" disabled={busy} type="submit">
            {busy ? "正在更新…" : "保存并进入工作区"}
            <ArrowRight />
          </Button>
        </form>
      </section>
    </main>
  );
}
