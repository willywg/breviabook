# Brevia — Build Specification & Roadmap

> **For Claude Code:** this document is the complete project specification. Build it following the phases in order. Read the "Core architectural principle" section first — it's the design decision that makes everything else possible.
>
> **Project name:** `Brevia`. Use it as the Python package name (`brevia/`).
>
> **License:** **Apache-2.0** (permissive, with an explicit patent grant — important in the LLM space). To keep it clean, this project **does not copy code from the reference repos and does not use AGPL dependencies** — see §14, which is required reading.
>
> **Task management:** this project is driven with the **PRP (Product Requirement Prompt) workflow**. The **PRP skill must be installed** in Claude Code before starting. Turn each phase in §10 into its own PRP and execute them in order; this spec is the source of truth those PRPs derive from.

---

## 1. Project goal

A command-line tool that takes a large technical ebook (EPUB or PDF, 200–400 pages) and produces a **condensed version** (target ~25–50% of the original) that reads fast and without filler, **preserving code examples, formulas, and the important images/diagrams**, and **optionally translated** to another language in the same pass.

Output in **three formats**: EPUB, PDF, and Markdown.

Multi-provider LLM from day one: **Ollama** (local), **OpenAI**, **Gemini**, **OpenRouter**, and any **OpenAI-compatible** endpoint (covers vLLM, LM Studio, LocalAI).

This is a project **inspired by** two open source repos (see section 2), **not a fork**. We reuse patterns and, where applicable, adapted approaches, respecting their licenses.

### Concrete owner use cases
- Read dense technical books fast, without losing key examples or graphics.
- Get the result in Spanish even when the original is in English.
- Run everything locally on a MacBook M3 Max (36 GB) with Ollama, or via a paid API when it makes sense.

---

## 2. Reference repos

Clone them into `~/projects/open-source/` and use them as **study material, not copy material**. When the text below says "reuse/adapt," it means **study the pattern and reimplement it with your own code** (clean-room), never copy/paste. This is mandatory for license reasons — read §14 before touching these repos.

### 2.1 `hydropix/TranslateBooksWithLLMs` (TBL) — the primary reference
A mature repo (~1.9k stars) for **book translation** with format preservation. It's our engineering-quality model, but its core does NOT work for summarization (see warning below).

