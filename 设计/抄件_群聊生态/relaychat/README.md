# RelayChatPlugin for AstrBot

**版本:** 1.0.17
**作者:** HikariFroya
**描述:** 一款为 AstrBot 设计的插件，允许多个虚拟Bot人格在同一个聊天会话（目前主要支持群聊）中进行轮流的连锁回复，营造更生动和沉浸的群聊对话体验。

---

## 🚀 效果预览 (模拟群聊场景)

可以用一个astrbot配置多个bot模拟真正的群聊环境。
一个配置了 **BotA**、**BotB** 和 **BotC** 三个Bot人格的群聊中：

**用户A:** “今天天气真好啊，大家有什么计划吗？”

**(等待一小会儿，BotA实例被触发...)**

**BotA:** “的确是个适合训练的好天气呢！我正准备去跑道进行一些速度训练，为下一场比赛做准备！💪”

**(RelayChatPlugin 检测到BotA回复，并安排BotB进行连锁...)**

**BotB:** “哦豁！BotA还是这么有活力啊！我嘛... 嘿嘿，正盘算着怎么在BotA的训练路径上设置一点小小的‘惊喜’呢！绝对会很有趣！🎉”

**(RelayChatPlugin 检测到BotB回复，并安排BotC进行连锁，假设连锁深度允许...)**

**BotC:** “（推了推眼镜）BotB同学，你的‘惊喜’最好不要妨碍到重要的实验数据收集。我对这种不可预测变量对训练效果的影响很感兴趣，但请控制在合理范围内。顺便一提，今天的大气湿度和风速非常适合进行空气动力学相关的测试。”

**(连锁回复可能在此结束，或根据配置继续...)**

**这就是 RelayChatPlugin 的魅力所在！它能让你的Bot们：**
*   **不再是孤立的回复者：** 它们会根据其他Bot的发言进行“接话”。
*   **展现不同个性：** 每个Bot都使用自己的人格进行回复。
*   **模拟真实对话：** 通过可配置的延迟和概率，让对话更自然。
*   **联机游戏：** 兼容多个用户与多个BOT在一个群聊中进行互动





## ✨ 功能特性

*   **多Bot人格管理**: 支持配置多个独立的Bot人格，每个Bot可以有自己的：
    *   服务平台实例 (例如，绑定到不同的VoceChat Bot账号)
    *   专属人格 (通过PersonaUtils调用AstrBot全局人格)
    *   专属物理Bot UID (用于识别消息来源和发送通道)
    *   独立的触发关键词
    *   独立的回复概率 (基础回复概率、连锁回复概率)
    *   独立的黑名单关键词
*   **智能连锁回复**:
    *   当一个被管理的Bot回复后，插件会自动安排其他配置的Bot（不同人格）进行连锁回复的尝试 (主要在群聊中)。
    *   连锁回复会考虑上一个回复者的身份，避免同一人格连续回复。
    *   支持设置最大连锁深度，防止无限对话。
    *   连锁回复之间有可配置的随机延迟，模拟真实对话节奏。
*   **灵活的回复决策**:
    *   **关键词触发**: Bot可以根据消息中是否包含特定关键词来决定是否回复。
    *   **概率触发**:
        *   **基础回复概率**: 在没有匹配到关键词时，Bot仍有一定概率主动回复用户消息。
        *   **连锁回复概率**: 在连锁对话中，Bot根据此概率决定是否接续上一个Bot的回复。
    *   **对话激励**: 在Bot主动回复或成功连锁回复后的一小段时间内，提高其对后续消息的回复意愿。
    *   **私聊强制回复**: 在私聊场景下，Bot会更主动地回复用户的首次消息（除非被黑名单等阻止）。
*   **历史消息上下文**:
    *   Bot在生成回复时，会参考最近的聊天历史记录。
    *   支持将会话中的图片（最近N张）作为上下文信息传递给支持图片理解的LLM。git init
    *   通过AstrBot的全局人格管理系统，为每个Bot人格应用专属的System Prompt。
    *   支持人格配置中可能存在的模型偏好。
*   **平台适配**:
    *   **目前主要在 VoceChat 平台上进行了开发和测试，并依赖特定的 `astrbot_plugin_vocechat` 平台适配器插件来正确处理事件和发送消息（特别是模拟连锁事件的创建）。**
    *   虽然设计上尝试保持一定的通用性，但在其他平台上的功能和兼容性未经充分验证。
*   **健壮的错误处理和日志**: 详细的日志输出，方便调试和追踪问题。
*   **配置灵活**:
    *   通过插件的 `_conf_schema.json` 和 AstrBot WebUI 进行配置。
    *   支持全局默认配置和每个Bot实例的独立配置。

