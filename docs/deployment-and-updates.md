# 部署与更新

## Docker 配置与备份

Docker Compose 首次启动会根据根目录 `.env.example` 生成 `.env`。默认 SQLite 数据库、上传文件、主密钥和沙箱状态保存在 Docker 数据卷；PostgreSQL 迁移后还需备份独立数据库卷。升级前先验证备份，避免使用 `docker compose down -v` 或 `docker volume prune` 删除数据。

```bash
LEARNGRAPH_EXPECTED_PROJECT=learngraph ./scripts/docker-update.sh --check
LEARNGRAPH_EXPECTED_PROJECT=learngraph ./scripts/docker-update.sh --backup-only
```

`LEARNGRAPH_EXPECTED_PROJECT` 要与实际 Compose 项目名一致。备份包含敏感恢复材料，应妥善保存。应用内全站备份与恢复说明见 [全站备份与恢复](../backend/SITE_BACKUP.md)。数据库迁移与容器拓扑见 [容器拓扑迁移](container-topology.md)。

局域网访问可在根目录 `.env` 中设置监听地址；交互式子应用的预览地址也需要让浏览器可访问：

```dotenv
LEARNGRAPH_LISTEN_HOST=0.0.0.0
LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN=http://<局域网IP>:18001
```

修改后执行 `docker compose up -d`。端口和其他配置见根目录 `.env.example` 与 `backend/.env.example`。

Docker 部署访问宿主机 Ollama 等服务时，可在宿主机运行 `node scripts/host-bridge.mjs`，并在生成的 `data/host-bridge/host-services/` 注册表中启用所需服务。公网访问需自行配置反向代理或内网穿透：主站使用 18000，交互式子应用预览使用 18001；源码模式对应 5173 和 8001。预览公网地址通过 `LEARNGRAPH_SUBAPP_PREVIEW_ORIGIN` 配置。源码模式另需在 `frontend/.env` 配置 `LEARNGRAPH_PUBLIC_ORIGIN`。

## 应用内更新

系统管理员可在「设置 → 关于 → 版本与更新」查看 Release、升级和回退。更新器是运行在 Docker 宿主机上的独立 Python 3.10+ 进程，需要 Docker CLI 和支持 `up --wait` 的 Compose v2。首次使用时，在原部署目录执行：

```bash
python -m updater --init-only
node scripts/compose.mjs up -d --build
python -m updater --project-dir .
```

生产环境应使用 systemd 或 Windows 任务计划程序保持更新器运行。Windows 也可运行 `pwsh -File updater/install-windows.ps1` 安装登录启动项；Linux 可参考 [systemd 示例](../updater/learngraph-updater.service)。若部署使用额外 Compose 文件，启动更新器时使用 `--compose-file` 依次指定同一组文件。

更新器默认监听 `0.0.0.0:34116`，通过服务 token 认证；请用宿主机防火墙将访问范围限制在 Docker 网络，并保护 `data/updater/token`。升级前会备份数据库，切换后检查服务健康；失败时尝试恢复原镜像。附件仍需备份整个数据卷。

更新后，普通 Compose 命令需包含生成的镜像选择文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.updater.yml -f docker-compose.update.json up -d --no-build --pull never
```

首次更新前该文件尚不存在。避免同时运行网页更新器和 `scripts/docker-update.sh`；更新器保留沙箱服务和 Compose 配置，部署协议变化需人工迁移。版本回退沿用当前数据库，提前确认备份和版本兼容性。

发布版本以根目录 `VERSION` 为准，Release tag 与该文件内容保持一致。Fork 可通过更新器的 `--repository` 和 `--image` 选项指定来源；私有仓库可设置 `LEARNGRAPH_UPDATE_GITHUB_TOKEN`，私有镜像需提前登录镜像仓库。
