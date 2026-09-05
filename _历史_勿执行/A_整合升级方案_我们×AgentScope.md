# 我们 × AgentScope · 后端+前端 整合升级方案

> 2026-06-17 舟。船主方向:我们已基于需求做了很多开发,AgentScope 有更成熟、大厂测试过的东西。**后端互补整合(能抄就抄、能搬就搬),前端同样查漏补缺。给公司一次大升级。别漏。**
> 总原则:**抄它成熟的零件,保留我们血换的灵魂,补双方都缺的。** 不盖第六座教堂——每阶段独立可验可回滚。

---

## 〇、底座架构决定(一句话)

**用 AgentScope 的核心层零件当地基,在它之上跑我们的「多模型圆桌」协作层和「船主的窗户」前端。** 不用 AgentScope 的 app 层 team(单模型,装不下五岗五模型),但 app 层的好东西(定时/唤醒/语音/后台任务/凭据)按需搬。

```
我们的窗户前端(命题:决策透明+判断居中+暗房亮桌)
        ↓ 接口
我们的协作层:多模型圆桌(会前议题→动态收敛→缺证据不编造→养专家五岗五模型)
        ↓ 建在
AgentScope 核心零件:model/credential/formatter · tool · permission · state/Task · event · workspace沙箱 · skill · mcp · tts · schedule
```

---

## 一、后端逐项对比(全)

标注:🟢=抄/搬 AgentScope(它更成熟) ｜ 🔵=保留我们的(血换灵魂,它没有) ｜ 🟡=补(双方缺或我们缺)