---

## ⚙️ 安装与配置

### 1. 前置依赖

*   **本插件目前高度依赖 `astrbot_plugin_vocechat` 平台适配器插件。请确保你已经正确安装并配置了我们提供的 VoceChat 平台适配器。**
    *   我自己目前是搭建在vocechat上，因为已经被QQ封号到狂躁了。vocechat的搭建非常简单，有兴趣可以去试一下。
    *   如果你愿意尝试一下在其他平台的效果，可以把尝试的结果告诉我，我来试一试能否适配。

### 2. 安装本插件

*   将 `astrbot_plugin_relaychat` 文件夹放置到 AstrBot 的 `data/plugins/` 目录下。
*   重启 AstrBot。

### 3. 配置插件 (`relaychat_config.json` 或 WebUI)

插件的主要配置项通过 AstrBot 的插件配置界面进行管理，或者直接编辑生成的 `data/config/relaychat_config.json` 文件。

以下是主要的配置项说明：

*   **`managed_bots`**: (核心配置) 这是一个字符串列表，用于定义每个由本插件管理的Bot实例。每个字符串的格式如下：
    ```
    PlatformInstanceID::PersonaName::VoceChatBotUID::KeywordsJSON::BaseReplyProb::ChainReplyProb::BlacklistKeywordsJSON
    ```
    *   **`PlatformInstanceID`**: (字符串) 此Bot人格绑定的AstrBot平台实例ID。这个ID必须与你在AstrBot“平台设置”中为该VoceChat连接设置的“机器人名称(id)”完全一致。例如: "McqueenVoce", "GoldshipVoce"。
    *   **`PersonaName`**: (字符串) 此Bot人格在AstrBot全局“人格管理”中定义的人格名称。例如: "mcqueen", "goldship"。插件将通过此名称获取对应的System Prompt和可能的模型配置。
    *   **`VoceChatBotUID`**: (字符串/数字) 此Bot人格在VoceChat中的实际Bot用户ID。用于识别Bot自身发送的消息，以及作为模拟连锁事件的发送者物理ID。
    *   **`KeywordsJSON`**: (JSON字符串列表) 触发此Bot回复的关键词列表。例如: `["麦昆", "你好"]` (注意是JSON字符串格式)。如果为空 `[]`，则会使用下面的 `default_global_keywords`。
    *   **`BaseReplyProb`**: (浮点数, 0.0到1.0) 基础回复概率。在非连锁、未匹配关键词时，以此概率决定是否回复。如果为空，则使用 `default_global_base_reply_probability`。
    *   **`ChainReplyProb`**: (浮点数, 0.0到1.0) 连锁回复概率。在连锁对话中，以此概率决定是否接续回复。如果为空，则使用 `default_global_chain_reply_probability`。
    *   **`BlacklistKeywordsJSON`**: (JSON字符串列表) 黑名单关键词列表。如果用户消息包含这些词，此Bot将不会回复。例如: `["推广", "广告"]`。如果为空 `[]`，则使用 `default_global_blacklist`。

    **示例 `managed_bots` 条目 (假设你的VoceChat平台实例ID为 "VcBotMcqueen", "VcBotGoldship"):**
    ```json
    [
      "VcBotMcqueen::mcqueen::7::[\"麦昆\",\"miki\"]::0.6::0.9::[]",
      "VcBotGoldship::goldship::9::[\"阿船\",\"golsi\"]::0.5::0.85::[\"禁止\"]"
    ]
    ```

