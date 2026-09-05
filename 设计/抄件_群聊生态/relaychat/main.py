# astrbot_plugin_relaychat/main.py
# Single Plugin Instance Managing Multiple Bot Personalities/Configurations

import asyncio
import random
import time 
import uuid 
import json 
# from collections import defaultdict # 似乎未使用，可以移除

from astrbot.api.all import (
    Context, AstrBotConfig, AstrMessageEvent, logger, 
    BaseMessageComponent, MessageMember, EventMessageType, MessageType,
    PlatformMetadata
)
from astrbot.api.platform import Platform 
from astrbot.api.event import filter 
from astrbot.core.star.register import register_star as register
from astrbot.core.star import Star
from astrbot.core.platform import AstrBotMessage
from astrbot.core.star.register import register_event_message_type as event_message_type
from astrbot.core.provider.entities import ProviderRequest, LLMResponse 

_VOCECHAT_EVENT_AVAILABLE = False
_VOCECHAT_ADAPTER_CLASS = None
try:
    from ..astrbot_plugin_vocechat.vocechat_event import VoceChatEvent
    from ..astrbot_plugin_vocechat.vocechat_adapter import VoceChatAdapter 
    _VOCECHAT_EVENT_AVAILABLE = True
    _VOCECHAT_ADAPTER_CLASS = VoceChatAdapter # type: ignore
    logger.info("RelayChatPlugin: Successfully imported VoceChatEvent and VoceChatAdapter.")
except ImportError:
    logger.info("RelayChatPlugin: VoceChatEvent 或 VoceChatAdapter 未找到。模拟事件将使用通用的 AstrMessageEvent。")
    class VoceChatEvent(AstrMessageEvent): pass # type: ignore

from .utils import ( 
    DecisionModule, LLMModule, PersonaUtils, HistoryStorage, MessageUtils, ImageCaptionUtils 
)
from typing import Optional, Dict, List, Any, Union

DEFAULT_LIST_STR_TYPEHINT: List[str] = [] 
DEFAULT_MAX_INTERNAL_CHAIN_DEPTH = 1 
DEFAULT_INITIAL_REPLY_MIN_DELAY = 0.1 
DEFAULT_INITIAL_REPLY_MAX_DELAY = 0.8
DEFAULT_INTERNAL_CHAIN_MIN_DELAY = 1.0 
DEFAULT_INTERNAL_CHAIN_MAX_DELAY = 3.0 
DEFAULT_GLOBAL_CHAIN_REPLY_PROBABILITY = 0.75
DEFAULT_GLOBAL_BASE_REPLY_PROBABILITY = 0.1
DEFAULT_CONVERSATION_INCENTIVE_PROB = 0.90
DEFAULT_CONVERSATION_INCENTIVE_DURATION = 120

