# context-dependent intent（语境依赖意图）解法

联网查证日期：2026-07-05  
本地核对：已读 `工具/活_本人.py` 里的 `_像招呼` / `_判话题边界` / `构造聊天消息` / `聊天唤醒`，以及现有 `G3` 两份设计文档。

## 0. 先把现有 bug 说准

现在这条链路是：

1. `_像招呼()` 先看表面词。`_招呼词` 里有 `在干嘛` / `在干什么` / `干嘛呢`，且 `t.startswith(w)` 也算命中。
2. `_判话题边界()` 一旦 `_像招呼()` 为真，就直接返回 `新话题 + 窗口1`，embedding 漂移不会再看。
3. `聊天唤醒()` 拿这个窗口调用 `构造聊天消息()`。
4. `构造聊天消息()` 只把 `records[-窗口:]` 发给模型。

所以 `你们在干嘛？` 如果被词表当招呼，模型只看见最新一句，完全看不见前面“用户要三个开场钩子、团队却写了一堆分析”这个不满证据。它只能按招呼/问进度答。

这不是“招呼词表少一个例外”。根因是：**我们把“意图判断需要的上下文”和“最终回答需要的上下文”绑成了同一个窗口。**  
窗口本来应该是判断后的结果，现在却在判断前被词表砍掉了。

## 1. 2025-2026 社区最新主流做法

### 1.1 不再把 intent 当“单句分类”，而是当 dialogue act / command

**Dialogue act（对话动作）**：不是问“这句话字面是什么意思”，而是问“这句话在当前对话里做了什么动作”。同一句 `你们在干嘛？` 可以是：

- `social_checkin`：招呼，问你们在忙啥。
- `repair_complaint`：修复/不满，意思是“你们跑偏了”。
- `status_query`：问当前任务进度。

Rasa CALM 的新式做法很典型：它不让 NLU 只吐一个 intent label，而是让 LLM-based command generator 读取当前对话，输出一组 commands，例如 `StartFlow`、`SetSlot`、`search and reply`。Rasa 文档明确说，这种 command 序列比传统分类式 NLU 更能表示用户想怎样推进对话。出处：<https://rasa.com/docs/reference/config/components/llm-command-generators/>

落到我们这里，就是不要让 `_像招呼()` 直接决定窗口，而是先产出类似：

```json
{
  "act": "repair_complaint",
  "rewritten_intent": "实例主人在指出刚才团队跑偏了，不是在问进度",
  "context_policy": "repair_window",
  "confidence": 0.86
}
```

### 1.2 intent rewriting / decontextualization：先把“当前意图”重写干净

**Decontextualization（去上下文化）**：把依赖上文才能懂的话，改写成独立可理解的一句话。  
LlamaIndex 的 `condense_question` / `condense_plus_context` 就是成熟工程范式：每次聊天先用“对话上下文 + 最新消息”生成 standalone question（独立问题），再拿这个独立问题检索和回答。出处：<https://developers.llamaindex.ai/python/examples/chat_engine/chat_engine_condense_question/>、<https://developers.llamaindex.ai/python/examples/chat_engine/chat_engine_condense_plus_context/>

2025 的 OR-CONVQA 论文也用类似结构：生成对话内问答、生成去上下文化问题，再让不懂对话历史的检索器工作。出处：<https://arxiv.org/abs/2507.04884>

更贴 agent planning 的是 RECAP（2025）：它把整段对话重写成“用户目标的简洁表示”，专门处理 ambiguity（歧义）、intent drift（意图漂移）、mixed-goal conversations（混合目标）。出处：<https://arxiv.org/abs/2509.04472>

对我们这个例子，重写结果应该是：

- 顺的时候：`实例主人在打招呼，问大家现在在做什么。`
- 刚惹烦的时候：`实例主人在表达不满，指出团队刚才没有按“三个开场钩子”的要求执行。`

### 1.3 topic segmentation：话题分段是对的，但不能覆盖 repair intent

**Topic segmentation（话题分段）**：把对话按话题切成段，当前回答只拿当前段和必要记忆，不拿整段 30 句。

