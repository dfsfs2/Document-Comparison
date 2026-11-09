from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from advisor import Advisor
from knowledge_import import extract_text
from formatter import A, Document, InvalidDocument, W, content_projection, format_document, prop, runs_of, text_of, val

HEADER = b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Original header</w:t></w:r></w:p></w:hdr>'
TYPES = """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
</Types>"""
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REFERENCE_STYLES = """
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
  <w:pPr><w:spacing w:after="160"/></w:pPr>
  <w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="宋体"/><w:sz w:val="24"/></w:rPr>
</w:style>
<w:style w:type="paragraph" w:styleId="ReportHeading">
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:jc w:val="center"/><w:outlineLvl w:val="0"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="36"/></w:rPr>
</w:style>
"""
REFERENCE_BODY = """
<w:p><w:pPr><w:pStyle w:val="ReportHeading"/></w:pPr><w:r><w:t>参照标题</w:t></w:r></w:p>
<w:p><w:r><w:t>这里是参照样式示例。只读取格式。</w:t></w:r></w:p>
<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="8"/></w:tblBorders></w:tblPr>
<w:tblGrid><w:gridCol w:w="2400"/></w:tblGrid><w:tr><w:tc>
<w:p><w:r><w:t>参照表格</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:sectPr><w:pgMar w:top="1200" w:right="1100" w:bottom="1200" w:left="1100"/></w:sectPr>
"""
TARGET_BODY = """
<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>保留这个标题</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">  原正文有空格。</w:t><w:tab/><w:br/></w:r>
<w:hyperlink r:id="rLink"><w:r><w:rPr><w:color w:val="0000FF"/></w:rPr><w:t>原链接</w:t></w:r></w:hyperlink></w:p>
<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid><w:tr><w:tc>
<w:p><w:r><w:t>客户编号 173</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:sectPr><w:headerReference w:type="default" r:id="rHeader"/><w:pgMar w:top="1440" w:left="1440"/></w:sectPr>
"""


def package(body, styles="", extra_parts=()):
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("[Content_Types].xml", TYPES)
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{RELS}">'
                         f'<Relationship Id="rDoc" Type="{REL_TYPE}/officeDocument" Target="word/document.xml"/>'
                         '</Relationships>')
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W[1:-1]}" xmlns:r="{REL_TYPE}">'
                         f'<w:body>{body}</w:body></w:document>')
        archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{W[1:-1]}">{styles}</w:styles>')
        archive.writestr("word/header1.xml", HEADER)
        archive.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="{RELS}">'
                         f'<Relationship Id="rHeader" Type="{REL_TYPE}/header" Target="header1.xml"/>'
                         f'<Relationship Id="rStyles" Type="{REL_TYPE}/styles" Target="styles.xml"/>'
                         f'<Relationship Id="rLink" Type="{REL_TYPE}/hyperlink" Target="https://example.com"'
                         ' TargetMode="External"/></Relationships>')
        for name, content in extra_parts:
            part = ZipInfo(name)
            part.filename = name  # Keep the original separators even when running on Windows.
            archive.writestr(part, content)
    return data.getvalue()


