# Hermes 核心学习闭环

pi-gui-agent 借鉴了 Hermes Agent 的任务后学习闭环，但没有迁移 Hermes 的完整外围系统。

## 当前流程

每次主任务完成后，在 learning 开启且主 session 有可用模型时，系统会：

1. 精简任务轨迹，保留文本、工具调用和工具结果，移除截图 Base64，并把长度限制在约 30,000 字符。
2. 使用主任务的同一模型启动隔离的 `low` thinking review session。
3. 将稳定的用户偏好、期望和长期事实写入 `.pi/learning/MEMORY.md`。
4. 将可复用的操作流程、技巧和纠错方法创建为 class-level skill，保存在 `.pi/learning/skills/<name>/SKILL.md`。
5. 更新已有 skill 前强制在当前 review session 中读取它，避免盲目覆盖。
6. 后续任务自动把 Memory 注入 system prompt，并通过 pi 原生 skill 发现机制加载 learned skills。

review session 与主任务隔离，并且只能使用以下学习工具：

- `save_memory`
- `list_skills`
- `read_skill`
- `upsert_skill`

Memory 和 Skill 的职责保持分离：Memory 记录稳定的用户信息，Skill 记录如何完成一类任务。一次性任务内容、临时页面状态和偶发环境故障不应持久化；没有可复用知识时允许不写入任何内容。

使用 `--no-learning` 可以临时关闭当前任务结束后的复盘，但不会阻止读取此前已有的 Memory 和 learned skills。

## 未迁移部分

当前实现没有迁移 Hermes 的 provider 插件、Curator、使用遥测、Skill 归档和后台线程等外围系统。因此这里的对齐仅指核心的任务后 Memory/Skill 学习闭环，不代表完整 Hermes 实现。
