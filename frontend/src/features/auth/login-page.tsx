import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  KeyRound,
  LockKeyhole,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import { apiClient, ApiError } from "@/api/client";
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

export function LoginPage() {
  const navigate = useNavigate();
  const { changePassword, login, register } = useAuth();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [passwordChangeRequired, setPasswordChangeRequired] = useState(false);
  const [registerMode, setRegisterMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await login(username, password);
      if (result.mustChangePassword) {
        setWorkspaceId(result.workspaceId);
        setPasswordChangeRequired(true);
        return;
      }
      navigate(`/w/${result.workspaceId}`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
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
      await changePassword(password, newPassword);
      navigate(`/w/${workspaceId}`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitRegister(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    if (password !== passwordConfirmation) {
      setError("两次输入的密码不一致");
      setBusy(false);
      return;
    }
    try {
      const result = await register({
        username,
        email: email || undefined,
        display_name: displayName,
        password,
      });
      navigate(`/w/${result.workspaceId}`);
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
        {passwordChangeRequired ? (
          <form onSubmit={submitPasswordChange}>
            <div className="eyebrow">
              <ShieldCheck /> 首次登录保护
            </div>
            <h1 id="auth-title">设置新密码</h1>
            <p className="form-intro">
              引导密码只能使用一次。修改后，其他已登录会话会由服务端立即撤销。
            </p>
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
                  onChange={(event) =>
                    setPasswordConfirmation(event.target.value)
                  }
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
        ) : registerMode ? (
          <form onSubmit={submitRegister}>
            <div className="eyebrow">
              <UserRound /> 创建你的学习空间
            </div>
            <h1 id="auth-title">注册 LearnGraph</h1>
            <p className="form-intro">
              注册后会自动创建个人工作区，完成后直接进入学习空间。
            </p>
            <div className="field-stack">
              <Label htmlFor="display-name">显示名称</Label>
              <div className="field-with-icon">
                <UserRound />
                <Input
                  autoComplete="name"
                  id="display-name"
                  onChange={(event) => setDisplayName(event.target.value)}
                  required
                  value={displayName}
                />
              </div>
            </div>
            <div className="field-stack">
              <Label htmlFor="register-username">用户名</Label>
              <div className="field-with-icon">
                <UserRound />
                <Input
                  autoComplete="username"
                  id="register-username"
                  minLength={3}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                  value={username}
                />
              </div>
            </div>
            <div className="field-stack">
              <Label htmlFor="register-email">邮箱（可选）</Label>
              <Input
                autoComplete="email"
                id="register-email"
                onChange={(event) => setEmail(event.target.value)}
                type="email"
                value={email}
              />
            </div>
            <div className="field-stack">
              <Label htmlFor="register-password">密码</Label>
              <div className="field-with-icon">
                <KeyRound />
                <Input
                  autoComplete="new-password"
                  id="register-password"
                  minLength={12}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </div>
            </div>
            <div className="field-stack">
              <Label htmlFor="register-password-confirmation">确认密码</Label>
              <Input
                autoComplete="new-password"
                id="register-password-confirmation"
                minLength={12}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                required
                type="password"
                value={passwordConfirmation}
              />
            </div>
            {error && <div className="form-error" role="alert">{error}</div>}
            <Button className="auth-submit" disabled={busy} type="submit">
              {busy ? "正在创建…" : "注册并开始学习"}
              <ArrowRight />
            </Button>
            <Button
              className="auth-demo"
              disabled={busy}
              onClick={() => {
                setRegisterMode(false);
                setError("");
              }}
              type="button"
              variant="ghost"
            >
              已有账号？返回登录
            </Button>
          </form>
        ) : (
          <form onSubmit={submitLogin}>
            <div className="eyebrow">
              <LockKeyhole /> 账号安全登录
            </div>
            <h1 id="auth-title">继续学习</h1>
            <p className="form-intro">
              登录会创建可查看、可单独吊销的服务端会话。
            </p>
            <div className="field-stack">
              <Label htmlFor="username">邮箱或用户名</Label>
              <div className="field-with-icon">
                <UserRound />
                <Input
                  autoComplete="username"
                  autoFocus
                  id="username"
                  onChange={(event) => setUsername(event.target.value)}
                  required
                  value={username}
                />
              </div>
            </div>
            <div className="field-stack">
              <Label htmlFor="password">密码</Label>
              <div className="field-with-icon">
                <KeyRound />
                <Input
                  autoComplete="current-password"
                  id="password"
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </div>
            </div>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <Button className="auth-submit" disabled={busy} type="submit">
              {busy ? "正在验证…" : "继续"}
              <ArrowRight />
            </Button>
            <Button
              className="auth-demo"
              disabled={busy}
              onClick={() => {
                setRegisterMode(true);
                setError("");
                setPassword("");
                setPasswordConfirmation("");
              }}
              type="button"
              variant="outline"
            >
              创建新账号
            </Button>
            <p className="login-footnote">本地优先 · API {apiClient.baseUrl}</p>
          </form>
        )}
      </section>
    </main>
  );
}
