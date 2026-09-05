# astrbot_plugin_relaychat/utils/message_utils.py
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple 
from astrbot.api.all import AstrMessageEvent # 导入 AstrMessageEvent
from astrbot.api.message_components import BaseMessageComponent, Plain, Image as AstrBotImageComponent

logger = logging.getLogger(__name__)

class MessageUtils:
    MAX_HISTORY_IMAGES_TO_LLM = 5 

    @staticmethod
    async def outline_message_list(message_list: Optional[List[BaseMessageComponent]], for_history: bool = False) -> str:
        outline_parts = []
        if not message_list: return ""
        
        for i in message_list:
            if isinstance(i, Plain):
                if i.text: outline_parts.append(i.text)
            elif isinstance(i, AstrBotImageComponent):
                filename = "[图片]"
                if hasattr(i, 'file') and i.file: 
                    if not i.file.startswith("base64://") and not i.file.startswith("http"):
                        filename = f"[{i.file.split('/')[-1]}]" 
                elif hasattr(i, 'path') and i.path: # 有些Image组件可能用path存文件名
                     filename = f"[{i.path.split('/')[-1]}]"
                outline_parts.append(filename)
            elif hasattr(i, 'type') and hasattr(i.type, 'value'):
                 outline_parts.append(f"[{i.type.value}]")
            elif hasattr(i, 'text') and i.text is not None:
                 outline_parts.append(str(i.text))
            else:
                 outline_parts.append(f"[{type(i).__name__}]")
        return " ".join(filter(None,outline_parts)).strip() # filter(None,...) 避免空字符串导致的双空格

    @staticmethod
    async def format_history_for_llm(
        history_dicts: List[Dict[str, Any]], 
        max_messages: Optional[int] = None
    ) -> Tuple[str, List[str]]: 
        formatted_entries: List[str] = []
        history_image_data_uris: List[str] = [] 
        
        history_to_process = history_dicts
        if max_messages is not None and len(history_dicts) > max_messages:
            history_to_process = history_dicts[-max_messages:]

        images_collected_count = 0
        temp_image_uris_reversed = [] # 用于临时反序存储，确保先拿到最新的图片

        for entry_dict in reversed(history_to_process): 
            if images_collected_count >= MessageUtils.MAX_HISTORY_IMAGES_TO_LLM:
                break 
            # 假设图片信息存储在 "image_base64_uri" 字段
            image_uri = entry_dict.get("image_base64_uri") 
            if image_uri and isinstance(image_uri, str) and image_uri.startswith("base64://"):
                temp_image_uris_reversed.append(image_uri)
                images_collected_count += 1
        
        history_image_data_uris = list(reversed(temp_image_uris_reversed)) # 恢复正确的时间顺序
        
        if history_image_data_uris:
            logger.debug(f"MessageUtils: 从最近历史中提取了 {len(history_image_data_uris)} 张图片给LLM。")

        for entry_dict in history_to_process: 
            name = entry_dict.get("name", "未知")
            user_id = entry_dict.get("user_id", "未知ID")
            text_content = entry_dict.get("text", "[内容缺失或非文本]") 
            text_content_single_line = text_content.replace("\n", " ").replace("\r", " ")
            formatted_entry = f"发送者: {name} (ID: {user_id})\n时间: {entry_dict.get('time', 'N/A')}\n内容: {text_content_single_line}"
            formatted_entries.append(formatted_entry)
            
        return "\n-\n".join(formatted_entries), history_image_data_uris

    @staticmethod
    async def get_text_from_event(event: AstrMessageEvent, for_history: bool = False) -> str:
        if not event or not event.get_messages(): return ""
        return await MessageUtils.outline_message_list(event.get_messages(), for_history=for_history)
    
    @staticmethod
    def dedup_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # 基于 "role", "user_id", "text" 和 "image_base64_uri" (如果存在) 作为去重键
        # 注意：这只是一个简单的示例，对于复杂情况（例如，文本相似但不完全相同），可能需要更高级的去重逻辑。
        seen_signatures = set()
        deduped_history: List[Dict[str, Any]] = []
        for entry in reversed(history): # 从后往前，保留最新的
            signature_parts = [
                entry.get("role", ""),
                str(entry.get("user_id", "")),
                entry.get("text", "") 
            ]
            if "image_base64_uri" in entry and entry["image_base64_uri"]:
                # 只取base64数据的前一小部分作为签名的一部分，避免过长的key
                signature_parts.append(entry["image_base64_uri"][:100]) 
            
            signature = "::".join(signature_parts)
            
            if signature not in seen_signatures:
                deduped_history.append(entry)
                seen_signatures.add(signature)
            else:
                logger.debug(f"MessageUtils: Deduped history entry. Sig: {signature[:100]}...")
        
        return list(reversed(deduped_history)) # 恢复原始顺序
