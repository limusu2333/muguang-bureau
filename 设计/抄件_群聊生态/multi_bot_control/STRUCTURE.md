# 插件结构说明

## 文件结构

```text
astrbot_plugin_multi_bot_control/
├─ main.py                 # 插件主逻辑
├─ _conf_schema.json       # AstrBot WebUI 插件配置 schema
├─ metadata.yaml           # 插件元信息与版本号
├─ README.md               # 用户说明文档
├─ DEVELOP.md              # 版本开发记录
├─ STRUCTURE.md            # 内部结构说明
├─ tests/
│  └─ test_core.py         # 核心逻辑单元测试
├─ logo.png                # 插件图标
└─ LICENSE
```

## 运行数据

插件数据目录由 AstrBot 的 `get_astrbot_plugin_data_path()` 决定，实际存放在：

```text
data/plugin_data/astrbot_plugin_multi_bot_control/
├─ group_bots.json              # 本群 /mbot 命令维护的不受控机器人名单
└─ controlled_priorities.json   # 受控机器人稳定优先级
```

## main.py 顶层常量

- `PLUGIN_NAME`：插件注册名。
- `PLUGIN_VERSION`：当前版本，需与 `metadata.yaml` 保持一致。
- `LOCAL_BOTS_FILE`：本群本地名单文件名。
- `PRIORITY_FILE`：受控机器人优先级文件名。
- `MAX_GROUP_WINDOWS`：最多保留的群窗口状态数量。
- `MAX_SESSION_STATES`：最多保留的单对机器人会话状态数量。
- `MAX_RECENT_MESSAGES`：每个会话保留的重复消息 hash 数量。

## 数据模型

### BotEntry

机器人名单条目。

- `qq`：机器人 QQ 号，唯一身份依据。
- `name`：显示昵称，仅用于文案和提示词。
- `call_name`：对该机器人的称呼，仅用于提示词。
- `kind`：`controlled` 或 `uncontrolled`。
- `source`：`global`、`group` 或 `runtime`。
- `allow_interaction`：是否允许与该机器人交互。
- `identity`：内部身份键，格式为 `qq:<qq>`。
- `display_name`：优先使用 `name`、`call_name`、`qq`。
- `effective_call_name`：提示词里的称呼 fallback。
- `to_json()`：持久化本群名单时使用。

### BotSessionState

单对机器人会话状态。

- `turns`：当前连续回复轮次。
- `cooldown_until`：单对机器人冷却截止时间。
- `last_accepted_at`：最近一次放行时间。
- `recent_messages`：最近消息 hash，固定长度 deque。

## 内部工具函数

- `_normalize_id(value)`：把 ID 转为去空格字符串，兼容 `123.0`。
- `_id_candidates(*values)`：从多个字段提取去重后的 ID 候选。
- `_candidate_has_match(candidates, expected)`：候选 ID 与目标 ID 精确匹配。
- `_extract_from_mapping(data, path)`：兼容 dict/object 的嵌套字段读取。
- `_stable_message_hash(text)`：生成稳定消息 hash，用于重复消息限制。
- `_stable_priority(identity)`：为受控机器人生成稳定优先级。
- `_component_type_name(component)`：提取消息组件类型名。

## MultiBotControlPlugin 方法分组

### 生命周期

- `__init__(context, config)`：保存配置、初始化数据路径、运行态缓存和状态表。
- `initialize()`：创建数据目录并补齐受控机器人优先级。
- `terminate()`：保存本群名单和优先级缓存。

### 配置读取

- `_enabled()`：读取插件开关。
- `_section(name)`：读取配置分组。
- `_limit_int(key, default, minimum)`：读取 `limits` 内整数。
- `_conf_bool(section, key, default)`：读取布尔配置。
- `_conf_int(section, key, default, minimum)`：读取任意分组整数配置。

### 持久化

- `_resolve_data_dir()`：获取 AstrBot 插件数据目录。
- `_read_json(path, default)`：读取 JSON，失败时返回默认值。
- `_write_json(path, data)`：原子写 JSON。
- `_load_group_bots_data()` / `_save_group_bots_data()`：本群名单缓存读写。
- `_load_priorities()` / `_save_priorities()`：优先级缓存读写。

### 名单处理

- `_normalize_bot_entry(raw, kind, source)`：兼容字符串和对象配置，生成 `BotEntry`。
- `_global_entries()`：读取 WebUI 全局名单。
- `_local_entries(group_id)`：读取当前群本地名单。
- `_effective_entries(group_id)`：合并全局与本群名单；全局受控机器人优先。
- `_entry_by_qq(entries)`：构造 QQ 到 `BotEntry` 的索引。
- `_match_entry(candidates, entries)`：用候选 QQ 号匹配名单。
- `_ensure_controlled_priorities()`：为新增受控机器人生成优先级。
- `_priority(entry)`：读取单个受控机器人的优先级。

### 身份提取

- `_platform_supports_qq_identity(event)`：判断事件平台是否能按 QQ 号识别。`qq_official` 返回 `False`。
- `_raw_value(event, *paths)`：读取原始平台事件字段。
- `_sender_id_candidates(event)`：提取消息发送者 QQ 候选。
- `_self_id_candidates(event)`：提取当前机器人 QQ 候选。
- `_group_key(event)`：生成运行态群键，格式为 `platform_id:group_id`。
- `_local_group_id(event)`：获取用于本群名单的群 ID。
- `_find_peer_bot(event, entries)`：识别消息来源机器人。
- `_self_entry(event, entries)`：识别当前受控机器人。

### 目标识别

