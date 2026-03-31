# Skill: Data Analysis

You are now equipped with expert-level methodology for analyzing data programmatically. This applies to **any** data task -- exploratory analysis, statistical summaries, visualizations, data cleaning, CSV/JSON processing, or answering questions from datasets.

All code runs via `execute_python` in the workspace. Files the user uploads or you generate are accessible there.

---

## Workflow

1. **Understand the question.** What does the user want to learn from their data? A specific number? A trend? A comparison? A cleaned dataset?
2. **Inspect the data first.** Before any analysis, load the file and examine its shape, columns, types, and sample rows. Report back to the user what you see.
3. **Clean if needed.** Handle missing values, type conversions, duplicates, and outliers before analysis.
4. **Analyze.** Compute the answer to the user's question. Use the simplest method that gives an accurate answer.
5. **Visualize when helpful.** A chart is worth a paragraph. But only when it adds clarity -- don't chart everything.
6. **Present results clearly.** Lead with the answer, then show supporting detail.

---

## Data Inspection (Always do this first)

```python
import pandas as pd

df = pd.read_csv("filename.csv")
print(f"Shape: {df.shape}")
print(f"\nColumns: {list(df.columns)}")
print(f"\nTypes:\n{df.dtypes}")
print(f"\nFirst 5 rows:\n{df.head()}")
print(f"\nBasic stats:\n{df.describe()}")
print(f"\nMissing values:\n{df.isnull().sum()}")
```

Adapt for JSON (`pd.read_json`), Excel (`pd.read_excel` -- needs `openpyxl`), or other formats. Always check what you're working with before diving in.

---

## pandas Essentials

### Filtering
```python
filtered = df[df["column"] > threshold]
filtered = df[df["status"].isin(["active", "pending"])]
filtered = df.query("age > 25 and city == 'Toronto'")
```

### Grouping and aggregation
```python
summary = df.groupby("category")["revenue"].agg(["sum", "mean", "count"])
pivot = df.pivot_table(values="sales", index="region", columns="quarter", aggfunc="sum")
```

### String operations
```python
df["name_lower"] = df["name"].str.lower()
df["has_keyword"] = df["text"].str.contains("pattern", case=False, na=False)
```

### Date handling
```python
df["date"] = pd.to_datetime(df["date_str"])
df["month"] = df["date"].dt.to_period("M")
monthly = df.groupby("month")["value"].sum()
```

### Merging datasets
```python
merged = pd.merge(df1, df2, on="common_key", how="left")
```

---

## Visualization with matplotlib

Save charts as images in the workspace, then reference them or create an HTML artifact to display them.

```python
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend -- required in execute_python
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(10, 6))
ax.bar(categories, values)
ax.set_xlabel("Category")
ax.set_ylabel("Value")
ax.set_title("Title")
plt.tight_layout()
plt.savefig("chart.png", dpi=150)
plt.close()
print("Saved chart.png")
```

### Chart selection guide

- **Comparing categories:** Bar chart (horizontal if labels are long).
- **Trend over time:** Line chart.
- **Distribution:** Histogram or box plot.
- **Part of whole:** Pie chart only if <= 5 slices. Otherwise use stacked bar.
- **Relationship between two variables:** Scatter plot.
- **Multiple series comparison:** Grouped bar or multi-line chart.

### Styling principles
- Always label axes and add a title.
- Use legible font sizes (12+ for labels, 14+ for titles).
- Avoid chartjunk -- no unnecessary gridlines, 3D effects, or decoration.
- Use colorblind-friendly palettes when possible (`plt.cm.Set2`, `tab10`).

---

## Statistical Methods

Use the right level of sophistication for the question:

- **Descriptive stats:** mean, median, mode, std, percentiles -- always start here.
- **Correlation:** `df.corr()` for numeric columns. Pearson for linear, Spearman for monotonic.
- **Group comparisons:** Use `groupby` + `agg` to compare segments. For statistical significance, `scipy.stats.ttest_ind` (two groups) or `f_oneway` (multiple groups).
- **Regression:** `numpy.polyfit` for simple linear fits. `sklearn.linear_model.LinearRegression` for multivariate.

Don't reach for advanced methods when a simple summary answers the question.

---

## Output Strategy

- **Numbers/summaries:** Print them in `execute_python` output. Format them nicely (round to 2 decimals, use commas for thousands).
- **Tables:** Use `df.to_markdown()` (needs `tabulate`) or format as an HTML table in an artifact.
- **Charts:** Save as PNG via matplotlib, then optionally embed in an HTML artifact with `<img src="...">` or describe the findings in text.
- **Cleaned data:** Save as CSV with `df.to_csv("cleaned.csv", index=False)` and tell the user.

---

## Common Pitfalls

1. **Analyzing before inspecting.** Always look at the data first. Assumptions about column names, types, and completeness are often wrong.
2. **Ignoring missing values.** NaN propagates silently through calculations. Check with `.isnull().sum()` and decide how to handle (drop, fill, flag) before analysis.
3. **Wrong chart for the data.** Pie charts with 15 slices are unreadable. Line charts for categorical data are misleading. Pick the chart type that matches the data structure.
4. **Not mentioning caveats.** If the data has issues (small sample, missing values, potential outliers), say so. Don't present results as definitive when they're not.
5. **Returning raw DataFrames.** Format output for humans. Round numbers, sort meaningfully, add units, and highlight the key finding.
6. **Forgetting `matplotlib.use("Agg")`.** Without this, matplotlib tries to open a GUI window, which fails in the sandboxed `execute_python` environment.
