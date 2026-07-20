# MobileWorld 实验

MobileWorld 适配使用上游官方任务初始化、快照、后端和 evaluator。pi-gui adapter 在每个
episode 中运行一次完整 agent loop，操作分配给该任务的 ADB 设备，随后由 MobileWorld
判分。当前支持官方 GUI-only 任务；MCP 和 agent-user-interaction 任务未接入，不能混入
这一成绩口径。

## 评测口径

本项目有意采用 external black-box agent 协议来比较完整的 pi-gui、Claude Code 和 Codex
agent 系统。每个 agent 在一次 MobileWorld `predict()` 中运行自己的完整观察、推理和 GUI
操作循环，完成后返回 `finished`，最终设备状态由 MobileWorld 官方 evaluator 判分。所有
被比较的 agent 必须使用这一整段执行口径，不能把逐动作 adapter 与黑盒 adapter 的结果
混入同一组成绩。

该协议复用 MobileWorld 官方任务、`init_state` 快照、环境 backend、任务初始化、tear-down
和 evaluator，但不遵循其标准的逐动作 `predict -> JSONAction -> env.execute_action` agent
协议。因此这里的成绩表示完整外部 agent 在官方任务上的成功率，不是官方逐动作 agent
协议的严格复现。MobileWorld `traj.json` 中一个 step 对应一次完整 agent run，不代表一次
真实 GUI 操作；真实动作、模型消息、截图和 ledger 以各 agent 自己的运行目录为准。官方
trajectory 的 step count、token usage 和逐动作截图不得用于不同 adapter 协议间的横向比较。

## 前置条件

- Linux、Docker、`/dev/kvm` 和足够的磁盘空间
- 宿主机需要 Docker CLI、Docker socket 权限、`uv`、官方 MobileWorld checkout 和可选的
  `.env`；Node、npm 依赖、pi-gui 编译产物、ADB、AVD 和 emulator 仍在派生 worker 镜像中
- 模型凭据位于仓库的 `.env`，鉴权方式见 [authentication.md](authentication.md)

`pi-gui-agent/mobileworld:latest` 基于官方 MobileWorld 镜像，一次性安装 Node 22、npm
依赖并编译 pi-gui，同时加入 runner 和 adapter。该镜像只作为 worker 环境镜像使用；控制器
和官方 `mw env run`/`mw eval` 在宿主机的 MobileWorld checkout 中通过 `uv` 运行。

构建脚本先尝试官方 GHCR，失败后自动使用
`ghcr.nju.edu.cn/tongyi-mai/mobile_world:latest`。`HTTP_PROXY`/`HTTPS_PROXY` 会传给
镜像构建，但不会固化到最终镜像；Node 下载和 npm 安装失败时自动尝试 npmmirror。
运行时模型代理可与其他凭据一同写入 `.env`，由 worker 内 agent 读取。

## 运行

```bash
# 可选：显式预构建；正常运行会命中缓存并检查镜像内容
./scripts/build-mobileworld-image.sh

# 只检查生成的官方容器和 evaluator 命令
./scripts/run-mobileworld-experiment.sh --dry-run

# 指向官方 MobileWorld checkout，然后启动或复用容器池并并发评测
export MOBILEWORLD_ROOT=/path/to/MobileWorld
./scripts/run-mobileworld-experiment.sh

# 两个任务的基础设施 smoke
./scripts/run-mobileworld-experiment.sh configs/mobileworld/smoke.toml

# 不再需要环境池时显式释放
./scripts/stop-mobileworld-containers.sh
```

接口只有一份 TOML。`experiment.workers` 同时决定官方环境容器数和 evaluator 的
`max_concurrency`。`suite.tasks = []` 表示全部 GUI-only 任务；指定任务时填写类名数组：

```toml
[experiment]
name = "mobileworld-smoke"
workers = 2

[suite]
tasks = ["OpenFlightModeTask", "CloseFlightModeTask"]
```

每个任务独占一个从并发队列取出的 MobileWorld 环境。adapter 根据任务 backend 的宿主
端口反查同一容器，并通过 `docker exec` 在该容器内使用 `emulator-5554`，避免并发任务
连接到错误设备；宿主机只运行 MobileWorld 控制器，不运行 ADB 或 emulator。

## 配置

`agent` 支持 `provider`、`model`、`thinking`、`learning`、超时、动作上限、单轮 token
上限和动作等待时间。`provider` 与 `model` 必须同时填写或同时省略。并发实验默认关闭
learning，避免跨任务学习改变 benchmark 条件。pi-gui 已位于镜像的
`/opt/pi-gui-agent`，runner 不再向每个容器复制 `node_modules`；宿主机不需要安装 Node
或 Android platform-tools。

`mobileworld` 控制派生镜像、容器名前缀、端口起点、启动间隔和容器保留策略。默认
`reuse_containers = true` 且 `keep_containers = true`：首次运行创建健康环境池，后续运行
直接复用；worker 数变化、镜像 digest 变化或池中出现 unhealthy 容器时会重建该池。
构建脚本记录镜像所含源码的统一指纹；修改 pi-gui、runner 或 adapter 后都会重建该镜像。
镜像 digest 变化时，已启动的 emulator 池会随之重建。
派生镜像使用 MobileWorld 自带的 `init_state` AVD snapshot，并以 `--no-sync` 启动 Python
服务，减少首次启动和重复构建 evaluator 包。实际启动时间仍受宿主 KVM/CPU 竞争影响。

`mobileworld.proxy_url` 可配置宿主机上的 loopback HTTP 代理，例如
`http://127.0.0.1:7892`。runner 会在实验期间启动一个 TCP relay，再通过 worker relay 和
`adb reverse` 为 Android 设置全局 HTTP 代理；因此 Chrome/Maps 等 app 和 agent 模型请求
都会使用该代理。`proxy_relay_port` 默认是 `17892`，该端口在实验期间必须空闲。

结果写入：

```text
benchmark-results/<name>-<timestamp>/
├── manifest.json
├── trajectories/          MobileWorld 官方 traj.json、截图和 result.txt
└── pi-runs/               pi session 与 adapter 结果
```

`trajectories/<task>/` 按官方任务名组织，记录初始化 observation、整段 agent 返回值和最终
评分；`pi-runs/<session>/` 按 agent session UUID 组织，记录内部每次 GUI 动作。两者粒度
不同：前者的一个 step 可以对应后者的多个 actions。

每个 `pi-runs/<session>/ledgers/` 保存该任务的 execution ledger；整个 session 从环境
容器回收时一并复制。`pi-runs/learning/` 保存本次实验的可选跨任务 learning 数据，
不会写回仓库 `.pi`。
同一 `.env` 通过宿主机绝对路径只读挂载到控制进程，再由官方 `mw env run` 挂载到环境
容器，供其中的 pi-gui 进程读取。`manifest.json` 记录实际镜像 digest、容器、命令、任务数和成功率，
不记录 `.env` secret。

smoke 测试可把 `suite.tasks` 限为两个 settings 任务，并将 `agent.max_actions` 设为较小
值；runner 仍会启动完整 emulator。结果中的 `recorded_tasks` 应等于指定任务数，即使
无模型执行器导致成功率为 0，也不能把缺失结果误报为完成。
