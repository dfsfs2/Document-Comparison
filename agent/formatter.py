"""Transfer visual properties while retaining the target OOXML package."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import re
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from lxml import etree as xml

from format_report import FormatReport, ROLES, RunChanges, property_changes, signature

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MAIN = "word/document.xml"
MAX_FILE = 10 * 1024 * 1024
P = ("keepNext", "keepLines", "widowControl", "pBdr", "shd", "spacing",
     "ind", "contextualSpacing", "jc", "textAlignment")
R = ("rFonts", "b", "bCs", "i", "iCs", "color", "spacing", "kern", "sz", "szCs")
TABLE = ("jc", "tblBorders", "shd", "tblCellMar")
PAGE = ("pgSz", "pgMar", "docGrid")
FONT_SLOTS = (("asciiTheme", "ascii"), ("hAnsiTheme", "hAnsi"),
              ("eastAsiaTheme", "eastAsia"), ("cstheme", "cs"))
EAST_ASIAN_SCRIPTS = {
    "zh-cn": ("Hans", "86"), "zh-sg": ("Hans", "86"), "zh-hans": ("Hans", "86"),
    "zh-tw": ("Hant", "88"), "zh-hk": ("Hant", "88"), "zh-mo": ("Hant", "88"), "zh-hant": ("Hant", "88"),
    "ja-jp": ("Jpan", "80"), "ko-kr": ("Hang", "81"),
}
TITLE_LABEL = re.compile(r"^(?:[（(]\s*(?:论文)?题\s*目\s*[）)]|(?:论文)?题\s*目\s*[:：])")
AUTHOR_LABEL = re.compile(r"\s+(?=[（(]\s*作\s*者\s*[）)])")
NUMBERED_HEADING = re.compile(r"([1-9][0-9]?(?:[.．][0-9]{1,2}){0,8})([.．、)）]?\s*)([^\W\d_].*)")
STRUCTURAL_ROLES = {"table", "table-header", "list"}
MAX_STYLE_SAMPLES = 32
MAX_MATCH_PARAGRAPHS = 32
PROPERTY_GROUPS = {"pPr": P, "rPr": R, "tblPr": TABLE, "sectPr": PAGE}
# Full sibling order for ordered property groups.
ORDERS = {
    "pPr": ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr",
            "widowControl", "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs",
            "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct", "topLinePunct",
            "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid",
            "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap",
            "jc", "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl",
            "divId", "cnfStyle", "rPr", "sectPr", "pPrChange"),
    "tblPr": ("tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
              "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders",
              "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription",
              "tblPrChange"),
    "sectPr": ("headerReference", "footerReference", "footnotePr", "endnotePr", "type",
               "pgSz", "pgMar", "paperSrc", "pgBorders", "lnNumType", "pgNumType",
               "cols", "formProt", "vAlign", "noEndnote", "titlePg", "textDirection",
               "bidi", "rtlGutter", "docGrid", "printerSettings", "sectPrChange"),
}


class InvalidDocument(ValueError):
    pass


def package_part_name(entry):
    name = entry.orig_filename
    # Some Word producers write Windows separators in empty ZIP directory markers.
    if entry.file_size == 0 and name.endswith(("/", "\\")):
        name = name.replace("\\", "/")
    components = name.removesuffix("/").split("/")
    if ("\\" in name or "\x00" in name or re.match(r"^[A-Za-z]:", name)
            or any(part in ("", ".", "..") for part in components)):
        raise InvalidDocument("文档包内有不受支持的部件路径，请另存为标准 .docx 后重试。")
    return name


def node(name, **values):
    element = xml.Element(W + name)
    for key, value in values.items():
        element.set(W + key, value)
    return element


def prop(parent, name):
    return parent.find(W + name) if parent is not None else None


def val(parent, name):
    child = prop(parent, name)
    return child.get(W + "val", "") if child is not None else ""


def parse(content):
    root = xml.fromstring(content, parser=xml.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False))
    if root.getroottree().docinfo.doctype:
        raise InvalidDocument("文档的 XML 包含不受支持的 DTD。")
    return root


class Document:
    def __init__(self, data):
        try:
            if len(data) > MAX_FILE:
                raise InvalidDocument("每个文档最大支持 10 MB。")
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 3000:
                    raise InvalidDocument("文档包内部件数量超过 3000 个。")
                if sum(entry.file_size for entry in entries) > 96 * 1024 * 1024:
                    raise InvalidDocument("文档包解压后超过 96 MB。")
                if any(entry.flag_bits & 1 for entry in entries):
                    raise InvalidDocument("文档包内有加密部件，请解除加密后重试。")
                names = {package_part_name(entry) for entry in entries}
                if len(names) != len(entries):
                    raise InvalidDocument("文档包内有重名部件，请另存为标准 .docx 后重试。")
                content_types = parse(archive.read("[Content_Types].xml"))
                if not any(item.get("PartName") == "/" + MAIN and item.get("ContentType") ==
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                           for item in content_types):
                    raise InvalidDocument("请上传标准的 .docx 文档。")
                self.root = parse(archive.read(MAIN))
                self.body = prop(self.root, "body")
                if self.root.tag != W + "document" or self.body is None:
                    raise InvalidDocument("文档缺少可识别的 Word 正文。")
                self.styles = self.read_optional(archive, "word/styles.xml")
                self.theme = self.read_optional(archive, "word/theme/theme1.xml")
                font_table = self.read_optional(archive, "word/fontTable.xml")
        except (BadZipFile, KeyError, xml.XMLSyntaxError, NotImplementedError, OSError) as error:
            raise InvalidDocument("无法读取文档。请另存为标准 .docx 后重试。") from error
        self.data = data
        self.style_map = {s.get(W + "styleId"): s for s in self.styles.findall(W + "style")}
        self.defaults = prop(self.styles, "docDefaults")
        language = prop(prop(prop(self.defaults, "rPrDefault"), "rPr"), "lang")
        locale = language.get(W + "eastAsia", "").lower() if language is not None else ""
        self.east_asia_script, charset = EAST_ASIAN_SCRIPTS.get(locale, ("Hans", None))
        matching_fonts = {font.get(W + "name") for font in font_table.findall(W + "font")
                          if font.get(W + "name") and val(font, "charset").lower() == charset}
        self.east_asia_font = next(iter(matching_fonts)) if len(matching_fonts) == 1 else ""
        self.default_p = next((s.get(W + "styleId") for s in self.style_map.values()
                               if s.get(W + "type") == "paragraph"
                               and s.get(W + "default") in ("1", "true")), "")
        self.paragraphs = [
            p for p in self.body.iter(W + "p")
            if not any(a.tag in (W + "p", W + "del", W + "moveFrom") for a in p.iterancestors())
        ]
        if len(self.paragraphs) > 12000:
            raise InvalidDocument("文档超过 12000 个段落。")

    @staticmethod
    def read_optional(archive, path):
        try:
            return parse(archive.read(path))
        except KeyError:
            return node("empty")

    def chain(self, style_id):
        seen = set()
        styles = []
        while style_id in self.style_map and style_id not in seen:
            seen.add(style_id)
            style = self.style_map[style_id]
            styles.append(style)
            style_id = val(style, "basedOn")
        return styles[::-1]

    def serialize(self):
        output = BytesIO()
        with ZipFile(BytesIO(self.data)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
            for entry in source.infolist():
                name = package_part_name(entry)
                content = (xml.tostring(self.root, encoding="UTF-8", xml_declaration=True)
                           if name == MAIN else source.read(entry))
                target.writestr(name, content)
        if output.tell() > 16 * 1024 * 1024:
            raise InvalidDocument("生成的文档超过 16 MB。")
        return output.getvalue()


def text_of(element):
    return "".join(part.text or "" for part in element.iter(W + "t"))


def runs_of(paragraph):
    return [run for run in paragraph.iter(W + "r")
            if next(run.iterancestors(W + "p")) is paragraph
            and any(child.tag == W + "t" for child in run)
            and not any(parent.tag in (W + "del", W + "moveFrom") for parent in run.iterancestors())]


def title_author_runs(paragraph):
    text = text_of(paragraph)
    match = AUTHOR_LABEL.search(text) if TITLE_LABEL.match(text.lstrip()) else None
    if match is None:
        return []
    runs = runs_of(paragraph)
    if "".join(map(text_of, runs)) != text:
        return []
    offset = match.start()
    for index, run in enumerate(runs):
        length = len(text_of(run))
        if offset == 0:
            return runs[index:]
        if offset < length:
            parts = run.findall(W + "t")
            if (len(parts) != 1 or any(child.tag not in (W + "rPr", W + "t") for child in run)
                    or prop(prop(run, "rPr"), "rPrChange") is not None):
                return []
            tail = deepcopy(run)
            tail_text = prop(tail, "t")
            tail_text.text = parts[0].text[offset:]
            tail_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            parts[0].text = parts[0].text[:offset]
            run.addnext(tail)
            if text_of(paragraph) != text:
                raise RuntimeError("Title formatting changed document text")
            return [tail, *runs[index + 1:]]
        offset -= length
    return []


def merge(groups, keys, *, toggles=False):
    result = {}
    for group in groups:
        if group is None:
            continue
        for name in keys:
            child = prop(group, name)
            if child is None:
                continue
            previous = result.get(name)
            if toggles and name in ("b", "bCs", "i", "iCs"):
                if child.get(W + "val") not in ("0", "false", "off"):
                    active = previous is not None and previous.get(W + "val") not in ("0", "false", "off")
                    result[name] = node(name, val="0" if active else "1")
                continue
            if previous is not None and name in ("spacing", "ind", "rFonts"):
                combined = deepcopy(previous)
                if name == "rFonts":
                    for theme, explicit in FONT_SLOTS:
                        if W + explicit in child.attrib and W + theme not in child.attrib:
                            combined.attrib.pop(W + theme, None)
                combined.attrib.update(child.attrib)
                result[name] = combined
            else:
                result[name] = deepcopy(child)
    return result


def materialize_theme(properties, document):
    theme = document.theme
    fonts = properties.get("rFonts")
    if fonts is not None:
        for attribute, slot in FONT_SLOTS:
            value = fonts.attrib.pop(W + attribute, "")
            if not value:
                continue
            family = "majorFont" if value.startswith("major") else "minorFont"
            script = "ea" if slot == "eastAsia" else "cs" if slot == "cs" else "latin"
            element = theme.find(f".//{A}{family}/{A}{script}")
            typeface = element.get("typeface") if element is not None else ""
            if slot == "eastAsia" and not typeface:
                element = theme.find(f".//{A}{family}/{A}font[@script='{document.east_asia_script}']")
                typeface = (element.get("typeface") if element is not None else ""
                            ) or fonts.get(W + slot) or document.east_asia_font
            if typeface:
                fonts.set(W + slot, typeface)
    color_aliases = {"text1": "dk1", "text2": "dk2", "background1": "lt1",
                     "background2": "lt2", "dark1": "dk1", "dark2": "dk2",
                     "light1": "lt1", "light2": "lt2", "hyperlink": "hlink",
                     "followedHyperlink": "folHlink"}
    for element in properties.values():
        for child in element.iter():
            for kind, fallback in (("themeColor", "color" if child.tag != W + "color" else "val"),
                                   ("themeFill", "fill")):
                theme_name = child.attrib.pop(W + kind, "")
                tint = child.attrib.pop(W + ("themeTint" if kind == "themeColor" else "themeFillTint"), "")
                shade = child.attrib.pop(W + ("themeShade" if kind == "themeColor" else "themeFillShade"), "")
                if not theme_name:
                    continue
                key = color_aliases.get(theme_name, theme_name)
                if not re.fullmatch(r"[a-zA-Z0-9]+", key):
                    continue
                entry = theme.find(f".//{A}clrScheme/{A}{key}")
                if entry is None or len(entry) == 0:
                    continue
                rgb = entry[0].get("lastClr") or entry[0].get("val", "")
                if re.fullmatch(r"[a-fA-F0-9]{6}", rgb):
                    channels = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)]
                    if re.fullmatch(r"[a-fA-F0-9]{2}", shade):
                        channels = [round(c * int(shade, 16) / 255) for c in channels]
                    if re.fullmatch(r"[a-fA-F0-9]{2}", tint):
                        channels = [round(255 - (255 - c) * int(tint, 16) / 255) for c in channels]
                    child.set(W + fallback, "".join(f"{c:02X}" for c in channels))
    return properties


def paragraph_properties(document, pp, styles):
    style_p = [prop(style, "pPr") for style in styles]
    paragraph_props = merge([prop(prop(document.defaults, "pPrDefault"), "pPr"),
                             *style_p, pp], P)
    if "jc" not in paragraph_props:
        paragraph_props["jc"] = node("jc", val="left")
    if "ind" not in paragraph_props:
        paragraph_props["ind"] = node("ind", left="0", right="0", firstLine="0")
    spacing = paragraph_props.setdefault("spacing", node("spacing"))
    # Omitted values would inherit the target's line and automatic paragraph spacing.
    for attr, default in (("before", "0"), ("after", "0"), ("line", "240"), ("lineRule", "auto"),
                          ("beforeAutospacing", "0"), ("afterAutospacing", "0")):
        if spacing.get(W + attr) is None:
            spacing.set(W + attr, default)
    return materialize_theme(paragraph_props, document)


def run_properties(document, run, styles):
    direct = prop(run, "rPr")
    character = document.chain(val(direct, "rStyle"))
    run_defaults = prop(prop(document.defaults, "rPrDefault"), "rPr")
    inherited = merge([run_defaults, *(prop(s, "rPr") for s in styles + character)], R)
    toggles = merge([prop(s, "rPr") for s in styles + character], ("b", "bCs", "i", "iCs"), toggles=True)
    inherited.update(toggles)
    merged_run = merge([group_for("rPr", inherited), direct], R)
    for key, default in (("b", "0"), ("bCs", "0"), ("i", "0"), ("iCs", "0"),
                         ("color", "auto"), ("sz", "22"), ("spacing", "0"), ("kern", "0")):
        merged_run.setdefault(key, node(key, val=default))
    merged_run.setdefault("szCs", node("szCs", val=merged_run["sz"].get(W + "val")))
    return materialize_theme(merged_run, document)


def style_for(document, paragraph):
    pp = prop(paragraph, "pPr")
    styles = document.chain(val(pp, "pStyle") or document.default_p)
    # The dominant run represents the template's baseline rather than its short inline emphasis.
    run = max(runs_of(paragraph), key=lambda r: len(text_of(r)), default=None)
    return paragraph_properties(document, pp, styles), run_properties(document, run, styles)


def table_properties(document, table):
    direct = prop(table, "tblPr")
    styles = document.chain(val(direct, "tblStyle"))
    properties = merge([*(prop(style, "tblPr") for style in styles), direct], TABLE)
    return materialize_theme(properties, document)


def group_for(name, properties):
    group = node(name)
    group.extend(deepcopy(list(properties.values())))
    return group


def classify(document, paragraph):
    if any(a.tag == W + "tc" for a in paragraph.iterancestors()):
        row = next(paragraph.iterancestors(W + "tr"), None)
        header = prop(prop(row, "trPr"), "tblHeader")
        return "table-header" if header is not None and header.get(W + "val") not in ("0", "false", "off") else "table"
    pp = prop(paragraph, "pPr")
    style_id = val(pp, "pStyle") or document.default_p
    styles = document.chain(style_id)
    for group in [pp, *(prop(s, "pPr") for s in reversed(styles))]:
        level = val(group, "outlineLvl")
        if level in "012345678" and len(level) == 1:
            return f"heading-{int(level) + 1}"
        if level == "9":
            break
    names = [style_id, *(val(s, "name") for s in reversed(styles))]
    for name in names:
        match = re.fullmatch(r"(?:heading|标题)\s*([1-9])", name, re.IGNORECASE)
        if match:
            return f"heading-{match[1]}"
        if name.lower() in ("title", "文档标题", "标题"):
            return "title"
    if any(prop(group, "numPr") is not None for group in [pp, *(prop(s, "pPr") for s in styles)]):
        return "list"
    return "body"


def numbered_heading(sample):
    if len(sample) > 72 or re.search(r"[。！？!?；;\r\n]|[.]$", sample):
        return None
    match = NUMBERED_HEADING.fullmatch(sample)
    if match is None:
        return None
    number, separator, title = match.groups()
    depth = len(re.split(r"[.．]", number))
    if not separator and (depth == 1 or not re.match(r"[\u3400-\u9fff]", title)):
        return None
    return depth


def paragraph_roles(document, texts):
    roles = [classify(document, p) for p in document.paragraphs]
    numbered = {i: depth for i, (role, sample) in enumerate(zip(roles, texts))
                if role == "body" and (depth := numbered_heading(sample)) is not None}
    # Repeated numbering gives stronger evidence of headings than an isolated short numeric phrase.
    if len(numbered) < 2:
        numbered.clear()
    first_text = next((i for i, sample in enumerate(texts) if sample), None)
    for i, sample in enumerate(texts):
        if roles[i] != "body" or not sample:
            continue
        if i in numbered:
            roles[i] = f"heading-{numbered[i]}"
        elif i == first_text and len(sample) <= 120 and TITLE_LABEL.match(sample):
            roles[i] = "title"
    return roles, numbered


def word_style(document, paragraph):
    style_id = val(prop(paragraph, "pPr"), "pStyle") or document.default_p
    return val(document.style_map.get(style_id), "name")


def style_hint(properties):
    paragraph, run = properties
    return {
        "alignment": paragraph["jc"].get(W + "val"),
        "fontSizeHalfPoints": run["sz"].get(W + "val"),
        "bold": run["b"].get(W + "val") not in ("0", "false", "off"),
    }


@dataclass
class TemplateSample:
    index: int
    role: str
    name: str
    properties: tuple
    count: int = 1
    prose_count: int = 0

    @property
    def id(self):
        return f"s{self.index}"

    @property
    def label(self):
        name = (self.name or ROLES[self.role])[:80]
        return f"第 {self.index} 个段落的{name}样式"


def template_samples(reference, texts, roles):
    variants = {}
    for index, (paragraph, text, role) in enumerate(zip(reference.paragraphs, texts, roles), 1):
        if not text:
            continue
        name = word_style(reference, paragraph)
        properties = style_for(reference, paragraph)
        key = (role, name.casefold(), tuple(tuple(signature(item) for _, item in sorted(group.items()))
                                          for group in properties))
        if key in variants:
            variants[key].count += 1
        else:
            variants[key] = TemplateSample(index, role, name, properties)
        if role == "body" and (len(text) > 90 or re.search(r"[。！？.!?]$", text)):
            variants[key].prose_count += 1
    by_role = defaultdict(list)
    for sample in variants.values():
        by_role[sample.role].append(sample)
    for group in by_role.values():
        group.sort(key=lambda sample: (-sample.prose_count, -sample.count))
    return list(variants.values()), by_role


def local_match(role, name, text, level, by_role, by_name, cover):
    compatible = [sample for sample in by_name.get(name.casefold(), ())
                  if sample.role == role or role == "body" and sample.role not in STRUCTURAL_ROLES]
    if len(compatible) == 1:
        return compatible[0], "Word 样式名称一致。"
    if len(compatible) > 1 and (role != "body" or len(text) <= 90):
        return None, "参照文档的同名 Word 样式出现了不同的排版，需确认匹配项。"
    shifted = "heading-1" if role == "title" else f"heading-{level + 1}" if level else ""
    available = (by_role.get(shifted) if cover else None) or by_role.get(role)
    available = available or by_role.get({"table-header": "table", "list": "body"}.get(role))
    if not available:
        return None, f"参照文档中未找到可确认的{ROLES[role]}样式。"
    if role == "body" and not compatible and len(text) <= 90 and not re.search(r"[。！？.!?；;：:]", text):
        if any(key not in STRUCTURAL_ROLES | {"body"} for key in by_role) or len(available) > 1:
            return None, "短段落的用途不明确，参照文档有多种可用样式。"
    if role not in STRUCTURAL_ROLES | {"body"} and len(available) > 1 and available[0].count == available[1].count:
        return None, "参照文档同一层级有多个样式样本，无法确认应套用哪一个。"
    if level:
        return available[0], "按段落编号和参照文档的标题层级匹配。"
    return available[0], f"按目标段落的{ROLES[role]}角色匹配。"


def match_paragraphs(reference, target, advisor):
    ref_texts = [text_of(p).strip() for p in reference.paragraphs]
    ref_roles, _ = paragraph_roles(reference, ref_texts)
    samples, by_role = template_samples(reference, ref_texts, ref_roles)
    if not samples:
        raise InvalidDocument("参照文档需要至少一个包含文字的段落。")
    cover = (samples[0].role == "heading-1" and by_role["heading-1"][0].count == 1
             and len(by_role["heading-1"]) == 1 and "title" not in by_role and "heading-2" in by_role
             and numbered_heading(ref_texts[samples[0].index - 1]) is None)
    by_name = defaultdict(list)
    for sample in samples:
        if sample.name:
            by_name[sample.name.casefold()].append(sample)

    texts = [text_of(p).strip() for p in target.paragraphs]
    roles, numbered = paragraph_roles(target, texts)
    decisions = {}
    queued = []
    neighbors = [index for index, text in enumerate(texts) if text]
    default_name = val(target.style_map.get(target.default_p), "name")
    for position, index in enumerate(neighbors):
        paragraph = target.paragraphs[index]
        role, text = roles[index], texts[index]
        name = word_style(target, paragraph)
        sample, reason = local_match(role, name, text, numbered.get(index), by_role, by_name, cover)
        decisions[index] = (sample, f"本地匹配参照文档{sample.label}。{reason}" if sample else reason)
        custom_style = name and name != default_name and name.casefold() not in by_name
        if role not in STRUCTURAL_ROLES and (sample is None or role != "body" or len(text) <= 120 or position == 0 or custom_style):
            priority = 0 if sample is None else 1 if role != "body" or position == 0 else 2
            queued.append((priority, index, position, name))
    if not advisor.use_cloud or not advisor.client.key or not queued:
        return roles, decisions, by_role, cover

    defaults = {group[0].id for group in by_role.values()}
    available = sorted((s for s in samples if s.role not in STRUCTURAL_ROLES),
                       key=lambda s: (s.id not in defaults, -s.count, s.index))[:MAX_STYLE_SAMPLES]
    catalog = [{
        "styleId": s.id, "referenceParagraph": s.index, "wordStyle": s.name[:80],
        "roleHint": s.role, "occurrences": s.count, "example": ref_texts[s.index - 1][:90],
        "outlineLevel": int(s.role.removeprefix("heading-")) if s.role.startswith("heading-") else None,
        **style_hint(s.properties),
    } for s in sorted(available, key=lambda s: s.index)]
    inputs = {}
    for _, index, position, name in sorted(queued)[:MAX_MATCH_PARAGRAPHS]:
        inputs[index + 1] = {
            "text": texts[index][:90], "length": len(texts[index]), "wordStyle": name[:80],
            "roleHint": roles[index], "numberDepth": numbered.get(index),
            "previous": texts[neighbors[position - 1]][:60] if position else "",
            "next": texts[neighbors[position + 1]][:60] if position + 1 < len(neighbors) else "",
            **style_hint(style_for(target, target.paragraphs[index])),
        }
    matched = advisor.match(catalog, inputs)
    if matched is not None:
        by_id = {sample.id: sample for sample in available}
        for index in inputs:
            style_id, confidence, reason = matched.get(index, (None, "low", "百炼未返回有效匹配。"))
            sample = by_id.get(style_id)
            if sample is not None and confidence == "high":
                decisions[index - 1] = (sample, f"百炼匹配参照文档{sample.label}。" + reason)
            else:
                suggestion = f"可复核参照文档{sample.label}。" if sample else ""
                decisions[index - 1] = (None, (reason or "百炼无法确认对应样式。") + suggestion)
    return roles, decisions, by_role, cover


def replace_properties(parent, name, properties, keys):
    group = prop(parent, name)
    if group is None:
        if not properties:
            return
        group = node(name)
        parent.insert(0, group)
    replace_group(group, name, properties, keys)


def replace_group(group, name, properties, keys):
    for child in list(group):
        if child.tag in {W + key for key in keys}:
            group.remove(child)
    order = ORDERS.get(name)
    if order is None:
        after_style = int(len(group) > 0 and group[0].tag == W + "rStyle")
        for index, child in enumerate(properties.values(), after_style):
            group.insert(index, deepcopy(child))
        return
    positions = {W + key: index for index, key in enumerate(order)}
    for key, child in properties.items():
        rank = positions[W + key]
        index = next((i for i, old in enumerate(group) if positions.get(old.tag, -1) > rank), len(group))
        group.insert(index, deepcopy(child))


def content_projection(root):
    """Compare all remaining nodes and attributes after excluding transferred formatting."""
    copy = deepcopy(root)
    locations = {
        "pPr": copy.iter(W + "pPr"), "rPr": copy.iter(W + "rPr"),
        "tblPr": copy.iter(W + "tblPr"), "sectPr": copy.iter(W + "sectPr"),
    }
    for group, elements in locations.items():
        for element in list(elements):
            parent = element.getparent()
            if group != "sectPr" and parent.tag != W + {"pPr": "p", "rPr": "r", "tblPr": "tbl"}[group]:
                continue
            for child in list(element):
                if child.tag in {W + key for key in PROPERTY_GROUPS[group]}:
                    element.remove(child)
            if group != "sectPr" and len(element) == 0 and not element.attrib and not (element.text or "").strip():
                parent.remove(element)
    return xml.tostring(copy, method="c14n")


def load_document(data, role):
    try:
        return Document(data)
    except InvalidDocument as error:
        raise InvalidDocument(f"{role}：{error}") from error


def format_document(reference_data, target_data, advisor):
    reference = load_document(reference_data, "参照文档")
    target = load_document(target_data, "目标文档")
    target_roles, decisions, by_role, cover = match_paragraphs(reference, target, advisor)

    author_runs = set()
    body = next(iter(by_role.get("body", ())), None)
    if body:
        for index, (sample, _) in decisions.items():
            if sample and (sample.role == "title" or cover and sample.role == "heading-1" and target_roles[index] == "title"):
                author_runs.update(title_author_runs(target.paragraphs[index]))
    before = content_projection(target.root)
    report = FormatReport()
    changed_paragraphs = 0
    for index, (paragraph, role) in enumerate(zip(target.paragraphs, target_roles), 1):
        runs = runs_of(paragraph)
        if not runs or index - 1 not in decisions:
            continue
        sample, reason = decisions[index - 1]
        if sample is None:
            report.add_review(index, reason, text_of(paragraph))
            continue
        paragraph_props, run_props = sample.properties
        direct = prop(paragraph, "pPr")
        styles = target.chain(val(direct, "pStyle") or target.default_p)
        old_paragraph = paragraph_properties(target, direct, styles)
        keys = tuple(key for key in P if key != "ind" or role != "list")
        replace_properties(paragraph, "pPr", {k: v for k, v in paragraph_props.items() if k in keys}, keys)
        mixed = {key for key in ("b", "bCs", "i", "iCs")
                 if 0 < sum(prop(prop(run, "rPr"), key) is not None for run in runs) < len(runs)}
        run_changes = RunChanges()
        for run in runs:
            old_run = run_properties(target, run, styles)
            run_sample = body.properties[1] if run in author_runs else run_props
            keep = mixed.copy()
            if next(run.iterancestors(W + "hyperlink"), None) is not None:
                keep.add("color")
            replace_properties(run, "rPr", {k: v for k, v in run_sample.items() if k not in keep},
                               tuple(key for key in R if key not in keep))
            run_changes.add(old_run, run_properties(target, run, styles))
        changes = property_changes(old_paragraph, paragraph_properties(target, prop(paragraph, "pPr"), styles), "p:")
        changes.extend(run_changes.summarize())
        if author_runs.intersection(runs):
            reason += "同段的作者信息使用参照文档的正文字符格式。"
        report.add("paragraph", index, reason, changes, text_of(paragraph))
        changed_paragraphs += 1

    ref_tables = list(reference.body.iter(W + "tbl"))
    changed_tables = 0
    if ref_tables:
        for index, table in enumerate(target.body.iter(W + "tbl")):
            source_index = min(index, len(ref_tables) - 1)
            props = table_properties(reference, ref_tables[source_index])
            if props:
                old_table = table_properties(target, table)
                replace_properties(table, "tblPr", props, tuple(props))
                reason = f"按表格顺序套用参照文档第 {source_index + 1} 个表格的样式。"
                if source_index < index:
                    reason = f"参照文档没有同序表格，沿用它的最后一个表格（第 {source_index + 1} 个）的样式。"
                report.add("table", index + 1, reason, property_changes(old_table, table_properties(target, table)))
                changed_tables += 1

    def sections(document):
        return [s for s in document.body.iter(W + "sectPr")
                if s.getparent().tag in (W + "body", W + "pPr")]
    reference_sections = sections(reference)
    if reference_sections:
        for index, section in enumerate(sections(target)):
            source_index = min(index, len(reference_sections) - 1)
            old_section = merge([section], PAGE)
            props = merge([reference_sections[source_index]], PAGE)
            replace_group(section, "sectPr", props, PAGE)
            reason = f"按节的顺序套用参照文档第 {source_index + 1} 节的页面设置。"
            if source_index < index:
                reason = f"参照文档没有同序的节，沿用它的最后一节（第 {source_index + 1} 节）的页面设置。"
            report.add("section", index + 1, reason, property_changes(old_section, merge([section], PAGE)))

    if before != content_projection(target.root):
        raise RuntimeError("Non-formatting content changed")
    return target.serialize(), {
        "paragraphs": changed_paragraphs, "tables": changed_tables,
        "mode": advisor.mode, "rules": sorted(advisor.rules), "report": report.result(),
    }
