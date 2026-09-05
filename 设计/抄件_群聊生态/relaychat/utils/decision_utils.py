# astrbot_plugin_relaychat/utils/decision_utils.py

import random
import time
from typing import Dict, Any, List, Optional # List 未在此文件中直接使用

from astrbot.api.all import AstrMessageEvent, AstrBotConfig, logger, MessageType
from .llm_module import LLMModule 

class DecisionModule:
    _active_conversation_sessions: Dict[str, float] = {} 
    CONVERSATION_INCENTIVE_PROBABILITY_DEFAULT = 0.90 
    CONVERSATION_INCENTIVE_DURATION_SECONDS_DEFAULT = 120

    def __init__(self, plugin_config: AstrBotConfig, managed_bot_configs_ref: Dict[str, Any]):
        self.plugin_config = plugin_config
        self.managed_bot_configs = managed_bot_configs_ref
        self.incentive_prob = float(plugin_config.get(
            "conversation_incentive_probability", 
            self.CONVERSATION_INCENTIVE_PROBABILITY_DEFAULT
        ))
        self.incentive_duration = int(plugin_config.get(
            "conversation_incentive_duration_seconds", 
            self.CONVERSATION_INCENTIVE_DURATION_SECONDS_DEFAULT
        ))
        logger.debug(f"DecisionModule initialized. IncentiveProb={self.incentive_prob}, IncentiveDuration={self.incentive_duration}s")

    def _get_session_key_for_incentive(self, event: AstrMessageEvent, bot_specific_config: Dict[str, Any]) -> str:
        platform_instance_id = bot_specific_config.get("platform_instance_id", "unknown_platform_instance")
        session_id = event.get_session_id() or "unknown_session"
        return f"incentive_{platform_instance_id}@{session_id}"

    def activate_reply_incentive(self, event: AstrMessageEvent):
        # ★★★ 修正了 event.get_extra 的调用方式 ★★★
        hook_config_from_extra = event.get_extra("relay_current_reply_config_for_hook") 
        
        if not hook_config_from_extra or not isinstance(hook_config_from_extra, dict):
            logger.warning("DecisionModule (activate_incentive): 'relay_current_reply_config_for_hook' not found or invalid in event.extra.")
            return        
        
        bot_specific_config_to_use = hook_config_from_extra.get("bot_specific_config")
        if not bot_specific_config_to_use or not isinstance(bot_specific_config_to_use, dict): 
            logger.warning("DecisionModule (activate_incentive): Missing 'bot_specific_config' within 'relay_current_reply_config_for_hook'. Cannot activate incentive.")
            return

        current_platform_id_for_log = bot_specific_config_to_use.get('platform_instance_id', 'UnknownPlat')
        session_key = self._get_session_key_for_incentive(event, bot_specific_config_to_use)

        if self.incentive_duration > 0:
            DecisionModule._active_conversation_sessions[session_key] = time.time() + self.incentive_duration
            logger.debug(f"DecisionModule (Incentive Activation @ {current_platform_id_for_log}): "
                         f"Timer activated/refreshed for session '{session_key}'.")
        else:
            logger.debug(f"DecisionModule (Incentive Activation @ {current_platform_id_for_log}): "
                         f"Incentive duration is <= 0, incentive not activated for session '{session_key}'.")

    def _is_conversation_incentive_active(self, event: AstrMessageEvent, bot_specific_config: Dict[str, Any]) -> bool:
        if self.incentive_duration <= 0: return False
        
        current_platform_id_for_log = bot_specific_config.get('platform_instance_id', 'UnknownPlat')
        session_key = self._get_session_key_for_incentive(event, bot_specific_config)
        expiry_time = DecisionModule._active_conversation_sessions.get(session_key)

        if expiry_time and time.time() < expiry_time:
            logger.debug(f"DecisionModule (Incentive Check @ {current_platform_id_for_log}): Active for '{session_key}'.")
            return True
        elif expiry_time: 
            logger.debug(f"DecisionModule (Incentive Check @ {current_platform_id_for_log}): EXPIRED for '{session_key}'. Removing.")
            DecisionModule._active_conversation_sessions.pop(session_key, None)
        # else:
            # logger.debug(f"DecisionModule (Incentive Check @ {current_platform_id_for_log}): No active incentive for '{session_key}'.")
        return False

    def should_reply(self, event: AstrMessageEvent, bot_specific_config: Dict[str, Any], is_chain_event: bool) -> bool:
        log_prefix_base = f"DecisionModule ({bot_specific_config.get('platform_instance_id', 'UnkPlat')}, " \
                          f"P: {bot_specific_config.get('persona_name', 'UnkPers')}, Chain: {is_chain_event})"

        message_content = event.get_message_str().lower()
        sender_id = event.get_sender_id()
        
        # 1. 黑名单关键词检查
        if not is_chain_event: # 只对非连锁事件检查黑名单 (假设连锁事件的内容是可信的)
            is_from_our_managed_bot = False
            for _, conf in self.managed_bot_configs.items():
                if conf.get("vocechat_bot_uid") == sender_id:
                    is_from_our_managed_bot = True; break
            
            if not is_from_our_managed_bot: # 如果消息不是来自我们管理的另一个Bot
                blacklist = bot_specific_config.get("blacklist_keywords", [])
                if any(keyword.lower() in message_content for keyword in blacklist if keyword.strip()):
                    logger.info(f"{log_prefix_base}: Message from {sender_id} (not our bot) matched blacklist. Not replying.")
                    return False

        # 2. 私聊消息强制回复 (仅对用户发起的首次回复，且未被黑名单阻止)
        if event.get_message_type() == MessageType.FRIEND_MESSAGE and not is_chain_event :
            logger.info(f"{log_prefix_base}: Private message from user. Forcing reply (passed blacklist check).")
            return True

        # 3. 连锁回复逻辑
        if is_chain_event:
            chain_prob = bot_specific_config.get("chain_reply_probability", 0.0)
            if random.random() < chain_prob:
                logger.info(f"{log_prefix_base}: Triggering CHAIN reply based on Chain probability ({chain_prob:.2f}).")
                return True
            else:
                logger.debug(f"{log_prefix_base}: NOT triggering CHAIN reply (Prob: {chain_prob:.2f}).")
                return False

        # --- 以下仅对非连锁的群聊事件 (因为私聊在上面已经return True了) ---
        # 4. 关键词触发逻辑
        keywords = bot_specific_config.get("keywords", [])
        if any(keyword.lower() in message_content for keyword in keywords if keyword.strip()):
            logger.info(f"{log_prefix_base}: Message from {sender_id} matched keyword. Triggering.")
            return True
            
        # 5. 对话激励逻辑
        if self._is_conversation_incentive_active(event, bot_specific_config):
            if random.random() < self.incentive_prob:
                logger.info(f"{log_prefix_base}: Triggering reply (Incentive Prob: {self.incentive_prob:.2f}).")
                return True
            else:
                logger.debug(f"{log_prefix_base}: NOT triggering reply (Incentive Prob: {self.incentive_prob:.2f}).")
        
        # 6. 基础概率回复逻辑
        base_prob = bot_specific_config.get("base_reply_probability", 0.0)
        if random.random() < base_prob:
            logger.info(f"{log_prefix_base}: Triggering reply (Base Prob: {base_prob:.2f}).")
            return True
        else:
            logger.debug(f"{log_prefix_base}: NOT triggering reply (Base Prob: {base_prob:.2f}).")
            return False
