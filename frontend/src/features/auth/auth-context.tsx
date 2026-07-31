import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import {
  changePassword as changeAccountPassword,
  deleteAccount as deleteCurrentAccount,
  listWorkspaces,
  login as loginAccount,
  register as registerAccount,
  logout as revokeCurrentSession,
} from "@/api/auth";
import { authStore } from "@/api/auth-store";
import { getCurrentUser } from "@/api/control";
import {
  clearAuthenticatedClientState,
  registerAuthInvalidationHandler,
} from "@/lib/auth-query-cache";
import { clearSelectionExplanations } from "@/features/chat/selection-explanation";

import {
  AuthContext,
  useAuth,
  type AuthContextValue,
} from "./auth-context-value";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(() => authStore.getSession());
  const [workspaceName, setWorkspaceName] = useState("");

  useEffect(
    () =>
      registerAuthInvalidationHandler(() => {
        setSession(null);
        setWorkspaceName("");
      }),
    [],
  );

  useEffect(() => {
    if (!session?.workspaceId) {
      setWorkspaceName("");
      return;
    }
    let cancelled = false;
    void listWorkspaces()
      .then((workspaces) => {
        if (!cancelled) {
          setWorkspaceName(
            workspaces.find((item) => item.id === session.workspaceId)?.name ??
              session.workspaceId,
          );
        }
      })
      .catch(() => {
        if (!cancelled) setWorkspaceName(session.workspaceId);
      });
    return () => {
      cancelled = true;
    };
  }, [session?.workspaceId]);

  useEffect(() => {
    if (!session?.accessToken) return;

    let checking = false;
    const checkSession = () => {
      if (checking) return;
      checking = true;
      void getCurrentUser()
        .catch(() => undefined)
        .finally(() => {
          checking = false;
        });
    };
    const interval = window.setInterval(checkSession, 30_000);
    window.addEventListener('focus', checkSession);
    document.addEventListener('visibilitychange', checkSession);

    return () => {
      window.clearInterval(interval);
      window.removeEventListener('focus', checkSession);
      document.removeEventListener('visibilitychange', checkSession);
    };
  }, [session?.accessToken]);

  const value = useMemo<AuthContextValue>(
    () => ({
      authenticated: Boolean(session?.accessToken && session.workspaceId),
      username: session?.displayName ?? session?.username ?? "",
      workspaceId: session?.workspaceId ?? "",
      workspaceName: workspaceName || session?.workspaceId || "",
      async setWorkspaceId(workspaceId) {
        if (!session || workspaceId === session.workspaceId) return;
        const workspaces = await listWorkspaces();
        if (!workspaces.some((workspace) => workspace.id === workspaceId)) {
          throw new Error("Workspace is not accessible to this account");
        }
        await clearAuthenticatedClientState();
        authStore.setWorkspaceId(workspaceId);
        setSession({ ...session, workspaceId });
      },
      async login(username, password) {
        await clearAuthenticatedClientState();
        const response = await loginAccount({ username, password });
        const nextSession = authStore.getSession();
        setSession(nextSession);
        if (!response.default_workspace_id) {
          throw new Error("This account has no accessible workspace");
        }
        return {
          workspaceId: response.default_workspace_id,
          mustChangePassword: response.must_change_password,
        };
      },
      async register(payload) {
        await clearAuthenticatedClientState();
        const response = await registerAccount(payload);
        const nextSession = authStore.getSession();
        setSession(nextSession);
        if (!response.default_workspace_id) {
          throw new Error("This account has no accessible workspace");
        }
        return {
          workspaceId: response.default_workspace_id,
          mustChangePassword: response.must_change_password,
        };
      },
      async changePassword(currentPassword, newPassword) {
        await changeAccountPassword(currentPassword, newPassword);
      },
      async deleteAccount(currentPassword, confirmation) {
        await deleteCurrentAccount(currentPassword, confirmation);
        clearSelectionExplanations();
        await clearAuthenticatedClientState();
        authStore.clear();
        setSession(null);
        setWorkspaceName("");
      },
      async logout() {
        try {
          await revokeCurrentSession();
        } finally {
          clearSelectionExplanations();
          await clearAuthenticatedClientState();
          authStore.clear();
          setSession(null);
          setWorkspaceName("");
        }
      },
    }),
    [session, workspaceName],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.authenticated) {
    return (
      <Navigate replace state={{ from: location.pathname }} to="/auth/login" />
    );
  }
  return children;
}
