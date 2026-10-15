import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import { Navigate, useLocation } from "react-router-dom";

import {
  changePassword as changeAccountPassword,
  deleteAccount as deleteCurrentAccount,
  demoLogin as demoLoginAccount,
  listWorkspaces,
  login as loginAccount,
  register as registerAccount,
  logout as revokeCurrentSession,
  selectWorkspace,
} from "@/api/auth";
import { authStore } from "@/api/auth-store";
import { getCurrentUser } from "@/api/control";
import {
  clearAuthenticatedClientState,
  clearWorkspaceClientState,
  registerAuthInvalidationHandler,
} from "@/lib/auth-query-cache";
import { clearSelectionExplanations, clearAllSelectionExplanations } from "@/features/chat/selection-explanation";

import {
  AuthContext,
  useAuth,
  type AuthContextValue,
} from "./auth-context-value";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(() => authStore.getSession());
  const [workspaceName, setWorkspaceName] = useState("");
  const [permissionScope, setPermissionScope] = useState<{ token: string; workspaceId: string; permissions: string[] } | null>(null);
  const [adminScope, setAdminScope] = useState<{ token: string; isAdmin: boolean } | null>(null);
  const workspaceSelectionRequestRef = useRef(0);

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
      setPermissionScope(null);
      return;
    }
    let cancelled = false;
    void listWorkspaces()
      .then((workspaces) => {
        if (!cancelled) {
          setPermissionScope({ token: session.accessToken, workspaceId: session.workspaceId, permissions: workspaces.find((item) => item.id === session.workspaceId)?.effective_permissions ?? [] });
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
  }, [session?.workspaceId, session?.accessToken]);

  useEffect(() => {
    if (!session?.accessToken) return;

    let checking = false;
    let cancelled = false;
    const checkSession = () => {
      if (checking) return;
      checking = true;
      void getCurrentUser()
        .then((user) => {
          if (cancelled || authStore.getAccessToken() !== session.accessToken) return;
          setAdminScope({ token: session.accessToken, isAdmin: user.is_system_admin });
          void listWorkspaces().then((workspaces) => {
            if (cancelled || authStore.getWorkspaceId() !== session.workspaceId) return;
            const permissions = workspaces.find((item) => item.id === session.workspaceId)?.effective_permissions ?? [];
            setPermissionScope((current) => {
              if (current?.permissions.some((permission) => !permissions.includes(permission))) void clearWorkspaceClientState(session.workspaceId);
              return { token: session.accessToken, workspaceId: session.workspaceId, permissions };
            });
          }).catch(() => { if (!cancelled) setPermissionScope(null); });
          const mustChangePassword = user.must_change_password;
          authStore.setMustChangePassword(mustChangePassword);
          setSession((current) =>
            current ? { ...current, mustChangePassword } : current,
          );
        })
        .catch(() => undefined)
        .finally(() => {
          checking = false;
        });
    };
    // Hydrate the password-change requirement immediately after refresh so the
    // route guard never lets a pending-change session enter the workspace.
    checkSession();
    const interval = window.setInterval(() => {
      // U1-2: skip the heartbeat while the tab is hidden; the focus and
      // visibilitychange listeners re-check immediately on return.
      if (document.visibilityState === 'hidden') return;
      checkSession();
    }, 30_000);
    window.addEventListener('focus', checkSession);
    document.addEventListener('visibilitychange', checkSession);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener('focus', checkSession);
      document.removeEventListener('visibilitychange', checkSession);
    };
  }, [session?.accessToken, session?.workspaceId]);

  const value = useMemo<AuthContextValue>(
    () => ({
      authenticated: Boolean(session?.accessToken && session.workspaceId),
      username: session?.displayName ?? session?.username ?? "",
      workspaceId: session?.workspaceId ?? "",
      userId: session?.userId ?? session?.username ?? "",
      isSystemAdmin: adminScope?.token === session?.accessToken && adminScope?.isAdmin === true,
      effectivePermissions: permissionScope?.token === session?.accessToken && permissionScope?.workspaceId === session?.workspaceId ? permissionScope?.permissions ?? [] : [],
      workspaceName: workspaceName || session?.workspaceId || "",
      mustChangePassword: session?.mustChangePassword ?? null,
      async setWorkspaceId(workspaceId) {
        if (!session || workspaceId === session.workspaceId) return;
        const requestId = workspaceSelectionRequestRef.current + 1;
        workspaceSelectionRequestRef.current = requestId;
        await selectWorkspace(workspaceId);
        // A slower, earlier selection must not overwrite the newest choice.
        if (requestId !== workspaceSelectionRequestRef.current) return;
        const previousWorkspaceId = session.workspaceId;
        await clearWorkspaceClientState(previousWorkspaceId);
        if (requestId !== workspaceSelectionRequestRef.current) return;
        authStore.setWorkspaceId(workspaceId);
        setSession((current) =>
          current && current.workspaceId === previousWorkspaceId
            ? { ...current, workspaceId }
            : current,
        );
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
      async demoLogin() {
        await clearAuthenticatedClientState();
        const response = await demoLoginAccount();
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
        // The current session stays valid and no longer requires a change.
        authStore.setMustChangePassword(false);
        setSession((current) =>
          current ? { ...current, mustChangePassword: false } : current,
        );
      },
      async deleteAccount(currentPassword, confirmation) {
        await deleteCurrentAccount(currentPassword, confirmation);
        clearAllSelectionExplanations();
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
    [session, workspaceName, permissionScope, adminScope],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.authenticated) {
    return (
      <Navigate
        replace
        state={{ from: location.pathname + location.search }}
        to="/auth/login"
      />
    );
  }
  if (auth.mustChangePassword === null) {
    // Hydrating the server-side password requirement after a refresh; do not
    // let a pending-change session briefly render workspace error states.
    return (
      <main aria-live="polite" className="grid min-h-svh place-items-center bg-background">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin" />
          正在检查登录状态…
        </div>
      </main>
    );
  }
  if (auth.mustChangePassword) {
    return <Navigate replace to="/auth/change-password" />;
  }
  return children;
}