class FormatTest(TestCase):
    def test_transfers_chinese_font_and_turns_off_inherited_automatic_spacing(self):
        reference_styles = """
        <w:docDefaults><w:rPrDefault><w:rPr>
          <w:rFonts w:eastAsiaTheme="minorEastAsia"/><w:lang w:eastAsia="zh-CN"/>
        </w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:before="120"/></w:pPr></w:pPrDefault></w:docDefaults>
        """
        reference_parts = (
            ("word/theme/theme1.xml", f'<a:theme xmlns:a="{A[1:-1]}"><a:minorFont><a:ea typeface=""/></a:minorFont></a:theme>'),
            ("word/fontTable.xml", f'<w:fonts xmlns:w="{W[1:-1]}">'
             '<w:font w:name="DengXian"><w:charset w:val="86"/></w:font>'
             '<w:font w:name="Times New Roman"><w:charset w:val="00"/></w:font>'
             '<w:font w:name="DengXian"><w:charset w:val="86"/></w:font></w:fonts>'),
        )
        target_styles = """
        <w:style w:type="paragraph" w:default="1" w:styleId="Web">
          <w:pPr><w:spacing w:line="480" w:beforeAutospacing="1" w:afterAutospacing="1"/></w:pPr>
          <w:rPr><w:rFonts w:eastAsia="宋体"/></w:rPr>
        </w:style>
        """
        body = "<w:p><w:r><w:t>这是一段测试正文。</w:t></w:r></w:p>"
        target = package(body, target_styles)
        output, _ = format_document(package(body, reference_styles, reference_parts), target, Advisor(False))
        paragraph = Document(output).paragraphs[0]
        fonts = prop(prop(prop(paragraph, "r"), "rPr"), "rFonts")
        spacing = prop(prop(paragraph, "pPr"), "spacing")
        self.assertEqual(fonts.get(W + "eastAsia"), "DengXian")
        self.assertEqual(spacing.get(W + "line"), "240")
        self.assertEqual(spacing.get(W + "beforeAutospacing"), "0")
        self.assertEqual(spacing.get(W + "afterAutospacing"), "0")
        self.assertEqual(content_projection(Document(output).root), content_projection(Document(target).root))

    def test_maps_unstyled_numbered_headings_below_the_reference_cover_title(self):
        reference_body = """
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:t>参照总标题</w:t></w:r></w:p>
        <w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:rPr><w:sz w:val="32"/></w:rPr><w:t>参照章节标题</w:t></w:r></w:p>
        <w:p><w:pPr><w:outlineLvl w:val="2"/></w:pPr><w:r><w:rPr><w:sz w:val="28"/></w:rPr><w:t>参照小节标题</w:t></w:r></w:p>
        <w:p><w:r><w:rPr><w:sz w:val="21"/></w:rPr><w:t>参照的正文段落。</w:t></w:r></w:p>
        """
        target_title = "（题　目）" + "这是必须保留的论文标题" * 5 + " （作　者）某某"
        mixed = "1.研究目的 " + "标题和正文保存在同一个段落里。" * 8
        target = package("".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in
                                 (target_title, "2.研究方法", "2.1文献资料法", mixed))
                         + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:docGrid w:type="lines" w:linePitch="312"/></w:sectPr>')
        reference = package(reference_body + "<w:sectPr/>")
        output, report = format_document(reference, target, Advisor(False))
        formatted = Document(output)
        sizes = [val(prop(prop(p, "r"), "rPr"), "sz") for p in formatted.paragraphs]
        self.assertEqual(sizes, ["44", "32", "28", "21"])
        title_runs = runs_of(formatted.paragraphs[0])
        self.assertEqual([val(prop(run, "rPr"), "sz") for run in title_runs], ["44", "21"])
        self.assertEqual([text_of(p) for p in formatted.paragraphs], [text_of(p) for p in Document(target).paragraphs])
        self.assertIsNone(prop(prop(formatted.body, "sectPr"), "pgSz"))
        self.assertIsNone(prop(prop(formatted.body, "sectPr"), "docGrid"))
        self.assertEqual([change["property"] for change in report["report"]["locations"][-1]["changes"]],
                         ["纸张尺寸", "文档网格"])
        reapplied, _ = format_document(reference, output, Advisor(False))
        self.assertEqual(reapplied, output)

    def test_applies_template_while_preserving_content_and_package_parts(self):
        original = package(TARGET_BODY)
        output, report = format_document(package(REFERENCE_BODY, REFERENCE_STYLES), original, Advisor(False))
        formatted = Document(output)
        heading, body, _ = formatted.paragraphs
        heading_p = prop(heading, "pPr")
        heading_r = prop(prop(heading, "r"), "rPr")
        self.assertEqual(val(heading_p, "outlineLvl"), "0")
        self.assertEqual(val(heading_p, "jc"), "center")
        self.assertEqual(val(heading_r, "sz"), "36")
        self.assertEqual(prop(heading_r, "rFonts").get(W + "eastAsia"), "宋体")
        self.assertEqual(prop(prop(body, "pPr"), "spacing").get(W + "after"), "160")
        self.assertEqual(val(prop(prop(prop(body, "hyperlink"), "r"), "rPr"), "color"), "0000FF")
        self.assertEqual(prop(prop(formatted.body, "sectPr"), "pgMar").get(W + "left"), "1100")
        self.assertEqual(content_projection(formatted.root), content_projection(Document(original).root))
        with ZipFile(BytesIO(original)) as source, ZipFile(BytesIO(output)) as result:
            self.assertEqual(source.namelist(), result.namelist())
            for name in source.namelist():
                if name != "word/document.xml":
                    self.assertEqual(source.read(name), result.read(name), name)
        self.assertEqual({key: report[key] for key in ("paragraphs", "tables", "mode", "rules")},
                         {"paragraphs": 3, "tables": 1, "mode": "local", "rules": []})
        detail = report["report"]
        self.assertEqual(detail["counts"], {"paragraph": 3, "table": 1, "section": 1})
        heading = detail["locations"][0]
        self.assertEqual((heading["kind"], heading["index"], heading["excerpt"]), ("paragraph", 1, "保留这个标题"))
        self.assertIn("参照文档第 1 个段落", heading["reason"])
        self.assertIn({"property": "对齐方式", "before": "左对齐", "after": "居中"}, heading["changes"])
        self.assertIn({"property": "字号", "before": "11 pt", "after": "18 pt", "affectedRuns": 1},
                      heading["changes"])
        self.assertEqual([entry["kind"] for entry in detail["locations"][-2:]], ["table", "section"])

    def test_report_omits_matching_formatting(self):
        original = package(REFERENCE_BODY, REFERENCE_STYLES)
        _, result = format_document(original, original, Advisor(False))
        self.assertEqual(result["report"], {
            "counts": {"paragraph": 0, "table": 0, "section": 0}, "locations": [],
        })

    def test_rejects_non_word_zip(self):
        with self.assertRaises(InvalidDocument):
            Document(b"this is not a word document")

    def test_accepts_legacy_empty_directories_and_normalizes_output(self):
        directories = [("word\\theme/", b""), ("word\\_rels/", b"")]
        reference = package(REFERENCE_BODY, REFERENCE_STYLES, directories)
        original = package(TARGET_BODY, extra_parts=directories)
        output, report = format_document(reference, original, Advisor(False))
        self.assertEqual(report["paragraphs"], 3)
        with ZipFile(BytesIO(output)) as result:
            self.assertIn("word/theme/", result.namelist())
            self.assertIn("word/_rels/", result.namelist())
            self.assertFalse(any("\\" in name for name in result.namelist()))

    def test_rejects_unsafe_paths_and_identifies_the_target_document(self):
        reference = package(REFERENCE_BODY, REFERENCE_STYLES)
        for part in [("word\\..\\outside/", b""), ("C:\\outside/", b""),
                     ("word\\media\\picture.png", b"data")]:
            with self.subTest(part=part), self.assertRaisesRegex(InvalidDocument, "目标文档.*路径"):
                format_document(reference, package(TARGET_BODY, extra_parts=[part]), Advisor(False))


