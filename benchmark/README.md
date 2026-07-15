# AndroidWorld benchmark

该适配层复用 AndroidWorld 的任务初始化、模拟器状态、scripted reward、checkpoint 和汇总逻辑。pi-gui-agent 通过同一模拟器的 ADB 完成任务。

## 准备

按上游 `android_world_v3.5` 文档安装 Python 依赖并配置 AndroidWorld，启动其模拟器和 gRPC 服务。然后构建本项目：

```bash
npm install
npm run build
```

涉及 Unicode 或多行文本的任务建议在评测模拟器安装 ADB Keyboard：

```bash
adb -s emulator-5554 install -r /path/to/ADBKeyboard.apk
adb -s emulator-5554 shell pm path com.android.adbkeyboard
```

Agent 会在首次文本输入时自动启用该 IME，并在每个任务结束后恢复原输入法。

例如在独立 Python 环境中安装上游项目：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e "$ANDROID_WORLD_ROOT"
```

运行脚本时，需要让 Python 找到 AndroidWorld。例如：

```bash
export ANDROID_WORLD_ROOT=/path/to/Mobile-Agent-v3.5/android_world_v3.5
export PYTHONPATH="$ANDROID_WORLD_ROOT:$PWD/benchmark"
```

## 单任务冒烟测试

任务名来自 AndroidWorld registry：

```bash
python benchmark/run_android_world.py \
  --adb-path "$HOME/Library/Android/sdk/platform-tools/adb" \
  --console-port 5554 \
  --grpc-port 8554 \
  --tasks SystemWifiTurnOn \
  --checkpoint-dir benchmark-results/wifi
```

首次配置全新模拟器时额外传入 `--perform-emulator-setup`，之后不要重复使用。

## 子集和完整评测

```bash
# 多个指定任务
python benchmark/run_android_world.py \
  --tasks SystemWifiTurnOn SystemWifiTurnOff \
  --n-task-combinations 3 \
  --checkpoint-dir benchmark-results/system

# 完整 android_world suite；省略 --tasks
python benchmark/run_android_world.py \
  --n-task-combinations 1 \
  --checkpoint-dir benchmark-results/full
```

checkpoint 支持使用同一个目录恢复未完成的评测。默认关闭跨 benchmark episode 的 learning，避免任务间知识污染；需要专门评估 online learning 时传入 `--learning`。

## Step 语义

适配器在一个 AndroidWorld episode step 内运行完整的 pi tool loop。AndroidWorld 分配的 step budget 会映射为 `maxActions`；只有 agent 显式调用 `finish` 并正常退出时，最终设备状态才会由官方 `task.is_successful(env)` 评分。`answer` 工具的结果会同步到 AndroidWorld 的 interaction cache，供 information-retrieval evaluator 使用。

这种设计不修改 AndroidWorld，也不复制其任务代码，适合后续成功率和功能测试。正常完成时 `episode_length` 为 1，未完成时 AndroidWorld 会耗尽外层 step budget；同时 `maxActions` 不统计 `bash`、观察和模型轮次，因此不能把 step-efficiency 指标与逐动作 AndroidWorld agent 直接比较。若后续需要严格的逐动作公平评测，应再实现持久 Node bridge，让每次 `agent.step()` 只执行一个设备动作。
