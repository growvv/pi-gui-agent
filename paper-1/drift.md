# Drift：面向长程 GUI 任务的可执行进度账本与主动验证

## 摘要

GUI agent 正从单一点击代理发展为能够使用 shell、ADB、浏览器和代码执行环境的混合 agent。然而，长程任务中的“做到了哪里、为什么改变策略、如何证明完成”仍通常隐含在模型上下文或松散的文本 memory 中。本文提出 Drift（暂名），一种把 coding-agent 的可执行工作流引入 GUI 任务管理的运行时设计。Drift 在同一 agentic loop 中提供 GUI 工具、bash/ADB 工具，以及四类一等的项目管理操作：`update_ledger`、`reflect_on_ledger`、`validate_ledger` 和 `finish`。账本不是不可执行的日志，而是受控的 Bash DSL：它显式保存原任务、子任务、验证子任务、反思、语义验证和最终报告。我们基于 `pi-coding-agent` 实现 pi-gui，并在 AndroidWorld 与 MobileWorld 上与 Claude Code、Codex、OpenClaw 等 coding-agent 基座比较。仓库当前保留的 AndroidWorld 全量运行包含 116 个任务模板，pi-gui 完成 115 个 episode，成功率为 64.78%；同口径基线 Claude Code、Codex、OpenClaw 分别为 39.72%、52.55% 和 20.72%。这些结果支持“代码化进度管理值得研究”的工程假设，但不能单独证明所有收益均来自 ledger；为此，本文给出严格的消融和机制分析方案。

## 1 背景与问题

AndroidWorld、OSWorld、WebArena、ScreenSpot 等工作表明，现代 agent 已能在真实或高保真应用中完成多步 GUI 操作；SWE-agent、CodeAct/OpenHands 则展示了 shell 和代码执行作为 agent 行动接口的价值。混合 GUI+CLI agent 因而成为自然方向，但其任务管理仍存在三个可检验的缺口：

1. **状态外显不足。** 任务拆解、子任务完成、关键结果和最终检查散落在长对话中，模型难以主动知道下一项工作和未闭合风险。
2. **上下文载体利用不足。** 隐式上下文、摘要或普通文本 memory 可读但不可执行，不能直接复用 coding agent 熟悉的解析、版本化和脚本操作。
3. **反思与验证不是一等动作。** 它们常写在 system prompt 或 skill 中，在长上下文、重复操作或界面变化后可能被跳过。

本文将上述判断转化为假设：显式、可调用、可审计的进度管理工具应提高长程任务的可追踪性和恢复能力，并降低未验证完成；同时会增加 tool call、token 和延迟成本。

## 2 研究问题与贡献

**RQ1（有效性）：** 在相同任务、模型预算和黑盒执行协议下，加入代码化 ledger 是否提高 task-level success？收益是否随任务长度增加？

**RQ2（主动性）：** 工具化的更新、反思、验证是否比仅通过 system prompt/skill 指令更能促使 agent 在合适时机调用它们？

**RQ3（载体）：** Bash DSL 相比 JSON 或普通文本，是否更适合 coding agent 维护结构化进度？

**RQ4（机制与代价）：** `reflect_on_ledger` 和 `validate_ledger` 分别通过哪些路径影响结果，并增加多少成本？

贡献是一个与 `pi-coding-agent` 兼容的 GUI+CLI 运行时、最小 ledger DSL、跨 coding-agent 基座的统一黑盒评测协议，以及围绕工具呈现方式、Bash 载体、反思和验证组件的消融方案。本文不声称提出新的反思算法，而是把项目状态、反思、验证和结束操作化为 coding agent 可直接调用的代码工具。

## 3 Drift 设计

### 3.1 运行时

