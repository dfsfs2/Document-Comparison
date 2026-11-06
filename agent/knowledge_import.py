"""Extract plain text for an editable knowledge-rule draft."""
from codecs import BOM_UTF16_BE, BOM_UTF16_LE
import re

from formatter import Document, InvalidDocument, MAX_FILE, W

MAX_EXTRACTED_CHARACTERS = 100_000
TEXT_TAG = W + "t"
SEPARATORS = {W + "tab": "\t", W + "br": "\n", W + "cr": "\n", W + "noBreakHyphen": "\u2011"}


def paragraph_text(paragraph):
    pieces = []
    for element in paragraph.iter(TEXT_TAG, *SEPARATORS):
        if next(element.iterancestors(W + "p"), None) is not paragraph:
            continue
        if any(parent.tag in (W + "del", W + "moveFrom") for parent in element.iterancestors()):
            continue
        pieces.append((element.text or "") if element.tag == TEXT_TAG else SEPARATORS[element.tag])
    return "".join(pieces)


def decode_text(data):
    encodings = ("utf-16",) if data.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
            break
        return text
    raise InvalidDocument("无法读取此 TXT 文件，请将它另存为 UTF-8 编码后重试。")


def extract_text(data, filename):
    if len(data) > MAX_FILE:
        raise InvalidDocument("导入的文档最大支持 10 MB。")
    filename = filename.lower()
    if filename.endswith(".docx"):
        text = "\n".join(paragraph_text(p) for p in Document(data).paragraphs)
    elif filename.endswith(".txt"):
        text = decode_text(data)
    else:
        raise InvalidDocument("请选择 .docx 或 .txt 文件。")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise InvalidDocument("文档没有可提取的文字，请选择含有正文的文件。")
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise InvalidDocument("提取的文字超过 100,000 字，请拆分文档后重试。")
    return text