*   **`max_chain_depth`**: (整数, 默认: `1`) 最大连锁深度。值为1表示：用户A -> Bot1(回复) -> Bot2(连锁回复)。之后连锁结束。值为0表示不允许连锁。
*   **`initial_reply_min_delay_seconds`**: (浮点数, 默认: `0.1`) Bot首次回复前的最小随机延迟（秒）。
*   **`initial_reply_max_delay_seconds`**: (浮点数, 默认: `0.8`) Bot首次回复前的最大随机延迟（秒）。
*   **`chain_reply_min_delay_seconds`**: (浮点数, 默认: `1.0`) Bot连锁回复前的最小随机延迟（秒）。
*   **`chain_reply_max_delay_seconds`**: (浮点数, 默认: `3.0`) Bot连锁回复前的最大随机延迟（秒）。
*   **`default_global_base_reply_probability`**: (浮点数, 默认: `0.1`) 如果 `managed_bots` 中没有指定 `BaseReplyProb`，则使用此全局默认值。
*   **`default_global_chain_reply_probability`**: (浮点数, 默认: `0.75`) 如果 `managed_bots` 中没有指定 `ChainReplyProb`，则使用此全局默认值。
*   **`default_global_keywords`**: (列表, 默认: `[]`) 如果 `managed_bots` 中没有指定 `KeywordsJSON`，则使用此全局默认关键词列表。
*   **`default_global_blacklist`**: (列表, 默认: `[]`) 如果 `managed_bots` 中没有指定 `BlacklistKeywordsJSON`，则使用此全局默认黑名单列表。
*   **`conversation_incentive_probability`**: (浮点数, 默认: `0.90`) 对话激励概率。
*   **`conversation_incentive_duration_seconds`**: (整数, 默认: `120`) 对话激励的持续时间（秒）。
*   **`history_storage_directory_name`**: (字符串, 默认: `"relaychat_history"`) 存储聊天历史的子目录名称（位于 `data/` 目录下）。
*   **`llm_max_history_default`**: (整数, 默认: `20`) LLM请求时默认截取的最大历史消息条数。
*   **`llm_lock_release_delay_seconds`**: (浮点数, 默认: `1.0`) LLM锁在请求完成后延迟释放的时间（秒）。

### 4. 全局人格配置

确保你在 AstrBot 的“人格管理”界面为 `managed_bots` 中配置的每个 `PersonaName` 都创建了对应的人格条目，并正确填写了它们的 **System Prompt**。

### 5. VoceChat 平台适配器配置

*   在 AstrBot 的“平台设置”中，为每个你想通过 `RelayChatPlugin` 管理的VoceChat Bot账号创建一个平台连接实例（使用 `astrbot_plugin_vocechat` 适配器）。
*   **重点：为每个平台连接实例设置的“机器人名称(id)”必须与你在 `RelayChatPlugin` 的 `managed_bots` 配置中使用的 `PlatformInstanceID` 完全一致。**
*   确保每个VoceChat Bot实例的API Key和Bot User ID (`default_bot_self_uid`) 在其各自的平台适配器配置中是正确的。

---

## 🚀 使用方法

1.  正确完成安装和配置。
2.  启动 AstrBot。
3.  **私聊场景 (VoceChat)**:
    *   当用户与 `managed_bots` 中配置的任一Bot人格绑定的VoceChat平台实例进行私聊时，该Bot会根据其配置（黑名单、本插件的私聊强制回复逻辑）决定是否回复。
    *   回复会使用对应的人格。
    *   **私聊中不会触发连锁回复。**
4.  **群聊场景 (VoceChat)**:
    *   当群聊中的消息满足 `managed_bots` 中某个Bot实例的触发条件（关键词或概率）时，该Bot会回复。
    *   回复后，如果未达到最大连锁深度，`RelayChatPlugin` 会为配置的其他Bot人格（不同于当前回复者的人格）安排连锁事件。
    *   被安排连锁的Bot会根据其 `chain_reply_probability` 决定是否接续回复。
    *   LLM在生成回复时会参考该群聊的最近历史记录（包括最近的图片内容）。

---

## 🛠️ 内部逻辑与模块

*   **`main.py` (`RelayChatPlugin`)**: 插件主类，处理事件的接收、分发给内部模块、以及连锁调度。
*   **`utils/`**: 包含各个功能模块的辅助工具类。
    *   **`decision_utils.py` (`DecisionModule`)**: 负责判断Bot是否应该回复。
    *   **`llm_module.py` (`LLMModule`)**: 负责准备LLM请求、管理LLM调用锁。
    *   **`persona_utils.py` (`PersonaUtils`)**: 负责从AstrBot全局人格库中获取人格信息。
    *   **`history_storage.py` (`HistoryStorage`)**: 负责聊天历史的本地存储和读取。
    *   **`message_utils.py` (`MessageUtils`)**: 提供消息格式化、文本提取等工具。
    *   **`image_caption.py` (`ImageCaptionUtils`)**: (目前未深度集成，但保留了骨架) 用于图片转文本描述的工具（如果将来需要）。

---

## 📄 未来可能的发展方向 (可选)

*   支持更多平台的连锁回复。
*   更精细化的连锁触发条件（例如，基于情感分析、特定话题等）。
*   允许Bot在回复时“引用”或“转发”用户的原始图片。
*   通过WebUI更方便地管理每个Bot的独立配置，而不是依赖复杂的字符串格式。

---

## 🐛 问题反馈与贡献

如果您在使用过程中遇到任何问题，或者有任何改进建议，欢迎通过 [你的GitHub Issues链接或其他联系方式] 提出。

也欢迎对本项目进行 Fork 和 Pull Request！

---