"""Build a report from the formatting properties before and after each edit."""
from collections import Counter, defaultdict
import re

LIMIT = 2000
LABELS = {
    "keepNext": "与下段同页", "keepLines": "段中不分页", "widowControl": "孤行控制",
    "pBdr": "段落边框", "shd": "底纹", "p:spacing": "段落间距与行距",
    "ind": "段落缩进", "contextualSpacing": "同样式段落间距", "jc": "对齐方式",
    "textAlignment": "文字垂直对齐", "rFonts": "字体", "b": "加粗", "bCs": "复杂文字加粗",
    "i": "斜体", "iCs": "复杂文字斜体", "color": "文字颜色", "r:spacing": "字符间距",
    "kern": "字距调整阈值", "sz": "字号", "szCs": "复杂文字字号",
    "tblBorders": "表格边框", "tblCellMar": "单元格边距", "pgSz": "纸张尺寸",
    "pgMar": "页边距", "docGrid": "文档网格",
}
TOGGLES = {"keepNext", "keepLines", "widowControl", "contextualSpacing", "b", "bCs", "i", "iCs"}
ALIGNMENT = {"left": "左对齐", "center": "居中", "right": "右对齐", "both": "两端对齐",
             "start": "起始侧对齐", "end": "结束侧对齐", "distribute": "分散对齐",
             "top": "顶端", "bottom": "底端", "baseline": "基线", "auto": "自动"}
EDGES = {"top": "上", "bottom": "下", "left": "左", "right": "右", "start": "起始侧", "end": "结束侧",
         "header": "页眉", "footer": "页脚", "gutter": "装订线", "insideH": "内部横线", "insideV": "内部竖线"}
ROLES = {"title": "总标题", "body": "正文", "list": "列表", "table": "表格正文", "table-header": "表头",
         **{f"heading-{i}": f"{i} 级标题" for i in range(1, 10)}}


def clipped(value, limit):
    return value if len(value) <= limit else value[:limit - 1] + "…"


def local_name(qname):
    return qname.rsplit("}", 1)[-1]


def unit(value, divisor, suffix):
    try:
        return f"{int(value) / divisor:.3f}".rstrip("0").rstrip(".") + suffix
    except (TypeError, ValueError):
        return value or "默认"


def color(value):
    if re.fullmatch(r"[a-fA-F0-9]{6}", value):
        return "#" + value.upper()
    return "自动" if value == "auto" else value


