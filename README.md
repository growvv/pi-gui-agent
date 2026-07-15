# pi-gui-agent

一个以 [`pi-coding-agent`](https://github.com/badlogic/pi-mono) 为运行时的 Android **GUI + CLI 混合 Agent**。它不是为了复现一个只能点按、滑动和输入的 GUI Agent，而是把 Android 视觉操作接入 coding agent 原生的 agentic loop：同一个模型可以观察屏幕、操作 UI，也可以通过 `bash`、ADB 和临时脚本直接查询或修改系统，并根据两条通道的反馈继续决策。

项目借鉴了 Mobile-Agent-v3.5 的视觉操作与长任务经验，但没有复刻其 Manager、Executor、Reflector 和 Notetaker 的手工多角色编排；规划、工具选择、执行、观察和纠错均由一个原生 pi tool loop 完成。

当前架构与行为语义见 [`docs/design.md`](docs/design.md)。Mobile-Agent-v3.5 迁移说明与 Hermes 核心学习闭环分别见 [`docs/mobile-agent-v3.5.md`](docs/mobile-agent-v3.5.md) 和 [`docs/hermes.md`](docs/hermes.md)。

AndroidWorld benchmark 适配与运行方法见 [`benchmark/README.md`](benchmark/README.md)。

## 项目定位

本项目探索的是一种 **coding-agent-native computer-use harness**：以成熟 coding agent 的 agentic loop 为调度与决策核心，把 GUI 作为新增的观察和动作通道，同时保留 CLI、文件编辑、代码执行、session、上下文压缩和 Skill 等原生能力。这里的 *harness* 指模型之外负责工具装配、反馈回传、循环控制、状态记录和任务生命周期的运行时，而不是一个新的基础模型或 GUI grounding 算法。

与动作空间仅包含点按、滑动、键盘输入等 UI 操作的 Agent 相比，这种组合有三个主要动机：

- **更高的执行效率**：能由 ADB 或系统命令确定完成的操作不必在多级菜单中逐屏导航；重复或批量操作可以临时编写脚本执行
- **更充分的决策依据**：模型同时获得截图、UIAutomator 可见文本、CLI 标准输出和可查询的系统状态，能够交叉验证 UI 表象与底层结果
- **更强的任务扩展能力**：`bash`、`read`、`write`、`edit` 不只是额外执行器，也让 Agent 能现场检查数据、编写小工具，并由同一个 loop 决定何时使用 GUI、CLI 或两者组合

这些是架构上的预期优势，不应在没有对照实验时直接等同于已证明的成功率提升。项目通过 AndroidWorld 评测任务完成率；若要证明混合 harness 优于纯 GUI，需要在相同模型、任务集和预算下比较成功率、耗时、token、模型轮次与 UI 动作数。

这条路线并非没有先例。Anthropic 的 computer-use 参考实现把 computer tools 与沙箱化 `bash`/Python 放在同一 agent loop 中；Microsoft UFO² 明确使用 Hybrid GUI-API Actions；pi 本身则把自己定义为可扩展的 terminal coding harness。因而更准确的说法是：本项目把正在形成的 **GUI 与程序化工具混合调用** 思路，具体落到 Android、ADB 和 pi 原生 tool loop 上，而不是宣称发明了这一类别。

相关资料：

- [Anthropic Computer Use Best Practices reference implementation](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-best-practices)
- [Microsoft UFO²: Hybrid GUI-API Actions](https://microsoft.github.io/UFO/ufo2/core_features/hybrid_actions/)
- [pi coding agent: minimal terminal coding harness](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent)
- [Mobile-Agent-v3.5](https://github.com/X-PLUG/MobileAgent/tree/main/Mobile-Agent-v3.5)

## 特点

- 每次改屏操作后等待界面稳定，并把新截图和 UIAutomator 可见文本返回给模型，形成 observe-act-verify 循环
- 坐标统一为 `0..1000`，不绑定 Android World 的 `1080x2400` 分辨率
- 通过 ADB 连接 Android World 模拟器、普通模拟器或真机；可选 ADB Keyboard 提供可靠的 Unicode 和多行输入
- 允许模型通过内置 `bash` 直接运行 ADB/CLI，确定性系统操作无需绕行 GUI
- 使用 TypeBox 定义 8 个原子 Android GUI 工具和 2 个生命周期工具，不再解析模型输出中的 JSON 文本
- `search_tools` 会列出并按需增量加载 `observe`、`tap`、`long_press`、`swipe`、`type_text`、`system_button`、`open_app`、`wait`
- `answer`、`finish` 作为生命周期工具常驻：前者记录信息查询结果，后者显式标记任务完成
- 保留 pi 内置的 `read`、`bash`、`edit`、`write` 工具
- 默认在每次主 session 后复盘轨迹，持久化 memory，并按需创建或迭代可复用 skill
- pi 原生管理模型、鉴权、重试、上下文压缩和 session 记录

## 环境

- Node.js 22.19+
- Android SDK Platform Tools，确保 `adb devices` 能看到目标设备
- 推荐在目标设备安装 [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard)，用于中文、emoji、复杂标点和多行文本输入
- 至少配置一种 pi 支持的模型。可使用环境变量，例如 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`，也可先运行全局安装的 `pi` 完成 `/login`

安装：

```bash
npm install
npm run build
```

安装 ADB Keyboard APK 后可确认包名：

```bash
adb install -r /path/to/ADBKeyboard.apk
adb shell pm path com.android.adbkeyboard
```

`type_text` 会自动检测并临时切换到 ADB Keyboard，通过 UTF-8 Base64 广播输入，任务结束后恢复原输入法。未安装时会警告一次并回退到 `adb shell input text`，任务仍会继续，但 Unicode、换行或特殊字符可能无法正确保留。

## 使用

使用 pi 当前配置的默认模型：

```bash
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

常用参数：

```text
--adb <path>          adb 可执行文件路径
--serial <serial>     adb 设备序列号
--max-actions <n>     最大改屏操作数，默认 30
--settle-ms <ms>      操作后等待 UI 稳定的时间，默认 1500
--thinking <level>    pi thinking level，默认 medium
--result-file <path>  写入 {finished, answer} JSON 结果
--no-learning         关闭任务后的 memory/skill 自动复盘
```

`--provider` 和 `--model` 必须同时提供；省略时使用 pi 当前配置的默认模型。运行轨迹由 pi 写入 `runs/`。

`finish` 是显式完成标记，不会直接终止 pi tool loop。主 session 在模型停止调用工具后结束；如果此前没有调用 `finish`，进程退出码为 `2`，便于调用方区分正常完成与模型提前停止。信息查询型任务应先调用 `answer`，其值会写入 `--result-file` 的 `answer` 字段。

## 迁移与学习设计

Mobile-Agent-v3.5 的组件映射、保留能力、设计变化和 AndroidWorld 公平评测条件见 [`docs/mobile-agent-v3.5.md`](docs/mobile-agent-v3.5.md)。任务后的 Memory/Skill 学习流程见 [`docs/hermes.md`](docs/hermes.md)。

Android World 的任务生成、应用安装和 reward evaluator 属于评测环境，不复制到 agent 内。先按上游 `android_world_v3.5` 文档启动配置好的模拟器，再用本项目连接对应的 ADB serial 即可。

## 开发

### 验证

```bash
npm test
npm run build
```

`npm test` 运行 Vitest 单元测试和 AndroidWorld Python adapter 测试；`npm run build` 使用项目 `tsconfig.json` 执行 TypeScript 类型检查与编译。

### 代码结构

- `src/adb.ts`：封装 ADB 进程调用、截图、UIAutomator 文本提取、触控、按键、应用启动和输入法管理
- `src/tools.ts`：定义模型可调用的 Android 原子工具、动作预算，以及每次操作后的截图和可见文本反馈
- `src/agent.ts`：创建 pi session，选择模型，装配内置工具与 Android 工具，注入初始截图、Memory 和 Skill，并管理主任务生命周期
- `src/learning.ts`：管理 Memory/Skill 的读取与持久化，压缩主任务轨迹，并运行隔离的任务后复盘 session
- `src/prompt.ts`：定义 GUI 与 CLI 混合调用、结果验证和安全边界等系统指令
- `src/cli.ts`：解析命令行参数，调用主 session，并负责结果文件和进程退出码
- `benchmark/android_world_agent.py`：把一次 AndroidWorld episode 适配为一次完整的 Node CLI 任务

主要依赖方向为 `cli.ts -> agent.ts -> tools.ts -> adb.ts`。`learning.ts` 由 `agent.ts` 调用，但不依赖 Android 工具实现，以保证任务执行与任务后学习相互隔离。

### Memory 与 Skill

学习产物默认保存在 `.pi/learning/`：

```text
.pi/learning/
├── MEMORY.md
└── skills/
    └── <skill-name>/
        └── SKILL.md
```

- `MEMORY.md` 保存可跨任务复用的稳定事实和用户偏好，不用于记录一次性任务细节
- `skills/*/SKILL.md` 保存可复用的程序性知识，例如特定任务类别的操作步骤、验证方法和常见失败恢复策略
- 项目预置的 `android-world-task-strategies` Skill 总结了 Mobile-Agent-v3.5 在 Chrome onboarding、Audio Recorder、exact duplicate 和多屏信息收集等任务上的经验

每次主任务启动时，历史 Memory 会被注入 system prompt，已有 Skill 由 pi 原生 Skill 机制发现并按需加载。主 session 结束后，系统默认使用同一模型和 `low` thinking 创建独立的 review session；输入是移除图片并截断后的任务轨迹，review session 只能调用 `save_memory`、`list_skills`、`read_skill` 和 `upsert_skill`，不能继续操作设备或使用普通文件与 shell 工具。

复盘失败只会通过学习错误回调报告，不会改变主任务的完成状态。使用 `--no-learning` 会跳过本次任务后的复盘和写入，但仍会读取并使用已有 Memory 与 Skill。更完整的生命周期、数据约束和已知限制见 [`docs/design.md`](docs/design.md#学习存储与复盘)。
