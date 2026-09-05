# 后端 · 逐条对照(我们 A × AgentScope B)

> 2026-06-17 舟。逐条印证,每条:A我们怎么做的 / B它怎么做的 / 重合还是各自独有 / 谁更成熟(带证据) / 整合路线。
> 证据来源:真读源码(我们 工具/*.py 全部;AgentScope .venv 源码 permission/_engine、state/_task、app/_tools/_agent_create、agent、team 等)。
> 判定图例:🟢抄B ｜ 🔵留A ｜ 🟣A+B合并 ｜ 🟡补(都缺/我们缺)

| # | 能力 | A:我们 | B:AgentScope | 重合/独有 | 谁成熟(证据) | 整合路线 |
|---|---|---|---|---|---|---|
| 1 | 多厂模型调用 | 模型接入.py 手写 urllib `_post/调用带元`,自己读服务端 model 字段 | `model`(8家ChatModel)+`credential`(10家)+`formatter`(各家Chat/MultiAgent),带流式/thinking/usage/重试 | 重合 | **B**(覆盖10家、官方维护、各厂适配) | 🟢 手写urllib→官方ChatModel,花名册保留为唯一真相 |
| 2 | 每岗用不同模型 | ✅ 花名册五岗五模型 | ❌ team成员强制继承leader(AgentCreate源码:worker.chat_model_config=leader的) | A独有(B的team做不到) | A(需求侧);B核心层各Agent可各配 | 🔵 留A的多模型,接在B核心Agent上(已验证qwen+deepseek) |
| 3 | 工具:读/写/搜/执行 | 引擎手写 读文件/写文件/执行验收命令 | `tool.Read/Write/Edit/Grep/Glob/Bash`现成+Toolkit+ToolResponse | 重合 | **B**(现成、经测试) | 🟢 抄B工具,弃手写 |
| 4 | 权限/白名单 | 引擎工具.写路径(单条:路径在不在白名单) | `PermissionEngine`5模式(每步问/只读/工作区自动批/全信任/无人值守转拒)+allow/deny/ask三类规则+危险操作强制确认+建议生成 | 重合 | **B远超**(我们1条 vs 它5模式+三类+安全豁免) | 🟢 抄B权限引擎当白名单底层 |
| 5 | 三级人事审批 | ✅ 审批.py(创建请示→经理预审[真模型判断/mock规则]→船主裁决) | ❌ 无(permission是工具权限,非"经理批/船主拍"人事流) | A独有 | A | 🔵 留A;"工具能不能碰"那层落到B的permission |
| 6 | 工单/任务/状态机 | 工单五列(待确认/待办/进行中/待验收/已完成/已作废)+控制文件 | `state.Task`(subject/desc/metadata/owner/三态:pending/in_progress/completed) | 重合 | 各有长:B是正经数据模型;**A状态更细**(多"待确认""待验收"=你拍板验收要的) | 🟣 B的Task结构 + A的5列验收语义合并 |
| 7 | 任务依赖 | 引擎._等依赖(手写按工单标题匹配) | Task原生`blocks/blocked_by`字段 | 重合 | **B**(正经字段 vs 我们字符串匹配) | 🟢 抄B的blocks/blocked_by |
| 8 | 执行循环 | 引擎._模型动作(自写ReAct:让模型读/写/验收/请示循环) | Agent.reply(官方ReAct,自带工具调用循环) | 重合 | **B**(官方ReAct,稳) | 🟢 抄B的Agent执行循环 |
| 9 | 预算/熔断 | 引擎._预算上限+_熔断(超步数停+熔断报告) | `ReActConfig.max_iters`(步数上限) | 重合 | B做"上限",**A的熔断报告更丰富** | 🟣 预算用B的max_iters;熔断后的"正式报告"留A |
| 10 | 急停/中断 | 引擎._应停/_停止信号(一票否决,熔断续步都让位) | `agent.interrupt()`+event(RequireUserConfirm) | 重合 | 各有:B有官方interrupt;**A的"一票否决优先级"语义更硬** | 🟣 用B的interrupt机制,保A的"急停最高优先"语义 |
| 11 | 重启恢复 | ✅ 引擎._恢复运行中(办公室重开接回在跑工单) | app `_manager`(后台任务管理/运行注册表) | 重合 | **B**(专门的后台任务管理器) | 🟢 抄B的manager |
| 12 | 正式报告(暂停/熔断/完工写原因·阶段·判断·下一步) | ✅ 引擎._写正式报告 | ❌ 无(只有event流) | A独有 | A | 🔵 留A(船主要的"成熟公司报告") |
| 13 | 圆桌:会前议题→发言→互驳 | ✅ 会议.py(项目经理定题+并行多岗两轮:发言+点名互驳) | 🟡 team(TeamSay/AgentCreate消息机制,但单模型) | A独有(灵魂) | A | 🔵 留A;消息传递可借B的event/message_bus |
| 14 | 动态收敛(轮数判出来) | ✅ 会议._评估收敛(经理判够不够/缺不缺证据) | ❌ 无 | A独有(灵魂) | A | 🔵 留A |
| 15 | 缺证据不编造 | ✅ 会议._尝试查阅+查不到请示船主 | ❌ 无 | A独有(灵魂,小酒红线) | A | 🔵 留A |
| 16 | 养专家(人格/记忆/岗位三件套) | ✅ 宪法+说明书+记忆×5岗 | ❌ 无(仅SubAgentTemplate.system_prompt一段) | A独有(灵魂) | A | 🔵 留A;system_prompt用B的模板注入口 |
| 17 | 技能系统 | ✅ 技能库内容(通用8+五岗28)+手写glob注入 | `skill.Skill/LocalSkillLoader`(加载机制) | 重合 | 内容A独有;**加载机制B现成** | 🟣 留A技能内容,加载用B的skill |
| 18 | 点名/开工自检 | ✅ 模型接入.点名(岗位体检单:职责/技能/边界/耗时) | 🟡 agent schema | A独有 | A | 🔵 留A;借B的agent schema |
| 19 | 会议事件流/可追溯纪要 | ✅ 协同.py(项目会/交办/受领回执/船主参会/项目状态摘要/json+md纪要) | 🟡 event流+message_bus(机器事件,无"会议纪要"概念) | A独有(给"窗户"的数据) | A | 🔵 留A;底层事件可挂B的event |
| 20 | 事件总线/流式 | 手写SSE | `event`(30+类型:Reply/ModelCall/ToolCall/Text/Thinking流式/人在环)+message_bus(redis) | 重合 | **B远超**(完整事件类型+redis总线) | 🟢 抄B的event/message_bus替手写SSE |
| 21 | 消息体系 | 手写dict | `message`(Msg+Text/Thinking/ToolCall/ToolResult/Data/Hint块) | 重合 | **B**(完整块类型) | 🟢 抄B的message |
| 22 | 过程追踪(给"窗户"透明) | 看板数据.py手写聚合 | `middleware.TracingMiddleware` | 重合 | **B**(官方追踪中间件) | 🟢 抄B的Tracing当底层,A的看板聚合呈现 |
| 23 | 隔离工位/沙箱 | ❌ 无(总纲C3要但没做) | `workspace`(Local/Docker/E2B) | B独有 | B | 🟡 补:抄B的workspace,填C3缺口 |
| 24 | MCP外部工具 | ❌ 无 | `mcp`(Client/Stdio/Http) | B独有 | B | 🟡 补:抄B的mcp |
| 25 | 记忆/向量检索 | 🟡 信箱/岗位记忆(纯文件,无检索) | `embedding`(8家向量模型+缓存) | B独有(检索) | B | 🟡 补:用B的embedding做记忆检索(对小酒记忆系统是金子) |
| 26 | 定时/唤醒(醒来窗) | ❌ 无 | app `_manager`(scheduler/wakeup_dispatcher)+schedule路由 | B独有 | B | 🟡 搬:对小酒"过日子/醒来窗"(总纲S3)现成金子 |
| 27 | 语音TTS | ❌ 无 | `tts`(DashScope含realtime)+TTSMiddleware | B独有 | B | 🟡 搬:对小酒表达层(P4)现成金子 |
| 28 | 多租户/会话隔离 | ❌ 单用户 | app(multi-tenant/session) | B独有 | B | ⬜ 自用不需要,不抄 |

---

## 后端结论(三类清账,逐条归位,别漏)

**🟢 抄 B(它成熟,替掉我们手写的)8项**:模型调用①、工具③、权限④、依赖⑦、执行循环⑧、重启恢复⑪、事件流⑳、消息体系㉑、过程追踪㉒。

**🔵 留 A(灵魂,B没有)8项**:多模型②、三级审批⑤、正式报告⑫、圆桌⑬、动态收敛⑭、缺证据不编造⑮、养专家⑯、点名⑱、会议纪要⑲。

**🟣 A+B 合并 3项**:工单(B结构+A验收5列)⑥、预算熔断(B上限+A报告)⑨、急停(B中断+A优先级)⑩、技能(A内容+B加载)⑰。

**🟡 补(我们缺,搬B)5项**:沙箱㉓、MCP㉔、记忆向量㉕、定时唤醒㉖、语音㉗——后三样是小酒本体直接要用的。

**⬜ 不抄1项**:多租户㉘(自用不需要)。

→ 一句话:**手写的底层零件(连模型/工具/权限/事件/依赖)全换成 B 的成熟件;我们血换的"圆桌+养专家+审批+正式报告+缺证据不编造"全留;顺手补上沙箱/记忆/定时/语音,给小酒铺路。**
