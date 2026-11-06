"""Transfer visual properties while retaining the target OOXML package."""
from collections import Counter, defaultdict
from copy import deepcopy
from io import BytesIO
from pathlib import PurePosixPath
import re
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from lxml import etree as xml

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MAIN = "word/document.xml"
MAX_FILE = 10 * 1024 * 1024
P = ("keepNext", "keepLines", "widowControl", "pBdr", "shd", "spacing",
     "ind", "contextualSpacing", "jc", "textAlignment")
R = ("rFonts", "b", "bCs", "i", "iCs", "color", "spacing", "kern", "sz", "szCs")
TABLE = ("jc", "tblBorders", "shd", "tblCellMar")
PAGE = ("pgSz", "pgMar")
FONT_SLOTS = (("asciiTheme", "ascii"), ("hAnsiTheme", "hAnsi"),
              ("eastAsiaTheme", "eastAsia"), ("cstheme", "cs"))
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
                names = [entry.filename for entry in entries]
                if (len(entries) > 3000 or len(set(names)) != len(names)
                        or sum(entry.file_size for entry in entries) > 96 * 1024 * 1024
                        or any(entry.flag_bits & 1 or PurePosixPath(entry.filename).is_absolute()
                               or ".." in PurePosixPath(entry.filename).parts
                               or "\\" in entry.filename for entry in entries)):
                    raise InvalidDocument("文档包过大、已加密或包含非法部件。")
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
        except (BadZipFile, KeyError, xml.XMLSyntaxError, NotImplementedError, OSError) as error:
            raise InvalidDocument("无法读取文档。请另存为标准 .docx 后重试。") from error
        self.data = data
        self.style_map = {s.get(W + "styleId"): s for s in self.styles.findall(W + "style")}
        self.defaults = prop(self.styles, "docDefaults")
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
                content = (xml.tostring(self.root, encoding="UTF-8", xml_declaration=True)
                           if entry.filename == MAIN else source.read(entry))
                target.writestr(entry.filename, content)
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


def materialize_theme(properties, theme):
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
                element = theme.find(f".//{A}{family}/{A}font[@script='Hans']")
                typeface = element.get("typeface") if element is not None else ""
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


def style_for(document, paragraph):
    pp = prop(paragraph, "pPr")
    styles = document.chain(val(pp, "pStyle") or document.default_p)
    style_p = [prop(style, "pPr") for style in styles]
    paragraph_props = merge([prop(prop(document.defaults, "pPrDefault"), "pPr"),
                             *style_p, pp], P)
    if "jc" not in paragraph_props:
        paragraph_props["jc"] = node("jc", val="left")
    if "ind" not in paragraph_props:
        paragraph_props["ind"] = node("ind", left="0", right="0", firstLine="0")
    paragraph_props.setdefault("spacing", node("spacing", before="0", after="0", line="240", lineRule="auto"))
    # The dominant run represents the template's baseline rather than its short inline emphasis.
    run = max(runs_of(paragraph), key=lambda r: len(text_of(r)), default=None)
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
    return materialize_theme(paragraph_props, document.theme), materialize_theme(merged_run, document.theme)


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


def paragraph_roles(document, advisor):
    roles = [classify(document, p) for p in document.paragraphs]
    snippets = {}
    for i, paragraph in enumerate(document.paragraphs):
        sample = text_of(paragraph).strip()
        if roles[i] == "body" and 0 < len(sample) <= 90:
            if i == 0 and len(sample) <= 45 and not re.search(r"[。！？.!?；;：:]", sample):
                roles[i] = "title"
            elif len(snippets) < 32:
                snippets[i] = sample
    for index, role in advisor.classify(snippets).items():
        roles[index] = role
    return roles


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


def format_document(reference_data, target_data, advisor):
    reference, target = Document(reference_data), Document(target_data)
    reference_roles = paragraph_roles(reference, advisor)
    target_roles = paragraph_roles(target, advisor)
    candidates = defaultdict(list)
    for paragraph, role in zip(reference.paragraphs, reference_roles):
        if text_of(paragraph).strip():
            candidates[role].append(paragraph)
    if not candidates:
        raise InvalidDocument("参照文档需要至少一个包含文字的段落。")
    samples = {}
    for role, paragraphs in candidates.items():
        # Repeated formats take precedence over a one-off direct override.
        styles = [style_for(reference, p) for p in paragraphs[:80]]
        signatures = [tuple((key, xml.tostring(value)) for part in style
                            for key, value in sorted(part.items())) for style in styles]
        most_common = Counter(signatures).most_common(1)[0][0]
        samples[role] = styles[signatures.index(most_common)]

    before = content_projection(target.root)
    changed_paragraphs = 0
    for paragraph, role in zip(target.paragraphs, target_roles):
        runs = runs_of(paragraph)
        if not runs:
            continue
        sample = samples.get(role)
        if sample is None and role == "table-header":
            sample = samples.get("table")
        if sample is None and role == "list":
            sample = samples.get("body")
        if sample is None:
            continue
        paragraph_props, run_props = sample
        keys = tuple(key for key in P if key != "ind" or role != "list")
        replace_properties(paragraph, "pPr", {k: v for k, v in paragraph_props.items() if k in keys}, keys)
        mixed = {key for key in ("b", "bCs", "i", "iCs")
                 if 0 < sum(prop(prop(run, "rPr"), key) is not None for run in runs) < len(runs)}
        for run in runs:
            direct = prop(run, "rPr")
            keep = mixed.copy()
            if next(run.iterancestors(W + "hyperlink"), None) is not None:
                keep.add("color")
            replace_properties(run, "rPr", {k: v for k, v in run_props.items() if k not in keep},
                               tuple(key for key in R if key not in keep))
        changed_paragraphs += 1

    ref_tables = list(reference.body.iter(W + "tbl"))
    changed_tables = 0
    if ref_tables:
        for index, table in enumerate(target.body.iter(W + "tbl")):
            sample = prop(ref_tables[min(index, len(ref_tables) - 1)], "tblPr")
            table_style = reference.chain(val(sample, "tblStyle"))
            props = merge([*(prop(s, "tblPr") for s in table_style), sample], TABLE)
            if props:
                replace_properties(table, "tblPr", materialize_theme(props, reference.theme), tuple(props))
                changed_tables += 1

    def sections(document):
        return [s for s in document.body.iter(W + "sectPr")
                if s.getparent().tag in (W + "body", W + "pPr")]
    reference_sections = sections(reference)
    if reference_sections:
        for index, section in enumerate(sections(target)):
            props = merge([reference_sections[min(index, len(reference_sections) - 1)]], PAGE)
            replace_group(section, "sectPr", props, tuple(props))

    if before != content_projection(target.root):
        raise RuntimeError("Non-formatting content changed")
    return target.serialize(), {
        "paragraphs": changed_paragraphs, "tables": changed_tables,
        "mode": advisor.mode, "rules": sorted(advisor.rules),
    }
