# 开发公司 · AgentScope 官方部署（迁移版）

> 定位：本文是开发公司迁移到 AgentScope 后的**官方标准部署/运行指南**。
> 决策：2026-06-16 船主拍板，公司骨架由自研零依赖引擎正式迁移到 AgentScope（总纲 C6 原选型）。
> 所有 AgentScope 用法均依据官方文档 `https://doc.agentscope.io`（2026-06-16 查证）。

## 一 · 安装（官方标准）

AgentScope 是标准 PyPI 包，官方建议在虚拟环境中安装：

```bash
cd ~/Movies/小酒_Project/30_开发公司
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -c "import agentscope, sys; print('agentscope', agentscope.__version__, '| python', sys.version.split()[0])"
```

唯一开发区需要在 macOS 上运行完整发布检查和本机模型测试，因此使用宿主依赖清单：

```bash
cd ~/Movies/小酒_Project/30_开发公司
source .venv/bin/activate
pip install -r requirements-host.txt
make release-check
```

`requirements-host.txt` 会汇总通用、平台和 macOS 本地模型依赖。正式用户的 Linux 镜像仍只安装对应的 Linux 清单，不会安装 MLX（Apple 芯片本地模型运行库）。

> 说明：本仓的沙箱环境（Claude 这侧）无法访问 PyPI，故安装与真模式运行**在船主的 Mac 上完成**——这不是"测试"，是 AgentScope 的标准本机部署位置（API key 与额度都在你机器上）。

## 二 · 配置密钥

五岗端点全部为 OpenAI 兼容，密钥仍只经环境变量读取（总纲 C6/C7、手册 E5.6）。`.env` 需包含：

| 岗位 | 环境变量 | 端点（base_url） |
|---|---|---|
| 项目经理 gpt-5.6-sol | `OPENAI_API_KEY` | 先生批准的中转站 http（只放行花名册登记根地址） |
| 首席工程师 qwen3.8-max | `BAILIAN_TOKEN_PLAN_API_KEY` | 百炼 Token Plan 团队版专用端点 |
| 工程师 glm-5.2 | `BAILIAN_TOKEN_PLAN_API_KEY` | 百炼 Token Plan 团队版专用端点 |
| 测试工程师 / 文案工程师 deepseek-v4-pro-0813 | `BAILIAN_TOKEN_PLAN_API_KEY` | 百炼 Token Plan 团队版专用端点 |

岗位↔模型↔端点的唯一真相仍是 `花名册.yaml`，改它即调岗，代码零改动。

## 三 · 模型接入映射（已落地：`工具/迁移_模型层.py`）

依据官方 `Model` 文档：五岗端点均 OpenAI 兼容，统一用 `OpenAIChatModel` + `client_kwargs={"base_url": ...}`。

- `generate_kwargs={"max_tokens": 16384}`：沿用已查证的推理模型预算决策（max_tokens 是上限非消耗，含 CoT，给足只防截断）。
- 岗位 Agent 用官方 `ReActAgent`（自带实时打断 `interrupt()`/`handle_interrupt()`、并行工具调用、结构化输出、状态管理）。

## 四 · 可观测（官方 Studio / Tracing，对应"船主的窗户"）

AgentScope 自带 Studio（`agentscope studio`）与 Tracing，是官方标准的"看见 agent 在想什么/做什么"的面板。迁移时评估：把"船主的窗户"命题（决策过程透明＋判断居中）尽量落到 Studio + 自定义视图，而非继续手维护线程版 `办公室.py`。详见迁移路线（手册第五节）。

## 五 · 迁移状态（实时以《致接任者_工作手册》第五节为准）

- ✅ 模型层：`工具/迁移_模型层.py`（花名册→OpenAIChatModel/ReActAgent）。
- ⏳ 圆桌：MsgHub + fanout_pipeline + 动态收敛判据 + 缺证据查证（制度逻辑保留，调用机制换官方）。
- ⏳ 执行引擎＋制度：ReActAgent + Toolkit(白名单写文件/验收命令) + interrupt 急停 + 预算 hook + 三级审批 + 养专家记忆注入。
- ✅ 办公室窗户：现役异步后端 + SSE 事件流 + React 大厅三域布局（左场次、中现场、右班子）。

## 六 · 待船主在 Mac 上首跑核验的开放项（不臆断，列清）

1. **身份铁证**：服务端 `model` 字段如何经 AgentScope 读出（官方 `ChatResponse` 未明示暴露原始 provider model 字段）。在确认前，点名/身份核验**继续沿用** `模型接入.调用带元`（raw urllib，已验证可读 `data["model"]`）。
2. **Token Plan 三种现役型号**已于 2026-08-20 通过专用端点与 AgentScope 接入层真实发言核验：`qwen3.8-max`、`glm-5.2`、`deepseek-v4-pro-0813` 均正常返回。
3. 推理模型 thinking 行为（DeepSeek/GLM 默认开思维链）在 AgentScope 下与现状是否一致。
4. 实际 `agentscope.__version__`（回填 requirements.txt）。
