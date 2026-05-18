# Model Recommendations for Apple Silicon

Examples checked around 2026-05-19. The local picker still does live Hugging Face lookup and RAM filtering, so run this for current options:

```bash
ai-obsidian models list
```

| Apple Silicon memory | Good default | Best for | Notes |
| --- | --- | --- | --- |
| 16 GB | Qwen3.5 2B/4B OptiQ 4-bit, Gemma 4 E2B/E4B | quick summaries, cleanup, short note chat | Stay in the small tier. Avoid 14B+ and 30B+ models. |
| 24 GB | small models first; try 7B-9B carefully | meeting notes, light restructuring | Watch memory pressure with long vault context. |
| 32 GB | Qwen3.5 9B OptiQ 4-bit, Qwen3 8B, Gemma 12B-class | daily vault work, better summaries, moderate edits | Balanced tier is the normal default. |
| 48 GB | 14B-27B 4-bit models cautiously | deeper restructuring, longer synthesis | Expect slower startup and generation. |
| 64 GB+ | Qwen3.5 27B, Gemma 4 26B/31B, Qwen3.6 35B-A3B-class | stronger reasoning, long research synthesis | Large tier is visible in normal picker only when RAM allows it. |
| 96/128 GB+ | large and MoE experiments | heavy local reasoning experiments | Still prefer downloaded/served models first for reliability. |

## Task Guidance

- Meeting notes: Qwen or Gemma small/balanced models.
- Vault restructuring: balanced Qwen, or large Qwen when memory allows.
- Coding and technical notes: Qwen/Coder family when available.
- Fast voice-note cleanup: small Gemma or Qwen.
- Long research synthesis: large local model, or explicit `--engine claude` / `--engine hermes`.
