# pi-gui-agent

pi-gui-agent 是一个基于 `pi-coding-agent` 的 Android GUI + CLI Agent。同一个
agent loop 可以观察截图和 UIAutomator 文本，调用原子 GUI 工具，也可以用 pi
内置的 `bash`/ADB 完成确定性查询与操作。

仓库明确分为三部分：

| 部分 | 目录 | 职责 |
| --- | --- | --- |
| pi-gui 核心 | `agents/pi_gui/` | TypeScript agent、ADB、工具、learning、skills |
| 对比 Agent | `baselines/` | Claude Code、Codex、OpenClaw 的独立适配器 |
| 实验系统 | `experiments/` | AndroidWorld、MobileWorld 配置与并行调度 |

架构和运行语义见 [docs/architecture.md](docs/architecture.md)，所有文档入口见
[docs/README.md](docs/README.md)。

## 核心能力

- `screenshot` 观察工具，以及 `tap`、`long_press`、`swipe`、`type_text`、`open_app`、`back` 六个原子 GUI 动作工具
- 每次动作后返回新截图、截图 fingerprint、归档路径和可见 UI 文本
- `0..1000` 归一化坐标，运行时从 PNG 读取真实屏幕尺寸
- progress guard 检测重复动作和无变化页面
- 轻量 execution ledger 记录任务拆解、子任务完成、可选反思、语义 validation 和 finish
- ledger 默认写入 `.pi/ledgers/`；benchmark 将其重定向并收集到实验结果目录
- 可选的任务后 Memory/Skill 复盘
- AndroidWorld 官方 suite、setup、checkpointer 和 evaluator
- MobileWorld 官方环境容器、任务初始化和 evaluator（GUI-only）

## 本地运行

要求 Node.js 22.19+、ADB 可访问的 Android 设备，以及至少一个 pi 支持的模型。

```bash
cd agents/pi_gui
npm install
npm run build
npm start -- "Open Settings and turn Wi-Fi on"
```

指定设备和模型：

```bash
npm start -- \
  --serial emulator-5554 \
  --provider anthropic \
  --model claude-sonnet-4-5 \
  "Create a note named Shopping in Markor"
```

模型鉴权见 [docs/authentication.md](docs/authentication.md)。复杂文本输入建议在设备上
安装 [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard)。

## AndroidWorld 实验

实验只接收一份 TOML 配置；不再通过两层 CLI 和 Docker 命令透传大量参数。

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
./scripts/prepare-androidworld-cache.sh
./scripts/build-images.sh

# 检查配置、任务分片和容器命令，不启动 worker
./scripts/run-main-experiment.sh --dry-run

# 主试验、三组 baseline、两组 ablation
./scripts/run-main-experiment.sh
./scripts/run-baseline-experiments.sh
./scripts/run-ablation-experiments.sh
```

公共参数在 `configs/androidworld/common.toml`，各实验配置只覆盖差异。完整配置说明、
容器要求、结果结构和保留成绩见 [docs/experiments.md](docs/experiments.md)。

## MobileWorld 实验

MobileWorld 同样只接收一份 TOML。启动脚本会用 Docker 缓存构建派生镜像，在临时控制
进程中启动官方 evaluator 和独立环境容器，并发执行任务。评测采用统一的 external
black-box agent 口径：pi-gui、Claude Code 和 Codex 各自在一次调用中运行完整 agent
loop，最终设备状态由官方 evaluator 判分；这不等同于官方逐动作 agent 协议：

```bash
./scripts/build-mobileworld-image.sh  # 可选；运行入口也会命中 Docker 构建缓存
./scripts/run-mobileworld-experiment.sh --dry-run
./scripts/run-mobileworld-experiment.sh
./scripts/stop-mobileworld-containers.sh  # 不再复用环境池时
```

默认配置在 `configs/mobileworld/main.toml`，详细要求和当前支持范围见
[docs/mobileworld.md](docs/mobileworld.md)。

## 目录

```text
agents/pi_gui/             pi-gui 核心实现
baselines/                 Claude Code、Codex、OpenClaw
experiments/androidworld/  AndroidWorld 执行与报告
experiments/mobileworld/   MobileWorld adapter 与容器生命周期
configs/androidworld/      可继承的实验配置
configs/mobileworld/       MobileWorld 实验配置
docker/androidworld/       worker 镜像与 emulator entrypoint
scripts/                   简洁的实验和构建入口
tests/                     Python 上层测试
docs/                      设计、实验、鉴权与来源说明
benchmark-results/         本地运行产物（Git ignored）
```

依赖方向是 `experiments -> pi-gui adapter / baselines -> shared process adapter`。
baseline 不进入 pi-gui 核心，Docker 和启动脚本也不与 Python 项目逻辑混放。

## 验证

```bash
python3 -m unittest discover -s tests -p 'test_*.py'

cd agents/pi_gui
npm test
npm run build
```

当前 Python 测试覆盖配置继承与严格校验、任务分片、容器命令、AndroidWorld/MobileWorld
adapter 和报告解析；TypeScript 测试覆盖 ADB、工具、ledger、learning 与任务生命周期。