class KnowledgeImportTest(TestCase):
    def test_extracts_word_paragraphs_and_table_text_in_order(self):
        body = """
<w:p><w:r><w:t>规则一</w:t><w:tab/><w:t>条件</w:t><w:br/><w:t>另起一行</w:t></w:r>
<w:del><w:r><w:t>已删除文字</w:t></w:r></w:del></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格内容</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
"""
        original = package(body, extra_parts=[("word\\theme/", b"")])
        self.assertEqual(extract_text(original, "规则.DOCX"), "规则一\t条件\n另起一行\n表格内容")

    def test_extracts_chinese_text_and_normalizes_newlines(self):
        text = "  标题规则\r\n正文规则  "
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            with self.subTest(encoding=encoding):
                self.assertEqual(extract_text(text.encode(encoding), "规则.txt"), "标题规则\n正文规则")

    def test_rejects_empty_binary_and_oversized_extraction(self):
        with self.assertRaisesRegex(InvalidDocument, "没有可提取"):
            extract_text(b" \r\n", "empty.txt")
        with self.assertRaisesRegex(InvalidDocument, "UTF-8"):
            extract_text(b"\x00binary", "invalid.txt")
        with self.assertRaisesRegex(InvalidDocument, "100,000"):
            extract_text(b"a" * 100001, "long.txt")


class AdvisorTest(TestCase):
    def test_retrieves_only_the_provided_library_and_preserves_an_empty_library(self):
        custom = {"id": "custom", "title": "交付标准", "rule": "独立成段的验收标准按二级标题处理。"}
        self.assertEqual(Advisor(False, [custom]).retrieve("验收标准"), ([custom], "keyword"))
        self.assertEqual(Advisor(False, [custom]).retrieve("完全无关"), ([], "keyword"))
        self.assertEqual(Advisor(False, []).retrieve("标题"), ([], "empty"))

    def test_passes_retrieved_knowledge_and_ignores_invented_roles(self):
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}):
            advisor = Advisor(True)
        with patch("advisor.index_knowledge", return_value=((1, 0), (0, 1), (0, 1),
                                                             (0, 1), (0, 1), (0, 1))), \
                patch.object(advisor.client, "embed", return_value=[(1, 0)]), \
                patch.object(advisor.client, "classify",
                             return_value={"2": "heading-1", "3": "new-text", "99": "title"}) as call:
            self.assertEqual(advisor.classify({2: "适用范围", 3: "项目备注"}), {2: "heading-1"})
        self.assertEqual(call.call_args.args[1][0]["id"], "document-title")
        self.assertEqual(advisor.mode, "bailian")
