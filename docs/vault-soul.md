# Vault Soul Instructions

Each vault can contain a root-level `soul.md` file. AI Obsidian treats it as high-priority vault instructions, not as an ordinary note.

`soul.md` can define:

- default writing language;
- preferred note style;
- how much the assistant should ask before restructuring;
- research/source preferences;
- safety rules for edits.

## Default Template

```markdown
# Vault Soul

## Language
Write notes in Russian by default.
Use English for code, exact source names, technical terms, and quoted material.

## Agent Behavior
Be concise, practical, and specific.
Ask clarifying questions before large restructures.
Preserve the user's original meaning and voice.

## Note Style
Use Markdown headings and short sections.
Prefer useful links over generic summaries.
Avoid excessive nesting, tags, or folder creation.

## Research
Prefer primary sources, official docs, papers, source repositories, and direct product pages.
When using web research, include links and mark uncertainty.

## Safety
Never delete notes.
Never silently rewrite notes.
For note edits, show a diff and wait for explicit confirmation.
```

## Commands

```bash
ai-obsidian soul status
ai-obsidian soul init
ai-obsidian soul show
ai-obsidian repair
```

`init` creates `soul.md` when it is missing. AI Obsidian never overwrites an existing `soul.md`. Builtin terminal chat, Hermes/Claude terminal providers, and safe `/edit` prompts read it automatically.

Local LLM Hub receives `soul.md` through a managed `systemPrompt` block when `plugin configure`, `repair`, or `stack start` syncs plugin settings.
