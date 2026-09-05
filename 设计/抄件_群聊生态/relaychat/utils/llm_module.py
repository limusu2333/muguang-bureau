# astrbot_plugin_relaychat/utils/llm_module.py
import asyncio
import uuid
import threading
from typing import Dict, Any, Optional, AsyncGenerator, List, Union

from astrbot.api.all import Context, AstrBotConfig, AstrMessageEvent, logger
from astrbot.core.provider.entities import ProviderRequest, LLMResponse
from astrbot.api.message_components import Image, Plain 
from .history_storage import HistoryStorage
from .message_utils import MessageUtils

class LLMModule:
    _llm_in_progress_status: Dict[tuple, bool] = {} 
    _llm_status_async_lock = asyncio.Lock()
    _llm_status_sync_read_lock = threading.Lock()

    LLM_LOCK_RELEASE_DELAY_SECONDS_DEFAULT = 1.0
    LLM_PROMPT_MAX_HISTORY_DEFAULT = 20

    def __init__(self, context: Context, plugin_config: AstrBotConfig):
        self.context = context
        self.plugin_config = plugin_config 
        self.release_delay = float(self.plugin_config.get("llm_lock_release_delay_seconds", self.LLM_LOCK_RELEASE_DELAY_SECONDS_DEFAULT))
        self.max_history_count_default = int(self.plugin_config.get("llm_max_history_default", self.LLM_PROMPT_MAX_HISTORY_DEFAULT))
        logger.debug(f"LLMModule initialized. LockReleaseDelay={self.release_delay}s, MaxHistoryDefault={self.max_history_count_default}")

    @staticmethod
    def get_llm_lock_key(event: AstrMessageEvent) -> tuple:
        platform_name = event.get_platform_name() or "unknown_platform"; 
        chat_id_for_lock = event.get_session_id()
        if not chat_id_for_lock: 
            is_private = event.is_private_chat()
            chat_id_for_lock = event.get_group_id() if not is_private and event.get_group_id() else event.get_sender_id()
        if not chat_id_for_lock: 
            event_mid = getattr(event.message_obj, 'message_id', None) if event.message_obj else None
            chat_id_for_lock = str(event_mid) if event_mid else f"fallback_lock_{str(uuid.uuid4())[:8]}"
            logger.warning(f"LLMModule: No stable chat_id for LLM lock. Using: {chat_id_for_lock}")
        return (platform_name.lower(), str(chat_id_for_lock))
    
    @staticmethod 
    def is_llm_in_progress_sync(lock_key: tuple) -> bool:
        with LLMModule._llm_status_sync_read_lock:
            return LLMModule._llm_in_progress_status.get(lock_key, False)

    @staticmethod
    async def set_llm_in_progress_async(lock_key: tuple, status: bool):
        async with LLMModule._llm_status_async_lock:
            if status: LLMModule._llm_in_progress_status[lock_key] = True; logger.debug(f"LLMModule: Async: LLM lock ACQUIRED for key {lock_key}")
            elif lock_key in LLMModule._llm_in_progress_status: del LLMModule._llm_in_progress_status[lock_key]; logger.debug(f"LLMModule: Async: LLM lock RELEASED for key {lock_key}")
        
    async def prepare_and_yield_request(self, event: AstrMessageEvent) -> AsyncGenerator[Union[ProviderRequest, LLMResponse], None]:
        lock_key = LLMModule.get_llm_lock_key(event)
        if LLMModule.is_llm_in_progress_sync(lock_key): 
            logger.warning(f"LLMModule ({lock_key}): LLM lock held. Aborting."); yield LLMResponse(role="err", completion_text="LLM_LOCKED_ON_ENTRY"); return
        await LLMModule.set_llm_in_progress_async(lock_key, True) 
        
        current_reply_config_for_hook: Optional[Dict[str, Any]] = None; persona_name_to_apply: Optional[str] = None
        try:
            current_reply_config_for_hook = event.get_extra("relay_current_reply_config_for_hook") # type: ignore
            if not (current_reply_config_for_hook and isinstance(current_reply_config_for_hook, dict)): logger.error(f"LLMModule ({lock_key}): Missing/invalid 'relay_current_reply_config_for_hook'."); yield LLMResponse(role="err", completion_text="MISSING_HOOK_DATA"); return
            bot_specific_config = current_reply_config_for_hook.get("bot_specific_config")
            if not (bot_specific_config and isinstance(bot_specific_config, dict)): logger.error(f"LLMModule ({lock_key}): Missing/invalid 'bot_specific_config'."); yield LLMResponse(role="err", completion_text="MISSING_BOT_SPECIFIC_CONFIG"); return
            persona_name_to_apply = bot_specific_config.get("persona_name")
            if not persona_name_to_apply: logger.error(f"LLMModule ({lock_key}): Missing 'persona_name'."); yield LLMResponse(role="err", completion_text="MISSING_PERSONA_NAME"); return
            
            logger.debug(f"LLMModule ({lock_key}): Preparing LLM request for P:'{persona_name_to_apply}'.")
            prompt_parts = []; current_event_image_data: List[str] = []; history_event_image_data: List[str] = []; text_parts_for_current_message_prompt: List[str] = []
            max_hist = int(bot_specific_config.get("llm_max_history", self.max_history_count_default))
            
            history_dicts = await HistoryStorage.get_history_as_dicts(event) # ★★★ 使用新的方法 ★★★
            deduped_history_dicts = MessageUtils.dedup_history(history_dicts) if history_dicts else [] # 去重

            formatted_history_text, history_event_image_data = await MessageUtils.format_history_for_llm(deduped_history_dicts, max_messages=max_hist)
            
            if formatted_history_text: prompt_parts.append(f"这是之前的聊天记录：\n{formatted_history_text}")
            if history_event_image_data: logger.debug(f"LLMModule: 从历史记录中获取了 {len(history_event_image_data)} 张图片。")

            current_message_components = event.get_messages()
            if current_message_components:
                for component in current_message_components:
                    if isinstance(component, Image):
                        if component.file and component.file.startswith("base64://"): current_event_image_data.append(component.file); text_parts_for_current_message_prompt.append("[图片]"); logger.debug(f"LLMModule: Added current image (base64): {component.file[:100]}...")
                        elif component.url: current_event_image_data.append(component.url); text_parts_for_current_message_prompt.append("[图片URL]"); logger.debug(f"LLMModule: Added current image URL: {component.url[:100]}...")
                    elif hasattr(component, 'text') and isinstance(component.text, str): text_parts_for_current_message_prompt.append(component.text)
            
            current_msg_text_for_prompt = " ".join(text_parts_for_current_message_prompt).strip()
            if not current_msg_text_for_prompt and current_event_image_data : current_msg_text_for_prompt = "[用户发送了一张或多张图片]"
            elif not current_msg_text_for_prompt: current_msg_text_for_prompt = event.get_message_str()
            prompt_parts.append(f"这是当前收到的消息内容 (来自发送者ID: {event.get_sender_id()}):\n{current_msg_text_for_prompt}")
            final_prompt_str = "\n\n".join(filter(None, prompt_parts)).strip()

            all_image_data_for_llm = history_event_image_data + current_event_image_data
            if len(all_image_data_for_llm) > MessageUtils.MAX_HISTORY_IMAGES_TO_LLM + 1: # +1 for current image
                logger.warning(f"LLMModule: 图片总数 ({len(all_image_data_for_llm)}) 可能过多，只取最后 {MessageUtils.MAX_HISTORY_IMAGES_TO_LLM +1 } 张。")
                all_image_data_for_llm = all_image_data_for_llm[-(MessageUtils.MAX_HISTORY_IMAGES_TO_LLM + 1):]

            if not final_prompt_str and not all_image_data_for_llm:
                logger.warning(f"LLMModule ({lock_key}): 最终提示词和图片均为空 (P:'{persona_name_to_apply}'). 中止。"); yield LLMResponse(role="err", completion_text="EMPTY_INPUT"); return
            session_id_for_req = event.get_session_id() or str(uuid.uuid4())
            provider_request = ProviderRequest(
                prompt=final_prompt_str, session_id=session_id_for_req,
                image_urls=all_image_data_for_llm if all_image_data_for_llm else None, 
                contexts=[], system_prompt="", conversation=getattr(event, 'conversation', None) )
            logger.debug(f"LLMModule ({lock_key}): 发起ProviderRequest (P:'{persona_name_to_apply}'). Prompt长度:{len(final_prompt_str)} 内容(开头):'{final_prompt_str[:100]}...'. 图片数量: {len(all_image_data_for_llm)}")
            yield provider_request
        except Exception as e:
            p_name_err = persona_name_to_apply or (current_reply_config_for_hook.get("bot_specific_config", {}).get("persona_name") if current_reply_config_for_hook else "UnknownP")
            logger.error(f"LLMModule ({lock_key}): prepare_and_yield_request 出错 (P:'{p_name_err}'): {e}", exc_info=True)
            yield LLMResponse(role="err", completion_text=f"LLM_MODULE_PREPARE_ERROR: {type(e).__name__}", error_message=str(e))
        finally:
            logger.debug(f"LLMModule ({lock_key}): {self.release_delay}秒后释放LLM锁。")
            await asyncio.sleep(self.release_delay); await LLMModule.set_llm_in_progress_async(lock_key, False)

