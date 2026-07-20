# AndroidWorld 实验

AndroidWorld 实验使用官方任务 registry、setup、suite、checkpointer 和 evaluator。
宿主负责均衡分片与 Docker 生命周期，每个 worker 在独立容器和 emulator 中执行。

## 前置条件

- Linux、Docker、可用的 `/dev/kvm`
- Python 3.10+，安装 `requirements.txt`
- 已构建的 `android_world:latest` 基础镜像
- 模型凭据写入 Git ignored 的 `.env`，或通过 `container.forward_env` 显式转发

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
./scripts/prepare-androidworld-cache.sh
./scripts/build-images.sh
```

镜像分为公共 AndroidWorld/pi-gui MCP 基础层，以及 `pi-gui`、`claude-code`、`codex`、
`openclaw` 四个 Agent 层。Dockerfile 固定 Node 和三个 baseline CLI 版本，避免实验实现
随 `latest` 漂移。Node、MCP 和 baseline CLI 都在镜像构建阶段安装，运行时不访问 npm。
容器以只读方式挂载代码；结果只写入 worker 输出目录。

`prepare-androidworld-cache.sh` 会并行预下载 AndroidWorld 使用的 APK、地图数据、
Accessibility forwarder 和 ADB Keyboard 到 `~/.cache/pi-gui-agent/androidworld/`。
worker 只读挂载该目录，缺失资源才回退到 AndroidWorld 官方下载地址。

## 启动

```bash
# 主试验
./scripts/run-main-experiment.sh

# Claude Code、Codex、OpenClaw，依次运行
./scripts/run-baseline-experiments.sh

# no-learning、medium-thinking，依次运行
./scripts/run-ablation-experiments.sh
```

所有脚本都只调用同一个入口：

```bash
python3 -m experiments.androidworld.parallel <config.toml>
python3 -m experiments.androidworld.parallel configs/androidworld/smoke-fix.toml
```

运行时会在终端实时汇总所有 worker 的输出，并用 `[worker-N]` 标识来源；每 15 秒还会
输出 worker 存活数和已记录 episode 数。无前缀的完整输出仍保存在各 worker 的
`worker.log` 中。

加 `--dry-run` 会完成配置校验、容器 registry 查询和任务分片，并打印最终命令，但不
创建结果目录或启动 worker。baseline/ablation 脚本会把该参数传给每组配置。
worker 数不能大于选定的 task template 数，避免产生空分片。

## 配置组织

`configs/androidworld/common.toml` 保存共同的 suite 和容器参数。其他配置通过顶层
`extends = "..."` 继承它，只覆盖实验差异：

- `main.toml`：pi-gui、high thinking、learning 开启
- `baseline-{claude-code,codex,openclaw}.toml`：三个独立对比 Agent
- `ablation-no-learning.toml`：关闭任务后复盘
- `ablation-medium-thinking.toml`：降低 thinking level

每个 worker 使用容器内全新的 AVD，实验之间不会共享模拟器状态；下载资源仍共享宿主只读
cache。

继承可以多层使用，路径相对当前配置文件。未知 section 或字段会直接报错，不会静默
忽略拼写错误。

### `experiment`

| 字段 | 含义 |
| --- | --- |
| `name` | 结果目录前缀，必填 |
| `workers` | 并行容器数 |
| `output_root` | 相对仓库根目录的结果根目录 |

### `agent`

| 字段 | 含义 |
| --- | --- |
| `name` | `pi-gui`、`claude-code`、`codex` 或 `openclaw` |
| `provider` / `model` | pi-gui 显式模型，两者必须同时提供或省略 |
| `thinking` | Agent thinking level |
| `learning` | pi-gui 任务后复盘开关 |
| `openclaw_model` | OpenClaw 模型 ID |

### `suite`

| 字段 | 含义 |
| --- | --- |
| `family` | AndroidWorld registry family |
| `tasks` | 可选 task template 数组；省略表示全量 |
| `combinations` / `seed` | 任务组合数与随机种子 |
| `fixed_task_seed` | 是否对任务使用相同参数 |
| `setup_mode` | `always` 每次 setup；`auto` 首次 setup 后复用 marker；`never` 跳过 |
| `timeout_seconds` | 每个 Agent 进程的总超时 |
| `action_budget_multiplier` / `min_actions` | AndroidWorld step 到 GUI budget 的映射 |
| `max_model_tokens` / `settle_ms` | 模型单轮 token 上限与动作后等待时间 |

#### 动作与模型预算

每个 episode 的 GUI 操作上限根据 AndroidWorld 任务的 `_max_steps` 计算：

```text
max_actions = max(min_actions, ceil(_max_steps * action_budget_multiplier))
```

默认 `action_budget_multiplier = 2.0`、`min_actions = 30`。例如任务的
`_max_steps = 20` 时允许 40 次 GUI 操作；`_max_steps = 10` 时计算结果为 20，
但受最低值约束，最终允许 30 次。点击、滑动和文本输入等设备动作计入该预算；模型
思考、观察和普通 bash 操作不计入。

`max_model_tokens = 4096` 限制每一轮模型响应的最大输出，而不是整个 episode 的累计
token；多轮调用的总量可以超过该值。`settle_ms = 1500` 表示每次设备动作后等待
1500ms，再截图并读取界面状态。等待过短可能捕获动画或加载中的界面，等待过长则会
增加总运行时间。

### `container`

| 字段 | 含义 |
| --- | --- |
| `image` | 可选镜像；默认 `pi-gui-agent/<agent>:latest` |
| `name_prefix` | worker 容器名前缀 |
| `env_file` | 相对仓库根目录的 Docker env file |
| `proxy_url` | 可选 HTTP/HTTPS/ALL proxy |
| `download_cache_dir` | 可选宿主下载缓存，只读挂载到 `/download-cache` |
| `keep_containers` | 默认 `false`；设为 `true` 时保留运行结束后的 stopped worker 容器 |
| `forward_env` | 从宿主显式转发的环境变量名数组 |
| `agent_config_dir` | baseline 的宿主配置模板目录；OpenClaw 会为每个 worker 复制为独立可写目录 |

OpenClaw 默认配置模板使用 `~/.openclaw`，runner 会为每个 worker 创建独立可写副本。
若完全通过环境变量配置，可删除对应 TOML 字段；
若指定的目录或 env file 不存在，runner 会在启动前失败。

保留容器主要用于运行后检查，不复用下一次实验的 `/output` mount。开启时容器名包含本次
run 时间戳，可用 `docker ps -a` 查看，检查完成后用 `docker rm <name>` 删除。快速重复
每次 worker 启动都会从镜像模板初始化 AVD。

## 输出和停止

结果写入：

```text
benchmark-results/<experiment-name>-<timestamp>/
├── manifest.json
├── status.json
├── results.md
└── worker-N/
    ├── config.json
    ├── worker.log
    ├── emulator.log
    ├── checkpoints/
    ├── ledgers/              每个 episode 的托管 execution ledger
    ├── runs/
    └── learning/