`runTask()` 创建设备适配器、模型会话和工具集合，在单一 agentic loop 中注入初始截图、UIAutomator 可见文本和任务目标。GUI 工具包括 `screenshot`、`tap/click`、`long_press`、`swipe`、`type_text`、`open_app`、`back`；bash 可通过 ADB 完成确定性查询。每次 GUI 动作后重新采集截图、UI 文本、时间戳和 fingerprint。坐标采用 0--1000 归一化空间，并校验元素 identity 以避免陈旧点击。

上下文只保留最新截图，历史截图替换为 fingerprint、可见文本和磁盘归档路径。progress guard 根据重复动作和无变化 fingerprint 发出警告，持续停滞时要求换策略并终止 loop。

### 3.2 可执行 ledger DSL

每个 episode 创建 `.pi/ledgers/<timestamp>-<task-hash>-<uuid>.sh`，包含受控函数声明和任务记录：

```bash
task "Save a track with waypoints ... in the same order"
subtask "open-osmand" "Open OsmAnd app"
subtask_for_validate "verify-result" "Verify saved waypoints and order"
reflection "find-create-track" "Search did not move" "Try coordinate search"
complete_subtask "open-osmand"
validation "complete" "Track is visible in My Places > TRACKS"
finish "passed" "Track saved with waypoints in order"
```

`update_ledger` 支持 append/replace/remove，并检查唯一 task、记录形状和 ID 引用。`reflect_on_ledger` 记录当前子任务、偏航原因及下一步；`validate_ledger` 接收 `task_completed`、摘要和未完成子任务，检查验证子任务是否闭合；只有最新更新后完成语义验证、所有验证子任务完成且任务被判定完成时，`finish` 才能写入 passed。工具维护 SHA-256 摘要，检测外部修改并恢复 canonical source。ledger 是可执行状态的审计边界，不是 GUI 证据；最终验证仍须回读设备 UI 或调用官方 evaluator。

### 3.3 设计原则

状态显式化、反思和验证一等化、Bash 代码亲和性、更新后验证失效的顺序安全，以及 ledger 判断与设备证据分离，是五项核心原则。

## 4 实现与实验协议

核心实现位于 `agents/pi_gui/src/tools.ts`、`src/agent.ts` 和 `src/cli.ts`。默认动作预算为 30，连续无进展阈值为 4，动作后等待 1500 ms。AndroidWorld 使用官方 registry、setup、checkpointer 和 evaluator；MobileWorld 使用独立容器和官方 evaluator，但采用 external black-box 协议：一个 `predict()` 内运行完整 agent loop，不能把官方 trajectory step 当作 GUI action。

### 4.1 基座对比

比较 pi-gui、Claude Code、Codex、OpenClaw。所有系统固定任务划分、设备初始化、超时、动作预算和 evaluator，记录成功率、exception、动作数、模型轮次、token、运行时和 ledger/tool 轨迹。仓库保留的 AndroidWorld 全量结果为：pi-gui 116 个模板、完成 115 个、成功率 64.78%；Claude Code 39.72%（42.5/107 completed）、Codex 52.55%（51.5/98）、OpenClaw 20.72%（23/111）。不同配置的 20/30-task smoke run 只能作为工程诊断，不能与全量结果混合。MobileWorld 需报告任务数、GUI-only 范围和黑盒协议限制。

### 4.2 消融

| 变体 | 进度更新 | 反思/验证 | 载体 |
|---|---|---|---|
| Full | tool call | tool call | Bash DSL |
| No-reflect | tool call | 仅验证 | Bash DSL |
| No-validate | tool call | 仅反思 | Bash DSL |
| Prompt/Skill | 隐式指令 | system prompt/skill | Bash DSL |
| Plain-text | 文本文件 | 文本指令 | Markdown/纯文本 |
| GUI+CLI | 无 ledger | 无 | 无 |

固定模型、任务、prompt 长度和预算，避免把额外 token 当作方法收益。报告成功率、验证覆盖率、反思调用率、验证捕获错误率、反思后恢复率、重复动作率、偏航率、未完成子任务数、tool calls、token、时延和成本。机制结果用相关或中介分析表述，不作无设计支持的因果断言。

