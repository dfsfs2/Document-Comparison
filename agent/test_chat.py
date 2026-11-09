from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from server import ChatRequest, ConversationTurn, chat, chat_models


class ChatPromptTest(TestCase):
    @patch("server.Bailian")
    def test_recalled_context_and_recent_messages_reach_the_configured_model(self, bailian):
        client = bailian.return_value
        client.key = "fixture"
        client.chat_model = "configured-model"
        client.post.return_value = {"choices": [{"message": {"content": "  可以继续。  "}}]}
        response = chat(ChatRequest(
            message="现在的问题", recent=[ConversationTurn(question="上一问", answer="上一答")],
            recalled=[ConversationTurn(question="旧项目代号", answer="星海")],
        ))
        self.assertEqual(response, {"answer": "可以继续。", "model": "configured-model"})
        path, payload = client.post.call_args.args
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(payload["model"], "configured-model")
        self.assertIn("旧项目代号", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1:], [
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
            {"role": "user", "content": "现在的问题"},
        ])

    @patch("server.Bailian")
    def test_requires_configured_credentials(self, bailian):
        bailian.return_value.key = ""
        bailian.return_value.chat_model = "qwen-plus"
        with self.assertRaises(HTTPException) as error:
            chat(ChatRequest(message="你好", recent=[], recalled=[]))
        self.assertEqual(error.exception.status_code, 503)
        bailian.return_value.post.assert_not_called()

    @patch.dict("os.environ", {"DASHSCOPE_CHAT_MODELS": "qwen-flash,qwen-max"})
    @patch("server.Bailian")
    def test_switches_only_to_allowed_models(self, bailian):
        client = bailian.return_value
        client.key = "fixture"
        client.chat_model = "qwen-plus"
        client.post.return_value = {"choices": [{"message": {"content": "回复"}}]}
        self.assertEqual(chat(ChatRequest(message="你好", recent=[], recalled=[], model="qwen-flash")),
                         {"answer": "回复", "model": "qwen-flash"})
        self.assertEqual(client.post.call_args.args[1]["model"], "qwen-flash")
        client.post.reset_mock()
        with self.assertRaises(HTTPException) as error:
            chat(ChatRequest(message="你好", recent=[], recalled=[], model="unlisted"))
        self.assertEqual(error.exception.status_code, 400)
        client.post.assert_not_called()

    @patch.dict("os.environ", {"DASHSCOPE_CHAT_MODEL": "qwen-plus",
                             "DASHSCOPE_CHAT_MODELS": " qwen-max, qwen-plus, qwen-max, "})
    def test_catalogue_keeps_default_and_deduplicates_choices(self):
        self.assertEqual(chat_models(), {"defaultModel": "qwen-plus", "models": ["qwen-plus", "qwen-max"]})
        with patch.dict("os.environ", {"DASHSCOPE_CHAT_MODELS": ""}):
            self.assertEqual(chat_models(), {"defaultModel": "qwen-plus", "models": ["qwen-plus"]})
