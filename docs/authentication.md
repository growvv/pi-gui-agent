# 模型与鉴权

pi-gui-agent 不单独保存或解析 API Key，而是复用 pi 的模型注册表和鉴权存储。主任务 session 与任务后的 review session 使用同一套模型与鉴权配置。

## 使用环境变量

在运行项目的同一个 shell 中导出对应 provider 的环境变量：

```bash
# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

# OpenAI
export OPENAI_API_KEY="sk-..."
```

然后显式选择与该 Key 对应的 provider 和模型：

```bash
npm start -- \
  --provider anthropic \
  --model claude-sonnet-4-5 \
  "Open Settings and turn Wi-Fi on"
```

其他 provider 使用 pi 规定的环境变量，例如：

| Provider | 环境变量 |
| --- | --- |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |

完整列表和模型 ID 以 [pi provider 文档](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/providers.md) 为准。

项目没有自动加载 `.env` 文件的逻辑。若将 Key 写在 `.env` 中，需要先通过 shell 或其他环境管理工具将其导入当前进程；项目的 `.gitignore` 已排除 `.env` 和 `.env.*`。不要把真实 Key 写入源码、公开文档或提交到 Git。

## 复用 pi 登录状态

先安装并启动 pi：

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
pi
```

在 pi 交互界面中执行 `/login`，选择 provider，并按提示完成 OAuth 登录或输入 API Key。凭据由 pi 保存在用户级鉴权文件 `~/.pi/agent/auth.json`，不会写入本项目目录。

完成登录后退出 pi，直接运行本项目：

```bash
npm start -- "Open Settings and turn Wi-Fi on"
```

## 模型选择

省略 `--provider` 和 `--model` 时，项目使用 pi 当前配置的默认模型：

```bash
npm start -- "Open Settings and turn Wi-Fi on"
```

需要覆盖默认模型时，`--provider` 和 `--model` 必须同时提供：

```bash
npm start -- \
  --provider openai \
  --model <model-id> \
  "Open Settings and turn Wi-Fi on"
```

这两个参数只负责选择模型，不负责传入或保存 API Key。如果指定的 provider 没有可用凭据，模型请求会失败。

## 安全建议

- 优先使用环境变量或 pi 的用户级鉴权存储，不要在命令参数中直接传递 Key
- 不要提交 `~/.pi/agent/auth.json`、`.env`、终端日志或包含真实凭据的运行轨迹
- 在 CI 中使用平台提供的 Secret 管理功能，并把 Secret 注入为对应的环境变量
- 如果 Key 曾进入 Git 历史、公开日志或截图，应立即在 provider 控制台撤销并重新生成
