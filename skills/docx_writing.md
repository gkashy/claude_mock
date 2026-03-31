# Skill: DOCX Document Writing

You are now equipped with expert-level knowledge for producing professional `.docx` documents using the `python-docx` library. This applies to **any** document type -- resumes, cover letters, reports, proposals, contracts, letters, invoices, or anything else the user needs.

---

## Workflow

1. **Understand the ask.** What kind of document? Who is the audience? Formal or casual? What information do you already have from memory/conversation?
2. **Gather missing info.** Ask the user for anything critical you do not have. Do not guess names, dates, or specifics.
3. **Generate the .docx** using `execute_python` with the python-docx code below.
4. **Create an HTML preview artifact** using `create_artifact` with `content_type: "html"` so the user can see a rendered preview in the side panel immediately.
5. **Tell the user** the .docx file is ready in the workspace and they can download it.

If the user asks for changes, regenerate the .docx with `execute_python` (overwrite the file) and use `update_artifact` for the HTML preview.

---

## python-docx API Essentials

### Imports you will always need

```python
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
```

### Core object model

- **Document** -- the file itself. Create with `Document()`.
- **Section** -- page layout (margins, orientation, size). Access via `doc.sections`.
- **Paragraph** -- a block of text. Add via `doc.add_paragraph()`.
- **Run** -- a contiguous span within a paragraph sharing the same formatting. Add via `paragraph.add_run()`.
- **Table** -- rows and columns. Add via `doc.add_table(rows, cols)`.
- **Style** -- named formatting presets. Access via `doc.styles`.

### Setting page margins

```python
for section in doc.sections:
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.6)
    section.right_margin = Inches(0.6)
```

### Paragraph formatting

```python
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER  # or LEFT, RIGHT, JUSTIFY
p.paragraph_format.space_before = Pt(0)
p.paragraph_format.space_after = Pt(4)
p.paragraph_format.line_spacing = Pt(14)
```

### Run formatting (inline text)

```python
run = p.add_run("Some text")
run.bold = True
run.italic = True
run.underline = True
run.font.size = Pt(11)
run.font.name = "Calibri"
run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
```

### Adding a horizontal rule

python-docx has no native HR. Use a bottom border on a paragraph:

```python
def add_horizontal_rule(paragraph):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "999999")
    pBdr.append(bottom)
    pPr.append(pBdr)
```

### Tables (for multi-column layouts)

```python
table = doc.add_table(rows=1, cols=2)
table.alignment = WD_TABLE_ALIGNMENT.CENTER
# Remove borders for invisible layout tables
for row in table.rows:
    for cell in row.cells:
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcBorders = OxmlElement("w:tcBorders")
        for edge in ("start", "top", "end", "bottom"):
            element = OxmlElement(f"w:{edge}")
            element.set(qn("w:val"), "none")
            element.set(qn("w:sz"), "0")
            element.set(qn("w:space"), "0")
            tcBorders.append(element)
        tcPr.append(tcBorders)
```

### Hyperlinks

python-docx does not have a built-in hyperlink API. Use this helper:

```python
def add_hyperlink(paragraph, text, url):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    c = OxmlElement("w:color")
    c.set(qn("w:val"), "0563C1")
    rPr.append(c)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)
    new_run.append(rPr)
    new_run.text = text
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
```

### Saving

```python
doc.save("output.docx")
```

The file lands in the workspace directory where `execute_python` runs. The user can then download it.

---

## Typography Principles

These are not rigid rules -- adapt them to the document type and audience.

- **Font choice:** Calibri, Cambria, Garamond, or Arial are safe professional defaults. Pick one serif or one sans-serif and stay consistent. Do not mix more than two typefaces.
- **Body text size:** 10.5-12pt for body copy. Headings scale up proportionally (14-16pt for major headings, 12-13pt for subheadings).
- **Line spacing:** 1.0-1.15 for dense professional documents (resumes, letters). 1.15-1.5 for long-form reading (reports, proposals).
- **Margins:** 0.5-1.0 inches depending on density. Tighter for content-heavy single-pagers, wider for formal documents.
- **Whitespace:** Use `space_before` and `space_after` on paragraphs deliberately. Whitespace groups related content and separates sections. Never rely on empty paragraphs for spacing.
- **Hierarchy:** The reader should be able to scan the document and understand its structure from font sizes, bolding, and spacing alone.
- **Consistency:** Once you pick a size/style for a heading level, use it everywhere. Same for bullet styles, date formats, and alignment.

---

## Common Pitfalls

1. **Forgetting imports.** Always import `Pt`, `Inches`, `RGBColor` from `docx.shared`. Missing these causes NameError at runtime.
2. **Style mutation.** Modifying `doc.styles['Normal']` changes ALL paragraphs using that style, including ones already added. Set formatting on individual paragraphs/runs instead, or create custom styles.
3. **Empty paragraphs for spacing.** Use `space_before`/`space_after` on paragraph_format instead. Empty paragraphs create inconsistent spacing and are hard to maintain.
4. **Encoding issues.** Always use UTF-8 strings. python-docx handles Unicode well, but watch out for special characters from copy-paste (curly quotes, em-dashes).
5. **Table cell paragraphs.** Each cell comes with one empty paragraph. Access it with `cell.paragraphs[0]` rather than adding a new one (which creates a blank line above your content).
6. **Not setting font on every run.** Font settings are per-run, not per-paragraph. If you add multiple runs to a paragraph, set the font on each one.
7. **Page breaks.** Use `doc.add_page_break()` or `paragraph.paragraph_format.page_break_before = True`. Do not add dozens of empty paragraphs.

---

## HTML Preview Strategy

After generating the .docx, create an HTML artifact that approximates the document visually. This gives the user instant feedback in the side panel. The HTML does not need to be pixel-perfect -- it should convey structure, typography, and content accurately.

Use inline CSS in the HTML artifact. Match the fonts, sizes, and spacing you used in the .docx. This way the user sees a live preview and can request changes before downloading the actual file.
