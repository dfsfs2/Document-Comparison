from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from fastapi import HTTPException

from server import ChatRequest, ConversationTurn, chat, chat_models, chat_payload, stream_chat


def sse(*chunks):
    return BytesIO(b"".join(b"data: " + (json.dumps(chunk).encode() if isinstance(chunk, dict) else chunk)
                           + b"\n\n" for chunk in chunks))


class ChatPromptTest(TestCase):
    @patch("server.Bailian")
    def test_recalled_context_and_recent_messages_reach_the_configured_model(self, bailian):
        client = bailian.return_value
        client.key = "fixture"
        client.chat_model = "configured-model"
        client.post.return_value = {"model": "configured-model",
                                    "choices": [{"message": {"content": "  可以继续。  "}}]}
        response = chat(ChatRequest(
            message="现在的问题", recent=[ConversationTurn(question="上一问", answer="上一答")],
            recalled=[ConversationTurn(question="旧项目代号", answer="星海")],
        ))
        self.assertEqual(response, {"answer": "可以继续。", "model": "configured-model", "reasoning": None})
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
        client.post.return_value = {"model": "qwen-flash", "choices": [
            {"message": {"content": "回复", "reasoning_content": "  百炼返回的推理  "}},
        ]}
        self.assertEqual(chat(ChatRequest(message="你好", recent=[], recalled=[], model="qwen-flash")),
                         {"answer": "回复", "model": "qwen-flash", "reasoning": "百炼返回的推理"})
        self.assertEqual(client.post.call_args.args[1]["model"], "qwen-flash")
        self.assertIs(client.post.call_args.args[1]["enable_thinking"], True)
        self.assertIn("qwen-flash", client.post.call_args.args[1]["messages"][0]["content"])
        client.post.reset_mock()
        with self.assertRaises(HTTPException) as error:
            chat(ChatRequest(message="你好", recent=[], recalled=[], model="unlisted"))
        self.assertEqual(error.exception.status_code, 400)
        client.post.assert_not_called()

    @patch.dict("os.environ", {}, clear=True)
    @patch("server.Bailian")
    def test_default_catalogue_includes_other_families_and_minimax_omits_thinking_flag(self, bailian):
        client = bailian.return_value
        client.key = "fixture"
        client.chat_model = "qwen-plus"
        client.post.return_value = {"model": "MiniMax-M2.5", "choices": [{"message": {"content": "回复"}}]}
        self.assertEqual(chat_models()["models"], [
            "qwen-plus", "qwen-flash", "qwen-max", "deepseek-v4.1-flash",
            "kimi-k2.6", "glm-5.2", "MiniMax-M2.5", "qwen3.6-plus",
        ])
        self.assertEqual(chat_models()["visionModels"], ["qwen3.6-plus"])
        result = chat(ChatRequest(message="你好", recent=[], recalled=[], model="MiniMax-M2.5"))
        self.assertEqual(result["model"], "MiniMax-M2.5")
        self.assertIsNone(result["reasoning"])
        self.assertNotIn("enable_thinking", client.post.call_args.args[1])

    @patch.dict("os.environ", {"DASHSCOPE_CHAT_MODELS": "deepseek-v4.1-flash"})
    @patch("server.Bailian")
    def test_rejects_missing_or_different_model_reported_by_bailian(self, bailian):
        client = bailian.return_value
        client.key = "fixture"
        client.chat_model = "qwen-plus"
        request = ChatRequest(message="你好", recent=[], recalled=[], model="deepseek-v4.1-flash")
        for metadata in ({}, {"model": "qwen-plus"}):
            with self.subTest(metadata=metadata):
                client.post.return_value = {**metadata, "choices": [{"message": {"content": "回复"}}]}
                with self.assertRaises(HTTPException) as error:
                    chat(request)
                self.assertEqual(error.exception.status_code, 502)
                self.assertIn("模型标识", error.exception.detail)
        self.assertEqual(client.post.call_count, 2)

    @patch.dict("os.environ", {"DASHSCOPE_CHAT_MODEL": "qwen-plus",
                             "DASHSCOPE_CHAT_MODELS": " qwen-max, qwen-plus, qwen-max, "})
    def test_catalogue_keeps_default_and_deduplicates_choices(self):
        self.assertEqual(chat_models(), {"defaultModel": "qwen-plus", "models": ["qwen-plus", "qwen-max"], "visionModels": []})
        with patch.dict("os.environ", {"DASHSCOPE_CHAT_MODELS": ""}):
            self.assertEqual(chat_models(), {"defaultModel": "qwen-plus", "models": ["qwen-plus"], "visionModels": []})

    def test_stream_delivers_reasoning_then_answer_and_requires_terminal_marker(self):
        client = Mock()
        model = "glm-5.2"
        chunks = [{"model": model, "choices": [{"delta": delta}]} for delta in
                  ({"reasoning_content": "正在"}, {"reasoning_content": "计算"}, {"content": "42"})]
        client.open.return_value = sse(*chunks, b"[DONE]")
        events = [json.loads(line) for line in stream_chat(client, {"model": model})]
        self.assertEqual(events, [{"type": "reasoning", "text": "正在"}, {"type": "reasoning", "text": "计算"},
                                  {"type": "answer", "text": "42"},
                                  {"type": "done", "model": model, "answer": "42", "reasoning": "正在计算"}])
        client.open.return_value = sse(*chunks)
        self.assertEqual([json.loads(line) for line in stream_chat(client, {"model": model})][-1]["type"], "error")

    def test_stream_checks_model_before_forwarding_content(self):
        client = Mock()
        client.open.return_value = sse({"model": "qwen-plus", "choices": [{"delta": {"content": "错的模型"}}]}, b"[DONE]")
        events = [json.loads(line) for line in stream_chat(client, {"model": "glm-5.2"})]
        self.assertEqual([event["type"] for event in events], ["error"])

    @patch.dict("os.environ", {"DASHSCOPE_CHAT_MODELS": "glm-5.2,qwen3.6-plus"})
    def test_attachments_keep_the_selected_model_and_require_vision_for_images(self):
        client = Mock(chat_model="qwen-plus", key="fixture")
        request = ChatRequest(message="请总结", recent=[], recalled=[], model="glm-5.2")
        document = Mock(filename="规则.txt", file=BytesIO("测试文档内容".encode()))
        payload = chat_payload(client, request, [document])
        self.assertEqual(payload["model"], "glm-5.2")
        self.assertIn("测试文档内容", payload["messages"][-1]["content"])
        png = b"\x89PNG\r\n\x1a\nfixture"
        with self.assertRaises(HTTPException) as error:
            chat_payload(client, request, [Mock(filename="image.png", file=BytesIO(png))])
        self.assertEqual(error.exception.status_code, 422)
        request.model = "qwen3.6-plus"
        payload = chat_payload(client, request, [Mock(filename="image.png", file=BytesIO(png))])
        self.assertEqual(payload["model"], "qwen3.6-plus")
        self.assertTrue(payload["messages"][-1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,"))
