# 架构

本文描述当前代码边界和关键运行语义。实验使用方式见
[experiments.md](experiments.md)，模型配置见 [authentication.md](authentication.md)。

## 边界

项目由三个相互独立的层次组成：

```text
agents/pi_gui/                 pi-gui 产品核心
├── src/                       TypeScript 实现
├── skills/                    内置执行 skill
└── test/                      核心单元测试

baselines/                     对比实验实现
├── claude_code.py
├── codex.py
└── openclaw.py

experiments/androidworld/      评测系统
├── agent.py                   CLI Agent 的公共 AndroidWorld 进程适配
├── pi_gui_agent.py            pi-gui 到公共适配器的薄桥接
├── factory.py                 评测 Agent registry
├── config.py                  TOML/worker JSON 配置契约
├── parallel.py                宿主分片和容器生命周期
├── run.py                     容器内官方 AndroidWorld suite
└── report.py                  静态轨迹报告

experiments/mobileworld/       MobileWorld 评测系统
├── agent.py                   pi-gui 动态 agent adapter
├── config.py                  TOML 配置契约
└── run.py                     官方容器与 evaluator 生命周期
```

`docker/androidworld/` 只保存镜像定义和 emulator entrypoint，`scripts/` 只保存用户
入口，`tests/` 只保存 Python 测试。项目逻辑不再放在 runner 脚本目录中。

AndroidWorld 的任务 registry、应用初始化、模拟器环境、checkpointer 和 reward evaluator
都属于上游。`run.py` 直接调用这些官方接口，不再针对 Expense、VLC、SMS、OsmAnd 或
Contacts 注入运行时 fallback。

MobileWorld 保持独立边界：容器化 runner 启动上游官方环境容器和 evaluator，adapter
仅把一个 GUI-only episode 交给 pi-gui。它不复用 AndroidWorld 的 suite、checkpointer
或 emulator entrypoint。

## pi-gui 内部

核心依赖方向保持单向：

```text
cli.ts -> agent.ts -> tools.ts -> adb.ts
                  -> learning.ts
```

`runTask()` 完成一个完整任务：初始化鉴权和模型，创建 ADB 设备与工具，载入 Memory
和 skills，发送任务与初始截图，运行 pi session，执行可选复盘，最后恢复输入法并释放
session。

### 工具

GUI 工具包括只读的 `screenshot`，以及 `tap`、`long_press`、`swipe`、`type_text`、`open_app` 和 `back`。
它们都计入 `maxActions`。直接通过 pi `bash` 执行的 ADB、模型轮次和 learning review
不计入该预算，因此 `maxActions` 不是完整成本上限。

执行记录工具包括 `update_ledger`、`reflect_on_ledger`、`validate_ledger`、`answer`
和 `finish`。ledger 默认保存在 `.pi/ledgers/`，也可通过 `--ledger-dir` 指向实验结果
目录；只允许通过受控工具修改。它只记录原始任务、子任务拆解与完成、GUI agent
自主触发的反思和任务完成，不复制截图 evidence，
也不做字段级严格闭合。`validate_ledger` 通过 prompt 参数记录 GUI agent 对任务和子任务
是否完成的语义判断；最近一次 ledger 更新后调用过 validation 即可调用 `finish`，不以
静态 schema 通过与否作为完成 gate。`finish` 设置 `state.finished = true`，但不会强制
终止模型 loop。

### 观察和上下文

初始观察与每次 GUI 动作都包含 PNG 和 UIAutomator 可见文本。坐标使用 `0..1000`
归一化空间，映射所需的宽高直接从当前 PNG 读取。

原始截图归档到 session 的 `screenshots/`。发送下一次 provider 请求前，只保留最新
截图；历史图片替换为文本占位，但 fingerprint、时间、可见文本和磁盘原图仍保留。
progress guard 使用截图/动作历史检测无变化和重复操作，在阈值后要求换策略，达到更高
阈值时终止停滞 loop。

### 文本输入

`type_text` 要求 UIAutomator 中存在已聚焦的 editable 节点。设备装有 ADB Keyboard
时，文本通过 UTF-8 Base64 广播输入，并在任务结束后恢复原 IME。没有 ADB Keyboard
时使用 Android `input text`，仅适合简单 ASCII；复杂文本不应依赖这条兼容路径。

### Learning

learning 数据默认写到 `.pi/learning/`：`MEMORY.md` 保存稳定事实，`skills/*/SKILL.md`
保存类别级流程，`reviews.jsonl` 保存复盘审计。review session 与主 session 隔离，只能
调用 learning 工具。`--no-learning` 关闭当前任务后的写入，但不会阻止读取已有内容。

## AndroidWorld 执行链

宿主入口 `parallel.py` 读取并严格校验 TOML，查询容器中的官方任务 registry，按任务
complexity 均衡分片。每个 worker 只生成一份 `/output/config.json`，Docker 命令只把
这一个路径传给 `run.py`。代码和镜像内的 Node/MCP/baseline CLI 均为只读固定资源；
宿主只挂载结果目录和 APK 下载缓存；AVD 始终使用容器内临时目录。

