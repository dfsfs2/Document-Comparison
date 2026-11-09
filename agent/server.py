import base64
import hmac
import json
import logging
import os
from urllib.error import HTTPError

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from advisor import Advisor, Bailian, default_knowledge, setting
from formatter import InvalidDocument, MAX_FILE, format_document
from knowledge_import import extract_text

app = FastAPI(title="Document Comparison Agent", docs_url=None, redoc_url=None, openapi_url=None)
LOG = logging.getLogger(__name__)
DEFAULT_CHAT_MODELS = "qwen-flash,qwen-max,deepseek-v4.1-flash,kimi-k2.6,glm-5.2,MiniMax-M2.5"


class KnowledgeRule(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=80)
    rule: str = Field(min_length=1, max_length=1000)
    builtIn: bool = False


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    knowledge: list[KnowledgeRule] = Field(max_length=100)
    use_cloud: bool = Field(default=False, alias="useCloud")


class ConversationTurn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    recent: list[ConversationTurn] = Field(max_length=8)
    recalled: list[ConversationTurn] = Field(max_length=3)
    model: str | None = Field(default=None, min_length=1, max_length=128)


KNOWLEDGE = TypeAdapter(list[KnowledgeRule])


def authenticate(x_agent_token: str = Header("")):
    token = setting("AGENT_TOKEN")
    if not token or not hmac.compare_digest(token.encode(), x_agent_token.encode()):
        raise HTTPException(403, "Agent authentication failed")


@app.get("/health")
def health():
    return {"status": "ok"}


def available_chat_models(default):
    extras = os.getenv("DASHSCOPE_CHAT_MODELS", DEFAULT_CHAT_MODELS).split(",")
    return list(dict.fromkeys([default, *(name.strip() for name in extras if name.strip())]))


@app.get("/chat/models", dependencies=[Depends(authenticate)])
def chat_models():
    default = Bailian().chat_model
    return {"defaultModel": default, "models": available_chat_models(default)}


@app.post("/chat", dependencies=[Depends(authenticate)])
def chat(request: ChatRequest):
    client = Bailian()
    model = request.model if request.model is not None else client.chat_model
    if model not in available_chat_models(client.chat_model):
        raise HTTPException(400, "这个模型不在可选列表中，请刷新后重新选择。")
    if not client.key:
        raise HTTPException(503, "百炼 API Key 尚未配置，请检查 DASHSCOPE_API_KEY。")
    messages = [{"role": "system", "content": (
        "你是文档对照工作台的聊天助手。按用户的语言回答。"
        f"本轮调用的模型标识是 {model}。用户问你是什么模型、型号或版本时，"
        "直接给出本轮模型标识。历史对话中的自我介绍只用于理解历史背景，"
        "回答当前模型身份时以本轮标识为准。"
        "较早的对话摘录仅供理解背景，结合当前问题使用，并以用户较新的表述为准。"
    )}]
    if request.recalled:
        history = [{"user": turn.question[:800], "assistant": turn.answer[:1000]} for turn in request.recalled]
        messages[0]["content"] += "\n较早的相关对话摘录\n" + json.dumps(history, ensure_ascii=False)
    for turn in request.recent:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer[:2000]})
    messages.append({"role": "user", "content": request.message})
    payload = {"model": model, "max_tokens": 1536, "messages": messages}
    if not model.casefold().startswith("minimax"):
        payload["enable_thinking"] = False
    try:
        result = client.post("/chat/completions", payload, timeout=75)
        reported_model = result.get("model") if isinstance(result, dict) else None
        if reported_model != model:
            LOG.warning("Bailian chat model mismatch: requested=%r, reported=%.128r", model, reported_model)
            raise HTTPException(502, "百炼返回的模型标识校验失败，请检查所选模型或稍后重试。")
        answer = result["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Empty chat completion")
        return {"answer": answer.strip()[:8000], "model": reported_model}
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        LOG.warning("Bailian chat unavailable: %s", type(error).__name__)
        if isinstance(error, HTTPError) and error.code in (400, 403, 404):
            raise HTTPException(502, "当前模型暂时无法调用，请切换模型或检查百炼模型权限。") from error
        raise HTTPException(502, "百炼暂时无法回复，请稍后重试。") from error


@app.get("/knowledge/defaults", dependencies=[Depends(authenticate)], response_model=list[KnowledgeRule])
def knowledge_defaults():
    return [{**entry, "builtIn": True} for entry in default_knowledge()]


@app.post("/knowledge/query", dependencies=[Depends(authenticate)])
def knowledge_query(request: KnowledgeQuery):
    matches, mode = Advisor(request.use_cloud, [rule.model_dump() for rule in request.knowledge]).retrieve(request.query)
    return {"matches": matches, "mode": mode}


@app.post("/knowledge/extract", dependencies=[Depends(authenticate)])
def extract_knowledge(file: UploadFile = File()):
    try:
        return {"text": extract_text(file.file.read(MAX_FILE + 1), file.filename or "")}
    except InvalidDocument as error:
        raise HTTPException(422, str(error)) from error


@app.post("/format", dependencies=[Depends(authenticate)])
def transform(reference: UploadFile = File(), target: UploadFile = File(),
              use_cloud: bool = Form(False), knowledge: str | None = Form(None)):
    rules = None
    if knowledge is not None:
        try:
            entries = KNOWLEDGE.validate_json(knowledge)
            if len(entries) > 100:
                raise ValueError("Too many knowledge rules")
            rules = [entry.model_dump() for entry in entries]
        except (ValidationError, ValueError) as error:
            raise HTTPException(422, "知识库规则数据无效。") from error
    try:
        output, report = format_document(reference.file.read(MAX_FILE + 1),
                                         target.file.read(MAX_FILE + 1), Advisor(use_cloud, rules))
    except InvalidDocument as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        LOG.exception("Document processing failed")
        raise HTTPException(500, "文档处理失败。") from error
    return {"output": base64.b64encode(output).decode("ascii"), **report}