这条路是对的。2026 的 Membox / Topic Loom 明确把相邻同话题 turns 放进“memory boxes”，批评先把对话拆成孤立 utterance、再靠 embedding 检索补回连贯性的做法会损坏叙事和因果流。出处：<https://arxiv.org/abs/2601.03785>

2025 的 DASH-DTS 也把 “handshake”（开启/切换话题的交互线索，比如“在吗”“我问个别的”）作为话题切换信号。出处：<https://arxiv.org/abs/2512.15042>

但这次问题正说明：**话题分段只解决“旧话题要不要进来”，不能单独解决“同一句话到底是招呼还是不满”。**  
embedding 看 `你们在干嘛？`，顺境和愤怒时几乎一样；词表更看不出来。这个必须看上一个用户目标、上一轮团队响应、是否跑偏、用户语气和 repair 信号。

### 1.4 context engineering：窗口不是固定值，而是 Select Context

**Context engineering（上下文工程）**：动态决定给模型什么信息、以什么格式给。LangChain 2026 文档把 context 分成 runtime / cross-conversation、dynamic / static；LangGraph memory 文档明确提醒，长对话会被 stale/off-topic content（过期/跑题内容）干扰。出处：<https://docs.langchain.com/oss/python/concepts/context>、<https://docs.langchain.com/oss/python/concepts/memory>

LangChain 的 context engineering 总结成四类：write（写到窗口外）、select（按任务选择拉入）、compress（压缩）、isolate（隔离）。出处：<https://www.langchain.com/blog/context-engineering-for-agents>

我们的问题就是 `select context` 做早了、做粗了：词表一判招呼，就把 repair 所需证据删掉。

### 1.5 repair（修复）要当一等意图

**Repair（对话修复）**：人发现对话出问题后发出的纠正、追问、不满、重开。  
2026 有论文专门研究 LLM 在多轮 repair 场景里的不稳定：一旦进入多轮修复，不同模型的行为差异会变大，不能按单句处理。出处：<https://arxiv.org/abs/2604.19245>

`你们在干嘛？` 在这里不是 topic shift，也不是 greeting，而是 **user-initiated repair**：用户在提醒“刚才你们没听懂我的要求”。

## 2. 怎么破“串味 vs 读意图”的两难

社区的解法不是“给同一句话硬选一个窗口”，而是拆成两层：

### 第一层：Turn Understanding Context（回合理解上下文）

这层永远要有，且很薄。它不是用来回答旧话题，而是用来判断最新一句在当前对话里的动作。

建议固定包含：

- 最新用户句。
- 最近 2-4 条对话。
- 上一个用户明确要求。
- 上一轮员工/团队回答的短摘要。
- 当前是否有任务、是否刚输出了长分析、是否刚被打回/驳回。
- 最近是否有“跑偏/别废话/不是这个/你们在干嘛/搞什么”等 repair 候选信号。

它很短，不会造成旧话题串味；但足够让模型知道 `你们在干嘛？` 是招呼还是不满。

### 第二层：Answer Context（回答上下文）

先判 act，再决定回答时带什么：

| act | 中文含义 | 回答上下文策略 |
|---|---|---|
| `social_checkin` | 招呼/问人在不在 | 窗口 1-2，只自然回应，不翻旧任务 |
| `repair_complaint` | 用户在指出跑偏/不满 | 带上一轮用户要求 + 团队跑偏回答 + 最新句，窗口约 6-10 |
| `followup` | 追问上文 | 带当前话题段，必要时检索历史 |
| `task_request` | 派活/要产出 | 转办事底盘，带当前任务相关段 |
| `status_query` | 问进度 | 带当前活状态，不带无关聊天 |
| `ambiguous` | 判断不稳 | 短问澄清：“先生，我先确认，您是问我们在忙啥，还是刚才我们跑偏了？” |

这样两难就解了：

- 纯招呼：意图层看薄上下文判成 `social_checkin`，回答层窗口薄。
- 语境不满：意图层看见“刚跑偏”，判成 `repair_complaint`，回答层带足证据。

