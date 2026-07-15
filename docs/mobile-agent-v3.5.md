# 从 Mobile-Agent-v3.5 迁移

本文说明 pi-gui-agent 从 Mobile-Agent-v3.5 借鉴了什么、如何迁移，以及两者为何不能被视为同一个实现。

## 迁移目标

迁移目标不是逐文件移植 Mobile-Agent-v3.5，而是保留 Android 视觉任务所需的核心闭环：观察屏幕、选择原子动作、执行动作、检查新状态并显式完成。模型编排、工具协议和持久化能力改为使用 pi 原生机制。

AndroidWorld 本身没有被迁入 TypeScript。任务 registry、应用初始化、模拟器控制、scripted reward 和 evaluator 继续由上游 Python 环境提供。

## 组件映射

| Mobile-Agent-v3.5 | pi-gui-agent |
| --- | --- |
| Manager 的任务规划 | system prompt 与主 pi session context |
| Executor 的动作生成与 JSON 解析 | TypeBox typed tool calling |
| ActionReflector | 每次动作后的截图与 UIAutomator 文本 |
| Notetaker | session history、pi compaction、可选持久学习 |
| JSONAction / AndroidWorld action controller | `tools.ts` + `adb.ts` |
| 固定或环境提供的屏幕尺寸 | 从当前 PNG 截图读取实际尺寸 |
| AndroidWorld step 驱动单步动作 | 一个 adapter step 启动完整 pi tool loop |
| ADB Keyboard 文本输入 | ADB Keyboard UTF-8 Base64 广播，缺失时 ADB 回退 |

原来的多角色手工编排被合并为一个主 session。模型直接看到历史工具调用和动作后的观测，不需要在 Manager、Executor、Reflector 与 Notetaker 之间复制或重新序列化状态。

## 保留的行为原则

- 使用截图完成视觉 grounding。
- 操作粒度保持为 tap、swipe、type、back 等原子动作。
- 每次只推进一个动作，并根据新截图纠错。
- 处理 onboarding、权限弹窗和滚动发现。
- 信息查询任务把答案交给 evaluator，而不仅修改设备状态。
- AndroidWorld 继续负责最终成功判定。

项目预置的 `.pi/learning/skills/android-world-task-strategies/SKILL.md` 还整理了部分可复用经验，例如 Chrome onboarding、Audio Recorder 控件辨识、exact duplicate 判定以及多屏信息收集。这是 seeded procedural knowledge，不是 agent 在线试错产生的结果。启用该 Skill 的成绩不能标记为无先验 cold start。

## 有意改变的设计

### 原生工具调用

动作通过 TypeBox schema 约束，不再要求模型生成一段约定格式的 JSON 文本后由业务代码解析。参数不合法时由工具层拒绝，减少动作协议漂移。

### 动态工具加载

8 个 Android 工具默认不进入模型初始 active tool list。模型先使用 `search_tools` 发现并启用需要的能力，以减少初始工具 schema 占用。Mobile-Agent-v3.5 没有这一层 pi 工具加载协议。

### ADB 与 GUI 双路径

当前 system prompt 允许对确定性系统操作使用 pi 内置 `bash` 直接调用 ADB，并要求修改后验证。这个能力有利于实际任务，但会突破纯 GUI agent 的动作口径；要求 GUI-only 的实验必须额外限制内置 bash。

### 跨任务学习

当前项目可在任务后生成持久 Memory 和 Skill，并在未来进程中读取。它不是 Mobile-Agent-v3.5 Manager/Notetaker 临时上下文的直接等价物，也会改变连续 episode 的实验条件。

## AndroidWorld 评测差异

`benchmark/android_world_agent.py` 是进程级桥接：AndroidWorld 调用一次 `step()`，Node 侧完成整个 tool loop，然后一次性返回是否显式完成。

这带来三个重要差异：

- 正常任务的 AndroidWorld `episode_length` 通常为 1，不能与逐动作 agent 的 step-efficiency 比较。
- AndroidWorld `_max_steps` 只映射到 Android 工具的 `maxActions`，不统计 bash、观察和模型轮次。
- adapter 只有在进程退出码为 0 且 `finished === true` 时才允许官方 evaluator 评分。

如果需要严格逐动作比较，应实现持久 Node bridge，让每次 AndroidWorld `step()` 只产生一个设备动作，并统一约束 bash、观察与模型调用预算。

## 能力差异

当前实现没有复刻 Mobile-Agent/GUI-Owl 专门训练得到的视觉 grounding 能力。相邻控件选择、复杂页面导航和首次启动页判断主要取决于所选基础模型，因此 cold-start 成功率不能仅凭架构相似性推断为相同。

另一方面，截图实际尺寸、typed tools、确定性 ADB 路径和持久 Skill 可能在特定任务上更可靠。两者的优势属于不同维度，应分别测量基础 GUI 成功率、成本、动作数和跨任务学习收益。

## 公平比较建议

- Cold start：隔离 `.pi/learning/`，关闭写入，并明确是否加载 seeded Skill。
- GUI-only：禁用或约束 bash，避免直接 ADB 改状态。
- Online learning：固定任务顺序并报告随 episode 变化的成功率，不与静态公开成绩混用。
- Train then test：在训练任务写入 Skill，冻结快照后在未见实例只读测试。
- 同时报告模型、prompt、最大动作数、超时、ADB Keyboard 和模拟器镜像版本。