| 能力 | 我们旧公司 | AgentScope | 结论 |
|---|---|---|---|
| **模型接入(各厂商)** | 手写 urllib(模型接入.py 调用带元) | ✅ `model`+`credential`+`formatter` 覆盖 10 家(OpenAI/DeepSeek/DashScope/Anthropic/Gemini/Moonshot/Ollama/XAI…),带流式/thinking/usage | 🟢 **抄**:手写 urllib → 官方 ChatModel。更稳、各厂适配、省维护 |
| **多模型(每岗不同)** | ✅ 花名册五岗五模型 | ❌ team 强制单模型(核心层各 Agent 可各配) | 🔵 **保留**我们的多模型,接在核心 Agent 上(已验证) |
| **工具(读/写/grep/bash)** | 手写 读文件/写文件/验收命令 | ✅ `tool.Read/Write/Edit/Grep/Glob/Bash` 现成 | 🟢 **抄**官方工具,别自己写 |
| **权限/白名单** | 手写 引擎工具.写路径 | ✅ `permission.PermissionEngine/Rule/Mode/Context` 细粒度大厂级 | 🟢 **抄** PermissionEngine 做白名单底层 |
| **三级审批(人事)** | ✅ 审批.py(经理预审→船主裁决) | ❌ 无(permission 是工具权限非人事) | 🔵 **保留**我们的三级审批逻辑,白名单那层落到官方 permission |
| **工单/任务/状态** | 手写 工单五列+控制文件 | ✅ `state.Task/TaskContext` + `tool.TaskCreate/Get/List/Update` | 🟢 **抄** state/Task 升级工单底座,保留我们五列语义 |
| **执行引擎(熔断/预算/依赖/重启恢复/正式报告)** | ✅ 引擎.py 828行(熔断/预算上限/_等依赖/_恢复运行中/正式报告/暂停) | 🟡 `ReActConfig.max_iters`(预算) + app `_manager`(后台任务/取消/恢复) | 🔵 **保留**熔断/正式报告/依赖/恢复的制度逻辑;🟢 预算用 max_iters、后台任务/恢复借 app manager |
| **圆桌协作(会前议题/动态收敛/缺证据/多岗互驳)** | ✅ 会议.py(灵魂) | 🟡 team(TeamSay/AgentCreate 消息机制,但单模型) | 🔵 **保留**圆桌逻辑;消息传递借鉴 event/message_bus |
| **急停** | ✅ 引擎._应停/_停止信号(一票否决) | ✅ `agent.interrupt()` + event(RequireUserConfirm/外部执行) | 🔵 保留语义;🟢 用官方 interrupt/event 机制 |
| **事件总线/流式** | 手写 SSE | ✅ `event`(全套事件类型) + app `message_bus`(redis) | 🟢 **抄** event/message_bus,替手写 SSE |
| **技能** | ✅ 技能库(通用8 + 五岗28) 手写 glob 注入 | ✅ `skill.Skill/LocalSkillLoader` | 🔵 保留技能**内容**;🟢 加载机制用官方 skill |
| **养专家(人格/记忆/岗位三件套)** | ✅ 宪法/说明书/记忆 五岗 | ❌ 无(仅 SubAgentTemplate.system_prompt) | 🔵 **保留**——灵魂,它没有 |
| **缺证据不编造** | ✅ 会议._尝试查阅+查证机制 | ❌ 无 | 🔵 **保留**——灵魂 |
| **动态收敛(轮数判出来)** | ✅ 会议._评估收敛 | ❌ 无 | 🔵 **保留**——灵魂 |
| **点名/开工自检** | ✅ 模型接入.点名(岗位体检单) | 🟡 agent schema | 🔵 保留;借鉴 agent schema |
| **隔离工位(沙箱)** | ❌ 无(总纲 C3 要但没做) | ✅ `workspace`(Local/Docker/E2B) | 🟡 **补**:抄 workspace,补上总纲 C3 缺口 |
| **MCP(外部工具生态)** | ❌ 无 | ✅ `mcp`(MCPClient/Stdio/Http) | 🟡 **补**:抄 mcp,接入外部工具 |
| **记忆向量检索** | 🟡 信箱/岗位记忆(纯文件) | ✅ `embedding`(向量+缓存) | 🟡 **补**:用 embedding 做记忆检索(对小酒记忆系统是金子) |
| **过程追踪(对"窗户"过程透明)** | 手写 看板数据聚合 | ✅ `middleware.TracingMiddleware` | 🟢 **抄** Tracing 做过程透明底层 |
| **定时/唤醒(醒来窗,对小酒)** | ❌ 无 | ✅ app `_manager`(scheduler/wakeup_dispatcher) + schedule | 🟡 **搬**:对小酒"过日子的进程/醒来窗"(总纲 S3)是现成金子 |
| **语音 TTS(对小酒)** | ❌ 无 | ✅ `tts` + `middleware.TTSMiddleware` | 🟡 **搬**:对小酒 P4 表达层是现成金子 |
| **消息体系(thinking/toolcall/toolresult块)** | 手写 dict | ✅ `message`(完整块类型) | 🟢 **抄** message 体系 |
| **多租户/会话隔离** | ❌ 单用户 | ✅ app(multi-tenant/session) | ⬜ 自用不需要,不抄 |

---

## 二、前端逐项对比(全)

| 能力 | 我们旧前端(页面.js 43KB 原生) | AgentScope web_ui(React+Vite+TS) | 结论 |
|---|---|---|---|
| **命题"船主的窗户"(决策透明+判断居中)** | ✅ 大厅/项目室/该你出手 三栏 | ❌ 通用聊天台,无此命题 | 🔵 **保留**——灵魂 |
| **暗房亮桌审美** | ✅ | ❌ 通用浅色 | 🔵 保留 |
| **多AI协作展示(泳道/会议旁听)** | ✅ 项目室泳道+项目会旁听 | 🟡 team sidebar(列成员,单模型) | 🔵 保留;借鉴 sidebar 交互 |
| **过程可追溯(过程札记/观察/正式报告/里程碑/叙事)** | ✅ 看板数据全套 | 🟡 消息流 | 🔵 保留——灵魂 |
| **历史工单/项目记忆** | ✅ | 🟡 sessions | 🔵 保留 |
| **回炉/请示/裁决卡** | ✅ 该你出手 | 🟡 | 🔵 保留 |
| **附件(图/文件/截图)** | ✅ | ✅ | 都有 |
| **配 key 界面(凭据)** | ❌ 改 .env | ✅ credential 页面 | 🟡 **抄**:配 key 界面 |
| **定时任务界面** | ❌ | ✅ schedule 页面 | 🟡 **抄**(对小酒) |
| **语音界面** | ❌ | ✅ AudioContext | 🟡 **抄**(对小酒) |
| **技能/工位管理界面** | 🟡 | ✅ useSkills/useWorkspace | 🟡 借鉴 |
| **多语言** | 中文 | 中英 i18n | ⬜ 中文够用 |
| **技术栈** | 原生 JS 单文件 | React+Vite+TS+shadcn(工程化、可维护、组件丰富) | 🟡 **待定**:是否迁 React(可维护性大升,但有重写成本)——A4 决策点 |