```

pi-gui 的 `--ledger-dir` 固定为 `/output/ledgers`，因此 ledger 脚本直接写入 worker
结果目录。CLI 结果和 AndroidWorld attempt 元数据同时记录 `ledgerPath`/`ledger_path`，
用于把 checkpoint、trajectory 与对应 ledger 关联起来。

`Ctrl-C` 会向 worker 进程组发送中断并删除对应容器。`manifest.json` 保存配置来源、
任务分片和容器命令，但不写入 env file 中的 secret。

### 启动速度和复现性

每个 worker 都从镜像内的 `/opt/android-avd-template` 初始化容器内临时 AVD，并使用
`setup_mode=always` 执行完整 app setup。每个 episode 的 `initialize_task()` 和
`tear_down()` 都会恢复该任务声明的 app snapshot；worker 退出后模拟器状态随容器删除。

该 reset 是任务级应用数据恢复，不是整个 emulator snapshot：未被任务声明的应用、共享
存储和部分系统全局状态仍可能保留。worker 退出后容器内状态会被清理，下一次运行重新
从镜像模板启动。

生成静态轨迹报告：

```bash
python3 -m experiments.androidworld.report \
  --result-dir benchmark-results/<run> \
  --output-dir /tmp/androidworld-report
```

补跑结果可按先原始、后补跑的顺序合并；后面的同名任务覆盖前面的结果：

```bash
python3 -m experiments.androidworld.report \
  --result-dir benchmark-results/<original-run> \
  --result-dir benchmark-results/<retry-run> \
  --output-dir benchmark-results/<original-run>/report
```

## 保留结果

仓库本地只保留
`benchmark-results/androidworld-full-v5-query-validator-20260718/`：

- 模型：`xiaomi-token-plan-cn/mimo-v2.5`
- thinking：`high`
- 任务：116 个 template，seed 30，每项 1 个 combination
- recorded：116；completed：115；exception：1
- successful reward sum：74.5
- completed success rate：64.78%
- episode runtime 总和：31,603 秒

该目录包含官方 checkpoints、worker 日志、manifest 和 116 个任务的静态轨迹报告。
它产生于本次目录/配置重构之前，因此 manifest 中的旧 `benchmark/...` 命令仅用于历史
审计；新实验必须使用本文的配置入口，不能复制旧 command 字段。
