# PRP: Phase 11 — Vision-based image ranking (`--rank-images`)

> Product Requirement Prompt for **Brevia**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §7.1 (Strategy B), §7.4, §6, §8, §10 (Phase 11).
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 006 (selector) and PRP 009 (providers).

## Goal

Add opt-in Strategy B: send each kept image (+ surrounding text) to a vision-capable model,
score its importance, drop those below a threshold, and optionally regenerate a concise
caption. Enabled by `--rank-images`. Uses the same provider/model (Gemini is multimodal).

## Why

- Strategy A (structural) keeps any image whose section survives; Strategy B prunes the
  decorative/redundant ones and improves captions — the project's differentiator (§7.1).

## Scope

**In scope:**
- `llm/base.py`: `VisionProvider` Protocol with `generate_with_image(prompt, images, model)`.
- `LiteLLMProvider` + `OllamaProvider`: implement `generate_with_image` (OpenAI-style image
  content / data URIs); reuse key rotation + usage accounting.
- `images/vision_ranker.py`: `VisionRanker.rank(doc) -> Document` — per referenced image,
  gather context, call the vision model, keep iff score ≥ threshold, update caption, prune
  dropped assets.
- Pipeline: when `rank_images`, run the ranker after translation, before the Strategy A
  selector; clear error if the provider isn't vision-capable. CLI: wire `--rank-images`.
- Tests (injected completer / fake vision provider; no network).

**Out of scope:** a separate `--vision-model` flag (reuse provider/model), local-VLM specifics.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §7.1)

- [ ] Opt-in only (`--rank-images`); default path unchanged (Strategy A).
- [ ] Code/tables/text never affected; only image keep/drop + captions change.
- [ ] Dropped images' assets are pruned (no orphan embeds).
- [ ] Vision calls reuse the provider (usage/cost accrues); clear error if not vision-capable.
- [ ] No GPL/AGPL deps; images sent as data URIs (no temp files).

## Context & references

```yaml
- docs/ROADMAP.md          # §7.1 Strategy B (score + regenerate caption), §7.4 generate_with_image
- brevia/llm/providers/litellm_base.py  # provider base to extend
- brevia/images/selector.py             # Strategy A (runs after)
- brevia/ir/models.py                    # ImageBlock/ImageAsset, model_copy
- litellm multimodal: messages=[{role:user, content:[{type:text},{type:image_url,image_url:{url:data:...}}]}]
```

## Design

- `generate_with_image(prompt, images: list[(bytes, mime)], model)`: build a user message with
  a text part + one image_url (data URI) per image; route through the same `_run` (rotation +
  usage) as `generate`.
- `VisionRanker(provider, model, *, threshold=0.5, update_captions=True)`:
  - For each `ImageBlock` with an asset, build context (its caption + nearest preceding/following
    heading/paragraph text in the chapter).
  - Prompt → JSON `{"score": 0..1, "essential": bool, "caption": str}`. Keep iff
    `score ≥ threshold`. Update caption when enabled and provided.
  - Rebuild the document; prune assets no longer referenced.

## Implementation blueprint

1. `llm/base.py`: `VisionProvider` Protocol.
2. `litellm_base.py`: factor `_run`, add `generate_with_image`; same in `ollama.py`.
3. `images/vision_ranker.py`: ranker + prompt + verdict parsing.
4. `pipeline.py`: `condense_book(..., rank_images=False)`; run ranker when set.
5. `cli.py`: pass `--rank-images` (drop the "ignored" notice).
6. Tests: `tests/test_vision_ranker.py`, provider vision test, pipeline `--rank-images` e2e,
   not-vision-capable error.

### New / changed files

- `brevia/llm/base.py`, `brevia/llm/providers/litellm_base.py`, `brevia/llm/providers/ollama.py`
- `brevia/images/vision_ranker.py`, `brevia/pipeline.py`, `brevia/cli.py`
- tests as above

## Validation gates (must all pass)

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict brevia
uv run pytest -q && uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `generate_with_image` sends a text part + image data URI(s) and returns content.
- [ ] Ranker keeps images with score ≥ threshold, drops below, updates captions, prunes assets.
- [ ] Pipeline with `--rank-images` drops a low-scored image end-to-end; default path unchanged.
- [ ] A non-vision provider raises a clear error when `--rank-images` is set.

## Acceptance criteria

- [ ] `--rank-images` enables vision ranking via the same (Gemini) provider.
- [ ] Strategy B composes with Strategy A; assets stay consistent.
- [ ] All five validation gates green.

## Confidence score

7/10 — Multimodal message shape is the main external unknown; isolated behind
`generate_with_image` and tested via injected completers.
