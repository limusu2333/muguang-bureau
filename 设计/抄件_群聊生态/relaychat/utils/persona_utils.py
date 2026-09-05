# astrbot_plugin_relaychat/utils/persona_utils.py

from astrbot.api.all import Context, logger 
from typing import List, Optional, Any, Dict, Union

class PersonaUtils:
    @staticmethod
    def get_persona_by_name(context: Context, persona_name: str) -> Optional[Dict[str, Any]]:
        if not persona_name:
            logger.warning("PersonaUtils: get_persona_by_name called with empty persona_name.")
            return None
        logger.debug(f"PersonaUtils: Attempting to get persona by name: '{persona_name}'")
        if not hasattr(context, 'provider_manager') or not context.provider_manager:
            logger.error("PersonaUtils: context.provider_manager is not available."); return None
        if not hasattr(context.provider_manager, 'personas') or not isinstance(context.provider_manager.personas, list):
            logger.error("PersonaUtils: context.provider_manager.personas is not available or not a list."); return None
        all_personas: List[Dict[str, Any]] = context.provider_manager.personas
        logger.debug(f"PersonaUtils: Iterating through {len(all_personas)} personas.")
        if len(all_personas) > 0 and isinstance(all_personas[0], dict):
             logger.debug(f"PersonaUtils: First persona dict keys: {list(all_personas[0].keys())}")
        elif len(all_personas) > 0:
             logger.warning(f"PersonaUtils: First persona object is not a dict, type: {type(all_personas[0])}")
        for i, persona_dict in enumerate(all_personas):
            if not isinstance(persona_dict, dict): 
                logger.warning(f"PersonaUtils: Persona entry #{i} is not a dictionary, skipping. Type: {type(persona_dict)}")
                continue
            current_p_name = persona_dict.get('name')
            p_id = persona_dict.get('id') 
            has_prompt_key = "prompt" in persona_dict or "system_prompt" in persona_dict
            logger.debug(f"PersonaUtils: Checking persona dict #{i}: Name='{current_p_name}', ID='{p_id}', HasPromptKey={has_prompt_key}")
            if current_p_name == persona_name:
                logger.info(f"PersonaUtils: Found matching persona for '{persona_name}' by name. Returning dict.")
                return persona_dict
        logger.warning(f"PersonaUtils: Persona named '{persona_name}' not found after checking all entries.")
        return None

    @staticmethod
    def get_persona_system_prompt(context: Context, persona_name: str, default_prompt: str = "") -> str:
        logger.debug(f"PersonaUtils: Requesting system_prompt for persona '{persona_name}'.")
        persona_dict = PersonaUtils.get_persona_by_name(context, persona_name)
        if persona_dict:
            system_prompt = persona_dict.get("system_prompt") or persona_dict.get("prompt") # Try both keys
            if system_prompt and isinstance(system_prompt, str) and system_prompt.strip():
                logger.info(f"PersonaUtils: Successfully retrieved system_prompt for '{persona_name}' (length: {len(system_prompt)}).")
                return system_prompt
            else:
                logger.warning(f"PersonaUtils: Persona '{persona_name}' found, but its 'system_prompt' (or 'prompt') field is empty or not a string. Using default prompt.")
        return default_prompt

    @staticmethod
    def get_persona_model(context: Context, persona_name: str, default_model: Optional[str] = None) -> Optional[str]:
        logger.debug(f"PersonaUtils: Requesting model for persona '{persona_name}'.")
        persona_dict = PersonaUtils.get_persona_by_name(context, persona_name)
        if persona_dict:
            model_name = persona_dict.get("model")
            if model_name and isinstance(model_name, str) and model_name.strip():
                logger.info(f"PersonaUtils: Successfully retrieved model '{model_name}' for '{persona_name}'.")
                return model_name.strip()
            else:
                logger.debug(f"PersonaUtils: Persona '{persona_name}' found, but no specific model configured or model is empty.")
        return default_model
