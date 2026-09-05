# A · AgentScope 原生重建开发公司 · 计划与上下文（新窗口的舟先读这份）

> 写于 2026-06-17，作者：舟。船主拍板的总路线：**归档旧公司 → 走 A（原生重建）走彻底 → 新旧比对、多退少补**。
> 本文是走 A 的主交接文档与实时进度。每次有分量的进展后必须更新。
> 旧公司已忠实归档：`10_工程遗产_历代身体/04_开发公司_自研引擎版/`（含 `_读我_这是什么.md` 与 9 条血换制度清单）。

---

## 一、为什么走 A（前因，别再翻案）

总纲【C6】原选型是 AgentScope，被上一任舟架空成自研引擎。2026-06-17 舟在船主 Mac 上 introspect 本机 **agentscope 2.0.2**（文档站是 v1.0 不可信，装好的包才是金标准），扒清一个颠覆事实：**AgentScope 2.0 原生几乎覆盖了我们手搓的全部公司能力**。继续"薄迁移"（拿 Agent+Toolkit+reply 包旧纯函数）= 在全功能框架上糊自研薄壳，红利全丢、负担全背，是又一座教堂。

船主决策：**先干净地拥抱框架（走 A 走彻底，不一边迁一边回头纠结），再用旧公司归档当对照表查漏补缺（多退少补）。** 两头风险都被这个结构兜住。

AgentScope 2.0 设计哲学原话："enabling reasoning and tool-use **rather than constraining them with rigid orchestrations**"——与我们血换的"养专家、机制最小化智能最大化、辩论归人设层不靠代码编排"是同一句话。深用它是顺势，不是将就。

---

## 二、AgentScope 2.0.2 真实能力 → 公司需求映射（金标准速查，2026-06-17 本机 introspect）

| 公司需求（旧公司手搓） | AgentScope 2.0 原生 | 模块 |
|---|---|---|
| 项目经理建团队/派活/给岗位发话/收敛 | `TeamCreate / TeamSay / TeamDelete / AgentCreate`（leader 派生管理 workers） | `agentscope.app._tools` |
| 白名单 + 写路径校验 + 三级审批 | `PermissionEngine / PermissionRule / PermissionMode / PermissionDecision`（细粒度，含 bypass） | `agentscope.permission` |
| 技能库 通用/岗位 + 手动 glob 拼 prompt | `Skill / LocalSkillLoader / SkillLoaderBase` | `agentscope.skill` |
| 工单五列 + 控制文件 + 状态机 | `AgentState / Task / TaskContext` + 工具 `TaskCreate/Get/List/Update` | `agentscope.state` / `agentscope.tool` |
| 读文件/写文件/验收命令工具 | `Read / Write / Edit / Grep / Glob / Bash`（现成） | `agentscope.tool` |
| 急停 / 中途干预 / 船主随时在场（总纲G） | 统一**事件总线 + human-in-the-loop** | `agentscope.event` |
| "船主的窗户"过程可见 | `TracingMiddleware`（+ 事件总线连前端） | `agentscope.middleware` |
| 隔离工位 git worktree（总纲C3） | workspace / sandbox（local / Docker / E2B） | `agentscope.workspace` |
| 熔断 / 后台任务 / 重启恢复 | 后台任务管理 / 取消调度 / 运行注册表 / 唤醒调度 | `agentscope.app._manager` |
| 前端窗户（办公室.py+页面.js 手搓） | `create_app`（FastAPI 服务，路由 chat/session/agent/model/workspace/schedule/tts）+ 官方 `examples/web_ui`（React） | `agentscope.app` + GitHub examples |

**关键 2.0.2 调用事实（已对齐，别再用 v1.0 名字）**：
- 模型：`OpenAIChatModel(credential=OpenAICredential(api_key=,base_url=), model=, parameters=OpenAIChatModel.Parameters(max_tokens=), formatter=OpenAIChatFormatter(), stream=)`
- 智能体：`Agent(name=, system_prompt=, model=, toolkit=, react_config=ReActConfig(max_iters=))`；调用 `await agent.reply(Msg) -> Msg`（**不是** `agent(msg)`）
- 消息：`Msg(name=, content=[TextBlock(type="text",text=)], role=)`（关键字 + 块列表）
- 工具：`Toolkit(tools=[FunctionTool(fn)...])`；自带 `Read/Write/Edit/Grep/Glob/Bash`
- 无 `agentscope.pipeline`（v1.0 的 MsgHub/fanout）；无 `agentscope` CLI（本机未装）

**起官方前端还差的依赖**：`fastapi`、`apscheduler`（必需）、`redis`（消息总线，可选）；已有 `uvicorn / sse_starlette / pydantic`。官方前端 `examples/web_ui`（React+pnpm）**不在 pip 包内**，需从 GitHub 取源码 + Node/pnpm。

来源：本机 introspect + [github.com/agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope)。

---

## 三、A 的子阶段（每段独立可验、可回滚；不一次盖大教堂）

