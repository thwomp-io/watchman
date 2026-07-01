# Skills

Agent skills the harness ships for **your** agent to load. The harness is driven by an AI agent; these
skills teach that agent how to operate it well.

| Skill | What it does |
|---|---|
| [`corpus-operator`](corpus-operator/SKILL.md) | The one that matters. Teaches your agent to **build and maintain your narrative corpus** — your stories, reasoning, and emotional texture in your *own raw voice* — over time via an interview-synthesis cadence: passively fishing context from normal conversation, capturing the raw voice verbatim, synthesizing it into living docs, and deriving the machine-readable weights the tools read. Then it drives the Watchman console off it. **The corpus is the product, the voice is the moat, and this skill is what builds it.** |
| [`console-operator`](console-operator/SKILL.md) | Teaches your agent to **operate the Watchman console + drive the `hn` lanes** (finance / career / travel) as a read-only sounding board: which surface to reach for (the CLI, the console zones, the message bus), the verb that answers each question, loading/swapping weight packs, and the determinism discipline that keeps the console trustworthy. Read-rich, execute-gated — it observes and recommends, never trades/books/applies. The operate-the-tool companion to `corpus-operator`'s build-the-corpus. |

> A skill is portable markdown with a `name` + `description` frontmatter. Point your agent at the relevant
> `SKILL.md` (e.g. drop it where your agent looks for skills, or reference it directly).
