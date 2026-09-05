# 调研报告 · 多智能体“公司”从 workflow 到自主 agent 的业界现状

> 完成时间：2026-06-25（Asia/Shanghai）  
> 执行范围：只读调研 + 写本报告；未写代码，未修改其他项目文件。  
> 关键词说明：  
> - **workflow**：固定流程。代码提前规定“第一步做什么、第二步做什么”。  
> - **agent**：自主智能体。模型自己根据目标、环境反馈和工具结果决定下一步。  
> - **guardrail**：护栏。用规则、模型检查、权限系统挡住越权、危险、跑偏。  
> - **HITL / human-in-the-loop**：人在环。关键节点暂停，等人批准、补信息或否决。  
> - **tracing / observability**：追踪 / 可观测性。记录模型调用、工具调用、移交、失败，方便人看见过程和复盘。  

## 30 秒结论速览

1. **船主的核心判断“workflow 和 agent 不是一回事”有业界支撑。** Anthropic 明确区分：workflow 是预定义代码路径，agent 是模型动态决定流程和工具使用；LangGraph 也给出同样分界。成熟等级：**业界成熟共识**。来源：[Anthropic](https://www.anthropic.com/engineering/building-effective-agents)、[LangGraph](https://docs.langchain.com/oss/python/langgraph/workflows-agents)。

2. **“人是使用者，平台是死物”不是业界标准术语，但它对应一组成熟工程概念：agent runtime / tools / permission / guardrails / tracing / workspace / session。** 最贴近这句话的是 AgentScope 2.0：工具、权限、事件流、工作区、Agent Service、Agent Team 都由平台提供，leader agent 可以自己创建团队和派 worker。成熟等级：**前沿但已有框架落地**。来源：[AgentScope 2.0](https://docs.agentscope.io/versions/2.0.2/en)、[Agent Team](https://docs.agentscope.io/versions/2.0.2/en/deploy/agent-team)。

3. **业界不支持“所有怎么做都放开给 agent”这句走到极端。** 主流建议是：简单任务先用单 agent 或固定 workflow；只有开放、步骤不可预测、需要模型判断的任务才上自主 agent。成熟等级：**业界成熟共识**。来源：[Anthropic](https://www.anthropic.com/engineering/building-effective-agents)、[OpenAI practical guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)。

4. **多 agent 不是越多越高级。** OpenAI 建议先把单 agent 做强，只有复杂逻辑、工具过载、角色边界真的带来收益时再拆多 agent；Anthropic 也强调只在复杂度能带来可测收益时增加复杂度。成熟等级：**业界成熟共识**。来源：[OpenAI](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)、[Anthropic](https://www.anthropic.com/engineering/building-effective-agents)。

5. **“自主但不能黑箱”有成熟解法：权限审批、HITL、事件流、tracing、checkpoint / session。** OpenAI Agents SDK、LangGraph、AgentScope 都提供暂停、审批、恢复、追踪或持久状态。成熟等级：**成熟工程实践**。来源：[OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)、[OpenAI tracing](https://openai.github.io/openai-agents-python/tracing/)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[AgentScope Permission](https://docs.agentscope.io/versions/2.0.2/en/building-blocks/permission-system)。

6. **“一个项目经理 agent 自主端到端判断要不要开会、派活、验收、报告”没有查到成熟通用范式。** 框架支持 supervisor / manager / team / handoff，但“PM 自主经营整家公司”的成功案例主要停留在框架能力、示例、早期产品和软件工程 benchmark，不是稳定公认答案。成熟等级：**前沿探索 / 仍有争议**。

7. **最大的坑不是“能不能跑”，而是成本、失控、不可复现、难调试、评估难。** Princeton 的《AI Agents That Matter》指出很多 agent benchmark 只看准确率、不看成本，导致复杂昂贵系统被误判为进步；这点对公司架构非常要命。成熟等级：**公认问题**。来源：[AI Agents That Matter](https://arxiv.org/abs/2407.01502)。

8. **对我们最稳的方向：不是回到死 workflow，也不是裸奔式全自主，而是“单个常驻 leader agent + 平台级工具/权限/事件/记忆 + 必要时动态拉专家”。** 这与船主的世界观大体一致，但必须加四道硬闸：权限、可观测、预算/步数、评估。

---

## A. 好答案是什么

### A1. “agent 当员工 / 平台提供工具权限”有没有成熟范式？

**结论：有相近范式，没有统一术语。**  
业界更常用的说法不是“员工 / 死平台”，而是：

- **augmented LLM（增强 LLM）**：LLM 加工具、检索、记忆。Anthropic 把它视为 agentic system 的基础积木。
- **agent runtime（智能体运行时）**：负责循环、状态、工具调度、事件流。
- **tool / capability（工具 / 能力）**：agent 能调用的外部动作。
- **permission / guardrail / policy（权限 / 护栏 / 策略）**：agent 想做什么可以自己判断，但能不能执行由平台挡。
- **ACI / agent-computer interface（智能体-计算机接口）**：Anthropic 提醒要像设计人机界面一样设计 agent 使用工具的界面。

证据：

- Anthropic 定义 agent 时强调模型动态决定流程和工具使用，同时强调工具接口要清楚、透明、可测试。[Anthropic](https://www.anthropic.com/engineering/building-effective-agents)
- OpenAI 把 agent 的基础拆成 model、tools、instructions，并把 guardrails 当成安全部署的必要层。[OpenAI practical guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
- AgentScope 2.0 把事件系统、权限系统、工作区、Agent Service、Agent Team 做成平台能力，且 README 明说它“leverages the models' reasoning and tool use abilities rather than constraining them with strict prompts and opinionated orchestrations”。[AgentScope GitHub](https://github.com/agentscope-ai/agentscope)

成熟分级：**前沿探索，但基础部件已成熟**。  
“agent 像员工”是好隐喻；“平台提供工具、权限、事件、状态”是实在的工程范式。

### A2. 主流框架在 workflow ↔ agent 光谱上的位置

| 框架 | 更偏哪边 | 官方 / 资料中的核心取舍 | 对我们这题的含义 |
|---|---:|---|---|
| LangGraph | workflow 与 agent 两边都能做 | 官方区分：workflow 是预定路径，agent 是动态决定流程；强项是 graph、checkpoint、interrupt、persistence。 | 适合要强控制、强恢复、显式流程的系统；容易把“怎么做”继续写回图里。 |
| AutoGen / AG2 | 多 agent 对话 / team | AutoGen Core 是 event-driven、distributed、actor model；AgentChat 有 RoundRobin、Selector、MagenticOne 等 team。AutoGen GitHub 已提示 maintenance mode，AG2 继续社区路线。 | 强在多 agent 交流范式；但 group chat 容易变成表演和成本黑洞。 |
| CrewAI | crews + flows 混合 | Crews 是自主协作团队；Flows 是事件驱动、精确控制。官方明确两者可组合。 | 很像“公司模拟”，但容易落入角色扮演 + 固定任务链。 |
| OpenAI Agents SDK | code-first agent runtime | 建议先单 agent，再多 agent；多 agent 分 manager pattern 和 decentralized handoff；内置 handoff、guardrails、sessions、tracing、HITL。 | 很适合“一个 leader 自主调度 + 必要时 handoff”，但公司级团队 / UI 需要自己搭。 |
| Swarm | 轻量 handoff 教学样例 | 官方 GitHub 说它是 educational framework，核心是 agents + handoffs，强调轻量、可控、易测试。 | 思想有用，生产性不够；Agents SDK 是更正式接替方向。 |
| AgentScope 2.0 | 最偏“平台 + 自主 agent” | Event System、Permission System、Workspace、Middleware、Agent Service、Agent Team。leader agent 可用 TeamCreate / AgentCreate / TeamSay 组队。 | 最贴近“死平台 + 活 agent”；值得重点验证。 |
| MetaGPT | SOP workflow 化的软件公司 | 论文和官方文档核心是 `Code = SOP(Team)`，用标准作业程序降低幻觉和不一致。 | 是“AI 软件公司”的早期强范式，但它恰恰偏 SOP / assembly line，不是船主要的自主 PM。 |

成熟分级：**成熟框架各有取舍；没有一个框架替你证明“全自主公司”就是最佳答案。**

### A3. 单个 agent 自主端到端做项目协调，有成熟实现吗？

**结论：查不到成熟通用答案。**  
查到的是三类局部成功：

1. **客服 / 支持类 agent**：成功标准清楚，可查订单、退款、更新工单，有明确 guardrail 和 human escalation。
2. **coding agent**：代码任务有测试反馈，能用环境结果迭代，Anthropic 认为这是 agent 适合的典型场景。
3. **框架级 manager / supervisor**：OpenAI manager pattern、LangGraph supervisor、CrewAI hierarchical process、AgentScope leader agent 都支持“一个中心 agent 调别人”。

但没有查到“项目经理 agent 自己长期经营一个开发公司，自动判断开不开会、派谁、验收、回炉、报告，并被行业公认为成熟”的证据。

成熟分级：**前沿探索 / 仍有争议**。  
对我们的启示：船主要的方向不是胡想，框架能力正在靠近；但不能把“框架能支持”误判成“这套组织形态已经被验证成熟”。

**对我们的启示：**  
公司重构可以把“智能放回 agent 身上”作为方向，但落地时必须承认：业界成熟共识只支持“该自主时自主”，不支持“任何流程都别写、全交给 agent”。正确边界更像：平台硬编码权限、状态、事件、预算、审批；agent 自主选择工具、协作对象、执行策略；策略好不好靠 eval 和真实任务验。

---

## B. 过程中遇到过什么问题

### B1. 从 workflow 转向自主 agent 的坑

**1. 成本和延迟上升。**  
Anthropic 明确说 agentic system 常以更高 latency / cost 换更好任务表现；agent 自主多轮调用还会复合错误。OpenAI 也建议先单 agent、后多 agent，避免过早复杂化。

成熟分级：**成熟共识**。

**2. 复杂框架遮住底层 prompt / response，调试变难。**  
Anthropic 提醒框架会增加抽象层，可能让开发者看不清底层提示词和响应，并诱惑人加不必要复杂度。

成熟分级：**成熟共识**。

**3. 自主性带来不可复现和跑偏。**  
agent 会根据环境反馈改变路径，同样输入可能走出不同轨迹。解决只能靠 tracing、checkpoint、日志、评估集，而不是靠“相信模型”。

成熟分级：**成熟工程问题**。

**4. 评估难。**  
《AI Agents That Matter》指出 agent 评估不能只看准确率，还要看成本、标准化、可复现和 benchmark 是否能防投机取巧。  
来源：[AI Agents That Matter](https://arxiv.org/abs/2407.01502)、[项目页](https://agents.cs.princeton.edu/)。

成熟分级：**公认未完全解决**。

### B2. 多 agent 协作模式与各自的坑

| 模式 | 人话解释 | 代表框架 | 主要优点 | 主要坑 |
|---|---|---|---|---|
| supervisor / hierarchical | 一个经理调多个专家 | LangGraph supervisor、CrewAI hierarchical、OpenAI manager | 上下文统一，老板只面对一个入口 | 经理成瓶颈；多一层模型调用；经理如果只复述指令就是浪费 |
| group chat | 多人都在一个群里轮流说 | AutoGen RoundRobin / Selector | 分歧可见，适合头脑风暴 | 容易表演、跑题、互相污染上下文、token 爆 |
| handoff / swarm | 当前 agent 把控制权交给更合适的人 | OpenAI Agents SDK、Swarm、AutoGen Swarm | 接近真实转接；局部自主 | 交接标准难；状态边界难；可能来回踢皮球 |
| blackboard | 大家读写同一块公共状态板 | 多数需自建，部分框架可模拟 | 适合共享任务状态和证据 | 并发写、责任归属、信息过载 |
| orchestrator-workers | 中央 agent 动态拆任务并汇总 | Anthropic pattern、OpenAI manager | 适合子任务不可预知的复杂任务 | 汇总质量和 worker 选择要评估；成本高 |

成熟分级：**模式成熟，最佳使用边界仍依任务而定。**

### B3. 什么时候用自主 agent，什么时候用固定 workflow？

业界共识比较清楚：

- 用 **workflow**：任务路径清楚、步骤固定、稳定性比灵活性重要。例如固定文档处理、标准审批链、可预先枚举的流水线。
- 用 **agent**：步骤不可提前预测，需要模型根据环境反馈规划、搜索、试错、调用工具。例如复杂代码修改、开放式研究、异常客服、跨文件调查。
- 用 **单 agent**：工具和规则还没复杂到一个 agent 管不住。
- 用 **多 agent**：工具太多、指令太复杂、角色专长确实不同，且拆分后能测出质量收益。

来源：[Anthropic](https://www.anthropic.com/engineering/building-effective-agents)、[OpenAI practical guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)、[LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)。

成熟分级：**成熟共识**。

**对我们的启示：**  
现在公司“随便立个项也全员开会”的病，业界会判定为过度 workflow / 过度 ceremony。更好的分界不是“永远开会 / 永远不开会”，而是项目经理 agent 先用轻量判断：能直接做就直接做；需要证据就查证；跨角色且有真实分歧或风险才开会；高风险动作交给权限系统暂停。

---

## C. 大家解决过什么问题

### C1. 能力 vs 权限分离怎么实现？

成熟做法是：**agent 决定想调用什么工具；平台在工具调用前后判断能不能执行。**

证据：

- OpenAI Agents SDK 的 HITL 可以给工具标 `needs_approval`，模型提出工具调用后暂停，审批结果写入 RunState，再恢复原 run。[OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- OpenAI Guardrails 分 input / output / tool guardrails；尤其说明 manager、handoff、nested specialist 场景下需要 tool guardrails，因为只靠首尾检查不够。[OpenAI guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- AgentScope Permission System 对每次 tool call 产出 allow / deny / ask，支持 DEFAULT、EXPLORE、ACCEPT_EDITS、BYPASS、DONT_ASK 等模式，还能对危险路径和命令做 bypass-immune ASK。[AgentScope Permission](https://docs.agentscope.io/versions/2.0.2/en/building-blocks/permission-system)

成熟分级：**成熟工程实践**。

对公司来说，“有没有权限”可以硬编码；“这事该不该找测试工程师”不该硬编码成固定流水线，而应由项目经理 agent 通过工具和团队能力判断。

### C2. 自主 ≠ 黑箱：透明、可审计、可中断

成熟部件：

- **tracing**：OpenAI Agents SDK 默认记录 LLM generation、tool call、handoff、guardrail 等；AgentScope TracingMiddleware 记录 reply、model call、tool execution、token counts、pending HITL 等。
- **event stream**：AgentScope 2.0 把 text、thinking、tool call、tool result 作为 typed stream，可直接喂 UI。
- **interrupt / approval**：LangGraph interrupt 可暂停 graph，持久化状态，等待外部输入再 resume；OpenAI HITL 和 AgentScope permission 也有类似暂停恢复。
- **checkpoint / session**：LangGraph checkpointer 保存线程状态，store 保存跨线程长期记忆；OpenAI Sessions 保存多轮会话历史。

来源：[OpenAI tracing](https://openai.github.io/openai-agents-python/tracing/)、[AgentScope Middleware](https://docs.agentscope.io/versions/2.0.2/en/building-blocks/middleware)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[OpenAI Sessions](https://openai.github.io/openai-agents-python/sessions/)。

成熟分级：**成熟工程实践**。

### C3. 防失控 / 控成本的成熟做法

1. **最大轮数 / 最大 step / stop condition**：agent loop 必须有退出条件。OpenAI 指出 run 通常循环到最终输出、错误、最大 turn 等条件；Anthropic 也建议 stopping conditions。
2. **预算**：token、工具调用次数、时间、费用都要有上限。
3. **sandbox**：高风险代码和文件操作放沙箱。
4. **read-only / edit mode**：探索时只读，动手时再提权。
5. **blocking guardrail**：高风险输入先检查，再启动昂贵模型或副作用工具。
6. **eval**：不靠感觉，建立任务集，追踪成功率、成本、耗时、人工介入次数。

成熟分级：**成熟工程实践；评估方法仍在演进**。

### C4. 持续性 / 常驻：长期记忆和状态保持

查到的主流分层：

- **短期状态**：当前 run / thread / session 的消息、工具结果、checkpoint。LangGraph checkpointer、OpenAI Sessions、AgentScope session 都属于这层。
- **长期记忆**：跨线程的用户偏好、事实、共享知识。LangGraph store、mem0、Letta / MemGPT 这类系统在做这层。
- **工作区状态**：文件系统、MCP 客户端、技能、凭证隔离。AgentScope Workspace / Agent Service 明确提供这层。

成熟分级：**短期状态成熟；长期记忆仍是前沿且多坑**。  
长期记忆的难点不是“存进去”，而是何时写、写什么、怎么防幻觉污染、怎么遗忘、怎么评估记忆是否帮了任务。

来源：[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[OpenAI Sessions](https://openai.github.io/openai-agents-python/sessions/)、[AgentScope Agent Service](https://docs.agentscope.io/versions/2.0.2/en/deploy/agent-service)。

### C5. 复杂度自适应：简单直接做、复杂才开会

业界有成熟构件，但没有一个“一键成熟 PM”的答案：

- **routing**：先判断任务类型，再进入不同流程。Anthropic 把 routing 列为常见 workflow。
- **single-agent loop**：一个 agent 自己运行到退出条件，过程中可调用工具、交回人。
- **manager pattern**：manager agent 把子任务分给 specialized agents。
- **handoff**：由当前 agent 转交给更合适的 agent。
- **AgentScope team tools**：leader agent 可动态 TeamCreate / AgentCreate / TeamSay。

成熟分级：**构件成熟；组合成公司级自适应组织仍是前沿探索**。

**对我们的启示：**  
公司的底层应保留“可见的房间、工具、权限、记忆、事件流”，但不应该把“立项必开会”写死。更像 AgentScope 的方向：leader agent 拿到任务后，自己决定是否创建团队、创建什么 worker、用什么权限模板；平台只负责把它每一步显示出来、拦住危险动作、保存状态。

---

## D. 目前无法解决 / 有争议的问题

### D1. 多 agent 涌现不可预测

多 agent 会出现单 agent 没有的行为：互相强化错误、绕圈、空泛互夸、责任稀释、上下文污染。AutoGen / group chat 类框架能让这些行为显形，但不能保证自动变好。

成熟分级：**仍有争议 / 开放难题**。  
来源：[LLM-based Multi-Agents Survey](https://arxiv.org/abs/2402.01680)。

### D2. 长程自主可靠性

长时间自主运行会把小错误滚成大偏差。越长的任务越需要环境反馈、checkpoint、人工节点、预算闸和回滚能力。业界已经有工程部件，但“长期可靠自主”还不是公认解决的问题。

成熟分级：**公认未解决**。

### D3. 自主性 vs 对齐可控

给 agent 更多自主权，会提高灵活性，也会提高误操作、越权、成本爆炸和黑箱决策风险。当前成熟做法不是消灭张力，而是用权限、HITL、tracing、sandbox、policy 分层兜住。

成熟分级：**长期张力，靠工程护栏缓解**。

### D4. 怎么评估“agent 用得好不好”

这是最关键的开放难题之一。单看最终答案不够，至少要同时看：

- 任务完成率
- 成本 / token
- 工具调用正确率
- 步数效率
- 是否查证
- 是否遵守权限
- 人工介入次数
- 是否可复现
- 失败时是否留下足够证据

《AI Agents That Matter》尤其提醒：如果 benchmark 不看成本，只看准确率，复杂昂贵 agent 会被错误奖励。

成熟分级：**公认未解决，但方向清楚**。  
来源：[AI Agents That Matter](https://arxiv.org/abs/2407.01502)、[Survey on Evaluation of LLM-based Agents](https://arxiv.org/html/2503.16416v1)。

### D5. 记忆污染和身份连续性

长期记忆让 agent 更像“同一个人”，但也会引入错误记忆、过期信息、隐私泄漏、错误自我叙事。对小酒项目尤其敏感：记忆不是越多越好，写入标准必须比普通 agent 更严。

成熟分级：**前沿探索 / 对我们是高风险核心问题**。

**对我们的启示：**  
自主公司不能靠“看起来会开会”验收。真正验收要看：它是否少开无用会、缺证据是否查证、权限是否守住、过程是否可见、成本是否受控、失败能不能复盘、经验是否正确进入岗位记忆。

---

## 给我们这套公司架构的判断

### 1. 船主世界观被支持的部分

- **workflow 与 agent 要分清**：支持。
- **工具 / 会议室 / 权限 / 工作区是平台**：支持。
- **agent 应该自己决定怎么用工具和协作通道**：在开放任务里支持。
- **权限和高风险动作必须由平台拦住**：强支持。
- **自主过程必须可见、可中断**：强支持。

### 2. 需要警惕的部分

- “凡怎么做都还给 agent”不能走成无边界裸奔。业界会保留 instructions、routines、policies、evals，这些不是“替 agent 思考”，而是组织对人的工作标准。
- 多 agent 不等于成熟公司。多一个 agent 就多一层成本、状态、失败方式。
- “项目经理自己判断开会”是正确方向，但必须被观测和评估：它为什么不开会 / 为什么开会，都要留下可读证据。

### 3. 最建议的落地形态（调研推导，不是执行方案）

**常驻 leader agent + 平台级 Agent Service + 权限 / 事件 / 记忆 / 工作区 + 动态专家团队。**

人话版本：

- 平时老板只面对一个项目经理。
- 项目经理先自己判断：直接答、直接做、查证、请示、还是拉人。
- 需要专家时，项目经理用平台工具创建 / 唤醒对应岗位。
- 每个岗位有自己的权限模板：探索者只读，工程师可编辑，测试可运行检查，文案可写文档。
- 删除、花钱、密钥、最终交付、越界写文件，一律平台暂停等船主。
- 所有工具调用、会议发言、移交、审批、失败、恢复，都进事件流，显示在“船主的窗户”。

这条路最贴近 AgentScope 2.0 的现成能力，也最贴近船主“人是使用者，平台是死物”的根。

### 4. 一句总判断

**业界支持我们从死 workflow 往自主 agent 走，但不支持没有护栏的全自主；最好的答案是“能力放在人身上，权限和可见性焊在平台里”。**

---

## 参考来源清单

1. Anthropic, “Building effective agents”, 2024-12-19.  
   https://www.anthropic.com/engineering/building-effective-agents

2. OpenAI, “A practical guide to building agents”.  
   https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/

3. OpenAI Agents SDK, Handoffs.  
   https://openai.github.io/openai-agents-python/handoffs/

4. OpenAI Agents SDK, Human-in-the-loop.  
   https://openai.github.io/openai-agents-python/human_in_the_loop/

5. OpenAI Agents SDK, Guardrails.  
   https://openai.github.io/openai-agents-python/guardrails/

6. OpenAI Agents SDK, Tracing.  
   https://openai.github.io/openai-agents-python/tracing/

7. OpenAI Agents SDK, Sessions.  
   https://openai.github.io/openai-agents-python/sessions/

8. OpenAI Swarm GitHub.  
   https://github.com/openai/swarm

9. LangGraph, Workflows and agents.  
   https://docs.langchain.com/oss/python/langgraph/workflows-agents

10. LangGraph, Multi-agent.  
    https://docs.langchain.com/oss/python/langchain/multi-agent

11. LangGraph, Interrupts.  
    https://docs.langchain.com/oss/python/langgraph/interrupts

12. LangGraph, Persistence.  
    https://docs.langchain.com/oss/python/langgraph/persistence

13. Microsoft AutoGen, Core.  
    https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/index.html

14. Microsoft AutoGen, Group Chat.  
    https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html

15. Microsoft AutoGen, Teams.  
    https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html

16. AG2 Documentation, Quickstart.  
    https://docs.ag2.ai/latest/docs/home/quickstart/

17. CrewAI Documentation.  
    https://docs.crewai.com/

18. CrewAI, Hierarchical Process.  
    https://docs.crewai.com/en/learn/hierarchical-process

19. CrewAI GitHub.  
    https://github.com/crewAIInc/crewAI

20. AgentScope 2.0 Documentation.  
    https://docs.agentscope.io/versions/2.0.2/en

21. AgentScope Permission System.  
    https://docs.agentscope.io/versions/2.0.2/en/building-blocks/permission-system

22. AgentScope Middleware.  
    https://docs.agentscope.io/versions/2.0.2/en/building-blocks/middleware

23. AgentScope Tool.  
    https://docs.agentscope.io/versions/2.0.2/en/building-blocks/tool

24. AgentScope Agent Service.  
    https://docs.agentscope.io/versions/2.0.2/en/deploy/agent-service

25. AgentScope Agent Team.  
    https://docs.agentscope.io/versions/2.0.2/en/deploy/agent-team

26. AgentScope GitHub.  
    https://github.com/agentscope-ai/agentscope

27. MetaGPT paper, “MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework”.  
    https://arxiv.org/abs/2308.00352

28. MetaGPT Documentation.  
    https://docs.deepwisdom.ai/main/en/guide/get_started/introduction.html

29. MetaGPT GitHub.  
    https://github.com/FoundationAgents/MetaGPT

30. Kapoor et al., “AI Agents That Matter”, 2024.  
    https://arxiv.org/abs/2407.01502

31. “AI Agents That Matter” project page.  
    https://agents.cs.princeton.edu/

32. Guo et al., “Large Language Model based Multi-Agents: A Survey of Progress and Challenges”, 2024.  
    https://arxiv.org/abs/2402.01680

33. Wang et al., “A Survey on Large Language Model based Autonomous Agents”.  
    https://arxiv.org/abs/2308.11432

34. “Survey on Evaluation of LLM-based Agents”, 2025.  
    https://arxiv.org/html/2503.16416v1

## 覆盖范围与未覆盖

已覆盖：Anthropic、OpenAI Agents SDK / Swarm、LangGraph、AutoGen / AG2、CrewAI、AgentScope 2.0、MetaGPT，以及 agent 评估和多 agent survey。  

未深入覆盖：AWS Strands、Google ADK、Semantic Kernel、LlamaIndex agents、Devin / Cursor / Claude Code 的闭源内部训练细节、企业私有落地案例的真实 ROI 数据。原因：本任务核心是“多智能体公司从 workflow 到自主 agent 的架构真相”，上述来源已能支撑主要结论；闭源产品细节无法核验，不强行编造。