关键句：**窗口大小是意图判断后的产物，不是意图判断前的门。**

## 3. 落到当前代码的最小改法

### 3.1 立刻停止让 `_像招呼()` 承重

当前 `_像招呼()` 的问题不是词表不全，而是它有权直接导致窗口 1。最小改法：

- 保留强招呼词，只做 fast path（省一跳）。
- 把 `你们在干嘛` / `你在干嘛` / `在干嘛` / `干嘛呢` / `搞什么` / `什么意思` / `啥意思` 这类放进“语境敏感短句”，禁止直接窗口 1。
- 语境敏感短句必须走回合理解。

建议分表：

```python
_强招呼词 = (
    "你在吗", "在吗", "在不在", "在线吗", "有空吗", "忙不忙",
    "睡了吗", "醒了吗", "起了吗",
)

_语境敏感短句 = (
    "你们在干嘛", "你在干嘛", "在干嘛", "在干什么", "干嘛呢",
    "搞什么", "什么意思", "啥意思", "啥情况", "什么情况",
)
```

注意：`你在吗` 可以快判招呼；`你们在干嘛` 不可以。后者天然是上下文依赖句。

### 3.2 在 `聊天唤醒()` 里新增 `_理解当前回合()`，放在 `_判话题边界()` 前

新增一个轻量 judge。只在“语境敏感短句”或低置信度时调用，不是每句话都烧一次模型。

设计：

```python
from dataclasses import dataclass

@dataclass
class 回合理解:
    act: str                 # social_checkin / repair_complaint / followup / task_request / status_query / ambiguous
    context_policy: str      # thin / repair_window / current_topic / work / clarify
    window: int
    rewritten_intent: str
    confidence: float
    reason: str


def _是语境敏感短句(句: str) -> bool:
    t = (句 or "").strip().rstrip("？?。.！!~ ")
    return any(t == w or t.startswith(w) for w in _语境敏感短句)


def _抽微上下文(records: list[dict], 最新句: str) -> str:
    # 只给 judge 看，不等于回答上下文。
    # 重点保留上一个明确用户要求、上一轮员工回答、最近 2-4 条。
    近 = records[-6:]
    行 = []
    for d in 近:
        who = str(d.get("who") or "")
        text = str(d.get("text") or "").strip()
        if text:
            行.append(f"{who}: {text[:500]}")
    return "\n".join(行)
```

LLM judge prompt 可照抄这个方向：

```text
你只做“当前回合意图判断”，不要回答用户。

最新句可能表面像招呼，但真实意图由对话流决定。
尤其注意：
- “你们在干嘛/你在干嘛/搞什么/什么意思”可能是招呼，也可能是用户不满、指出跑偏；
- 如果前面用户刚提出明确要求，而团队回答偏题、过长、没有交付用户要的东西，短句更可能是 repair_complaint；
- 如果前面对话顺畅，且没有跑偏/打回/不满信号，短句更可能是 social_checkin。

输出 JSON：
{
  "act": "social_checkin | repair_complaint | followup | task_request | status_query | ambiguous",
  "rewritten_intent": "用一句中文改写用户当前真实意图",
  "confidence": 0.0-1.0,
  "reason": "引用微上下文里的证据",
  "context_policy": "thin | repair_window | current_topic | work | clarify"
}
```

调用策略：

```python
async def _理解当前回合(最新句: str, records: list[dict]) -> 回合理解:
    if _像强招呼(最新句) and not _是语境敏感短句(最新句):
        return 回合理解("social_checkin", "thin", 1, "实例主人在打招呼。", 0.95, "强招呼词")

    if _是语境敏感短句(最新句):
        # 调一次便宜 judge；失败时保守按 repair_window，不要窗口1。
        return await _LLM判回合(_抽微上下文(records, 最新句), 最新句)

    # 其他句子再交给现有 topic boundary / embedding。
    return 回合理解("unknown", "topic_boundary", 30, "", 0.5, "交给话题分段")
```

失败兜底要反过来：**不要默认当招呼窗口 1**。  
如果 judge 失败，语境敏感短句默认 `window=8`，让模型看见上文自己判断；最多多带一点上下文，不会直接失明。

