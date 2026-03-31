# Skill: Presentation & Slide Design

You are now equipped with expert-level methodology for creating presentations. This applies to **any** presentation format -- pitch decks, technical talks, educational slides, status updates, proposals, or workshop materials.

Presentations are delivered as HTML artifacts using `create_artifact` with `content_type: "html"`. The HTML renders in the artifact panel and can be opened in a browser for full-screen viewing.

---

## Workflow

1. **Understand the context.** Who is the audience? What is the goal (persuade, inform, teach, update)? How long is the presentation? How many slides?
2. **Outline first.** Structure the narrative arc before designing any slides. Share the outline with the user for approval if the topic is complex.
3. **Build the HTML slide deck.** Use the approach below for a clean, navigable slide experience.
4. **Deliver via `create_artifact`** with `content_type: "html"`.

---

## Narrative Structure

Every presentation tells a story. Even technical ones. Structure depends on the goal:

### Persuasion (pitch decks, proposals)
1. Problem / opportunity
2. Your solution
3. How it works (brief)
4. Evidence / traction
5. Ask / next steps

### Information (status updates, reports)
1. Context / recap
2. Key findings or updates
3. Implications
4. Next steps

### Teaching (tutorials, workshops)
1. What you'll learn (set expectations)
2. Concept explanation
3. Example / demo
4. Practice / exercise prompt
5. Summary / key takeaways

### Technical (architecture, design reviews)
1. Problem statement
2. Constraints and requirements
3. Approach / architecture
4. Trade-offs and alternatives considered
5. Open questions

Adapt these to the specific talk. Not every presentation fits a template -- use your judgment.

---

## Slide Design Principles

- **One idea per slide.** If a slide needs two headings, it's two slides.
- **Less text, more visually.** Use bullet points sparingly. Prefer short phrases over sentences. If you need a paragraph, you need another slide.
- **Visual hierarchy.** The most important thing on each slide should be the largest and most prominent. The eye should know where to go immediately.
- **Consistent layout.** Pick a layout grid and stick to it across slides. Title position, content area, and footer should be in the same place on every slide.
- **Whitespace.** Don't fill every pixel. Generous padding makes slides feel professional and readable.
- **6 x 6 rule.** Maximum 6 bullet points per slide, maximum 6 words per point. This is a guideline, not a law, but it keeps content scannable.

---

## HTML Slide Implementation

Use a simple CSS-based slide system. No external dependencies needed.

```html
<style>
  .slide {
    width: 100%;
    max-width: 960px;
    min-height: 540px;
    margin: 0 auto 2rem;
    padding: 3rem;
    background: white;
    border-radius: 8px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    display: flex;
    flex-direction: column;
    justify-content: center;
    page-break-after: always;
  }

  .slide h1 { font-size: 2.2rem; margin-bottom: 1rem; }
  .slide h2 { font-size: 1.6rem; margin-bottom: 0.75rem; color: #333; }
  .slide p, .slide li { font-size: 1.2rem; line-height: 1.7; }
  .slide ul { padding-left: 1.5rem; }
  .slide li { margin-bottom: 0.5rem; }

  .slide-title {
    text-align: center;
    justify-content: center;
    align-items: center;
  }
  .slide-title h1 { font-size: 2.8rem; }
  .slide-title .subtitle { font-size: 1.3rem; color: #666; margin-top: 0.5rem; }
</style>

<div class="slide slide-title">
  <h1>Presentation Title</h1>
  <p class="subtitle">Subtitle or author name</p>
</div>

<div class="slide">
  <h2>Slide Heading</h2>
  <ul>
    <li>Point one</li>
    <li>Point two</li>
    <li>Point three</li>
  </ul>
</div>
```

This gives a scrollable deck in the artifact panel. Each slide is a visually distinct card. The user scrolls through them or can open in a browser and print to PDF.

### Variations

- **Split slide** (text + visual side by side): Use a flex or grid container inside `.slide` with two columns.
- **Quote slide**: Center a large quote with attribution below.
- **Metric slide**: Large number or statistic centered with a brief label underneath.
- **Image slide**: If describing a diagram conceptually, use styled divs, SVG, or describe what the image would show and let the user add it.

---

## Color Theming

Pick a theme and apply it consistently:

```css
:root {
  --slide-accent: #2563eb;
  --slide-bg: #ffffff;
  --slide-text: #1a1a2e;
  --slide-muted: #64748b;
}
```

Use the accent color for headings, highlights, and key metrics. Keep everything else neutral. One accent color is enough -- don't make it look like a coloring book.

---

## Common Pitfalls

1. **Walls of text.** Slides are not documents. If you're writing paragraphs, you need more slides with less content each.
2. **No narrative flow.** Slides should build on each other. Each slide should feel like a natural next step, not a random topic.
3. **Inconsistent styling.** If slide 3 has a different font size, color, or layout than slide 5 for no reason, it looks sloppy.
4. **Missing title slide.** Always start with a title slide that sets context.
5. **No conclusion.** Always end with a summary, call to action, or "thank you / questions" slide.
6. **Too many slides for the content.** A slide with just one bullet point wastes the audience's attention. Combine thin slides.
