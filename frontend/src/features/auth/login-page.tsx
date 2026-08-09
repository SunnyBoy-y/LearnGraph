import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  KeyRound,
  LockKeyhole,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { apiClient } from "@/api/client";
import { KnowledgeGraph } from "@/components/graph/knowledge-graph";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/features/auth/auth-context-value";
import { authErrorMessage, SESSION_EXPIRED_MESSAGE } from "./auth-messages";
import {
  PASSWORD_RULE_SUMMARY,
  passwordRuleViolations,
} from "./password-rules";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { demoLogin, login, register } = useAuth();
  const demoLoginEnabled = import.meta.env.VITE_ENABLE_DEMO_LOGIN !== "false";
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [registerMode, setRegisterMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(() =>
    searchParams.get("reason") === "session_expired"
      ? SESSION_EXPIRED_MESSAGE
      : "",
  );

  function returnToAfterLogin(workspaceId: string): string {
    const from = (location.state as { from?: string } | null)?.from;
    if (from && from.startsWith("/") && !from.startsWith("/auth/")) {
      return from;
    }
    return `/w/${workspaceId}`;
  }

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await login(username, password);
      if (result.mustChangePassword) {
        // The dedicated page survives refresh and asks for the current
        // password again if this in-memory value is lost.
        navigate("/auth/change-password", {
          state: { currentPassword: password },
        });
        return;
      }
      navigate(returnToAfterLogin(result.workspaceId), { replace: true });
    } catch (reason) {
      setError(authErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitDemoLogin() {
    setBusy(true);
    setError("");
    try {
      const result = await demoLogin();
      if (result.mustChangePassword) {
        navigate("/auth/change-password", {
          state: { currentPassword: "learn-graph-local" },
        });
        return;
      }
      navigate(returnToAfterLogin(result.workspaceId), { replace: true });
    } catch (reason) {
      setError(authErrorMessage(reason));
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
    const violations = passwordRuleViolations(password, username);
    if (violations.length > 0) {
      setError(violations[0]);
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
      setError(authErrorMessage(reason));
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
        {registerMode ? (
          <form onSubmit={submitRegister}>
            <div className="eyebrow">
              <UserRound aria-hidden="true" /> 创建你的学习空间
            </div>
            <h1 id="auth-title">注册 LearnGraph</h1>
            <p className="form-intro">
              注册后会自动创建个人工作区，完成后直接进入学习空间。
            </p>
            <div className="field-stack">
              <Label htmlFor="display-name">显示名称</Label>
              <div className="field-with-icon">
                <UserRound aria-hidden="true" />
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
                <UserRound aria-hidden="true" />
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
                <KeyRound aria-hidden="true" />
                <Input
                  aria-describedby="register-password-hint"
                  autoComplete="new-password"
                  id="register-password"
                  minLength={12}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </div>
              <p className="form-intro" id="register-password-hint">
                {PASSWORD_RULE_SUMMARY}
              </p>
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
              <ArrowRight aria-hidden="true" />
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
              <LockKeyhole aria-hidden="true" /> 账号安全登录
            </div>
            <h1 id="auth-title">继续学习</h1>
            <p className="form-intro">
              登录会创建可查看、可单独吊销的服务端会话。
            </p>
            <div className="field-stack">
              <Label htmlFor="username">邮箱或用户名</Label>
              <div className="field-with-icon">
                <UserRound aria-hidden="true" />
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
                <KeyRound aria-hidden="true" />
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
              {busy ? "正在验证…" : "登录"}
              <ArrowRight aria-hidden="true" />
            </Button>
            {demoLoginEnabled && (
              <Button
                className="auth-demo"
                disabled={busy}
                onClick={submitDemoLogin}
                type="button"
                variant="outline"
              >
                <Sparkles />
                试用 Demo
              </Button>
            )}
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
