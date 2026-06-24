# Brevia

> Condense large technical ebooks (EPUB/PDF) into a fast, filler-free version —
> preserving code, formulas, and essential diagrams — and optionally translate them
> in the same pass. Outputs **EPUB, PDF, and Markdown**.

Multi-provider LLM from day one: **Ollama** (local), **OpenAI**, **Gemini**,
**OpenRouter**, and any **OpenAI-compatible** endpoint (vLLM, LM Studio, LocalAI).

Runs fully local on a MacBook with Ollama, or via a paid API when it makes sense.

## Status

🚧 Early development. Built in phases following the **[roadmap](docs/ROADMAP.md)** using
the PRP (Product Requirement Prompt) workflow. **MVP target = Phases 0–7** (condense an
EPUB → EPUB + PDF + Markdown with Ollama).

## How it works

Everything flows through a format-agnostic **Intermediate Representation (IR)**:

```
parse → chunk → condense → synthesize → (translate) → image-select → render
```

Parsers turn EPUB/PDF into the IR; the condenser and translator transform its text
blocks (leaving code and images intact); renderers emit the final files. See the
[roadmap](docs/ROADMAP.md) for the full design.

## License

[Apache-2.0](LICENSE). Brevia is **inspired by** open-source work but contains no copied
code and depends on no copyleft (GPL/AGPL) libraries — see roadmap §14.