worker entrypoint 启动 emulator 并等待 boot complete。`run.py` 为容器内动态的
accessibility gRPC 端口建立 ADB reverse，然后调用官方 setup、suite 和 checkpointer。
这是容器网络适配，不修改应用 setup 或 task 初始化语义。

启动脚本从 `/opt/android-avd-template` 初始化容器内的 AVD 目录，不保存 Quickboot snapshot；
启动时会清理由强制退出遗留的 lock。AndroidWorld 的
`download_app_data` 和 accessibility forwarder 下载器
只在对应缓存文件存在时读取本地文件；缓存缺失时仍使用上游 URL，因此预热脚本不是运行
时必需条件。

默认使用 `suite.setup_mode=always`，避免依赖跨运行的模拟器状态。AndroidWorld 在每个
episode 前后恢复任务涉及应用的 data snapshot；`always` 用于强制重做 setup，`never` 只适用于
已经准备好的镜像或基础设施 smoke。

每个 AndroidWorld episode 对 pi-gui 或 baseline 调用一次进程级 `step(goal)`。进程在
episode 内只运行一次；AndroidWorld 的 `_max_steps` 映射成 GUI action budget。最终成功
仍由官方 evaluator 根据设备状态评分。

## MobileWorld 执行链

宿主入口 `scripts/run-mobileworld-experiment.sh` 在官方 MobileWorld checkout 的 `uv`
环境中运行 `experiments.mobileworld.run`；控制器通过上游 `mw env run` 启动独立容器，再
调用 `mw eval`。`experiment.workers` 同时控制环境数和 evaluator concurrency；
上游线程队列保证一个任务执行期间独占一个环境。

MobileWorld 动态 agent adapter 根据当前 backend 端口反查同一容器，运行时通过
`docker exec` 在该容器内部调用 Node、ADB 和 `emulator-5554`，然后返回终止动作。任务
初始化、截图轨迹、tear-down 和 reward 仍由官方 runner 执行。benchmark 专用 app map
作为单次 CLI 参数传入，不改变 AndroidWorld 的同名应用映射。

派生 worker 镜像预装 Node 22、npm 依赖、pi-gui 编译产物、runner/adapter 和 AVD；宿主
checkout 提供官方 Python evaluator。worker 使用 `init_state` snapshot 启动 emulator，
以 `uv --no-sync` 启动服务。MobileWorld 轨迹、pi sessions 和可选 learning 写回本次结果
目录；模型凭据不会进入 manifest。

这是有意选择的 external black-box agent 协议：pi-gui、Claude Code 和 Codex 分别在一次
官方 `predict()` 调用内完成自己的完整 agent loop，随后由官方 evaluator 检查最终设备
状态。它复用官方任务与评分，但不复现逐动作 `JSONAction` 协议；官方 trajectory 的一个
step 对应整个 agent run，实际 GUI actions 应从 agent 自身轨迹读取。只有使用相同黑盒
整段执行协议的完整 agent 系统才进入同一成绩比较。

## 对比 Agent

Claude Code、Codex 和 OpenClaw 各自拥有命令构造与认证方式，共享的只有：任务 prompt、
ADB/MCP 目标、超时、进程降权和 AndroidWorld 返回结构。失败后不会自动创建新 session
或跨 session retry，避免把 baseline 的额外尝试隐藏在 adapter 中。

Claude Code、Codex 和 OpenClaw 都通过 pi-gui 的 Android MCP server 使用相同原子
GUI 工具。三者默认不注册 ledger 工具；实验配置显式开启 `enable_ledger_tool` 后，
才会增加独立的 ledger MCP server。OpenClaw 的本次运行配置从其独立 worker 配置派生，
并会移除模板中其他 MCP server，避免污染实验动作空间。所有实现都由 AndroidWorld
官方 evaluator 评分。

pi-gui 默认保持原有 ledger 工具、`ledger-use` skill、system prompt 和完成校验语义。
仅当配置显式设置 `disable_ledger_tool = true` 时，才移除 ledger 工具及对应 prompt；
此时 `answer` 和 `finish` 也不会注册；agent 正常结束模型响应时直接完成任务，并使用
最后一条 assistant 文本作为问答结果，不会创建 execution ledger。

## 已知限制

- 固定 `settleMs` 不等同于真正的 UI 稳定检测。
- UIAutomator 无法完整描述 Canvas、WebView 等渲染内容。
- GUI action budget 不覆盖 bash、模型调用和 token。
- 进程级 adapter 的 AndroidWorld `episode_length` 不能与逐动作 Agent 直接比较。
- MobileWorld 黑盒 adapter 的官方 step、token usage 和逐动作截图不代表 agent 内部轨迹。
- learning 会改变后续 episode 条件，实验必须明确是否启用及是否复用存储。
