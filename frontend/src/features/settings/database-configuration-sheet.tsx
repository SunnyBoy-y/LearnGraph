import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Database, ShieldCheck, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  listDatabaseConfigurations,
  saveDatabaseConfiguration,
} from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type {
  DatabaseConfigurationInput,
  DatabaseSslMode,
  MigrationDatabaseKind,
} from "@/types/migrations";

const databaseLabels: Record<MigrationDatabaseKind, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
};

const defaultPorts: Record<MigrationDatabaseKind, number> = {
  postgresql: 5432,
  mysql: 3306,
};

interface DatabaseConfigurationSheetProps {
  open: boolean;
  providerKind: MigrationDatabaseKind;
  onOpenChange: (open: boolean) => void;
  onSaved: () => Promise<void> | void;
}

export function DatabaseConfigurationSheet({
  open,
  providerKind,
  onOpenChange,
  onSaved,
}: DatabaseConfigurationSheetProps) {
  const queryClient = useQueryClient();
  const configurations = useQuery({
    queryKey: ["migration-database-configurations"],
    queryFn: listDatabaseConfigurations,
    enabled: open,
  });
  const current = configurations.data?.find(
    (item) => item.provider_kind === providerKind,
  );
  const [host, setHost] = useState("");
  const [port, setPort] = useState(String(defaultPorts[providerKind]));
  const [databaseName, setDatabaseName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [sslMode, setSslMode] = useState<DatabaseSslMode>("prefer");

  useEffect(() => {
    if (!open) return;
    setHost(current?.host ?? "");
    setPort(String(current?.port ?? defaultPorts[providerKind]));
    setDatabaseName(current?.database_name ?? "");
    setUsername(current?.username ?? "");
    setPassword("");
    setSslMode(current?.ssl_mode ?? "prefer");
  }, [current, open, providerKind]);

  const save = useMutation({
    mutationFn: (payload: DatabaseConfigurationInput) =>
      saveDatabaseConfiguration(providerKind, payload),
    onError: (error) => toast.error(error.message),
    onSuccess: async (configuration) => {
      await queryClient.invalidateQueries({
        queryKey: ["migration-database-configurations"],
      });
      await onSaved();
      if (configuration.connection_verified) {
        toast.success(`${databaseLabels[providerKind]} 配置已保存，连接校验通过`);
        onOpenChange(false);
      } else {
        toast.warning(
          `${databaseLabels[providerKind]} 配置已保存，但连接校验未通过`,
        );
      }
    },
  });

  const parsedPort = Number(port);
  const canSubmit =
    host.trim().length > 0 &&
    databaseName.trim().length > 0 &&
    username.trim().length > 0 &&
    Number.isInteger(parsedPort) &&
    parsedPort >= 1 &&
    parsedPort <= 65535 &&
    (current?.password_configured || password.length > 0) &&
    !save.isPending;

  return (
    <Sheet onOpenChange={onOpenChange} open={open}>
      <SheetContent className="w-[min(480px,100vw)] overflow-y-auto sm:max-w-[480px]">
        <SheetHeader className="border-b px-6 pb-5 pt-6">
          <div className="mb-3 flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Database className="size-5" />
          </div>
          <SheetTitle>配置 {databaseLabels[providerKind]}</SheetTitle>
          <SheetDescription className="max-w-sm leading-5">
            连接信息仅供当前工作区迁移预检使用。密码会进入服务端 Secret Store，
            不会回传浏览器、日志或备份。
          </SheetDescription>
        </SheetHeader>
        <form
          className="flex min-h-0 flex-1 flex-col"
          onSubmit={(event) => {
            event.preventDefault();
            if (!canSubmit) return;
            save.mutate({
              host,
              port: parsedPort,
              database_name: databaseName,
              username,
              ...(password ? { password } : {}),
              ssl_mode: sslMode,
            });
          }}
        >
          <div className="space-y-5 px-6 py-6">
            {current ? (
              <div className="flex items-start gap-3 border-b pb-5 text-xs">
                {current.connection_verified ? (
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" />
                ) : (
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-600" />
                )}
                <div>
                  <p className="font-medium">
                    {current.connection_verified
                      ? "上次连接校验通过"
                      : "上次连接校验未通过"}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    {current.last_verified_at
                      ? new Date(current.last_verified_at).toLocaleString()
                      : "尚未校验"}
                    {current.last_error_code
                      ? ` · ${current.last_error_code}`
                      : ""}
                  </p>
                </div>
              </div>
            ) : null}
            <div className="grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
              <div className="space-y-2">
                <Label htmlFor="migration-db-host">主机地址</Label>
                <Input
                  autoComplete="off"
                  id="migration-db-host"
                  onChange={(event) => setHost(event.target.value)}
                  placeholder="db.internal.example"
                  value={host}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="migration-db-port">端口</Label>
                <Input
                  id="migration-db-port"
                  inputMode="numeric"
                  max={65535}
                  min={1}
                  onChange={(event) => setPort(event.target.value)}
                  type="number"
                  value={port}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="migration-db-name">数据库名称</Label>
              <Input
                autoComplete="off"
                id="migration-db-name"
                onChange={(event) => setDatabaseName(event.target.value)}
                placeholder="learngraph"
                value={databaseName}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="migration-db-username">用户名</Label>
              <Input
                autoComplete="username"
                id="migration-db-username"
                onChange={(event) => setUsername(event.target.value)}
                placeholder="learngraph_migrator"
                value={username}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="migration-db-password">
                密码{current?.password_configured ? "（留空则保持不变）" : ""}
              </Label>
              <Input
                autoComplete="new-password"
                id="migration-db-password"
                onChange={(event) => setPassword(event.target.value)}
                placeholder={
                  current?.password_configured ? "已安全保存" : "输入数据库密码"
                }
                type="password"
                value={password}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="migration-db-tls">传输加密</Label>
              <Select
                onValueChange={(value) => setSslMode(value as DatabaseSslMode)}
                value={sslMode}
              >
                <SelectTrigger
                  aria-label="数据库 TLS 模式"
                  id="migration-db-tls"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="require">必须使用 TLS</SelectItem>
                  <SelectItem value="prefer">优先使用 TLS</SelectItem>
                  <SelectItem value="disable">不使用 TLS</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-start gap-3 rounded-xl bg-muted/35 p-3 text-xs leading-5 text-muted-foreground">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />
              <p>
                保存时服务端会使用所选驱动建立真实连接并执行{" "}
                <code className="font-mono text-foreground">SELECT 1</code>
                。连接失败不会伪装成可用状态。
              </p>
            </div>
            {configurations.isError ? (
              <p className="text-xs text-destructive">
                读取现有配置失败：{configurations.error.message}
              </p>
            ) : null}
          </div>
          <SheetFooter className="border-t px-6 py-4">
            <Button disabled={!canSubmit} type="submit">
              {save.isPending ? "正在保存并校验…" : "保存并测试连接"}
            </Button>
            <Button
              onClick={() => onOpenChange(false)}
              type="button"
              variant="ghost"
            >
              取消
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
