# Skill: HTML & CSS Document Design

You are now equipped with expert-level knowledge for producing beautiful, modern HTML/CSS artifacts. This applies to **any** rendered output -- reports, landing pages, dashboards, email templates, data presentations, resumes, invoices, documentation pages, or anything the user wants to see rendered visually.

Use `create_artifact` with `content_type: "html"` to deliver the output. The artifact panel renders HTML in an iframe, so your output must be a self-contained HTML document.

---

## Workflow

1. **Understand the purpose.** What is this document for? Who reads it? Is it for screen, print, or both?
2. **Choose the right layout approach.** A single-column report is different from a two-column resume or a dashboard grid. Pick the CSS layout strategy that fits (see Layout section).
3. **Build the HTML structure first.** Semantic elements, correct heading hierarchy, logical grouping. Style comes after structure.
4. **Apply styling.** Use CSS variables for theming, consistent spacing scale, and intentional typography.
5. **Deliver via `create_artifact`** with `content_type: "html"` and a descriptive filename.

---

## Self-Contained Document Template

Every HTML artifact must be a complete document. Start from this skeleton and adapt:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Document Title</title>
  <style>
    /* CSS variables for easy theming */
    :root {
      --font-sans: 'Segoe UI', system-ui, -apple-system, sans-serif;
      --font-serif: Georgia, 'Times New Roman', serif;
      --font-mono: 'Cascadia Code', 'Fira Code', monospace;

      --text-primary: #1a1a2e;
      --text-secondary: #555;
      --text-muted: #888;
      --accent: #2563eb;
      --accent-light: #dbeafe;
      --bg: #ffffff;
      --bg-subtle: #f8f9fa;
      --border: #e2e8f0;

      --space-xs: 0.25rem;
      --space-sm: 0.5rem;
      --space-md: 1rem;
      --space-lg: 1.5rem;
      --space-xl: 2rem;
      --space-2xl: 3rem;
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--font-sans);
      color: var(--text-primary);
      background: var(--bg);
      line-height: 1.6;
      max-width: 900px;
      margin: 0 auto;
      padding: var(--space-xl);
    }
  </style>
</head>
<body>
  <!-- Content here -->
</body>
</html>
```

This is a **starting point**, not a rigid template. Modify everything -- colors, fonts, layout, max-width -- to match the task.

---

## Typography

Good typography makes or breaks a document. These principles apply universally.

- **Font stack:** Always use a system font stack as fallback. The CSS variables above provide sensible defaults for sans-serif, serif, and monospace.
- **Scale:** Use a consistent size scale. A practical one: 0.75rem, 0.875rem, 1rem, 1.125rem, 1.25rem, 1.5rem, 2rem, 2.5rem. Don't pick arbitrary sizes.
- **Line height:** 1.5-1.7 for body text. 1.1-1.3 for headings (tighter at large sizes).
- **Measure (line length):** Keep body text between 45-75 characters per line. This is why `max-width` on the body matters. For wider layouts, use multi-column or increase side padding.
- **Weight contrast:** Use font-weight differences to create hierarchy. Regular (400) for body, medium (500) for subheadings, bold (600-700) for headings. Don't bold everything.
- **Color contrast:** Body text should be dark but not pure black (`#1a1a2e` or `#333` rather than `#000`). Secondary text in `#555-#777`. Muted text in `#888-#999`. Always ensure WCAG AA contrast ratios.

---

## Layout

Choose the right CSS strategy for the content structure.

### Single column (reports, articles, letters)
```css
body { max-width: 800px; margin: 0 auto; padding: 2rem; }
```

### Two column (resumes, CVs, sidebars)
```css
.container { display: grid; grid-template-columns: 1fr 2fr; gap: 2rem; }
/* Or for sidebar layouts: */
.container { display: grid; grid-template-columns: 250px 1fr; gap: 2rem; }
```

### Card grid (dashboards, portfolios)
```css
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; }
```

### Flexbox for inline groups
```css
.row { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
```

Use **Grid** for 2D layouts (rows and columns). Use **Flexbox** for 1D alignment (items in a row or column). Do not use floats or tables for layout.

---

## Spacing System

Consistent spacing creates visual rhythm. Use the CSS variable scale and apply it deliberately:

- **Sections:** Separate major sections with `var(--space-2xl)` or `var(--space-xl)`.
- **Within sections:** Use `var(--space-md)` to `var(--space-lg)` between related elements.
- **Tight groups:** Use `var(--space-xs)` to `var(--space-sm)` for label/value pairs, list items, or tightly coupled content.

Never use arbitrary pixel values scattered throughout. Pick from the scale.

---

## Color

- **Primary palette:** Pick one accent color. Derive light/dark variants from it. The default blue (`#2563eb`) works for most professional contexts.
- **Semantic colors:** Use color intentionally -- accent for links and highlights, green for success, red for errors/warnings, muted grays for secondary info.
- **Backgrounds:** Use subtle background tones (`#f8f9fa`, `#f1f5f9`) to create visual sections without hard borders.
- **Borders:** Light gray (`#e2e8f0`) for subtle separation. Use sparingly -- whitespace is usually better than a border.

---

## Common Components

### Section headers
```css
h2 {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--text-primary);
  border-bottom: 2px solid var(--accent);
  padding-bottom: var(--space-xs);
  margin-top: var(--space-xl);
  margin-bottom: var(--space-md);
}
```

### Tags / badges
```css
.tag {
  display: inline-block;
  padding: 0.15rem 0.6rem;
  background: var(--accent-light);
  color: var(--accent);
  border-radius: 9999px;
  font-size: 0.8rem;
  font-weight: 500;
}
```

### Timeline / entries
```css
.entry { margin-bottom: var(--space-lg); }
.entry-header { display: flex; justify-content: space-between; align-items: baseline; }
.entry-title { font-weight: 600; font-size: 1.05rem; }
.entry-date { color: var(--text-muted); font-size: 0.875rem; }
```

### Tables
```css
table { width: 100%; border-collapse: collapse; }
th, td { padding: var(--space-sm) var(--space-md); text-align: left; border-bottom: 1px solid var(--border); }
th { font-weight: 600; background: var(--bg-subtle); }
tr:hover { background: var(--bg-subtle); }
```

These are **patterns**, not rules. Adapt them to the document's purpose and style.

---

## Print Styles

If the document might be printed or saved as PDF, add print-specific CSS:

```css
@media print {
  body { max-width: none; padding: 0; font-size: 10pt; }
  a { color: inherit; text-decoration: none; }
  .no-print { display: none; }
  h2, h3 { page-break-after: avoid; }
  .entry { page-break-inside: avoid; }
}
```

---

## Common Pitfalls

1. **No `box-sizing: border-box`.** Without it, padding adds to element width and breaks layouts. Always include the reset shown in the template.
2. **Hardcoded widths everywhere.** Use `max-width`, percentages, `fr` units, and `auto-fit`/`auto-fill` for responsive layouts. Only hardcode widths for specific fixed-size elements (icons, sidebars with known content).
3. **Inconsistent spacing.** Random pixel values create visual noise. Stick to the spacing scale.
4. **Too many colors.** One accent color, a few grays, and semantic colors are enough. More than that creates visual chaos.
5. **Missing viewport meta tag.** Without `<meta name="viewport" ...>`, mobile rendering breaks. Always include it even for artifacts (the iframe respects it).
6. **Huge CSS with unused styles.** Keep styles minimal and purposeful. Only define what you actually use.
7. **Pure black text on pure white.** Slightly off-black on white (`#1a1a2e` on `#fff`) is easier on the eyes and looks more polished.