- `_at_targets(event)`：提取 @ 目标 QQ，包括 OneBot 原始 payload fallback。
- `_has_at_all(event)`：判断是否 @全体。
- `_reply_sender_ids(event)`：提取引用回复来源 QQ。
- `_message_targets_entry(event, entry)`：判断消息是否明确指向某个机器人。
- `_wake_prefix_only_targets_self(event, self_entry)`：按配置把唤醒前缀视为指向当前机器人。
- `_event_was_woken(event)`：判断 AstrBot 原始唤醒流程是否已经唤醒该事件。插件不会把未唤醒事件强制唤醒。
- `_targeted_controlled_entries(event, entries)`：列出被明确指向的受控机器人。
- `_self_rank_delay(self_entry, targeted_controlled)`：多受控机器人排队延迟。

### 限流状态

- `_session_key(event, peer, self_entry)`：生成单对机器人会话键，包含平台、群、当前机器人和对端机器人。
- `_state_for(key)`：获取或创建会话状态，并限制最大状态数。
- `_record_human_activity(group_key)`：人类发言后重置当前群无人类统计。
- `_prune_times(values, now, window)`：剪掉窗口外时间戳。
- `_group_times(store, group_key)`：获取群级时间 deque，并限制最大群数。
- `_deny_with_cooldown(state, now)`：设置单对机器人冷却。
- `_deny_without_human_with_cooldown(group_key, now)`：设置当前群无人类冷却。
- `_can_accept_bot_request(event, peer, state, now)`：执行所有限制判断。
- `_record_bot_request(event, state, now)`：记录一次被放行的机器人请求。

### 提示词注入

- `_self_platform_nickname(event, self_entry)`：获取当前机器人在群内显示名。
- `_bot_prompt(event, peer, self_entry)`：渲染机器人交流提示词。
- `_set_bot_context_extra(event, peer, self_entry, session_key)`：把对端机器人、会话 key、提示词写入 event extra。
- `_pending_bot_reply(event, peer, self_entry, session_key, message_hash)`：前置路由通过后写入挂起回复标记，不提交计数。
- `_commit_bot_reply(session_key, group_key, message_hash, now)`：成功发送后提交轮次、群窗口、无人类连续对话和重复消息统计。
- `commit_bot_reply_after_sent(event)`：`after_message_sent` 钩子。只有平台发送阶段确认发生过发送操作后才提交挂起回复统计。
- `inject_bot_prompt(event, req)`：`on_llm_request` 钩子，把提示词作为临时 user content 或 system prompt 注入。
- `_is_unapproved_bot_llm_request(event)`：LLM 阶段兜底拦截未被前置路由放行的机器人消息。

### 主事件路由

- `route_group_messages(event)`：群消息前置路由。

执行顺序：

1. 检查插件开关与群 ID。
2. 合并当前群有效名单。
3. 确认当前 AstrBot 实例 QQ 命中全局受控机器人名单。
4. 忽略当前机器人自己发出的消息。
5. 不支持 QQ 号身份的平台直接跳过。
6. 识别消息来源是否为名单内机器人；不是则记录人类活动。
7. 如果消息来源是名单内机器人，但 AstrBot 原始唤醒规则没有唤醒当前事件，则直接返回，不注入提示词、不写挂起标记、不改唤醒标志。
8. 识别消息是否指向当前受控机器人或其他受控机器人。
9. 执行轮次、冷却、窗口、无人类、重复消息限制。
10. 放行时注入提示词、写入挂起回复标记，但不修改 `event.is_wake` / `event.is_at_or_wake_command`。
11. 按基础延迟和多受控机器人优先级延迟调用后续 LLM。
12. 发送阶段完成后由 `after_message_sent` 钩子提交统计；如果没有实际成功发送回复，挂起标记不会转化为轮次。

### `/mbot` 命令

- `mbot()`：命令组入口。
- `mbot_help(event)`：帮助。
- `mbot_slash_fallback(event)`：兼容 `/mbot ...` 形式。
- `mbot_list(event)`：列出当前群本地名单。
- `mbot_add(event, qq, name, call_name)`：群主添加当前群本地不受控机器人。
- `mbot_remove(event, key)`：群主删除当前群本地机器人。
- `mbot_clear(event)`：群主清空当前群本地名单。
- `_upsert_local_entry(group_id, entry)`：插入或更新本地名单。
- `_remove_local_entry(group_id, key)`：删除本地名单。
- `_dispatch_slash_mbot(event)`：解析 slash fallback。
- `_mbot_help_text()`：命令帮助文本。
- `_ensure_group_owner(event)`：校验 QQ 群主。
- `_group_owner_from_event(event)`：从事件对象读群主。
- `_raw_sender_is_owner(event)`：从原始事件读 sender role。

## WebUI 配置映射

- `enabled`：插件总开关。
- `bot_registry.controlled_bots`：全局受控机器人名单。
- `bot_registry.uncontrolled_bots`：全局不受控机器人名单。
- `targeting.require_explicit_target`：是否要求机器人消息明确指向当前机器人。
- `targeting.enable_reply_target`：引用回复是否算目标。
- `targeting.treat_at_all_as_target`：@全体是否算目标。
- `targeting.treat_wake_prefix_as_target`：唤醒前缀是否算目标。
- `limits.*`：轮次、冷却、窗口、无人类、重复消息限制。
- `prompting.bot_prompt_template`：机器人来源消息提示词模板。
- `prompting.reply_style_prompt`：机器人回复风格提示。
- `multi_controlled.*`：多受控机器人同时被指向时的排队策略。

## 测试结构

`tests/test_core.py` 使用 stub 模拟 AstrBot 依赖，只测试插件自身逻辑。测试重点是 bug 修复路径，不启动真实 AstrBot。