@register( "relaychat", "HikariFroya", "多Bot配置轮流连锁回复插件", "1.0.18" ) # 版本递增
class RelayChatPlugin(Star):

    @filter.on_llm_request(priority=1) 
    async def _apply_persona_to_llm_request_hook(self, event: AstrMessageEvent, request: ProviderRequest):
        plugin_instance_name_for_log = getattr(self, 'name', None) or getattr(self, 'id', 'RelayChatPluginHook')
        reply_config_val = event.get_extra("relay_current_reply_config_for_hook")
        if not (reply_config_val and isinstance(reply_config_val, dict)):
            logger.debug(f"{plugin_instance_name_for_log}: 钩子: 未找到 'relay_current_reply_config_for_hook'。"); return
        persona_name_to_apply = reply_config_val.get("persona_name")
        if not persona_name_to_apply:
            logger.warning(f"{plugin_instance_name_for_log}: 钩子: hook数据中缺少 'persona_name'。"); return
        
        logger.info(f"{plugin_instance_name_for_log}: 钩子: 尝试为LLM请求应用人格 '{persona_name_to_apply}'。")
        final_system_prompt = PersonaUtils.get_persona_system_prompt(self.context, persona_name_to_apply)
        final_model = PersonaUtils.get_persona_model(self.context, persona_name_to_apply) 
        
        original_req_sys_prompt_preview = request.system_prompt[:100] if request.system_prompt else '[空]'

        if final_system_prompt: 
            logger.info(f"{plugin_instance_name_for_log}: 钩子: 为人格 '{persona_name_to_apply}' 应用System Prompt (长度: {len(final_system_prompt)}). 原提示: '{original_req_sys_prompt_preview}'")
            logger.debug(f"{plugin_instance_name_for_log}: 钩子: System Prompt 内容 (开头): '{final_system_prompt[:100]}...'")
            request.system_prompt = final_system_prompt
        else: 
            logger.warning(f"{plugin_instance_name_for_log}: 钩子: 未为 '{persona_name_to_apply}' 应用System Prompt. 当前请求提示 (开头): '{original_req_sys_prompt_preview}'.")
        
        if final_model: 
            logger.info(f"{plugin_instance_name_for_log}: 钩子: 人格 '{persona_name_to_apply}' 建议模型 '{final_model}'.")
            event.set_extra("relay_model_to_use", final_model)
        else: 
            logger.debug(f"{plugin_instance_name_for_log}: 钩子: 未通过 PersonaUtils 找到人格 '{persona_name_to_apply}' 的特定模型。")
            event.set_extra("relay_model_to_use", None)
        
        logger.debug(f"{plugin_instance_name_for_log}: 钩子结束: request.system_prompt (应用后开头100字符) = '{request.system_prompt[:100] if request.system_prompt else '[空]'}'")

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config; self.context = context 
        logger.info(f"========== RelayChatPlugin 单例已创建 (对象 ID: {id(self)}) ==========")
        logger.info(f"RelayChatPlugin __init__: 收到全局插件配置: {self.config}")
        if hasattr(HistoryStorage, 'init'): HistoryStorage.init(self.config) # 传递插件配置给HistoryStorage
        if hasattr(ImageCaptionUtils, 'init'): ImageCaptionUtils.init(self.context, self.config)
        self.max_chain_depth: int = int(self.config.get("max_chain_depth", DEFAULT_MAX_INTERNAL_CHAIN_DEPTH))
        self.initial_min_delay: float = float(self.config.get("initial_reply_min_delay_seconds", DEFAULT_INITIAL_REPLY_MIN_DELAY))
        self.initial_max_delay: float = float(self.config.get("initial_reply_max_delay_seconds", DEFAULT_INITIAL_REPLY_MAX_DELAY))
        logger.debug(f"RelayChatPlugin __init__: 初始回复延迟范围: {self.initial_min_delay}s - {self.initial_max_delay}s")
        self.chain_min_delay: float = float(self.config.get("chain_reply_min_delay_seconds", DEFAULT_INTERNAL_CHAIN_MIN_DELAY))
        self.chain_max_delay: float = float(self.config.get("chain_reply_max_delay_seconds", DEFAULT_INTERNAL_CHAIN_MAX_DELAY))
        logger.debug(f"RelayChatPlugin __init__: 连锁回复延迟范围: {self.chain_min_delay}s - {self.chain_max_delay}s")
        self.default_global_chain_reply_probability = float(self.config.get("default_global_chain_reply_probability", DEFAULT_GLOBAL_CHAIN_REPLY_PROBABILITY))
        self.default_global_base_reply_probability = float(self.config.get("default_global_base_reply_probability", DEFAULT_GLOBAL_BASE_REPLY_PROBABILITY))
        self.default_global_keywords: List[str] = self.config.get("default_global_keywords", DEFAULT_LIST_STR_TYPEHINT)
        self.default_global_blacklist: List[str] = self.config.get("default_global_blacklist", DEFAULT_LIST_STR_TYPEHINT)
        self.managed_bot_configs: Dict[str, Dict[str, Any]] = {} 
        managed_bots_str_list = self.config.get("managed_bots", [])
        if not isinstance(managed_bots_str_list, list): managed_bots_str_list = []
        for i, bot_config_str in enumerate(managed_bots_str_list):
            if not isinstance(bot_config_str, str) or not bot_config_str.strip(): logger.warning(f"RelayChatPlugin: managed_bots 条目 #{i} 为空或非字符串: '{bot_config_str}'"); continue
            parts = bot_config_str.split("::")
            if len(parts) < 7: logger.warning(f"RelayChatPlugin: managed_bots 条目 #{i} 格式不正确 (应至少7段,实为{len(parts)}段): '{bot_config_str}'"); continue
            try:
                platform_id, persona, bot_uid = parts[0].strip(), parts[1].strip(), parts[2].strip()
                kw_str = parts[3].strip(); kws = json.loads(kw_str) if kw_str else self.default_global_keywords
                if not isinstance(kws, list): kws = self.default_global_keywords
                base_p_str = parts[4].strip(); base_p = float(base_p_str) if base_p_str else self.default_global_base_reply_probability
                chain_p_str = parts[5].strip(); chain_p = float(chain_p_str) if chain_p_str else self.default_global_chain_reply_probability
                bl_str = parts[6].strip(); bl_kws = json.loads(bl_str) if bl_str else self.default_global_blacklist
                if not isinstance(bl_kws, list): bl_kws = self.default_global_blacklist
                if platform_id and persona and bot_uid:
                    self.managed_bot_configs[platform_id] = {"platform_instance_id": platform_id, "persona_name": persona, "vocechat_bot_uid": bot_uid, "keywords": kws, "base_reply_probability": base_p, "chain_reply_probability": chain_p, "blacklist_keywords": bl_kws }
                    logger.info(f"RelayChatPlugin: 已解析平台 '{platform_id}' 的Bot配置: 人格='{persona}', UID='{bot_uid}'.")
                else: logger.warning(f"RelayChatPlugin: managed_bots 条目 #{i} 缺少关键的平台ID/人格名/BotUID: '{bot_config_str}'")
            except (IndexError, ValueError, json.JSONDecodeError) as e: logger.warning(f"RelayChatPlugin: 解析 managed_bots 条目 #{i} '{bot_config_str}' 时出错: {e}")
        if not self.managed_bot_configs: logger.error("RelayChatPlugin: 未从 'managed_bots' 配置中解析出任何有效的Bot配置。")
        self.decision_module = DecisionModule(self.config, self.managed_bot_configs) 
        self.llm_module = LLMModule(self.context, self.config) 
        self.active_chain_tasks: Dict[str, asyncio.Task] = {}; self.chain_scheduler_lock = asyncio.Lock()
        logger.info(f"RelayChatPlugin (单例) 初始化完成。共解析 {len(self.managed_bot_configs)} 个Bot配置。")

    async def __aexit__(self, exc_type, exc_val, exc_tb): 
        logger.info(f"RelayChatPlugin (单例): 正在关闭，清理连锁任务...")
        async with self.chain_scheduler_lock: 
            for task_key in list(self.active_chain_tasks.keys()): # Iterate over keys to allow popping
                task = self.active_chain_tasks.pop(task_key, None)
                if task and not task.done(): 
                    task.cancel()
                    logger.debug(f"RelayChatPlugin (单例): 已取消关闭前的连锁任务 (TaskKey: {task_key})。")
        logger.info(f"RelayChatPlugin (单例): 关闭完成。"); await super().__aexit__(exc_type, exc_val, exc_tb)

    def _get_event_platform_id(self, event: AstrMessageEvent) -> str:
        return str(event.platform_meta.id) if event.platform_meta and hasattr(event.platform_meta, 'id') and event.platform_meta.id else "未知平台"

    def _get_chain_info_from_event(self, event: AstrMessageEvent) -> tuple[bool, int, Optional[str], Optional[str], Optional[str]]:
        raw_msg_data = getattr(event.message_obj, 'raw_message', {}); is_dict_raw = isinstance(raw_msg_data, dict)
        is_chain = bool(event.get_extra("relay_is_chain") if event.get_extra("relay_is_chain") is not None else (raw_msg_data.get("__relay_is_chain__", False) if is_dict_raw else False))
        depth_val = event.get_extra("relay_chain_depth"); depth_from_raw = raw_msg_data.get("__relay_depth__") if is_dict_raw else None
        depth_intermediate: Any = 0 
        if depth_val is not None: depth_intermediate = depth_val
        elif depth_from_raw is not None: depth_intermediate = depth_from_raw
        try: final_depth = int(depth_intermediate)
        except (ValueError, TypeError): logger.warning(f"RelayChatPlugin: 转换深度 '{depth_intermediate}' 为整数失败，默认为0."); final_depth = 0
        last_persona = event.get_extra("relay_last_replier_persona") or (raw_msg_data.get("__relay_last_replier_persona__") if is_dict_raw else None)
        orig_mid_val = event.get_extra("relay_original_user_message_id") or (raw_msg_data.get("__relay_original_user_mid__") if is_dict_raw else None)
        orig_mid = str(orig_mid_val) if orig_mid_val is not None else None
        orig_sender_id_val = event.get_extra("relay_original_user_sender_id") or (raw_msg_data.get("__relay_original_user_sender_id__") if is_dict_raw else None)
        orig_sender_id = str(orig_sender_id_val) if orig_sender_id_val is not None else None
        return is_chain, final_depth, last_persona, orig_mid, orig_sender_id

    async def _common_message_handler(self, event: AstrMessageEvent, message_type_str: str):
        event_platform_id = self._get_event_platform_id(event)
        _is_chain_event, _chain_depth, _last_replier_persona, _orig_mid, _orig_sender_id = self._get_chain_info_from_event(event)
        bot_specific_config = self.managed_bot_configs.get(event_platform_id)
        if not _is_chain_event: # 仅对非连锁的初始事件检查LLM锁
            llm_lock_key = LLMModule.get_llm_lock_key(event)
            if LLMModule.is_llm_in_progress_sync(llm_lock_key): logger.debug(f"RelayChatPlugin ({event_platform_id}): LLM锁 '{llm_lock_key}' 已被占用。忽略此初始事件。"); return
        if not _is_chain_event and not bot_specific_config: return # 初始事件但此平台未配置Bot
        if not bot_specific_config and _is_chain_event: logger.debug(f"RelayChatPlugin ({event_platform_id}): 收到连锁事件，但此平台未配置Bot。放弃处理。"); return
        elif not bot_specific_config: return # 其他未配置情况
        if _orig_mid: event.set_extra("relay_original_user_message_id", _orig_mid)
        if _orig_sender_id: event.set_extra("relay_original_user_sender_id", _orig_sender_id)
        serving_persona_for_log = bot_specific_config['persona_name']
        log_prefix = f"RelayChatPlugin (平台: {event_platform_id}, 服务人格: {serving_persona_for_log})"
        logger.debug(f"{log_prefix}: 收到 {message_type_str}。连锁:{_is_chain_event}, 深度:{_chain_depth}, 上个回复者:{_last_replier_persona or '无'}, 原始消息ID:{_orig_mid or '无'}")
        if not _is_chain_event and self.initial_min_delay < self.initial_max_delay and (self.initial_max_delay > 0) : # 确保延迟有意义
            await asyncio.sleep(round(random.uniform(self.initial_min_delay, self.initial_max_delay), 2))
        async for result in self._handle_event(event, bot_specific_config, _is_chain_event, _chain_depth, _last_replier_persona): yield result

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent): 
        async for res in self._common_message_handler(event, "群聊消息"): yield res
    @event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent): 
        async for res in self._common_message_handler(event, "私聊消息"): yield res

    async def _handle_event(self, event: AstrMessageEvent, bot_specific_config: Dict[str, Any], is_chain_event: bool, chain_depth: int, last_replier_persona: Optional[str]):
        current_persona_name = bot_specific_config["persona_name"]
        log_prefix = f"RelayChatPlugin (平台: {bot_specific_config['platform_instance_id']}, 处理人格: {current_persona_name})"
        if not is_chain_event:
            await HistoryStorage.process_and_save_user_message(event)
            original_user_mid_str = event.get_extra("relay_original_user_message_id") 
            if not original_user_mid_str: 
                original_user_mid_str = str(event.message_obj.message_id if event.message_obj and hasattr(event.message_obj, 'message_id') else uuid.uuid4())
                event.set_extra("relay_original_user_message_id", original_user_mid_str)
            if not event.get_extra("relay_original_user_sender_id"): event.set_extra("relay_original_user_sender_id", event.get_sender_id())
            async with self.chain_scheduler_lock:
                task_keys_to_remove_or_cancel = [k for k in self.active_chain_tasks if k.startswith(str(original_user_mid_str))]
                for task_key in task_keys_to_remove_or_cancel:
                    task = self.active_chain_tasks.pop(task_key, None)
                    if task and not task.done(): task.cancel(); logger.info(f"{log_prefix}: 新用户消息 (OrigMID {original_user_mid_str})，已取消之前的连锁任务 (TaskKey: {task_key})。")
        
        should_plugin_reply = self.decision_module.should_reply(event, bot_specific_config, is_chain_event)
        if event.get_message_type() == MessageType.FRIEND_MESSAGE and not is_chain_event and not should_plugin_reply:
            logger.info(f"{log_prefix}: 本插件决定不回复此私聊消息 (连锁:{is_chain_event})。停止事件传播。")
            event.stop_event(); return
        
        logger.info(f"{log_prefix}: 处理消息. 连锁:{is_chain_event}, 深度:{chain_depth}, 上个回复者:{last_replier_persona or '无'}")
        am_i_qualified = (not is_chain_event) or (current_persona_name != last_replier_persona)
        if not am_i_qualified: logger.debug(f"{log_prefix}: 不符合回复条件 (原因: 连锁且与上个回复者人格相同)。"); return
        
        if should_plugin_reply: 
            logger.info(f"{log_prefix}: 决策模块: 是，将回复。")
            
            hook_config_for_extra = {"persona_name": current_persona_name, "bot_specific_config": bot_specific_config }
            event.set_extra("relay_current_reply_config_for_hook", hook_config_for_extra)
            event.set_extra("relay_is_chain", is_chain_event); event.set_extra("relay_chain_depth", chain_depth)
            event.set_extra("relay_triggering_event_platform_meta_id", bot_specific_config['platform_instance_id'])
            event.set_extra("relay_triggering_event_platform_meta_name", event.platform_meta.name if event.platform_meta else "unknown_platform_name")
            event.set_extra("relay_triggering_event_session_id", event.get_session_id())
            event.set_extra("relay_triggering_event_message_type", event.get_message_type())
            if last_replier_persona: event.set_extra("relay_last_replier_persona", last_replier_persona)

            self.decision_module.activate_reply_incentive(event) 
            
            async for llm_obj in self.llm_module.prepare_and_yield_request(event): yield llm_obj
        else: logger.debug(f"{log_prefix}: 决策模块: 否，不回复。")
        
    @filter.after_message_sent(priority=10) 
    async def _after_my_reply_sent(self, event: AstrMessageEvent):
        hook_config_val = event.get_extra("relay_current_reply_config_for_hook"); 
        if not (hook_config_val and isinstance(hook_config_val, dict)): logger.debug("RelayChatPlugin AfterSent: 未找到hook配置。跳过连锁。"); return
        bot_specific_config_from_hook = hook_config_val.get("bot_specific_config")
        if not (bot_specific_config_from_hook and isinstance(bot_specific_config_from_hook, dict)): logger.warning("RelayChatPlugin AfterSent: hook数据中bot_specific_config无效。跳过连锁。"); return
        event_platform_id = self._get_event_platform_id(event) 
        if bot_specific_config_from_hook.get("platform_instance_id") != event_platform_id: return
        replied_bot_config = bot_specific_config_from_hook; replied_persona_name = replied_bot_config.get("persona_name")
        replied_bot_physical_id_that_just_spoke = replied_bot_config.get("vocechat_bot_uid")
        log_prefix = f"RelayChatPlugin ({event_platform_id}, 已回复人格: {replied_persona_name}) AfterSent"
        depth_val = event.get_extra("relay_chain_depth"); current_reply_depth = int(depth_val) if depth_val is not None else 0
        actual_reply_chain: Optional[List[BaseMessageComponent]] = None
        if hasattr(event, '_result') and event._result : # _result 可能是 LLMResponse 或 AstrBotMessage
            if isinstance(event._result, LLMResponse) and hasattr(event._result, 'result_chain') and event._result.result_chain: actual_reply_chain = event._result.result_chain.chain
            elif hasattr(event._result, 'chain') and isinstance(event._result.chain, list): actual_reply_chain = event._result.chain # 适用于非LLM的直接回复
        
        # 如果 _result 不是对象，尝试从 event.message_obj 获取（如果 event 本身就是 Bot 的 "sent" 记录）
        if not actual_reply_chain and isinstance(event.message_obj, AstrBotMessage) and hasattr(event.message_obj, 'message') and event.message_obj.message:
            actual_reply_chain = event.message_obj.message

        if actual_reply_chain and len(actual_reply_chain) > 0:
            try: await HistoryStorage.process_and_save_bot_reply(event, actual_reply_chain, str(replied_bot_physical_id_that_just_spoke), str(replied_persona_name))
            except Exception as e_h: logger.error(f"{log_prefix}: 保存Bot回复历史出错: {e_h}", exc_info=True)
            logger.info(f"{log_prefix}: 回复已发送 (原始事件深度: {current_reply_depth})。")
            
            original_trigger_message_type = event.get_extra("relay_triggering_event_message_type")
            if original_trigger_message_type != MessageType.GROUP_MESSAGE:
                logger.info(f"{log_prefix}: 原始触发事件类型为 {original_trigger_message_type}，非群聊消息。跳过连锁调度。")
                _cleanup_relay_extras(event); return
            
            if current_reply_depth < self.max_chain_depth:
                original_user_mid_val = event.get_extra("relay_original_user_message_id"); original_user_sender_id_val = event.get_extra("relay_original_user_sender_id")
                original_session_id = event.get_extra("relay_triggering_event_session_id") 
                if not (original_user_mid_val and original_session_id): logger.warning(f"{log_prefix}: 缺少原始消息ID或会话ID，无法进行连锁。"); _cleanup_relay_extras(event); return
                original_user_mid_str = str(original_user_mid_val); next_chain_depth = current_reply_depth + 1; triggered_chain_count = 0
                for target_platform_id_str, target_bot_config_entry in self.managed_bot_configs.items():
                    target_persona_name = target_bot_config_entry.get("persona_name")
                    if target_persona_name == replied_persona_name: logger.debug(f"{log_prefix}: 跳过向自身人格 '{replied_persona_name}' (目标平台 '{target_platform_id_str}') 的连锁。"); continue
                    target_platform_type_name = event.get_extra("relay_triggering_event_platform_meta_name") or "vocechat"
                    target_bot_physical_id_for_target_event = target_bot_config_entry.get("vocechat_bot_uid")
                    if not target_bot_physical_id_for_target_event: logger.warning(f"{log_prefix}: 目标Bot '{target_persona_name}' (平台:'{target_platform_id_str}') 缺少 'vocechat_bot_uid'。跳过连锁。"); continue
                    sim_event_platform_meta_for_target = PlatformMetadata( name=target_platform_type_name, id=target_platform_id_str, description=f"模拟连锁事件 for {target_platform_id_str}" )
                    logger.info(f"{log_prefix}: 安排群聊连锁: 从人格'{replied_persona_name}'(UID:{replied_bot_physical_id_that_just_spoke}) "
                                f"到目标平台'{target_platform_id_str}'(目标人格'{target_persona_name}', 目标UID:{target_bot_physical_id_for_target_event}), "
                                f"目标深度:{next_chain_depth}, 原始消息ID:{original_user_mid_str}, 会话ID:{original_session_id}.")
                    async with self.chain_scheduler_lock:
                        chain_task_key = f"{original_user_mid_str}_to_{target_platform_id_str}_depth{next_chain_depth}"
                        if chain_task_key not in self.active_chain_tasks or self.active_chain_tasks[chain_task_key].done():
                            self.active_chain_tasks[chain_task_key] = asyncio.create_task(
                                self._schedule_internal_chain_trigger(
                                    target_platform_meta = sim_event_platform_meta_for_target, target_session_id = original_session_id,
                                    target_message_type = MessageType.GROUP_MESSAGE, replied_persona_name = str(replied_persona_name), 
                                    replied_bot_physical_id = str(replied_bot_physical_id_that_just_spoke), 
                                    target_bot_physical_id_for_self_id = str(target_bot_physical_id_for_target_event), 
                                    replied_message_components = actual_reply_chain, chain_depth_for_next_event = next_chain_depth, 
                                    original_user_message_id = original_user_mid_str,
                                    original_user_sender_id = str(original_user_sender_id_val) if original_user_sender_id_val else "未知原始发送者" ))
                            triggered_chain_count += 1
                if triggered_chain_count == 0 and len(self.managed_bot_configs) > 1 : logger.info(f"{log_prefix}: 没有其他符合条件的Bot进行群聊连锁。")
                elif triggered_chain_count > 0: logger.info(f"{log_prefix}: 已安排 {triggered_chain_count} 个群聊连锁事件。")
            else: 
                logger.info(f"{log_prefix}: 已达到最大连锁深度 ({self.max_chain_depth}) (当前回复深度: {current_reply_depth})。")
                original_user_mid_val = event.get_extra("relay_original_user_message_id")
                if original_user_mid_val: 
                    async with self.chain_scheduler_lock: 
                        keys_to_pop = [k for k,v in self.active_chain_tasks.items() if k.startswith(str(original_user_mid_val)) and v.done()]
                        for k_pop in keys_to_pop: self.active_chain_tasks.pop(k_pop, None) # 清理已完成的任务
        else: logger.debug(f"{log_prefix}: 无实际回复内容。跳过历史保存和连锁。")
        _cleanup_relay_extras(event)

    async def _schedule_internal_chain_trigger(self, 
                                             target_platform_meta: PlatformMetadata, target_session_id: str, target_message_type: MessageType,
                                             replied_persona_name: str, replied_bot_physical_id: str, 
                                             target_bot_physical_id_for_self_id: str, 
                                             replied_message_components: List[BaseMessageComponent], 
                                             chain_depth_for_next_event: int, original_user_message_id: str, 
                                             original_user_sender_id: str):
        session_key_for_log = f"{target_platform_meta.id or '未知'}@{target_session_id or '未知'}"
        if self.chain_min_delay < self.chain_max_delay and self.chain_max_delay > 0: # 确保延迟有意义
            await asyncio.sleep(round(random.uniform(self.chain_min_delay, self.chain_max_delay), 2))
            
        log_prefix = f"RelayChatPlugin 连锁 ({session_key_for_log}, 来自人格: {replied_persona_name}, 来自BotUID: {replied_bot_physical_id}, 目标平台: {target_platform_meta.id})"
        logger.info(f"{log_prefix}: 安排连锁事件, 类型: {target_message_type}, 目标深度: {chain_depth_for_next_event}.")
        simulated_event: Optional[AstrMessageEvent] = None
        try:
            new_msg_obj = AstrBotMessage(); new_msg_obj.message = replied_message_components
            new_msg_obj.message_str = await MessageUtils.outline_message_list(replied_message_components)
            new_msg_obj.sender = MessageMember(user_id=replied_bot_physical_id, nickname=f"Bot_{replied_persona_name}")
            new_msg_obj.self_id = target_bot_physical_id_for_self_id ; new_msg_obj.type = target_message_type
            if target_message_type == MessageType.GROUP_MESSAGE: new_msg_obj.group_id = target_session_id; new_msg_obj.session_id = target_session_id 
            elif target_message_type == MessageType.FRIEND_MESSAGE: new_msg_obj.group_id = None; new_msg_obj.session_id = target_session_id 
            else: new_msg_obj.group_id = None; new_msg_obj.session_id = target_session_id # 其他类型也用 session_id
            new_msg_obj.timestamp = int(time.time()); new_msg_obj.message_id = str(uuid.uuid4()); 
            new_msg_obj.raw_message = { "trigger_type": "relay_chat_chain", "__relay_is_chain__": True, "__relay_depth__": chain_depth_for_next_event, "__relay_last_replier_persona__": replied_persona_name, "__relay_last_replier_physical_id__": replied_bot_physical_id, "__relay_original_user_mid__": original_user_message_id, "__relay_original_user_sender_id__": original_user_sender_id }
            
            if _VOCECHAT_EVENT_AVAILABLE and target_platform_meta.name == "vocechat":
                target_adapter_instance: Optional[Platform] = None # 明确类型
                if hasattr(self.context, 'platform_manager') and self.context.platform_manager:
                    if hasattr(self.context.platform_manager, 'get_platform_by_id'): 
                        target_adapter_instance = self.context.platform_manager.get_platform_by_id(str(target_platform_meta.id)) # id 可能是数字或字符串
                    elif hasattr(self.context.platform_manager, 'get_insts'):
                        for inst_plat in self.context.platform_manager.get_insts(): # type: ignore
                            if hasattr(inst_plat, 'metadata') and inst_plat.metadata and str(inst_plat.metadata.id) == str(target_platform_meta.id): 
                                target_adapter_instance = inst_plat; break # type: ignore
                if target_adapter_instance and isinstance(target_adapter_instance, _VOCECHAT_ADAPTER_CLASS): # type: ignore
                    try: simulated_event = VoceChatEvent(message_obj=new_msg_obj, platform_meta=target_platform_meta, adapter_instance=target_adapter_instance) # type: ignore
                    except Exception as e_vce: logger.error(f"{log_prefix}: 创建VoceChatEvent失败: {e_vce}, 回退到通用事件."); simulated_event = None
            if not simulated_event: simulated_event = AstrMessageEvent(new_msg_obj.message_str, new_msg_obj, target_platform_meta, new_msg_obj.session_id)
            
            if simulated_event:
                simulated_event.set_extra("relay_is_chain", True); simulated_event.set_extra("relay_chain_depth", chain_depth_for_next_event) 
                simulated_event.set_extra("relay_last_replier_persona", replied_persona_name); simulated_event.set_extra("relay_last_replier_physical_id", replied_bot_physical_id)
                simulated_event.set_extra("relay_original_user_message_id", original_user_message_id); simulated_event.set_extra("relay_original_user_sender_id", original_user_sender_id)
                logger.info(f"{log_prefix}: 最终模拟事件 (类型: {type(simulated_event).__name__}) 已准备好，深度 {chain_depth_for_next_event}.")
        except Exception as e: logger.error(f"{log_prefix}: 创建模拟事件对象时出错: {e}", exc_info=True); return
        
        if simulated_event:
            event_queue = self.context.get_event_queue(); 
            if event_queue: await event_queue.put(simulated_event); logger.info(f"{log_prefix}: 模拟事件已提交到事件队列 (目标平台: {target_platform_meta.id}).")
            else: logger.error(f"{log_prefix}: 无法获取事件队列。")
        
        chain_task_key = f"{original_user_message_id}_to_{target_platform_meta.id}_depth{chain_depth_for_next_event}"
        async with self.chain_scheduler_lock: self.active_chain_tasks.pop(chain_task_key, None) # 任务完成后从字典中移除

def _cleanup_relay_extras(event: AstrMessageEvent): 
    # 清理所有本次交互中设置的relay_* extra
    keys_to_remove = [k for k in event._extras if k.startswith("relay_")]
    for k in keys_to_remove:
        event.set_extra(k, None)
    # logger.debug(f"RelayChatPlugin: Cleaned up relay extras for event.")
