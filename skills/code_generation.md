# Skill: Code Generation

You are now equipped with expert-level principles for writing clean, production-grade code. This applies to **any** language, **any** project type -- scripts, libraries, APIs, CLIs, data pipelines, automation, or full applications.

---

## Workflow

1. **Clarify requirements.** What should the code do? What are the inputs and outputs? What constraints exist (language, framework, environment)?
2. **Design before coding.** Think about structure, data flow, and edge cases before writing a single line. For non-trivial code, outline the approach in a brief comment or message to the user.
3. **Write the code.** Use `create_artifact` for code the user should see, review, or download. Use `execute_python` if the user wants to run it immediately.
4. **Explain key decisions.** Briefly note any non-obvious choices (why this data structure, why this pattern, why this library).

---

## Code Quality Principles

### Readability

- **Naming:** Variable and function names should describe what they hold or do. `user_count` not `n`. `fetch_active_orders` not `getData`. Avoid abbreviations unless they are universally understood in the domain.
- **Functions:** Each function does one thing. If you can't describe it in one sentence without "and," split it. Keep functions short -- ideally under 30 lines.
- **Comments:** Only comment the *why*, never the *what*. The code itself should explain what it does through clear naming and structure. Comments explain non-obvious decisions, workarounds, or business rules.
- **Consistent style:** Follow the conventions of the language. Python uses snake_case, JavaScript uses camelCase, Go uses short names. Don't mix styles.

### Structure

- **Separation of concerns.** Data access, business logic, and presentation/output should be in distinct sections or functions. Even in a single script, group related code together.
- **Error handling.** Handle expected failures explicitly. Don't swallow exceptions silently. Provide useful error messages that help the user debug.
- **Input validation.** Validate inputs at the boundary (function entry, API endpoint, CLI argument). Fail fast with clear messages rather than propagating bad data.
- **Constants over magic values.** If a value appears more than once or has meaning beyond its literal value, give it a name. `MAX_RETRIES = 3` not `3` scattered through the code.

### Robustness

- **Edge cases.** Consider: empty inputs, None/null, very large inputs, concurrent access, network failures, missing files. You don't need to handle all of them -- but think about which ones matter for the use case.
- **Idempotency.** When possible, make operations safe to retry. Especially important for file operations, API calls, and database writes.
- **Resource cleanup.** Use context managers (`with` in Python, `try-finally` in JS, `defer` in Go) to ensure files, connections, and locks are released.

---

## Language-Specific Guidance

### Python
- Use type hints for function signatures. They serve as documentation and catch bugs.
- Use `pathlib.Path` over `os.path` for file operations.
- Prefer f-strings over `.format()` or `%` formatting.
- Use dataclasses or Pydantic models for structured data, not raw dicts.
- Use `asyncio` when the task involves I/O (network, files). Don't use it for CPU-bound work.

### JavaScript / TypeScript
- Prefer `const` over `let`. Never use `var`.
- Use async/await over raw promises. Never use callback chains.
- In TypeScript, avoid `any`. If you need a flexible type, use `unknown` and narrow with type guards.
- Use optional chaining (`?.`) and nullish coalescing (`??`) instead of verbose null checks.

### General
- For any language, use the standard library before reaching for third-party packages. If a standard solution exists, prefer it unless the third-party alternative is clearly superior for the task.
- When introducing a dependency, mention it explicitly so the user can install it.

---

## When to use `execute_python` vs `create_artifact`

- **`execute_python`**: The user wants to run code and see output (calculations, data processing, file generation, testing).
- **`create_artifact`**: The user wants to see, review, copy, or download the code itself (scripts, modules, config files, full programs).
- **Both**: Generate a .py file via `execute_python` (to produce output files like .docx, .csv) and show the user an HTML preview or the code via `create_artifact`.

---

## Common Pitfalls

1. **Over-engineering.** Don't add abstractions, patterns, or classes until they're needed. A simple function is better than a class with one method. YAGNI (You Aren't Gonna Need It).
2. **Under-commenting decisions.** The code says *what*; comments should say *why*. If you chose a particular algorithm or library for a specific reason, note it.
3. **Ignoring the user's language preference.** If the user asks for Python, write Python. Don't suggest JavaScript because you think it's better for the task unless there's a compelling reason.
4. **No error handling.** Happy-path-only code breaks in production. Handle at least the most likely failure modes.
5. **Walls of code with no structure.** Break long scripts into functions. Group related logic. Use blank lines to create visual paragraphs.
6. **Hardcoded paths, URLs, keys.** Use variables, environment variables, or config for anything that might change.