### 3.3 `_判话题边界()` 降级为“话题策略”，不再处理 repair

现在 `_判话题边界()` 同时干了三件事：招呼识别、话题分段、窗口选择。建议拆：

- `_理解当前回合()`：判断 act。
- `_判话题边界()`：只在 act 不是 `repair_complaint / social_checkin / task_request` 时，用 embedding 判断新旧话题。
- `_按策略选记录()`：根据 act 选回答上下文。

`聊天唤醒()` 的结构变成：

```python
理解 = await _理解当前回合(_最新船主句, _records)

if 理解.act == "social_checkin":
    _窗口 = 1
elif 理解.act == "repair_complaint":
    _窗口 = 8  # 或用 _修复窗口(records) 精确从“上个用户要求”切到现在
elif 理解.act == "ambiguous" and 理解.confidence < 0.65:
    return "先生，我先确认一下：您是问我们在忙什么，还是刚才我们跑偏了？"
else:
    _是新话题, _依据, _窗口 = await _判话题边界(_最新船主句, _之前句)

msgs = 构造聊天消息(岗位, 本轮已说, 历史记录=_records, 窗口=_窗口)
```

更好一点，把 repair 窗口从固定 8 改成“从上一个用户明确要求到现在”：

```python
def _修复窗口(records: list[dict]) -> list[dict]:
    # 从最新句往前找上一个非短句的船主要求，再带后续所有员工回应。
    start = max(0, len(records) - 10)
    for i in range(len(records) - 2, -1, -1):
        who = str(records[i].get("who") or "")
        text = str(records[i].get("text") or "").strip()
        if who == "船主" and len(text) >= 8 and not _是语境敏感短句(text):
            start = max(0, i)
            break
    return records[start:]
```

如果不想立刻改 `构造聊天消息()` 签名，先用 `窗口=len(_修复窗口(...))` 也能止血。更干净是让 `构造聊天消息()` 支持传 `选中记录`，不要所有策略都硬塞成数字窗口。

### 3.4 给模型的回答姿态也要随 act 变

如果 `act=repair_complaint`，模型不该汇报进度。它应该先承认跑偏、复述用户原要求、给出纠偏动作。

可以在 repair 场景给一条临时系统/用户提示：

```text
当前回合判断：实例主人这句是在表达不满/指出跑偏，不是在问你们进度。
回答规矩：先承认刚才跑偏，用一句话说清你理解的原要求，然后直接回到原要求；不要汇报“我们正在做什么”。
```

这不是用 prompt 兜底意图；意图已经由 `_理解当前回合()` 判了。这里是根据 act 调整说话姿态。

### 3.5 最小验收集

必须把以下例子做成回归测试，别只测 `你在吗`：

| 场景 | 上文 | 最新句 | 期望 act | 期望上下文 |
|---|---|---|---|---|
| 纯招呼 | 前面对话顺畅或空白 | 你们在干嘛？ | `social_checkin` | 窗口 1-2 |
| 不满修复 | 用户要三个钩子，团队写长分析 | 你们在干嘛？ | `repair_complaint` | 带上原要求和跑偏回答 |
| 强招呼 | 前面刚聊 B 站 | 你在吗？ | `social_checkin` | 窗口 1 |
| 追问 | 刚给三个钩子 | 第三个再短点 | `followup` | 当前话题段 |
| 状态问询 | 有活正在执行 | 你们到哪了？ | `status_query` | 当前工作状态 |
| 低置信度 | 上文证据不足 | 你们在干嘛？ | `ambiguous` | 问一句澄清 |

## 4. 哪些成熟可直接抄，哪些还在研究阶段

### 成熟，可直接抄

1. **两阶段理解：先理解当前回合，再组装回答上下文。**  
   Rasa command generator、LlamaIndex condense question、LangChain context engineering 都是这个方向。

2. **把 intent 改写成独立描述。**  
   `rewritten_intent` 是很实用的中间产物，能给路由、日志、回放、测试用。