**What TO reuse / adapt (real repo paths):**
- `src/core/llm/` — provider layer. The most valuable part. Has `base.py` (common protocol), `factory.py` (provider selection), `key_pool.py` + `rate_limit_handler.py` (API key rotation with failover), `providers/{ollama,openai,gemini,openrouter,litellm,...}.py`. **Adapt this pattern almost as-is** for our LLM layer.
- `src/core/glossary/` — term consistency (includes `ner.py`, `store.py`, `injector.py`). Useful so the **translation** keeps technical terms consistent ("thread" doesn't get translated five different ways).
- `src/core/context_optimizer.py` — adaptive chunking strategy based on actual token usage (uses `tiktoken` with a character-based estimation fallback). Take the idea, **not the size** (see section 7.2).
- `src/persistence/checkpoint_manager.py` — checkpoint/resume pattern. Adapt it to resume long jobs.
- General `.env` structure + config in `src/config.py`.

**⚠️ What NOT to reuse (critical):**
- `src/core/epub/xhtml_translator.py` and `src/core/epub/translator.py` — they implement **1:1 placeholder reinjection**: every HTML tag/image becomes a placeholder the LLM must preserve EXACTLY, and the translated text is reinjected node by node into the original XHTML. That's what makes their EPUB come out identical to the original. **For summarization this is an anti-pattern**: when you condense you delete content, the output structure no longer matches the input, and forcing the preservation of every `<img>` and every tag fights against the "remove filler" goal. We **build a brand-new EPUB from scratch**, we don't reinject.

### 2.2 `cognitivetech/ollama-ebook-summary` — conceptual summarization reference
Generates "bulleted notes" of books. Markdown output, **loses images/tables**. Don't copy code, but **do adopt two lessons**:
- **Chunking that respects chapters/sections** (don't split blindly by tokens; respect the document structure).
- **Chunk size ~2000 tokens**: it cites the paper *"Same Task, More Tokens" (Levy et al., 2024)* — the LLM's reasoning capacity drops sharply above ~2000–3000 input tokens. That's why for summarization we use large chunks (~2000), not TBL's ~450.
- Useful disclaimer: if a summary comes out **longer than the input**, that's an error signal; watch for it.

### 2.3 `sanjaymarison/OllamaBook-Summarize` — extraction reference
Summarizes pdf/epub with Ollama. **Lesson to take:**
- **TOC inference**: when the document has no table of contents, it sends the first ~20 pages to the LLM to infer the chapter structure. Have a `--manual-toc FILE` (JSON) fallback.
- Reference constants it uses: `MAX_CHUNK_CHARS=12000`, `CHUNK_OVERLAP=800`. Good starting points, tune them.

---

## 3. Core architectural principle: the IR (Intermediate Representation)

**This is the key decision that makes the project tractable.** TBL can't summarize easily because it works directly on the EPUB's XHTML. We introduce an intermediate, format-agnostic model.

Define an **Intermediate Document (IR)** with `pydantic`:

```
Document
  ├─ metadata: title, author, language, source_format
  ├─ images: { image_id -> ImageAsset(bytes, mime, original_path, alt_text) }
  └─ chapters: [ Chapter ]
       Chapter
         ├─ title
         └─ blocks: [ Block ]
              Block(type, ...) where type ∈ {
                heading(level, text),
                paragraph(text),
                code(language, text),     # NEVER summarized or split
                image(image_id, caption), # references the asset by id
                table(rows),              # preserved or summarized carefully
                quote(text),
                list(items)
              }
```

The whole pipeline operates on the IR:
- **Parsers** (EPUB, PDF) → produce a `Document`.
- **Condenser** and **Translator** → transform the text `Block`s of the `Document` (leaving `code` and `image` structurally intact).
- **Renderers** (EPUB, PDF, MD) → consume a condensed `Document` and emit the final file.

Benefit: adding an input or output format = writing one parser or one renderer, without touching the condensation logic. Images live as **assets referenced by id**, so they survive condensation naturally.

---

## 4. Tech stack

> **License rule (see §14):** all runtime dependencies must be permissive (MIT/BSD/Apache). **AGPL is forbidden** because it would drag Brevia into AGPL even if we don't copy any code. This rules out `ebooklib` and `PyMuPDF` (both AGPL-3.0), which were the obvious choice but would tie us down.

- **Python 3.11+**
- **CLI:** `typer` (+ `rich` for progress/tables). *(MIT)*
- **EPUB read/write:** **our own**, with `zipfile` (stdlib) + `lxml`/`beautifulsoup4`. An EPUB is a ZIP with XHTML + an `.opf` + a manifest; we parse and assemble it ourselves. **Do not use `ebooklib` (AGPL-3.0).** It's a bit more work but gives us our own, license-clean EPUB handling.
- **PDF read:** `pdfplumber` *(MIT)* for text/tables + `pypdf` *(BSD)* for embedded images and metadata. **Do not use `PyMuPDF` (AGPL-3.0).**
- **PDF write:** `weasyprint` *(BSD)* — HTML/CSS → PDF, reflowable, handles images. We generate HTML from the IR and weasyprint converts it.
- **Tokens:** `tiktoken` *(MIT)* (with a character-based fallback).
- **LLM:** `httpx` *(BSD)* (async) for our own providers, **and/or** `litellm` *(MIT)* as a unifying layer over 100+ providers. Leaning on **LiteLLM** instead of hand-writing each provider is recommended — besides saving work, it removes the temptation to copy TBL's provider layer (AGPL).
- **Config/models:** `pydantic` v2 *(MIT)* + `python-dotenv` *(BSD)*.
- **NER glossary (optional, late phase):** `spacy` *(MIT)*.

### Quality tooling (from Phase 0)
- **`ruff`** — linter + formatter (replaces isort/flake8/black in a single, very fast tool). Config in `pyproject.toml`. Enable at least the rule sets: `E,F,I,UP,B,SIM`.
- **`mypy`** — static type checking in `strict` mode. Pays off a lot with the pydantic IR; type the whole pipeline.
- **`pytest`** + **`pytest-asyncio`** — tests (see §11).
- **`pre-commit`** with hooks: `ruff` (lint+format), `mypy`, and `pytest` for unit tests. Better than TBL, which only uses isort + basic hooks and has no type checking.
- **`pip-licenses`** — license audit of the full dependency tree. Run it in CI with `--fail-on "GPL"` so the pipeline fails if any dependency (direct or transitive) introduces GPL/AGPL. This is the automated guarantee behind §14, not a manual review.

---

## 5. Pipeline architecture

```
  INPUT (.epub / .pdf)
        │
        ▼
  [1] Parser ───────────────► Document (IR)
        │                         (chapters, blocks, image assets)
        ▼
  [2] Chunker ──────────────► chunks (respects chapters and blocks,
        │                            ~2000 tokens, never splits code)
        ▼
  [3] Condenser (LLM) ──────► per chunk: condensed text +
        │                            keep/drop decision for images
        ▼
  [4] Synthesizer (LLM) ────► hierarchical per-chapter pass:
        │                            adjusts to target length, smooths
        ▼
  [5] (optional) Translator ► translates the text blocks of the
        │                            condensed IR (+ glossary)
        ▼
  [6] Image selector ───────► keeps images marked essential
        │                            (+ optional vision-model ranking)
        ▼
  [7] Renderers ────────────► EPUB (own zip) + PDF (weasyprint) + MD
        │
        ▼
  OUTPUT/  condensed-book.{epub,pdf,md}

  Checkpoint/resume active between [2]–[5].
```

---

## 6. Proposed folder structure

```
brevia/
  __init__.py
  cli.py                 # typer entrypoint
  config.py              # pydantic settings + .env
  ir/
    models.py            # Document, Chapter, Block, ImageAsset (pydantic)
  parsers/
    base.py
    epub_parser.py       # zipfile + lxml/bs4 -> Document
    pdf_parser.py        # pdfplumber + pypdf -> Document (with TOC inference)
    toc_inference.py     # fallback: LLM infers chapters
  llm/
    base.py              # Protocol: async generate(messages, model, **opts)
    factory.py
    key_pool.py          # key rotation (adapted from TBL)
    rate_limit.py
    providers/
      ollama.py
      openai.py          # covers OpenAI-compatible via base_url (vLLM/LM Studio)
      gemini.py
      openrouter.py
  condense/
    chunker.py           # chapter-aware, token-based chunking
    condenser.py         # prompt + per-chunk call
    synthesizer.py       # hierarchical pass / length control
    prompts.py           # prompt templates (condense / synthesize)
  translate/
    translator.py        # translates the IR text blocks
    glossary.py          # term consistency (adapted from TBL)
  images/
    selector.py          # basic keep/drop
    vision_ranker.py     # optional: vision-model ranking
  render/
    base.py
    md_renderer.py
    epub_renderer.py     # own builder (zipfile), re-embeds kept assets
    pdf_renderer.py      # IR -> HTML -> weasyprint
  persistence/
    checkpoint.py        # resume (adapted from TBL)
  utils/
    tokens.py
    security.py          # path validation (zip-slip), etc.
tests/
  fixtures/              # a tiny .epub and .pdf for testing
  ...
pyproject.toml
.env.example
README.md
```

---

## 7. The hard parts (design detail)

### 7.1 Image preservation — THE DIFFERENTIATOR
None of the reference repos do this well; it's our reason to exist.

- In the parser, extract **each image as an `ImageAsset`** (bytes + mime + alt_text) and replace its appearance in the text with a `Block(type=image, image_id=...)`.
- In the condensation prompt, present the chunk's images as readable references (e.g. `[IMG:fig-3.1 — "architecture diagram"]`) and ask the LLM to **mark which ones are essential** to understanding the retained content (keep/drop decision), but **without** turning all the text into placeholders (unlike TBL).
- **Strategy A (default, cheap):** keep every image whose anchoring section/paragraph survives the condensation. Drop only when its whole section is cut.
- **Strategy B (opt-in `--rank-images`, uses a vision model):** send the image + surrounding text to a vision-capable model (Gemini, GPT-4o, or a local VLM) → score importance (diagram/figure/technical chart vs. decorative) and optionally **regenerate a caption**. Keep those above the threshold.
- In the render, **re-embed only the kept assets** into the new EPUB, link them in the PDF/HTML and in the MD (`![caption](images/fig-3.1.png)`).

### 7.2 Chunking
- **Respect the structure**: chunk per chapter; within a chapter, group blocks up to ~**2000 tokens** (not TBL's ~450; summarization needs more context — see the paper in §2.2).
- **NEVER split a `Block(code)` or a table** across two chunks.
- Light context overlap (~the last block) for continuity, inspired by `CHUNK_OVERLAP` from OllamaBook-Summarize.

### 7.3 Hierarchical summarization
Summarizing each chunk independently produces a choppy result. Two levels:
1. **Per-chunk condensation** (step 3): each chunk → condensed version keeping examples/code/image refs.
2. **Per-chapter synthesis** (step 4): take a chapter's condensations and smooth transitions / trim to approach `--target-ratio`. This is where the real length is tuned.

Length control: estimate output tokens and, if it exceeds the target, run an extra trimming pass. If a chunk's output comes out **longer than the input**, flag it as an error (cognitivetech disclaimer).

### 7.4 Multi-provider
- `llm/base.py`: protocol `async generate(messages, model, **opts) -> str` (and `generate_with_image(...)` for vision ranking).
- `factory.py` selects by `--provider`.
- **OpenAI-compatible:** the `openai.py` provider must accept `--api-endpoint` (base_url) to point at vLLM/vllm-metal/LM Studio/LocalAI. (Note for the owner: on the M3 Max, the efficient path is **Ollama** or LM Studio MLX; vLLM gives no benefit for a single user.)
- Key rotation and rate-limit handling adapted from TBL's `key_pool.py` / `rate_limit_handler.py`.

### 7.5 Integrated translation
Since the owner wants it **in the same pass**: after condensing (and before rendering), translate the text `Block`s of the condensed IR using the **same LLM layer** + glossary. Translating the **already-condensed** book is much cheaper than translating the full book. Code blocks stay untranslated; in paragraphs, preserve identifiers/URLs/paths (take the `preserve_technical_content` idea from TBL's prompt_options).

---

## 8. CLI design

```bash
brevia condense INPUT.epub \
  --provider ollama \              # ollama|openai|gemini|openrouter
  --model qwen3:14b \
  --api-endpoint URL \             # optional, for OpenAI-compatible (vLLM/LM Studio)
  --target-ratio 0.30 \            # 0.30 = ~30% of the original
  --formats epub,pdf,md \          # any of the three
  --source-lang English \
  --translate-to Spanish \         # optional; omit = no translation
  --rank-images \                  # optional; enables the vision model
  --glossary glossary.json \       # optional
  --out ./output/ \
  --resume \                       # resume from checkpoint
  --dry-run                        # only estimate tokens/cost, no LLM call
```

`--dry-run` must estimate input tokens and approximate cost per provider before launching an expensive job.

---

## 9. Configuration (`.env` + defaults)

```
LLM_PROVIDER=ollama
OLLAMA_ENDPOINT=http://localhost:11434
DEFAULT_MODEL=qwen3:14b

OPENAI_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=

DEFAULT_TARGET_RATIO=0.30
DEFAULT_CHUNK_TOKENS=2000
IMAGE_STRATEGY=keep_referenced   # keep_referenced | vision_ranked
```
Keys accept a comma-separated list for rotation (TBL pattern).

---

## 10. Phased build plan (milestones)

Build in this order; each phase must be functional and tested before the next. Each phase = one PRP.

- **Phase 0 — Scaffold.** `pyproject.toml`, folder structure, `config.py`, CLI skeleton with `typer`, `.env.example`. **Quality tooling configured up front:** `ruff` (lint+format) and `mypy` (strict) in `pyproject.toml`, `pytest` + `pytest-asyncio`, and `.pre-commit-config.yaml` with ruff + mypy + pytest hooks. **CI workflow** (`.github/workflows/ci.yml`) running ruff + mypy + pytest + **`pip-licenses --fail-on "GPL"`** (blocks any GPL/AGPL dependency). `LICENSE` file with **Apache-2.0** and a `NOTICE` with the inspiration attribution (see §14). LLM layer `base.py` + `factory.py` + **Ollama** provider (or LiteLLM) working with a "hello world." `mypy` and `ruff` must pass clean from the first commit.
- **Phase 1 — IR + EPUB parser.** Define `ir/models.py`. `epub_parser.py`: EPUB → `Document` with image extraction as assets. Test that it parses the fixture without losing blocks or images.
- **Phase 2 — Markdown renderer.** `md_renderer.py`: `Document` → `.md` (with image links). This validates the IR end-to-end (parse EPUB → render MD) **without an LLM yet**.
- **Phase 3 — Chunker + checkpoint.** Chapter-aware, token-based chunking, without splitting code. Persistence/resume.
- **Phase 4 — Condenser.** `condenser.py` + prompts. Condenses per chunk, marks keep/drop for images. Watch for "output > input".
- **Phase 5 — Hierarchical synthesis + length control.** Approach `--target-ratio`.
- **Phase 6 — Image selector (strategy A) + EPUB renderer.** `epub_renderer.py` with our own builder (zipfile) re-embedding only kept images. Validate it opens in a real reader.
- **Phase 7 — PDF renderer.** IR → HTML → `weasyprint`.
- **Phase 8 — PDF parser.** `pdf_parser.py` with pdfplumber + pypdf + `toc_inference.py` (LLM fallback) + `--manual-toc`.
- **Phase 9 — More providers.** OpenAI (+ OpenAI-compatible via base_url), Gemini, OpenRouter. Key rotation.
- **Phase 10 — Integrated translation + glossary.**
- **Phase 11 — Vision-based image ranking (`--rank-images`).**
- **Phase 12 — Polish.** `--dry-run` with cost estimation, logging with `rich`, README.

**Useful MVP = Phases 0–7** (summarize an EPUB and output EPUB+PDF+MD with Ollama). Everything else is incremental.

---

## 11. Testing

- **IR round-trip:** parse EPUB → render EPUB produces an equivalent document (same blocks, same images).
- **Chunking:** never splits a code block; respects chapter boundaries; size within range.
- **Image tracking:** every image in the original appears as an asset with a unique id; the selector keeps/drops based on marks.
- **Mock provider** (deterministic) for pipeline tests without calling a real LLM.
- **Fixtures:** a small `.epub` and `.pdf` in `tests/fixtures/`.

---

## 12. Mistakes NOT to repeat (lessons from studying TBL and cognitivetech)

Detected in TBL's real issues/code — avoid them by design:
- **Don't copy the 1:1 placeholder reinjection.** (See §2.1.)
- **Chunks too small:** TBL uses ~450 tokens (fine for translation, bad for summarization). Use ~2000.
- **Don't split code blocks or tables** across chunks (TBL had paragraph-break losses at chunk boundaries — issue #208; PDF/DOCX broke images — #143).
- **Zip-slip:** validate the entry paths when unpacking an EPUB (TBL had this bug — #215). Implement it in `utils/security.py`.
- **Don't commit API keys or job databases** (TBL had keys in cleartext in git — #213). `.env` in `.gitignore`.
- **SSRF / attacker endpoint:** if an arbitrary `--api-endpoint` is allowed, don't forward other providers' keys to that host (TBL — #211).
- **"Output longer than input" = red flag** (cognitivetech disclaimer): detect it and warn.
- **Verify that figure references aren't hallucinated** after condensing.

---

## 13. v1 acceptance criteria

1. `brevia condense book.epub --provider ollama --model qwen3:14b --formats epub,pdf,md --out ./out/` produces all three files.
2. The condensed EPUB opens in a standard reader, weighs a fraction of the original, and **keeps the code blocks and the images that remained in retained sections**.
3. With `--translate-to Spanish`, the condensed content comes out in Spanish, with code untranslated.
4. `--resume` resumes an interrupted job without redoing already-processed chunks.
5. `--dry-run` reports estimated tokens and approximate cost without calling the LLM.
6. The final length lands reasonably close to `--target-ratio`.

---

## 14. Licensing and how to stay clean (clean-room)

> I'm not a lawyer and this isn't legal advice; it's the standard engineering practice for not inheriting copyleft obligations. If Brevia becomes a serious product, a real legal review is worthwhile.

**The key principle:** copyright protects the **expression** (the code as written), **not the ideas, the architecture, the algorithms, or the APIs**. Drawing inspiration from how TBL solves the problem and reimplementing it with your own code does **not** bind you to its AGPL. What binds you is **copying its code**.

There are **two contagion channels** for AGPL, and both must be blocked:

**Channel 1 — Copying code.** Concrete rules:
- **Never** copy/paste code from TBL (nor from cognitivetech / OllamaBook-Summarize) into `brevia/`. Not "disguised line-by-line translation" either — that's still a derivative work.
- Keep the reference repos **outside** the Brevia repo (in `~/projects/open-source/`). **Don't vendor them** or add them as a submodule.
- Clean-room flow: read to **understand** the pattern → close the file → implement from *this* spec, with your own structure, names, and style. What's in §2 is "what to study," not "what to copy."
- Where TBL solves a generic problem, **use a permissive library** instead of porting its code. Examples: `LiteLLM` (MIT) instead of porting `src/core/llm/providers/`; your own EPUB with `zipfile` instead of its `epub/`. This removes the temptation and the risk at the root.
- Attribution is good faith and fine: a `NOTICE` saying *"architecture studied and inspired by TranslateBooksWithLLMs and ollama-ebook-summary; no code reused."* Attribution by itself does **not** create AGPL obligations (what would create them is using their code).

**Channel 2 — Depending on AGPL libraries at runtime.** Even if you copy nothing, if Brevia *imports* an AGPL library, distributing or exposing Brevia as a service inherits the AGPL obligations. Therefore:
- **Forbidden at runtime:** `ebooklib` (AGPL-3.0) and `PyMuPDF`/`fitz` (AGPL-3.0). Confirmed on their repos/PyPI.
- Permissive substitutes already chosen in §4: own EPUB with `zipfile`+`lxml`; PDF with `pdfplumber` (MIT) + `pypdf` (BSD); output PDF with `weasyprint` (BSD).
- Dependency license table (verify when pinning versions; licenses can change):

| Dependency | License | OK |
|---|---|---|
| typer, rich, tiktoken, litellm, pydantic, spacy, pdfplumber | MIT | ✅ |
| httpx, pypdf, weasyprint, python-dotenv | BSD | ✅ |
| ruff, mypy | permissive (MIT/Apache) | ✅ (dev only) |
| **ebooklib** | **AGPL-3.0** | ❌ do not use |
| **PyMuPDF / fitz** | **AGPL-3.0** | ❌ do not use |

**Brevia's license: Apache-2.0.** With both channels blocked, you're free to choose; we go with **Apache-2.0** because it's fully permissive (free for the community: anyone can use, modify, and even integrate it into closed products) and additionally includes an **explicit patent grant** and retaliation clause — relevant in the LLM space, where patents are a latent risk; it protects both the project and adopters from a contributor later asserting a patent. It lets you turn Brevia into a closed product later without issue. (MIT would be the more minimalist and also valid alternative; Apache was chosen for the extra patent-protection layer.)

**Pre-publish checklist:**
1. Safety `grep`: no Brevia file contains blocks copied from the reference repos.
2. CI green: `pip-licenses --fail-on "GPL"` passes, confirming no dependency (direct or transitive) is GPL/AGPL.
3. `LICENSE` exists with **Apache-2.0** and `NOTICE` with the inspiration attribution.
4. The reference repos are not inside the project tree.
