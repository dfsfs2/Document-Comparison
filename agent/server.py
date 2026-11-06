import base64
import hmac
import logging

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from advisor import Advisor, default_knowledge, setting
from formatter import InvalidDocument, MAX_FILE, format_document

app = FastAPI(title="Document Comparison Agent", docs_url=None, redoc_url=None, openapi_url=None)
LOG = logging.getLogger(__name__)


class KnowledgeRule(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=80)
    rule: str = Field(min_length=1, max_length=1000)
    builtIn: bool = False


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    knowledge: list[KnowledgeRule] = Field(max_length=100)
    use_cloud: bool = Field(default=False, alias="useCloud")


KNOWLEDGE = TypeAdapter(list[KnowledgeRule])


def authenticate(x_agent_token: str = Header("")):
    token = setting("AGENT_TOKEN")
    if not token or not hmac.compare_digest(token.encode(), x_agent_token.encode()):
        raise HTTPException(403, "Agent authentication failed")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/knowledge/defaults", dependencies=[Depends(authenticate)], response_model=list[KnowledgeRule])
def knowledge_defaults():
    return [{**entry, "builtIn": True} for entry in default_knowledge()]


@app.post("/knowledge/query", dependencies=[Depends(authenticate)])
def knowledge_query(request: KnowledgeQuery):
    matches, mode = Advisor(request.use_cloud, [rule.model_dump() for rule in request.knowledge]).retrieve(request.query)
    return {"matches": matches, "mode": mode}


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
