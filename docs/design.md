# pi-gui-agent 设计

本文按当前实现说明 pi-gui-agent 的模块边界、运行时流程和行为语义。项目来源与外部系统的比较分别见：

- [从 Mobile-Agent-v3.5 迁移](mobile-agent-v3.5.md)
- [Hermes 核心学习闭环](hermes.md)

## 目标与边界

pi-gui-agent 是一个由 `pi-coding-agent` 驱动的 Android GUI + CLI 混合 Agent。它的目标不是复现一个纯 GUI Agent，而是把 Android 视觉操作作为 coding-agent harness 的新增通道：模型通过截图、UIAutomator 文本和一组原子工具观察并操作设备，也可以在存在确定性方案时使用 pi 内置的 `bash` 工具执行 ADB 命令、查询系统状态或编写临时脚本。两类工具由同一个 pi agentic loop 负责选择、调度和基于反馈继续决策。

这里的创新边界是 harness 的组合与 Android 落地，不是新的基础模型或 GUI grounding 算法。混合动作空间预期可以减少不必要的 UI 导航，并用 CLI 结果补充或验证视觉反馈；效率和任务完成率是否实际提升，仍需在相同模型、任务和预算下通过对照评测确认。

项目负责：

- ADB 设备 I/O
- Android 原子工具定义与动作预算
- pi session、模型和工具装配
- 任务结果与进程退出语义
- 可选的任务后 Memory/Skill 复盘
- AndroidWorld 的进程级适配

项目不负责准备 AndroidWorld 任务、安装评测应用、启动模拟器或计算 reward。这些仍由 AndroidWorld 环境完成。

## 代码结构

```text
src/
├── adb.ts       ADB 调用、截图、触控、文本输入和 UIAutomator
├── agent.ts     主 session、动态工具加载和任务生命周期
├── apps.ts      友好应用名到 Android package 的映射
├── cli.ts       CLI 参数、结果文件和退出码
├── learning.ts  Memory/Skill 存储及隔离 review session
├── prompt.ts    Android 操作系统提示词
└── tools.ts     TypeBox Android 工具及动作预算

benchmark/
└── android_world_agent.py  AndroidWorld 到 Node CLI 的适配器
```

依赖方向保持单向：`cli.ts -> agent.ts -> tools.ts -> adb.ts`。`learning.ts` 由 `agent.ts` 调用，不依赖 Android 工具。

## 主任务生命周期

`runTask()` 执行一个完整任务：

1. 创建 session 目录和 `LearningStore` 目录。
2. 初始化 pi 的鉴权、模型注册表和内存设置。
3. 根据 `provider/model` 选择指定模型；未指定时由 pi 使用当前默认模型。
4. 创建 `AdbDevice`、共享 `AgentState` 和 Android 工具集合。
5. 读取历史 Memory，并把 Memory、ADB executable 和 serial 注入 system prompt。
6. 注册 Android 工具和 `search_tools`，加载 learned skills。
7. 截取当前屏幕，把任务和初始 PNG 一起发送给主 session。
8. 主 session 结束后，在启用 learning 且模型可用时运行隔离复盘。
9. 在 `finally` 中恢复任务期间切换的输入法、取消事件订阅并释放 session。

模型的文本增量通过 `onText` 回调输出。`runTask()` 返回共享的 `AgentState`：

```ts
interface AgentState {
  actions: number;
  answer?: string;
  finished: boolean;
}
```

## 工具注册与动态加载

`createAndroidTools()` 定义 10 个工具。

设备与观察工具：

- `observe`
- `tap`
- `long_press`
- `swipe`
- `type_text`
- `system_button`
- `open_app`
- `wait`

生命周期工具：

- `answer`
- `finish`

所有工具在 session 启动前注册。session 开始时，8 个设备与观察工具不进入 active tool list；模型先通过 `search_tools` 按工具名或原子能力搜索并增量启用。`answer` 和 `finish` 始终可用，确保模型即使只使用 `bash`/ADB，也能记录答案并显式完成任务。

搜索使用工具名称、描述和中英文别名进行简单包含匹配，按匹配词数量排序。它不是语义检索，也不接受业务级操作作为隐式组合工具。

pi 自带的 `read`、`bash`、`edit` 和 `write` 保持可用。system prompt 允许模型对系统设置等确定性任务直接使用 ADB，但要求针对配置的 serial、禁止未授权的破坏性操作，并在修改后查询状态或重新观察。

## 观察与动作反馈

初始观察和每次设备动作后的反馈都包含：

- `screencap -p` 产生的 PNG 截图
- `uiautomator dump` 中节点的 `text` 与 `content-desc` 去重结果

动作完成后先等待 `settleMs`，默认 1500 ms，再获取反馈。`wait` 默认等待 2 秒后观察。固定等待实现简单，但不会判断动画、网络请求或页面是否真正稳定，模型仍需根据结果决定是否继续等待。

UIAutomator 文本只作为截图的补充。它可能遗漏 Canvas/WebView 内容，也可能包含当前不可操作的节点，因此不能单独作为成功依据。

## 坐标系统

工具对模型暴露 `0..1000` 的归一化坐标。`normalizedPoint()` 会先截断越界值，再映射到 `[0, width - 1]` 和 `[0, height - 1]`。