### 4.3 统计与复现

预注册任务列表、seed、模型版本、temperature、动作预算和超时；发布配置、容器 digest、ledger、截图 fingerprint、官方 evaluator 输出和失败轨迹。每组至少 3 个 seed，报告 macro 平均和 95% bootstrap CI；对异常 episode 分别报告 infrastructure exception 与 agent failure，并进行不同分母分析。

## 5 结果与案例

主表应同时列出 success、completed、exception 和成本；第二表按任务长度展示 Full 与消融差值；第三表统计首次拆解延迟、每任务更新数、反思/验证调用率和验证后恢复率。OsmAnd waypoint 任务可作定性案例：agent 先拆解，在搜索未定位时反思并改变策略，随后逐点完成、创建验证子任务、检查 My Places > TRACKS，最后写入 `finish`。该案例说明可审计流程，不替代总体统计。

结果解释须强调：64.78% 与基线差异是当前工程运行的观察结果，需用同模型、同任务和重复 seed 复核；ledger completion 不等同于 evaluator success；若反思/验证增加成本但只改善少数长任务，应讨论成本效益。

## 6 相关工作

GUI 基准与 agent 包括 AndroidWorld、OSWorld、WebArena、ScreenSpot、AppAgent 和 Mobile-Agent；coding-agent 方向包括 SWE-agent 与 OpenHands/CodeAct。MemGPT、Generative Agents、Voyager 研究分层记忆、反思和技能积累；ReAct、Reflexion、Self-Refine、Tree-of-Thoughts 和 Toolformer 研究显式推理、反馈和工具调用。Drift 的差异在于把进度状态、反思、验证和结束操作化为可调用代码工具，并在 GUI 长任务上系统评估其行为与代价。

## 7 局限与威胁

模型/provider 版本、prompt/skill 熟悉度、MCP 延迟、模拟器性能、任务难度和 evaluator 偏差均可能影响结果。Bash DSL 的优势可能部分来自额外 schema token；ledger 也可能被机械填写而不改变行为。UIAutomator 对 Canvas/WebView 不完整，固定 settle time 不是真正稳定性检测；动作预算不覆盖 bash、模型调用和 token。MobileWorld 黑盒 adapter 不能与官方逐动作协议直接比较。应通过多模型、多 seed、任务长度分层、盲化日志标注和成本报告降低威胁。

## 8 结论

Drift 将 GUI agent 的“做什么、做到哪、是否偏航、如何证明完成”变成可执行、可审计的 coding 工作流。初步 AndroidWorld 结果显示该方向具有工程潜力；论文的核心证据仍应来自严格的同口径消融和机制分析，而非单一成功率。若 Full 在长程任务、恢复率和验证覆盖率上稳定优于 prompt/文本基线，Drift 可作为混合 GUI+CLI agent 的通用项目管理层；若收益主要来自额外提示长度，则应将贡献收窄为审计与诊断基础设施。

## 参考文献（按 FSE 格式核验）

Rawles et al. AndroidWorld, arXiv:2405.14573, 2024；Xie et al. OSWorld, NeurIPS 2024；Zhou et al. WebArena, ICLR 2024；Cheng et al. ScreenSpot/ScreenSpot-Pro, 2024/2025；Yang et al. AppAgent, arXiv:2312.13771, 2024；Wang et al. Mobile-Agent, 2024；Yang et al. SWE-agent, NeurIPS 2024；Wang et al. OpenHands/CodeAct, 2024/2025；Packer et al. MemGPT, arXiv:2310.08560, 2023；Park et al. Generative Agents, UIST 2023；Wang et al. Voyager, TMLR 2023；Yao et al. ReAct, ICLR 2023；Shinn et al. Reflexion, NeurIPS 2023；Madaan et al. Self-Refine, NeurIPS 2023；Yao et al. Tree of Thoughts, NeurIPS 2023；Schick et al. Toolformer, NeurIPS 2023。
