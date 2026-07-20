# HyMobileAgent 论文深度分析与复现指南

> **论文**: HyMobileAgent: Data-Environment Co-Scaling for Efficient GUI Agents
> **作者**: Hy Vision Team (腾讯混元)
> **日期**: 2026-07-17
> **arXiv**: [2607.14548](https://arxiv.org/abs/2607.14548)
> **PDF**: 已下载至 `./HyMobileAgent.pdf`

---

## 一、论文核心思想

### 1.1 一句话总结

**GUI Agent的能力提升不应只依赖模型参数扩大（Model Scaling），而应同时扩展训练数据（Data Scaling）和交互环境（Environment Scaling）。**

### 1.2 解决的三个核心瓶颈

| 瓶颈 | 具体问题 | HyMobileAgent的解法 |
|------|---------|-------------------|
| **GUI感知不精准** | 小图标、密集文本、嵌套布局识别困难 | GUI感知飞轮（Mock合成 + 拒绝采样 + 图标增强） |
| **高质量数据难规模化** | 真机采集成本高、开源数据质量参差不齐 | 三层数据系统 + PhoneWorld Mock环境 |
| **长程任务误差累积** | 多步操作后错误逐步放大 | 结构化规划+反思 + 死循环检测 |

### 1.3 核心哲学

传统思路：**更大的模型 → 更好的Agent**
HyMobileAgent：**更好的数据 × 更多的环境 → 更好的Agent**（在A3B参数规模下超越GPT-5.4-Pro等大模型）

---

## 二、架构详解

### 2.1 基础模型：Hy3.0-VL-A3B

选择这个模型有三个关键原因：

1. **原生任意分辨率输入**（最高4K）：移动设备屏幕尺寸/比例差异大，固定分辨率（如448×448）会裁剪/填充导致细节丢失
2. **A3B参数规模**（约3B激活参数）：适合端侧部署，满足延迟/内存/功耗约束，且支持隐私保护的本地推理
3. **32K上下文窗口**：足够承载高分辨率截图的视觉token + 历史决策文本，避免长轨迹中的截断遗忘

### 2.2 动作空间：13个原子操作

```
基础交互: click, double_click, long_press, type, scroll, drag
系统控制: button_press(back/home/menu/enter), open_app, close_app
人机协同: wait, call_user(交还控制权), output(文本回复), finish(结束)
```

**关键设计**：
- 所有坐标使用 **[0, 1000] 归一化坐标系**，跨设备通用
- 动作集刻意最小化，避免动作预测分布过于复杂
- `open_app` 通过包名直接启动，跳过找图标的视觉链
- `call_user` 在登录/支付等敏感场景交还控制权

### 2.3 结构化规划与反思（Planning-and-Reflection）

每个决策步骤输出**五字段结构**：

```
Thought:
  <current_state>      ← 当前屏幕状态 + 从之前步骤继承的关键信息（如验证码）
  <long_term_planning>  ← 全局多步路线图，防止逐步漂移
  <next_plan>           ← 立即要完成的子目标
  <action_description>  ← 即将执行的动作的自然语言描述
  <expected_result>     ← 执行后期望的屏幕变化（用于偏差检测）

Action:
  <tool_call>...</tool_call>
```

**死循环检测机制**：
- 当同一动作连续出现3次 → 触发确定性反射信号
- 反射步骤必须：(i) 承认重复无效 (ii) 归因失败原因 (iii) 枚举2-3条替代路径 (iv) 选择最低成本替代方案
- 这不是纯粹的prompt技巧，而是**可训练的结构化恢复行为**

**历史压缩**：
- 只保留历史步骤的文本决策轨迹（Thought + Action），不保留历史截图
- 通过 `current_state` 字段传递视觉事实
- 让上下文增长与步数成正比而非与图片数成正比

---

## 三、数据构建系统（论文最核心贡献）

### 3.1 感知层：GUI感知飞轮

#### 3.1.1 Mock界面合成（规模化）

```
种子截图 → 检测交互区域 → 裁剪UI单元 → Prompt改写（颜色/字体/控件多样性）
→ HTML生成 → 渲染新截图 + 精确坐标标签 → VLM反向一致性检查 → 保留匹配样本
```

**核心价值**：打破真实设备采集的成本天花板

#### 3.1.2 拒绝采样精炼（质量控制）

对每个开源grounding样本，从强VLM采样8次独立回答：

| 桶 | 8次中正确次数 | 处理策略 |
|----|-------------|---------|
| Easy | 8/8 | 作为对齐数据使用 |
| Golden-difficulty | 1/7 ~ 7/8 | **最高价值**——位于模型能力前沿 |
| Suspected | 0/8 | 可能是错标或超出前沿，需人工复查 |

**飞轮闭环**：评估时发现的错误 → 反馈到合成prompt分布 + 拒绝采样过滤器 → 下一轮训练获得更难、更有代表性的样本

#### 3.1.3 图标专项增强（长尾覆盖）

- 从公开图标库爬取带高质量描述的图标
- **Icon QA**：构建图标功能/使用场景/视觉特征的问答对
- **Icon Grounding**：将图标嵌入合成GUI场景，构建"语义描述 → 图标位置"的监督信号

### 3.2 知识层：教程视频 → 结构化交互数据

```
教程视频/书籍/帖子
    ↓ 语义分段
语义连贯的视频片段
    ↓ VLM总结 + 自评判质量门
    ↓ 知识蒸馏
两种互补信号：
  ├── 状态转换对（界面如何随动作变化）
  └── 规划链（用户如何将高层目标分解为有序子任务）
```

### 3.3 动作层：百万级轨迹生产

- **规模**: 2000+ 沙箱和真机实例
- **数据量**: 百万级动作轨迹
- **自动故障归因**：将失败轨迹分为4类：
  - 环境拦截（登录墙等）
  - 指令歧义
  - 感知漂移
  - 决策死循环
- 归因结果反馈到指令合成和轨迹筛选

### 3.4 PhoneWorld Mock App Factory

| 指标 | 数值 |
|-----|------|
| 模拟应用数 | 34个（基于18个可复用交互组件） |
| 单应用任务 | 34,000+ |
| 跨应用任务 | 500个（如：从地图复制地址到打车应用） |
| 任务验证 | 严格基于规则的验证器，可编程判定成功/失败 |
| 可重置性 | 完全可控的初始状态 |

**PhoneWorld的双重角色**：
1. **数据生产**：确定性状态生成SFT数据（无动态环境噪声）
2. **RL训练环境**：提供可验证的奖励信号（基于真实界面状态而非启发式代理）

---

## 四、训练流程

### 4.1 三阶段渐进式优化

```
Hy3.0-VL-A3B (Stage 2 基础模型)
    ↓
[Stage 1] Mid-training (50B动作token + 300B语言token)
    ├── 基础GUI数据（QA、grounding、教程）
    ├── 序列化GUI数据（沙箱环境自动采集）
    ├── 通用视觉+高分辨率感知数据（OCR、grounding）
    └── 文本能力数据（Agent工作流、编码、长上下文）
    ↓
[Stage 2] SFT (8.6B对齐token)
    ├── 大幅增加多步交互轨迹（尤其是真机+人工标注）
    ├── 保留基础能力数据防遗忘
    ├── 统一工具调用格式
    └── 从"基础多模态模型"→"可执行GUI Agent"
    ↓
[Stage 3] Offline RL (单步级)
    ├── Grounding: 规则奖励（预测点是否在标注框内）
    ├── QA: RM奖励（LLM一致性打分）
    └── Action: 规则奖励（动作类型匹配 + 参数相似度加权）
    ↓
[Stage 4] Online RL (轨迹级)
    ├── GRPO算法
    ├── 环境混合：~500 AndroidWorld + ~1200真机 + ~1000模拟器
    ├── Rubric-based奖励：每个指令多个评分标准，全部通过才算成功
    └── 同组rollout来自同设备类型，跨组可混合
```

### 4.2 在线RL环境配比

| 环境类型 | 数量 |
|---------|------|
| AndroidWorld设备 | ~500 |
| 真机（云真机+虚拟机） | ~1,200（其中300云真机 + 900虚拟机） |
| PhoneWorld模拟应用虚拟机 | ~1,000 |

---

## 五、实验结果

### 5.1 端到端任务执行

| Benchmark | HyMobileAgent(A3B) | Gemini 3.1 | Seed 2.0 Pro | GPT-5.4-Pro | Claude-4.7 |
|-----------|-------------------|-----------|-------------|------------|-----------|
| **AndroidWorld** | **82.6%** | 80.2 | 71.5 | 70.7 | 56.0 |
| **HyMobileWorld** | 42.0% | - | 44.7 | - | - |

### 5.2 GUI Grounding

| Benchmark | HyMobileAgent | 对比模型最佳 |
|-----------|--------------|------------|
| **ScreenSpot V2** | **96.2** | 96.4 (Qwen large) |
| **ScreenSpot-Pro** | 66.5 | **71.1** (MA3.5 large) |
| **MMBench-GUI L2** | **89.3** | 90.3 (Qwen large) |
| **HyMobileGrounding** | **93.1** | 95.0 (MA3.5 large) |

### 5.3 问答理解

| Benchmark | HyMobileAgent | 对比模型最佳 |
|-----------|--------------|------------|
| **MMBench-GUI L1** | 93.7 | **97.5** (MA3.5 large) |
| **HyMobileQA** | 87.0 | **89.4** (MA3.5 large) |

### 5.4 核心结论

- A3B规模模型在AndroidWorld上**超越GPT-5.4-Pro 12个百分点**
- 在同规模开源模型中**遥遥领先**（UI-Venus 1.5 A3B仅9.7%）
- Grounding能力接近或超过更大规模模型
- 在ScreenSpot-Pro和MMBench-GUI L1上与大模型仍有差距，说明感知和理解仍有扩展空间

---

## 六、复现指南

### 6.1 可复现 vs 不可复现部分

| 组件 | 可复现性 | 说明 |
|------|---------|------|
| 基础模型 Hy3.0-VL-A3B | ⚠️ 部分 | Hy3已开源(295B MoE)，但VL-A3B变体未明确发布 |
| GUI感知飞轮 | ✅ 可复现 | 需要构建合成pipeline |
| 教程视频知识提取 | ✅ 可复现 | 需要视频处理+VLM |
| PhoneWorld | ⚠️ 需查 | 论文引用Tang et al. 2026 (arXiv:2605.29486) |
| 训练流程 | ✅ 可复现思路 | 需大规模GPU集群 |
| 评估基准 | ✅ 大部分公开 | AndroidWorld/ScreenSpot/MMBench均公开 |

### 6.2 最小可行复现方案

#### Phase 1: 基础环境搭建

```bash
# 1. 克隆相关仓库
git clone https://github.com/Tencent-Hunyuan/Hy3.git  # 基础语言模型
git clone https://github.com/OSU-NLP-Group/GUI-Agents-Paper-List.git  # 参考

# 2. 安装AndroidWorld评估环境
pip install android-world  # Google官方
# 或使用AndroidWorld的Docker镜像

# 3. 安装ScreenSpot评估
# 从 https://github.com/Computer-Agent/ScreenSpot 下载
```

#### Phase 2: 感知飞轮复现

```python
# 2.1 Mock界面合成Pipeline
# 核心思路：种子截图 → HTML改写 → 渲染 → 标签生成

# Step 1: 收集种子截图
# - 从公开GUI数据集获取（如Rico, Enrico, VINS）
# - 使用UIED等工具检测交互区域

# Step 2: Prompt改写生成多样性
# - 使用GPT-4V/Claude改写HTML代码的颜色、字体、布局
# - 关键：保持交互逻辑不变，只变化视觉样式

# Step 3: 渲染为截图
# - 使用Playwright/Selenium渲染HTML
# - 自动提取元素坐标和语义标签

# Step 4: VLM反向验证
# - 用强VLM（如GPT-4V）从截图预测答案
# - 只保留预测正确的样本
```

```python
# 2.2 拒绝采样
def reject_sampling(dataset, vlm_model, n_samples=8):
    """对每个样本采样n次，按正确率分桶"""
    buckets = {"easy": [], "golden": [], "suspect": []}
    for sample in dataset:
        correct_count = sum(
            1 for _ in range(n_samples)
            if vlm_model.predict(sample["image"]) == sample["answer"]
        )
        if correct_count == 8:
            buckets["easy"].append(sample)
        elif correct_count == 0:
            buckets["suspect"].append(sample)
        else:
            buckets["golden"].append(sample)  # 最高价值
    return buckets
```

#### Phase 3: 动作数据采集

```python
# 3.1 沙箱环境数据采集
# 使用Appium + Android模拟器

from appium import webdriver

def collect_trajectory(task, driver):
    """自动采集交互轨迹"""
    trajectory = []
    for step in task.steps:
        screenshot = driver.get_screenshot()
        action = step.action  # click/type/scroll...
        result = driver.execute(action)
        trajectory.append({
            "screenshot": screenshot,
            "action": action,
            "result": result,
            "success": step.verify(result)
        })
    return trajectory

# 3.2 故障自动归因
def attribute_failure(trajectory):
    """将失败轨迹归因到4类"""
    if is_login_wall(trajectory):
        return "environment_interception"
    elif is_ambiguous_instruction(trajectory):
        return "instruction_ambiguity"
    elif is_perception_drift(trajectory):
        return "perception_drift"
    elif is_dead_loop(trajectory):
        return "decision_deadloop"
```

#### Phase 4: 结构化推理模板

```python
# 实现HyMobileAgent的推理模板

SYSTEM_PROMPT = """You are a GUI agent. Given an instruction, the current screenshot,
prior step history, and intermediate state information, predict the
next operation needed to complete the user's instruction.
Coordinate values are normalised to the range [0, 1000].

Action space: click, double_click, long_press, type, scroll, drag,
button_press, open_app, close_app, wait, call_user, output, finish

Output format:
Thought: <current_state>. <long_term_planning>. <next_plan>. <action_description>. <expected_result>
Action: <tool_call>...</tool_call>
"""

def detect_dead_loop(history, threshold=3):
    """检测连续3次相同动作"""
    if len(history) < threshold:
        return False
    recent_actions = [h["action"] for h in history[-threshold:]]
    return len(set(recent_actions)) == 1

def reflect_and_replan(history):
    """死循环反思"""
    return """Thought: I notice that my recent actions have been repeating without progress.
The search entry point has not been correctly located.
Alternative paths:
1. Try revealing a hidden menu by long-pressing
2. Go back and use a different entry point
3. Switch to category filtering instead
I will try option 1 as it has the lowest operational cost.
Action: ..."""
```

#### Phase 5: 训练流程

```python
# 5.1 Mid-training (大规模预训练)
# 数据配比：
# - 50B tokens 动作轨迹数据
# - 300B tokens 通用+领域语料
# 使用全参数微调

# 5.2 SFT
# - 8.6B tokens 高质量对齐数据
# - 重点增加多步交互轨迹比例
# - 使用拒绝采样筛选"金难度"样本

# 5.3 Offline RL (GRPO)
import torch

def grounding_reward(pred_point, gt_bbox):
    """规则奖励：预测点是否在标注框内"""
    x, y = pred_point
    x1, y1, x2, y2 = gt_bbox
    return 1.0 if (x1 <= x <= x2 and y1 <= y <= y2) else 0.0

def action_reward(pred_action, gt_action):
    """规则奖励：动作类型+参数匹配"""
    if pred_action["type"] != gt_action["type"]:
        return 0.0
    # 参数相似度（坐标用距离，文本用编辑距离）
    arg_score = compute_arg_similarity(pred_action["args"], gt_action["args"])
    return 0.5 + 0.5 * arg_score  # 类型匹配50% + 参数50%

def qa_reward(pred_answer, reference, reward_model):
    """RM奖励：LLM一致性打分"""
    return reward_model.score(pred_answer, reference)

# 5.4 Online RL
# - 在混合环境（真机+模拟器+PhoneWorld）中rollout
# - Rubric-based验证：每个指令多个评分标准
# - GRPO算法：同组rollout来自同设备类型
```

### 6.3 硬件需求估算

| 阶段 | 最低GPU需求 | 说明 |
|------|-----------|------|
| Mid-training | 256×A100 80GB (估算) | 350B tokens全参数微调 |
| SFT | 64×A100 80GB | 8.6B tokens |
| Offline RL | 64×A100 80GB | 单步GRPO |
| Online RL | 128×A100 80GB + 2000+设备 | 需要大规模设备池 |
| 推理/评估 | 1×A100 或端侧 | A3B规模可单卡部署 |

### 6.4 低成本替代方案

如果资源有限，可以只复现**部分核心思想**：

1. **感知飞轮**（最容易复现）：用开源VLM + 合成HTML + 拒绝采样
2. **结构化推理模板**：直接在现有模型（如Qwen-VL）上用SFT实现五字段Thought
3. **死循环检测**：纯工程实现，无需训练
4. **PhoneWorld替代**：用AndroidWorld + 手动构建的简单模拟应用

### 6.5 评估基准复现

```bash
# AndroidWorld (公开)
pip install android-world
# 参考: https://github.com/google-research/android_world

# ScreenSpot V2 / ScreenSpot-Pro (公开)
# 参考: https://github.com/Computer-Agent/ScreenSpot

# MMBench-GUI (公开)
# 参考: https://github.com/open-compass/MMBench

# HyMobileWorld / HyMobileGrounding / HyMobileQA (内部基准)
# 暂未公开，可用类似方法自建
```

---

## 七、论文创新点总结

### 7.1 方法论创新

1. **数据×环境协同扩展框架**：不是单一维度的scaling，而是数据和环境的联合扩展
2. **GUI感知飞轮**：闭环系统——评估错误反馈到合成分布，持续自我改进
3. **PhoneWorld Mock App Factory**：可重置、可验证的大规模模拟环境
4. **五字段结构化决策 + 死循环检测**：将规划/反思从prompt技巧变为可训练行为

### 7.2 工程创新

1. **百万级轨迹自动故障归因**：4类归因反馈到数据筛选
2. **Rubric-based轨迹验证**：将整体判定分解为多个独立检查项
3. **混合环境Online RL**：真机+模拟器+PhoneWorld的异构环境池
4. **归一化坐标系 [0,1000]**：跨设备通用的动作空间

### 7.3 对研究方向的启示

- **数据质量 > 数据数量**：拒绝采样的"金难度"桶比全量数据更有价值
- **环境多样性 > 环境真实性**：模拟环境可以补偿真机数据的不足
- **结构化推理 > 自由推理**：强制的决策结构让错误可追溯、可训练
- **小模型 + 好数据 > 大模型 + 一般数据**：A3B规模超越GPT-5.4-Pro

---

## 八、相关工作对比

| 方法 | 模型规模 | 核心策略 | AndroidWorld |
|------|---------|---------|-------------|
| **HyMobileAgent** | A3B | 数据+环境协同扩展 | **82.6%** |
| Gemini 3.1 | Large | 通用大模型 | 80.2% |
| Seed 2.0 Pro | Large | 通用大模型 | 71.5% |
| GPT-5.4-Pro | Large | 通用大模型 | 70.7% |
| Claude-4.7 | Large | 通用大模型 | 56.0% |
| UI-Venus 1.5 | A3B | 开源GUI模型 | 9.7% |
| MobileAgent 3.5 | 8B | 多Agent协作 | 40.7% |

---

## 九、局限性与未来方向

1. **ScreenSpot-Pro表现一般**（66.5 vs 71.1）：高分辨率专业界面的grounding仍是挑战
2. **HyMobileWorld仅42%**：真实世界端到端任务仍有巨大提升空间
3. **PhoneWorld未开源**：复现需要自建模拟环境
4. **训练资源门槛极高**：完整复现需要千卡级集群
5. **仅限Android**：未覆盖iOS等其他移动平台

---

## 十、关键参考文献

- Hy3.0-VL-A3B: [Tencent-Hunyuan/Hy3](https://github.com/Tencent-Hunyuan/Hy3)
- PhoneWorld: arXiv:2605.29486
- AndroidWorld: [google-research/android_world](https://github.com/google-research/android_world)
- ScreenSpot: [Computer-Agent/ScreenSpot](https://github.com/Computer-Agent/ScreenSpot)
- GRPO: Shao et al., 2024
- ReAct: Yao et al., 2022
- Reflexion: Shinn et al., 2023