设备宽高直接读取当前截图 PNG 的 IHDR，而不是解析 `wm size`。因此坐标使用截图的实际方向和分辨率，能够覆盖横屏、旋转和 `wm size` override。`tap`、`long_press` 和 `swipe` 在执行前各自读取当前尺寸；`long_press` 由起止点相同的长时 swipe 实现。

## 文本输入

`type_text` 不负责聚焦或清空输入框，模型必须先选中目标字段。

文本输入优先使用 ADB Keyboard：

1. 用 `pm path com.android.adbkeyboard` 检测 APK。
2. 首次使用时保存当前默认 IME 和 ADB Keyboard 的启用状态。
3. 必要时启用并切换到 `com.android.adbkeyboard/.AdbIME`。
4. 将 UTF-8 文本编码为 Base64，通过 `ADB_INPUT_B64` 广播发送。
5. 任务清理时恢复原 IME；如果原先未启用 ADB Keyboard，再将其禁用。

检测和初始化结果在单个 `AdbDevice` 生命周期内缓存，避免连续输入时反复切换。如果 APK 未安装，设备会通过 `onWarning` 警告一次，然后回退到 `adb shell input text`。回退路径会处理 `%`、空格和远端 shell 引号，但 Android `input` 对 Unicode、换行及部分字符的支持不可靠；失败不会由本层自动判定。

## 动作预算

`maxActions` 默认值为 30，只统计以下会改变设备状态的 Android 工具：

- `tap`
- `long_press`
- `swipe`
- `type_text`
- `system_button`
- `open_app`

计数发生在 ADB 操作之前，所以执行失败也会消耗一次预算。达到上限后，后续设备动作抛出工具错误，但 session 仍可调用 `observe`、`wait`、`answer` 或 `finish`。

以下内容不计入预算：`search_tools`、pi 内置工具、直接通过 `bash` 执行的 ADB、模型轮次、观察、等待和任务后复盘。因此 `maxActions` 不是完整的成本或循环上限。

CLI 在进入 `runTask()` 前要求：

- `--max-actions` 是正整数
- `--settle-ms` 是有限非负数
- `--provider` 与 `--model` 同时出现或同时省略

## 完成、答案与退出码

`answer(text)` 只把信息查询结果写入 `state.answer`。`finish(summary)` 只把 `state.finished` 设为 `true`；两者都不会主动终止 pi tool loop。

CLI 可通过 `--result-file` 写出：

```json
{"finished": true, "answer": "optional answer"}
```

如果 session 结束时未调用 `finish`，CLI 设置退出码 2。模型调用 `finish` 后仍需自行停止继续调用工具。

## 学习存储与复盘

`LearningStore` 默认使用：

```text
.pi/learning/
├── MEMORY.md
└── skills/
    └── <skill-name>/
        └── SKILL.md
```

每次运行都会初始化目录并读取已有 Memory/skills。`--no-learning` 只关闭本次任务后的写入复盘，不阻止读取既有学习结果。

当 learning 开启且主 session 有模型时，系统使用同一模型和 `low` thinking 创建隔离 review session。输入轨迹会移除图片数据，只保留文本、工具调用和压缩后的工具结果，最大约 30,000 字符。review session 禁用普通工具，只激活：

- `save_memory`
- `list_skills`
- `read_skill`
- `upsert_skill`

Memory 用于稳定的用户偏好和事实，采用大小写不敏感的文本去重。Skill 用于类别级操作流程，名称必须为最长 64 字符的 lowercase kebab-case。更新已有 Skill 前必须在当前 review session 中读取；写入通过同目录临时文件和原子 rename 完成。

复盘失败通过 `onLearningError` 报告，不改变主任务返回状态。复盘不要求任务已调用 `finish`，因此失败轨迹也可能产生可复用经验。

## AndroidWorld 适配

Python adapter 的一个 `step(goal)` 会启动一次完整的 Node CLI 进程，而不是执行单个 Android 动作。它把 AndroidWorld 的 `_max_steps` 映射为 `--max-actions`，默认关闭跨 episode learning，并设置总进程超时。

只有 Node 进程正常退出且结果文件中 `finished === true` 时，adapter 才返回 `done=True`，让 AndroidWorld 对最终设备状态评分。`answer` 会同步到 `env.interaction_cache`。同一 episode 的重复 `step()` 会被拒绝。

这种进程级适配适合功能和成功率评测，但 `episode_length` 与逐动作 agent 不可直接比较；直接 `bash` 操作和模型轮次也不受 Android 动作预算约束。

## 已知限制

- `search_tools` 是词面匹配，不保证召回描述方式不同的能力。
- 固定 `settleMs` 不等同于真实 UI 稳定检测。
- UIAutomator 不能完整覆盖所有渲染技术。
- 缺少 ADB Keyboard 时，复杂文本输入只提供尽力而为的回退。
- `maxActions` 不限制 bash、观察、模型轮次或总 token。
- `finish` 是状态标记，不是强制停止机制。
- learned Skill 的正确性由 review 模型决定，没有自动执行验证或回滚。

## 自动化验证

当前测试覆盖坐标换算、截图尺寸读取、ADB Keyboard 输入与 IME 恢复、缺少键盘时的警告回退、应用名解析、轨迹摘要、Memory 去重、Skill CRUD/read-before-update，以及 AndroidWorld adapter 的完成和答案同步语义。TypeScript 编译由 `npm run build` 验证。