---

## 三、整合升级 · 分阶段计划(每阶段独立可验、可回滚)

> 顺序原则:先换"白捡红利最大、风险最低"的底层零件,再重建协作灵魂,最后接前端、补未来能力。

- **升级 1 · 模型与工具底座换官方(白捡最大、风险最低)**
  - 模型接入.py 的手写 urllib → AgentScope `model`+`credential`+`formatter`(五岗端点全官方);保留花名册=唯一真相。
  - 手写读写/验收命令 → 官方 `tool.Read/Write/Grep/Bash` + `Toolkit`。
  - 验收:五岗各自真调通(含 DeepSeek/GLM thinking)、工具读写在白名单内跑通。
- **升级 2 · 权限/状态/工单换官方底座**
  - 白名单 → `permission.PermissionEngine`;工单五列语义保留、底座换 `state.Task`。
  - 验收:越界写被拦、工单状态流转、三级审批仍生效。
- **升级 3 · 多模型圆桌协作层重建(灵魂,核心)**
  - 在 AgentScope 核心 `Agent`(各配各模型)之上,重建我们的圆桌:会前议题→并行发言→动态收敛→缺证据不编造→养专家五岗人格注入。消息/急停用官方 event/interrupt。
  - 验收:五岗五模型真开会、真互驳、动态收敛、缺证据请示(对照旧公司 9 条制度逐条不丢)。
- **升级 4 · 前端窗户对接 + 抄官方便利界面**
  - 我们的"窗户"(三栏/暗房/过程可追溯)对接新后端;抄官方的"配 key 界面";决定前端技术栈(保留原生 JS 还是迁 React——单列决策)。
  - 验收:船主打开能看见五岗五模型协作全过程、能拍板;配 key 不再改 .env。
- **升级 5 · 补未来能力(沙箱/MCP/记忆向量/定时/语音)**
  - 抄 workspace(隔离工位=总纲C3)、mcp(外部工具)、embedding(记忆检索);搬 schedule/wakeup(小酒醒来窗 S3)、tts(小酒语音 P4)。
  - 验收:逐项小验,不强求一次全上。

---

## 四、整合后,三类内容清账(别漏)

**🟢 抄/搬 AgentScope(它成熟、大厂测试,替掉我们手写的):** 模型接入×10厂、读写grep工具、PermissionEngine权限、state/Task工单、event/message_bus事件流、message消息体系、TracingMiddleware过程追踪、workspace沙箱、mcp、embedding、schedule/wakeup定时唤醒、tts语音、前端配key/定时/语音界面。

**🔵 保留我们的(血换灵魂,它没有):** 多模型五岗、圆桌(会前议题/动态收敛/缺证据不编造/多岗互驳)、养专家(岗位三件套人格+记忆)、三级审批人事逻辑、熔断/正式报告/依赖/重启恢复制度、点名自检、技能库内容(通用8+五岗28)、前端"船主的窗户"命题+三栏+暗房亮桌+过程可追溯+历史工单+项目记忆。

**🟡 补(双方缺/我们缺):** 隔离工位沙箱(总纲C3)、MCP外部工具、记忆向量检索、定时唤醒(小酒S3)、语音(小酒P4)、配key界面。

---

## 五、纪律与红利

- **不盖教堂**:五个升级各自独立可验可回滚;旧公司已归档(10_/04)作对照基准,随时多退少补。
- **灵魂不丢**:每步对照旧公司 9 条血换制度逐条核,丢一条算失败。
- **给小酒的红利**:这次大升级顺手把 schedule/wakeup(醒来窗)、tts(语音)、workspace(隔离)、embedding(记忆检索)这些**小酒未来直接要用的官方能力**引进了门——公司练兵场和小酒本体在这里第一次共享技术底座。
