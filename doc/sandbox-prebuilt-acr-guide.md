# LearnGraph 沙箱镜像发布与预构建拉取指南

## 目标

沙箱 Runner 镜像（Chromium + ffmpeg + CJK 字体 + Python/Node 工具链）构建耗时
较长。通过发布预构建镜像，普通用户「有网就能拉」，初始化时只 pull 不做现场编译。
当前发布渠道为**阿里云 ACR 个人版**（公开仓库，华东 1 杭州）。

## 仓库信息

| 项 | 值 |
|---|---|
| 仓库地址 | `crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph` |
| 类型 | 公开（拉取无需登录） |
| 绑定代码仓库 | `https://github.com/SunnyBoy-y/LearnGraph` |
| 架构 | linux/amd64（ACR 个人版仅单架构） |

## 发布路径：ACR 自动构建（推荐，本地零构建）

### 控制台配置（一次性）

1. 阿里云容器镜像服务 → 实例 `learngraph` → 镜像仓库 `learngraph` → **自动构建**
2. 创建构建规则：

| 配置项 | 值 |
|---|---|
| 代码仓库 | SunnyBoy-y/LearnGraph（已绑定） |
| 触发方式 | Tag 触发，Tag 通配符 `sandbox-v*` |
| Dockerfile 路径 | `backend/sandbox/Dockerfile` |
| 构建上下文目录 | `backend/sandbox` |
| 镜像版本号 | `sandbox-v1.0.0`（与 tag 保持一致） |
| 构建参数（可选，加速） | `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`、`NPM_REGISTRY=https://registry.npmmirror.com` |

> ACR 自动构建为 amd64 单架构；Apple Silicon 用户无法拉取，会自动回退本地构建
> （未配置预构建源时的默认行为）。

### 发布步骤

```bash
git tag sandbox-v1.0.0
git push --tags
```

构建完成后在 ACR 控制台「镜像版本」确认 digest，并在部署侧配置（见下）。

## 发布路径（备选）：本地构建 + 手动推送

```powershell
# 1. 本地构建（一次性，约几十分钟）
cd backend
.\scripts\build_sandbox_image.ps1

# 2. 冒烟 + 打 ACR tag（只打 tag 不 push）
.\scripts\prepare_sandbox_acr_release.ps1 -Version 1.0.0

# 3. 登录并推送
docker login --username=<阿里云账号全名> crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com
docker push crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:1.0.0

# 4. 获取不可变 digest（推荐填写到部署配置）
docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:1.0.0
```

## 用户侧配置（部署管理员）

### 方式一：设置页（推荐，无需改环境变量）

沙箱管理页 →「沙箱镜像来源（部署级）」：

- **自动（推荐）**：已配置预构建镜像时拉取，否则本地构建
- **预构建镜像**：总是从仓库拉取，不在本机构建
- **本地构建**：总是现场 docker build

选择「预构建镜像」并填写：
`crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:sandbox-v1.0.0`

保存后持久化到 `data/sandbox-bootstrap-source.json`，全工作区成员初始化按此执行。

### 方式二：环境变量（env 优先于设置页地址）

```env
LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:sandbox-v1.0.0
```

稳定后可升级为 digest 引用：
`LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph@sha256:<digest>`

## 行为说明

- Bootstrap（页面「初始化沙箱」）按选定策略执行：预构建 → `docker pull` →
  解析 RepoDigest → 加固冒烟 → 持久化不可变 digest 到 `data/sandbox-runtime.json`；
  运行期始终使用该 digest。
- 预构建下载时前端展示真实下载进度（层下载 MB 数 / 总大小），进度条由后端
  持久进度驱动，刷新不回跳。
- 公开 ACR 仓库无需登录即可拉取；不要在任何配置文件写入 registry token。

## 限制

- ACR 个人版仅 `linux/amd64`。如需 arm64（Apple Silicon）预构建，需 ACR 企业版
  多架构构建，或改用 GHCR multi-arch。
- ACR 自动构建使用阿里云构建机，pip/npm 官方源可能较慢；必要时通过构建参数
  指向国内镜像源。