- **A0 地基 · 验官方原语**：补装 `fastapi`/`apscheduler`；`create_app` 起官方后端；从 GitHub 取 `web_ui` 跑前端；跑通官方 `TeamCreate→AgentCreate→TeamSay` 最小协作 demo——亲眼确认官方原语能装下「项目经理派活→岗位发言→收敛」（落实总纲 P0-T2 最小流程）。**先看货，别赌。**
- **A1 团队骨架**：花名册五岗 → 官方 team 工具（项目经理=leader，四岗=worker）；跑通 立项→项目会→出工单。
- **A2 执行 + 权限**：`permission` 引擎做白名单/审批；`state/Task` 做工单；自带 `Read/Write/Grep` 做动手；`event/cancel` 做急停。
- **A3 技能 + 人格**：`skill` 加载五岗技能与人格（养专家、缺证据不编造的制度注入）。
- **A4 窗户**：`create_app`+`web_ui` 起前端，按"船主的窗户"（过程透明+判断居中+暗房亮桌）定制；评估官方 web_ui 改造 vs 保留手搓前端。
- **A5 命脉贯通**：完整跑通一次「领工单→执行→验收→复盘进记忆→急停」——A 彻底走完的标志。

**A 的工作目录建议（A0 第一个决定）**：开新分支 `agentscope-native`；新代码新建在干净子结构，旧 `.py` 暂留 `工具/` 不删（有归档兜底，但留着随手对照），走完 A、比对完再清。

---

## 四、当前进度

- ✅ 旧公司忠实归档到 `10_工程遗产_历代身体/04_开发公司_自研引擎版/`（含读我+9条制度清单），密钥未入档。
- ✅ AgentScope 2.0.2 真实能力扒清，映射表见上（第二节）。
- ✅ **A0 地基跑通（2026-06-17）**：2.0.2=官方最新（不升级）；官方源码 clone 到持久位置 `30_开发公司/_官方参考_agentscope/`（已 gitignore，**别再放 /tmp，会被清**）；装 `agentscope[full]`+Redis(brew services)+pnpm(corepack)；**官方完整工作台三服务全起通**——前端 5173 / web_ui Node BFF 3000 / AgentScope 核心 8000，浏览器 `http://localhost:5173/` HTTP 200。
- ✅ **命门查清（2026-06-17，三方铁证：官方文档 + 源码 + 社区）**：官方 app 层 Agent Team **强制成员继承 leader 同一个模型**（官方文档原话 "Workers inherit the leader's chat model"；AgentCreate 源码第466行 worker session 写死复制 leader chat_model_config；SubAgentTemplate/AgentCreate 均无 model 字段；GitHub 无人做多模型 team）。**五岗五模型装不进官方现成 team。** 且官方 2.0 删了核心层多 agent 编排原语（pipeline/MsgHub），新的还在排队（issue #1420）；Agent Team 是 2026-06 新功能、issue 里 team/流式/DeepSeek-400 等 bug 不少。
- ✅ **多模型协作·核心层验证通过（2026-06-17 真跑 qwen+deepseek）**：用 agentscope 核心 `Agent` 各配各模型 + 手动串协作（`agent.reply` + 把上一位发言拼进下一位 prompt），老梁(qwen)主数据库、老纪(deepseek)真针对性反驳"你把理想当常态了"——**不同模型互补可行,这是公司灵魂需求的活证据。**
- 📌 **据此定路线（修正之前"全盘官方 app"的乐观）**：后端用**官方核心零件**（permission/skill/state/tool/workspace/单 agent 多模型），**"五个不同模型坐一桌协作"这层自接**（官方没有、逃不掉，但已验证就几行）；官方带 team 的前端 web_ui 绑死单模型，大概率自定义。
- ⏭️ **下一步（待船主拍）**：把这个骨架（官方零件 + 自接协作层 + 前端）怎么搭理成方案，船主点头再动手。**别抢跑。**
- 启动备忘（重开机后恢复官方工作台）：①`brew services start redis` ②后端 `cd _官方参考_agentscope/examples/agent_service && source ../../.venv/bin/activate && uvicorn main:app --port 8000` ③前端 `cd _官方参考_agentscope/examples/web_ui && HUSKY=0 pnpm --config.verify-deps-before-run=false dev`（绕 pnpm 依赖预检）④浏览器开 5173，界面里填 endpoint `http://localhost:8000`。

## 五、新窗口的舟读取顺序

1. `~/Movies/全局记忆/CLAUDE.md`
2. `~/Movies/全局记忆/舟的自传.md`（含 2026-06-17 走 A 决策条目）
3. `~/Movies/全局记忆/致接任者_工作手册.md`（第五节当前真相）
4. **本文件**（走 A 主文档）
5. 比对基准：`10_工程遗产_历代身体/04_开发公司_自研引擎版/_读我_这是什么.md`（旧公司 9 条血换制度）
6. 需要时：`总纲_2026重启版_v1.0.md`（C6 含迁移注记）

读完先听船主说，别抢跑。先统一想法再动手；动手前过《查证 5 问》；本地能跑就别拿船主机器当编译器（已不再是限制——本机有 .venv 能 introspect/真跑）。
