from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from copy import deepcopy
from pathlib import Path


def _install_astrbot_stubs() -> None:
    for name in list(sys.modules):
        if name == "main" or name.startswith("astrbot"):
            sys.modules.pop(name, None)

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    provider_mod = types.ModuleType("astrbot.api.provider")
    star_mod = types.ModuleType("astrbot.api.star")
    comp_mod = types.ModuleType("astrbot.api.message_components")

    class _Logger:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    class _Filter:
        class EventMessageType:
            GROUP_MESSAGE = "GroupMessage"

        class _CommandGroup:
            def __call__(self, func):
                return self

            def command(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

        @staticmethod
        def event_message_type(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def on_llm_request(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def after_message_sent(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def regex(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def command_group(*args, **kwargs):
            return _Filter._CommandGroup()

    class _Star:
        def __init__(self, context):
            self.context = context

    def register(*args, **kwargs):
        def decorator(cls):
            return cls

        return decorator

    class ProviderRequest:
        def __init__(self):
            self.system_prompt = ""
            self.extra_user_content_parts = []

    class At:
        type = "at"

        def __init__(self, qq, name=""):
            self.qq = qq
            self.name = name

    class AtAll(At):
        def __init__(self):
            super().__init__("all", "全体成员")

    class Reply:
        type = "reply"

        def __init__(self, sender_id="", qq="", message_str=""):
            self.sender_id = sender_id
            self.qq = qq
            self.message_str = message_str

    class Plain:
        type = "plain"

        def __init__(self, text=""):
            self.text = text

    api.AstrBotConfig = dict
    api.logger = _Logger()
    event_mod.AstrMessageEvent = object
    event_mod.filter = _Filter
    provider_mod.ProviderRequest = ProviderRequest
    star_mod.Context = object
    star_mod.Star = _Star
    star_mod.register = register
    comp_mod.At = At
    comp_mod.AtAll = AtAll
    comp_mod.Reply = Reply
    comp_mod.Plain = Plain

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.provider"] = provider_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.message_components"] = comp_mod


_install_astrbot_stubs()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
main = importlib.import_module("main")


BASE_CONFIG = {
    "enabled": True,
    "bot_registry": {
        "controlled_bots": [
            {"qq": "111", "name": "bot-a", "call_name": "A"},
        ],
        "uncontrolled_bots": [
            {"qq": "222", "name": "bot-b", "call_name": "B"},
        ],
    },
    "targeting": {
        "require_explicit_target": False,
        "enable_reply_target": True,
        "treat_at_all_as_target": False,
        "treat_wake_prefix_as_target": False,
    },
    "limits": {
        "enable_max_turns_per_session": True,
        "max_turns_per_session": 2,
        "enable_cooldown_after_limit": True,
        "cooldown_seconds": 60,
        "bot_request_delay_seconds": 0,
        "enable_min_interval_per_peer": False,
        "enable_group_window_limit": False,
        "enable_without_human_limit": False,
        "enable_duplicate_message_limit": False,
    },
    "prompting": {
        "bot_prompt_template": "peer=<botid>;self=<selfnickname>",
        "reply_style_prompt": "",
    },
    "multi_controlled": {
        "enable_priority_order": True,
        "controlled_reply_spacing_seconds": 0,
        "priority_order": "small_first",
    },
}


class Sender:
    def __init__(self, user_id: str, nickname: str = ""):
        self.user_id = user_id
        self.nickname = nickname


class MessageObj:
    def __init__(self, group_id, self_id, sender_id, messages, raw_message=None):
        self.group_id = group_id
        self.self_id = self_id
        self.sender = Sender(sender_id, f"user-{sender_id}")
        self.message = messages
        self.raw_message = raw_message
        self.group = None


class FakeEvent:
    def __init__(
        self,
        group_id="1000",
        self_id="111",
        sender_id="222",
        messages=None,
        raw_message=None,
        platform_name="aiocqhttp",
        platform_id="aiocqhttp-main",
        message_str="hello",
        is_woken=True,
    ):
        self.message_str = message_str
        self.message_obj = MessageObj(group_id, self_id, sender_id, messages or [], raw_message)
        self.platform_name = platform_name
        self.platform_id = platform_id
        self.is_at_or_wake_command = is_woken
        self.is_wake = is_woken
        self.stopped = False
        self.extras = {}
        self._has_send_oper = False

    def get_group_id(self):
        return self.message_obj.group_id

    def get_self_id(self):
        return self.message_obj.self_id

    def get_sender_id(self):
        return self.message_obj.sender.user_id

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def get_messages(self):
        return self.message_obj.message

    def get_platform_name(self):
        return self.platform_name

    def get_platform_id(self):
        return self.platform_id

    def get_session_id(self):
        return self.message_obj.group_id

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def stop_event(self):
        self.stopped = True


def make_plugin(config_patch=None):
    config = deepcopy(BASE_CONFIG)
    if config_patch:
        for section, value in config_patch.items():
            if isinstance(value, dict) and isinstance(config.get(section), dict):
                config[section].update(value)
            else:
                config[section] = value
    return main.MultiBotControlPlugin(object(), config)


class MultiBotControlCoreTests(unittest.TestCase):
    def test_global_qq_registry_matches_sender_and_self(self):
        plugin = make_plugin()
        event = FakeEvent(messages=[main.Comp.At(qq="111")])
        entries = plugin._effective_entries("1000")

        self.assertEqual(plugin._find_peer_bot(event, entries).qq, "222")
        self.assertEqual(plugin._self_entry(event, entries).qq, "111")
        self.assertTrue(plugin._message_targets_entry(event, plugin._self_entry(event, entries)))

    def test_group_local_entry_does_not_override_global_controlled_bot(self):
        plugin = make_plugin()
        plugin._group_bots_cache = {
            "groups": {
                "1000": {
                    "bots": [
                        {"qq": "111", "name": "wrong-local-entry"},
                    ],
                },
            },
        }

        entries = plugin._effective_entries("1000")
        entry = next(item for item in entries if item.qq == "111")

        self.assertEqual(entry.kind, "controlled")
        self.assertEqual(entry.source, "global")

    def test_require_explicit_target_blocks_untargeted_bot_message(self):
        plugin = make_plugin({"targeting": {"require_explicit_target": True}})
        event = FakeEvent(messages=[main.Comp.Plain("hello")])

        asyncio.run(plugin.route_group_messages(event))

        self.assertTrue(event.stopped)
        self.assertIsNone(event.get_extra("multi_bot_control_prompt"))

    def test_bot_request_is_not_counted_until_reply_is_sent(self):
        plugin = make_plugin()
        event = FakeEvent(messages=[main.Comp.At(qq="111")])

        asyncio.run(plugin.route_group_messages(event))

        self.assertFalse(event.stopped)
        self.assertIsNotNone(event.get_extra(main.PENDING_REPLY_EXTRA))
        self.assertEqual(len(plugin.sessions), 1)
        state = next(iter(plugin.sessions.values()))
        self.assertEqual(state.turns, 0)

        event._has_send_oper = True
        asyncio.run(plugin.commit_bot_reply_after_sent(event))

        self.assertIsNone(event.get_extra(main.PENDING_REPLY_EXTRA))
        self.assertEqual(state.turns, 1)

    def test_unwoken_bot_message_is_not_forced_awake(self):
        plugin = make_plugin()
        event = FakeEvent(
            messages=[main.Comp.Plain("not for this bot")],
            is_woken=False,
        )

        asyncio.run(plugin.route_group_messages(event))

        self.assertFalse(event.stopped)
        self.assertFalse(event.is_wake)
        self.assertFalse(event.is_at_or_wake_command)
        self.assertIsNone(event.get_extra("multi_bot_control_prompt"))
        self.assertIsNone(event.get_extra(main.PENDING_REPLY_EXTRA))
        self.assertEqual(plugin.sessions, {})

    def test_failed_send_does_not_commit_pending_reply(self):
        plugin = make_plugin()
        event = FakeEvent(messages=[main.Comp.At(qq="111")])

        asyncio.run(plugin.route_group_messages(event))
        asyncio.run(plugin.commit_bot_reply_after_sent(event))

        self.assertIsNotNone(event.get_extra(main.PENDING_REPLY_EXTRA))
        state = next(iter(plugin.sessions.values()))
        self.assertEqual(state.turns, 0)

    def test_session_limits_are_isolated_by_group(self):
        plugin = make_plugin(
            {
                "limits": {
                    "max_turns_per_session": 1,
                    "enable_min_interval_per_peer": False,
                    "enable_group_window_limit": False,
                    "enable_without_human_limit": False,
                    "enable_duplicate_message_limit": False,
                },
            },
        )

        first = FakeEvent(group_id="group-1", messages=[main.Comp.At(qq="111")])
        second_same_group = FakeEvent(group_id="group-1", messages=[main.Comp.At(qq="111")])
        other_group = FakeEvent(group_id="group-2", messages=[main.Comp.At(qq="111")])

        asyncio.run(plugin.route_group_messages(first))
        first._has_send_oper = True
        asyncio.run(plugin.commit_bot_reply_after_sent(first))
        asyncio.run(plugin.route_group_messages(second_same_group))
        asyncio.run(plugin.route_group_messages(other_group))

        self.assertFalse(first.stopped)
        self.assertTrue(second_same_group.stopped)
        self.assertFalse(other_group.stopped)
        self.assertIn("aiocqhttp-main:group-1:qq:111:qq:222", plugin.sessions)
        self.assertIn("aiocqhttp-main:group-2:qq:111:qq:222", plugin.sessions)

    def test_qq_official_openid_is_not_matched_as_qq_number(self):
        plugin = make_plugin()
        event = FakeEvent(
            self_id="qq_official",
            sender_id="openid-222",
            messages=[main.Comp.At(qq="qq_official")],
            platform_name="qq_official",
            platform_id="qq-official-main",
        )

        asyncio.run(plugin.route_group_messages(event))

        self.assertFalse(event.stopped)
        self.assertIsNone(event.get_extra("multi_bot_control_prompt"))

    def test_human_message_resets_only_current_group_non_cooldown_turns(self):
        plugin = make_plugin()
        peer = main.BotEntry(qq="222")
        bot_event_g1 = FakeEvent(group_id="group-1", messages=[main.Comp.At(qq="111")])
        bot_event_g2 = FakeEvent(group_id="group-2", messages=[main.Comp.At(qq="111")])
        key_g1 = plugin._session_key(bot_event_g1, peer, plugin._self_entry(bot_event_g1, plugin._effective_entries("group-1")))
        key_g2 = plugin._session_key(bot_event_g2, peer, plugin._self_entry(bot_event_g2, plugin._effective_entries("group-2")))
        state_g1 = plugin._state_for(key_g1)
        state_g2 = plugin._state_for(key_g2)
        state_g1.turns = 2
        state_g2.turns = 2
        state_g1.cooldown_until = 0.0
        state_g2.cooldown_until = 0.0

        human_event = FakeEvent(group_id="group-1", sender_id="333", messages=[main.Comp.Plain("human")])
        asyncio.run(plugin.route_group_messages(human_event))

        self.assertEqual(state_g1.turns, 0)
        self.assertEqual(state_g2.turns, 2)


if __name__ == "__main__":
    unittest.main()