def describe(key, element):
    if element is None:
        return "默认或继承"
    attrs = {local_name(k): v for k, v in element.attrib.items()}
    value = attrs.get("val", "")
    if key in TOGGLES:
        return "关闭" if value in ("0", "false", "off") else "开启"
    if key in ("jc", "textAlignment"):
        return ALIGNMENT.get(value, value) or "默认"
    if key in ("sz", "szCs", "kern"):
        return unit(value, 2, " pt")
    if key == "r:spacing":
        return unit(value, 20, " pt")
    if key == "color":
        return color(value)
    if key == "rFonts":
        fonts = {"ascii": "西文", "hAnsi": "扩展西文", "eastAsia": "东亚文字", "cs": "复杂文字", "hint": "文字类型"}
        return "，".join(f"{fonts.get(name, name)} {font}" for name, font in sorted(attrs.items()))
    if key == "pgSz":
        orientation = {"landscape": "横向", "portrait": "纵向"}
        size = " × ".join(unit(attrs.get(name), 1440 / 2.54, " cm") for name in ("w", "h"))
        return size + ("，" + orientation.get(attrs["orient"], attrs["orient"]) if "orient" in attrs else "")
    if key == "pgMar":
        return "，".join(f"{EDGES.get(name, name)} {unit(v, 1440 / 2.54, ' cm')}" for name, v in sorted(attrs.items()))
    if key == "docGrid":
        modes = {"default": "默认", "lines": "行网格", "linesAndChars": "行和字符网格", "snapToChars": "字符对齐"}
        mode = attrs.get("type", "default")
        return modes.get(mode, mode) + ("，行距 " + unit(attrs["linePitch"], 20, " pt") if "linePitch" in attrs else "")
    if key == "p:spacing":
        fields = {"before": "段前", "after": "段后", "beforeLines": "段前", "afterLines": "段后",
                  "beforeAutospacing": "自动段前", "afterAutospacing": "自动段后"}
        items = []
        for name, v in sorted(attrs.items()):
            if name == "lineRule":
                continue
            if name == "line":
                rule = attrs.get("lineRule", "auto")
                kind = {"auto": "行距", "exact": "固定行距", "atLeast": "最小行距"}.get(rule, rule)
                items.append(f"{kind} " + (unit(v, 240, " 倍") if rule == "auto" else unit(v, 20, " pt")))
            elif name.endswith("Lines"):
                items.append(f"{fields.get(name, name)} {unit(v, 100, ' 行')}")
            elif name.endswith("Autospacing"):
                items.append(f"{fields.get(name, name)} " + ("关闭" if v in ("0", "false", "off") else "开启"))
            else:
                items.append(f"{fields.get(name, name)} {unit(v, 20, ' pt')}")
        return "，".join(items) or "默认"
    if key == "ind":
        fields = EDGES | {"firstLine": "首行", "hanging": "悬挂"}
        return "，".join(
            f"{fields.get(name.removesuffix('Chars'), name)} "
            + (unit(v, 100, " 字符") if name.endswith("Chars") else unit(v, 20, " pt"))
            for name, v in sorted(attrs.items())
        )

    fields = {"val": "样式", "sz": "粗细", "color": "颜色", "fill": "填充", "space": "距离",
              "type": "单位", "w": "宽度"}
    values = {"single": "单线", "double": "双线", "nil": "无", "none": "无", "clear": "纯色",
              "solid": "实色", "dxa": "缇", "auto": "自动"}

    def details(attributes):
        result = []
        for name, v in sorted(attributes.items()):
            if key == "tblCellMar" and name == "type" and v == "dxa":
                continue
            if key == "tblCellMar" and name == "w" and attributes.get("type") == "dxa":
                display = unit(v, 20, " pt")
            elif name == "sz":
                display = unit(v, 8, " pt")
            elif name in ("fill", "color"):
                display = color(v)
            else:
                display = values.get(v, v)
            result.append(f"{fields.get(name, name)} {display}")
        return " ".join(result)

    pieces = [details(attrs)] if attrs else []
    for child in element:
        if not isinstance(child.tag, str):
            continue
        name = local_name(child.tag)
        pieces.append(f"{EDGES.get(name, name)} {details({local_name(k): v for k, v in child.attrib.items()})}")
    return "，".join(pieces) or "已设置"


def signature(element):
    if element is None:
        return None
    return element.tag, tuple(sorted(element.attrib.items())), tuple(signature(child) for child in element)


def differences(before, after, prefix=""):
    for name in dict.fromkeys((*before, *after)):
        key = prefix + name if name == "spacing" else name
        old, new = before.get(name), after.get(name)
        if signature(old) != signature(new):
            old_value, new_value = describe(key, old), describe(key, new)
            if old_value != new_value:
                yield key, clipped(old_value, 180), clipped(new_value, 180)


def property_changes(before, after, prefix=""):
    return [{"property": LABELS[key], "before": old, "after": new}
            for key, old, new in differences(before, after, prefix)]


class RunChanges:
    def __init__(self):
        self.by_property = defaultdict(Counter)

    def add(self, before, after):
        for key, old, new in differences(before, after, "r:"):
            self.by_property[key][old, new] += 1

    def summarize(self):
        changes = []
        for key, transitions in self.by_property.items():
            examples = list(transitions)[:3]
            suffix = f"等 {len(transitions)} 种变化" if len(transitions) > len(examples) else ""
            changes.append({
                "property": LABELS[key],
                "before": "；".join(old for old, _ in examples) + suffix,
                "after": "；".join(new for _, new in examples) + suffix,
                "affectedRuns": sum(transitions.values()),
            })
        return changes


class FormatReport:
    def __init__(self):
        self.counts = {"paragraph": 0, "table": 0, "section": 0}
        self.locations = []

    def add(self, kind, index, reason, changes, text=""):
        if not changes:
            return
        self.counts[kind] += 1
        if len(self.locations) < LIMIT:
            self.locations.append({"kind": kind, "index": index, "reason": reason,
                                   "excerpt": clipped(" ".join(text.split()), 85), "changes": changes})

    def result(self):
        return {"counts": self.counts, "locations": self.locations}