3. **强规则只做 fast path，不能承重。**  
   `你在吗` 这种强招呼可以省一次 judge；`你们在干嘛` 这种语境敏感句不能省。

4. **context policy 表驱动。**  
   `social_checkin -> thin`、`repair_complaint -> repair_window`、`followup -> current_topic`，这比一个 `_窗口` 数字清楚得多。

5. **话题分段 + 检索门控。**  
   embedding 漂移检测可以保留，但只解决 topic，不解决 repair。它应该服务于 `followup/current_topic`，不是覆盖所有短句。

### 可借鉴，但别当成马上要全量落地

1. **Membox / Topic Loom。**  
   方向很贴：按话题盒子存记忆。但完整架构比我们当前最小修复重。先借“当前话题段”思想。

2. **DASH-DTS 的 handshake recognition。**  
   “招呼/切话题线索”很有用，但别把它抄成新词表承重墙。它只能给候选信号。

3. **RECAP 训练专门 intent rewriter。**  
   概念很对，但自训练/微调暂时不必。先用 prompt-based judge + 结构化 JSON。

4. **attention steering / KV 级注意力控制。**  
   这是研究天花板，不适合我们现在走 API 的系统。

## 5. 给本仓库的明确建议

不要继续沿着“招呼词表 + embedding 漂移 + fail-safe”这条线硬补。它对 G-3 串味有效了一半，但现在暴露了更深的问题：**context-dependent intent 不能靠先删上下文解决。**

最小正确方案：

1. `_像招呼()` 改成 `_像强招呼()`，只处理绝对安全的在线/在不在类招呼。
2. 新增 `_语境敏感短句`，把 `你们在干嘛/你在干嘛/在干嘛/搞什么/什么意思` 全部踢出强招呼。
3. `聊天唤醒()` 里先跑 `_理解当前回合()`，它只看微上下文，输出 `act + rewritten_intent + context_policy`。
4. 根据 `context_policy` 决定 `构造聊天消息()` 的输入窗口。
5. `_判话题边界()` 保留，但只负责 topic shift，不负责 repair / dissatisfaction。
6. 所有判定写进 progress 日志，方便压测回放：`回合理解：repair_complaint，证据：上一轮未给三个钩子而给长分析，窗口=8`。

一句话版：**先用薄上下文判“这句话在这段对话里干什么”，再用判定结果决定带厚还是带薄上下文。词表只能省一跳，不能砍证据。**

## 6. 参考来源

- Rasa LLM Command Generators：对话理解输出 commands，比分类式 NLU 更适合多轮流程。<https://rasa.com/docs/reference/config/components/llm-command-generators/>
- LlamaIndex Condense Question / Condense Plus Context：先把对话 + 最新消息压成 standalone question，再检索/回答。<https://developers.llamaindex.ai/python/examples/chat_engine/chat_engine_condense_question/>、<https://developers.llamaindex.ai/python/examples/chat_engine/chat_engine_condense_plus_context/>
- RECAP: REwriting Conversations for Intent Understanding in Agentic Planning，2025。<https://arxiv.org/abs/2509.04472>
- Building Open-Retrieval Conversational QA by Decontextualizing User Questions，2025。<https://arxiv.org/abs/2507.04884>
- LLM Task Interference，2024：无关历史会造成任务切换干扰。<https://arxiv.org/abs/2402.18216>
- Membox / Topic Loom，2026：按话题连续性组织 agent 长期记忆。<https://arxiv.org/abs/2601.03785>
- DASH-DTS，2025：用 dialogue-aware similarity 和 handshake recognition 做话题分段。<https://arxiv.org/abs/2512.15042>
- Recent Trends in Linear Text Segmentation，2024 survey。<https://arxiv.org/abs/2411.16613>
- Talking to a Know-It-All GPT or a Second-Guesser Claude? Repair in Multi-Turn Behavior，2026。<https://arxiv.org/abs/2604.19245>
- LangChain Context overview / Memory overview / Context Engineering for Agents。<https://docs.langchain.com/oss/python/concepts/context>、<https://docs.langchain.com/oss/python/concepts/memory>、<https://www.langchain.com/blog/context-engineering-for-agents>
