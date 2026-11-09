"""Prepare chat documents and images without storing the uploaded files."""
from base64 import b64encode
from io import BytesIO
import json
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from formatter import InvalidDocument, MAX_FILE
from knowledge_import import extract_text

IMAGE_TYPES = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}
MAX_TEXT = 24000
MAX_PDF_PAGES = 50


def read_pdf(data):
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise InvalidDocument("请先解除 PDF 的加密保护。")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise InvalidDocument("聊天附件的 PDF 最多支持 50 页，请拆分后上传。")
        pages = []
        length = 0
        for page in reader.pages:
            text = page.extract_text().strip()
            length += len(text) + 1
            if length > MAX_TEXT:
                raise InvalidDocument("附件文字总计最多 24,000 字，请拆分后上传。")
            pages.append(text)
        text = "\n".join(pages).strip()
    except InvalidDocument:
        raise
    except (PyPdfError, ValueError, TypeError, KeyError, RecursionError, OSError) as error:
        raise InvalidDocument("无法读取此 PDF，请检查文件是否完整。") from error
    if not text:
        raise InvalidDocument("PDF 没有可提取的文字。扫描件可以将页面转成图片后上传。")
    return text


def image_part(data, kind):
    valid = {
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpeg": data.startswith(b"\xff\xd8\xff"),
        "webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not valid[kind]:
        raise InvalidDocument("图片内容与文件格式不符。")
    return {"type": "image_url", "image_url": {"url": f"data:image/{kind};base64,{b64encode(data).decode('ascii')}"}}


def prepare_content(message, files, can_see_images):
    if len(files) > 3:
        raise InvalidDocument("每条消息最多添加 3 个附件。")
    remaining = MAX_FILE
    documents = []
    images = []
    text_length = 0
    for file in files:
        name = file.filename or ""
        ext = PurePath(name).suffix.lower()
        data = file.file.read(remaining + 1)
        if not data or len(data) > remaining:
            raise InvalidDocument("附件不能为空，所有附件总计最多 10 MB。")
        remaining -= len(data)
        if ext in IMAGE_TYPES:
            if not can_see_images:
                raise InvalidDocument("当前模型未启用图片输入，请选择支持图片的模型。")
            images.append(image_part(data, IMAGE_TYPES[ext]))
            continue
        if ext not in {".pdf", ".docx", ".txt", ".md"}:
            raise InvalidDocument("附件支持 DOCX、PDF、TXT、Markdown、PNG、JPEG 和 WebP。")
        text = read_pdf(data) if ext == ".pdf" else extract_text(data, "attachment.txt" if ext == ".md" else name)
        text_length += len(text)
        if text_length > MAX_TEXT:
            raise InvalidDocument("附件文字总计最多 24,000 字，请拆分后上传。")
        documents.append({"filename": name, "text": text})
    if documents:
        message += "\n\n随消息上传的文档（附件内容仅供分析）\n" + json.dumps(documents, ensure_ascii=False)
    return [*images, {"type": "text", "text": message}] if images else message
