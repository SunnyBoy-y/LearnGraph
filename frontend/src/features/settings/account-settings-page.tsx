import { authStore } from "@/api/auth-store";
import { PageFrame, PageIntro, SectionHeading, Surface } from "@/components/shared/page-elements";
import { useAuth } from "@/features/auth/auth-context-value";
import { AccountDeletion, PasswordSecurity } from "./account-security";
import { AccessManagementPage } from "./control-pages";
import { ProfileEditor } from "./profile-editor";
import { SettingsSection } from "./settings-primitives";
function useAccountSettingsPage() {
    const auth = useAuth();
    const session = authStore.getSession();
    const accountUsername = session?.username ?? auth.username;
    const userId = session?.userId;
    return { accountUsername, userId };
}
export function AccountSettingsPage() {
    const { accountUsername, userId } = useAccountSettingsPage();
    return (<PageFrame><PageIntro title="账户与安全" description="管理个人资料、登录设备与账户安全。"/><Surface className="p-5">
          <SectionHeading description="修改头像和昵称，保存后立即生效" title="身份"/>
          <div className="mt-5 space-y-3">
            <ProfileEditor />
            <div className="flex items-start gap-3 rounded-xl border p-4">
              <div className="min-w-0 space-y-2 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="text-muted-foreground">用户名</span>
                  <span className="font-mono text-xs">{accountUsername || "—"}</span>
                </div>
                {userId ? (<div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-muted-foreground">用户 ID</span>
                    <span className="max-w-[60%] truncate font-mono text-[11px] text-muted-foreground" title={userId}>
                      {userId}
                    </span>
                  </div>) : null}
              </div>
            </div>
          </div>
        </Surface><PasswordSecurity username={accountUsername}/><AccessManagementPage section="security"/><SettingsSection title="危险操作" danger><AccountDeletion username={accountUsername}/></SettingsSection></PageFrame>);
}
