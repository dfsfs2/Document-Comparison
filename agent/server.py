import base64
import hmac
import logging

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

from advisor import Advisor, setting
from formatter import InvalidDocument, MAX_FILE, format_document

app = FastAPI(title="Document Comparison Agent", docs_url=None, redoc_url=None, openapi_url=None)
LOG = logging.getLogger(__name__)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/format")
def transform(reference: UploadFile = File(), target: UploadFile = File(),
              use_cloud: bool = Form(False), x_agent_token: str = Header("")):
    token = setting("AGENT_TOKEN")
    if not token or not hmac.compare_digest(token.encode(), x_agent_token.encode()):
        raise HTTPException(403, "Agent authentication failed")
    try:
        output, report = format_document(reference.file.read(MAX_FILE + 1),
                                         target.file.read(MAX_FILE + 1), Advisor(use_cloud))
    except InvalidDocument as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        LOG.exception("Document processing failed")
        raise HTTPException(500, "文档处理失败。") from error
    return {"output": base64.b64encode(output).decode("ascii"), **report}
