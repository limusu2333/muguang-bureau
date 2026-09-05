# astrbot_plugin_relaychat/utils/history_storage.py
import os
import json # 使用标准json
import logging 
from typing import List, Optional, Dict, Any, Tuple # Tuple for return type
from astrbot.api.all import AstrMessageEvent, AstrBotMessage, MessageMember, MessageType, BaseMessageComponent, AstrBotConfig
from astrbot.api.message_components import Image, Plain 
from datetime import datetime
import uuid
import traceback 

logger = logging.getLogger(__name__)

class HistoryStorage:
    config: Optional[AstrBotConfig] = None # 用于存储插件配置
    base_storage_path: Optional[str] = None
    MAX_HISTORY_ENTRIES = 200 

    @staticmethod
    def init(plugin_config: AstrBotConfig): 
        HistoryStorage.config = plugin_config
        storage_dir_name = plugin_config.get("history_storage_directory_name", "relaychat_history")
        # 使用 astrbot.core.utils.astrbot_path 来获取更标准的data路径 (如果可用)
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            HistoryStorage.base_storage_path = os.path.join(get_astrbot_data_path(), storage_dir_name)
        except ImportError:
            logger.warning("HistoryStorage: astrbot_path utility not found, using os.getcwd() for data path.")
            HistoryStorage.base_storage_path = os.path.join(os.getcwd(), "data", storage_dir_name)
            
        HistoryStorage._ensure_dir(HistoryStorage.base_storage_path)
        logger.info(f"RelayChat HistoryStorage: 存储路径已初始化为 {HistoryStorage.base_storage_path}")

    @staticmethod
    def _ensure_dir(directory: str):
        if not os.path.exists(directory):
            try: os.makedirs(directory, exist_ok=True)
            except Exception as e: logger.error(f"RelayChat HistoryStorage: 创建目录失败 {directory}: {e}")

    @staticmethod
    def _get_file_path_for_chat(event: AstrMessageEvent) -> Optional[str]:
        if not HistoryStorage.base_storage_path and HistoryStorage.config: 
            HistoryStorage.init(HistoryStorage.config) 
        elif not HistoryStorage.base_storage_path:
             logger.error("RelayChat HistoryStorage: 未初始化 (config is None). 无法获取历史文件路径。")
             return None

        platform_name = event.get_platform_name() or "unknown_platform"
        chat_type_dir = ""
        chat_id = ""

        if event.get_message_type() == MessageType.GROUP_MESSAGE:
            chat_type_dir = "group"
            chat_id = event.get_group_id()
        elif event.get_message_type() == MessageType.FRIEND_MESSAGE:
            chat_type_dir = "private"
            # 对于私聊，session_id 通常是对方的 user_id
            chat_id = event.get_session_id() if event.get_session_id() != event.get_self_id() else event.get_sender_id()
            if not chat_id: chat_id = event.get_sender_id() # 备选

        else:
            logger.warning(f"RelayChat HistoryStorage: 不支持的消息类型用于历史记录: {event.get_message_type()}")
            return None

        if not chat_id:
            logger.warning(f"RelayChat HistoryStorage: 无法确定聊天ID (平台: {platform_name}, 类型: {event.get_message_type()}, sender: {event.get_sender_id()}, session: {event.get_session_id()}).")
            return None
            
        directory = os.path.join(str(HistoryStorage.base_storage_path), platform_name, chat_type_dir) # 确保 path 是 str
        HistoryStorage._ensure_dir(directory)
        return os.path.join(directory, f"{str(chat_id)}.json")

    @staticmethod
    async def _extract_relevant_info_for_history(components: Optional[List[BaseMessageComponent]]) -> Tuple[str, Optional[str]]:
        from .message_utils import MessageUtils 
        
        text_summary = ""
        if components:
            text_summary = await MessageUtils.outline_message_list(components, for_history=True)
        
        image_base64_uri = None        
        if components:
            for comp in components:
                if isinstance(comp, Image) and comp.file and comp.file.startswith("base64://"):
                    image_base64_uri = comp.file 
                    break 
        return text_summary, image_base64_uri

    @staticmethod
    async def process_and_save_user_message(event: AstrMessageEvent):
        file_path = HistoryStorage._get_file_path_for_chat(event)
        if not file_path: return
        history: List[Dict[str, Any]] = await HistoryStorage.get_history_as_dicts(event)

        text_summary, image_uri = await HistoryStorage._extract_relevant_info_for_history(event.get_messages())
        
        msg_obj = event.message_obj
        sender_name = "未知用户"
        sender_id_val = "未知ID"
        if msg_obj and msg_obj.sender:
            sender_name = msg_obj.sender.nickname or f"User_{msg_obj.sender.user_id}"
            sender_id_val = msg_obj.sender.user_id
        elif hasattr(event, 'get_sender_name') and hasattr(event, 'get_sender_id'): # Fallback to event methods
            sender_name = event.get_sender_name() or f"User_{event.get_sender_id()}"
            sender_id_val = event.get_sender_id()

        message_id_val = str(uuid.uuid4())
        if msg_obj and hasattr(msg_obj, 'message_id') and msg_obj.message_id:
            message_id_val = str(msg_obj.message_id)


        history_entry = {
            "role": "user", "name": sender_name, "user_id": sender_id_val,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": text_summary, "message_id": message_id_val,
        }
        if image_uri: history_entry["image_base64_uri"] = image_uri
        
        history.append(history_entry)
        if len(history) > HistoryStorage.MAX_HISTORY_ENTRIES:
            history = history[-HistoryStorage.MAX_HISTORY_ENTRIES:]
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            logger.debug(f"RelayChat HistoryStorage: 用户消息已保存到 '{file_path}' (事件MID: {message_id_val}).")
        except Exception as e:
            logger.error(f"RelayChat HistoryStorage: 保存用户消息到 '{file_path}' 失败: {e}", exc_info=True)
            
    @staticmethod
    async def process_and_save_bot_reply(event: AstrMessageEvent, bot_reply_chain: List[BaseMessageComponent], bot_physical_id: str, bot_persona_name: str):
        file_path = HistoryStorage._get_file_path_for_chat(event)
        if not file_path: return
        history: List[Dict[str, Any]] = await HistoryStorage.get_history_as_dicts(event)

        text_summary, image_uri = await HistoryStorage._extract_relevant_info_for_history(bot_reply_chain)

        # 从 event 中获取 message_id 作为关联，或者生成新的
        triggering_message_id = str(uuid.uuid4())
        if event.message_obj and hasattr(event.message_obj, 'message_id') and event.message_obj.message_id:
            triggering_message_id = str(event.message_obj.message_id)


        history_entry = {
            "role": "assistant", "name": f"Bot_{bot_persona_name}", "user_id": bot_physical_id,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": text_summary, "message_id": triggering_message_id + "_bot_reply",
        }
        if image_uri: history_entry["image_base64_uri"] = image_uri
            
        history.append(history_entry)
        if len(history) > HistoryStorage.MAX_HISTORY_ENTRIES:
            history = history[-HistoryStorage.MAX_HISTORY_ENTRIES:]
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            logger.debug(f"RelayChat HistoryStorage: Bot回复已保存到 '{file_path}'.")
        except Exception as e:
            logger.error(f"RelayChat HistoryStorage: 保存Bot回复到 '{file_path}' 失败: {e}", exc_info=True)

    @staticmethod
    async def get_history_as_dicts(event: AstrMessageEvent) -> List[Dict[str, Any]]:
        file_path = HistoryStorage._get_file_path_for_chat(event)
        if not file_path or not os.path.exists(file_path): return []
        try:
            with open(file_path, "r", encoding="utf-8") as f: 
                file_content = f.read()
                if not file_content.strip(): # 如果文件为空
                    return []
                history_data = json.loads(file_content)
            if not isinstance(history_data, list): 
                logger.warning(f"RelayChat HistoryStorage: 历史文件 '{file_path}'内容不是列表。")
                return []
            return history_data
        except json.JSONDecodeError as e_json:
            logger.error(f"RelayChat HistoryStorage: JSON解码历史文件 '{file_path}' 失败: {e_json}. 文件内容(前100字节): '{file_content[:100]}'")
            return []
        except Exception as e: 
            logger.error(f"RelayChat HistoryStorage:读取历史 '{file_path}' 失败: {e}", exc_info=True); return []
            
    @staticmethod
    def clear_history(event: AstrMessageEvent) -> bool:
        file_path = HistoryStorage._get_file_path_for_chat(event)
        if not file_path: return False
        try:
            if os.path.exists(file_path): os.remove(file_path)
            logger.info(f"RelayChat HistoryStorage: 已清空历史 '{file_path}'.")
            return True
        except Exception as e:
            logger.error(f"RelayChat HistoryStorage: 清空历史 '{file_path}' 失败: {e}"); return False
