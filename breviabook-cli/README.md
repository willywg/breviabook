# Interactive CLI

This separate Python launcher lets you check multiple books and process them in a queue,
using Docker (recommended) or local Python. It needs Python 3.11+ and Questionary for the
checkbox UI; Docker mode needs Compose, while local mode needs the engine's dependencies.

## Start

**Terminal → repository root.**

1. Install the launcher's UI dependency once:

   ```sh
   python -m pip install -r breviabook-cli/requirements.txt
   ```

2. Run the launcher:

   ```sh
   python breviabook-cli
   ```

   It creates the repository's `input` and `output` folders if missing.

3. Drop `.epub` or `.pdf` books into `input`, using any filenames or subfolders.
4. Press Enter to rescan if the folder was empty.
5. Select books with the checkboxes; **no books are checked by default**. Use arrows to
   move, Space to toggle a book, **a** to toggle all, **i** to invert, and Enter to queue.
6. Select **Docker (recommended)** or **Local Python** if Docker is available; otherwise
   the launcher selects local Python automatically.
7. Select OpenRouter, OpenAI, Gemini, or Ollama.
8. Enter the model ID you use with that provider; OpenRouter IDs use `publisher/model-name`.
9. Enter the API key at the hidden prompt; Ollama does not require a key.

Books run one at a time. Runtime and provider choices are reused across the queue, and each
provider's key is requested once per batch and kept in memory. Interrupted books keep their
own saved settings; completed books still ask redo/cancel. Submit with no books checked
to rescan input, or press Ctrl+C to quit.

All single-choice menus use the same arrow-key picker: **What next?**, provider, runtime,
PDF handling, and rescan/quit. Use arrows to highlight one option and Enter to select it.
Books use checkboxes for multiple selections. Existing defaults, including **Cancel** for
completed books, remain highlighted automatically.

Docker mode checks Compose and the active engine, including Docker-compatible setups.
Each Docker run prepares a shared image using cached layers, then creates and starts a new
container named after the book. The container is removed when it exits; summaries and
checkpoints remain in `output`. A missing Buildx endpoint triggers a process-local
legacy-builder workaround automatically.
Keys are passed through the child process environment and are never saved by the launcher.
Interactive terminals use the engine's original live progress bars and usage panel.
Redirected output prints updated completion counts as chunks and chapters finish.

## Prepare local Python

**Terminal → repository root; use these steps when Docker is unavailable or local mode is preferred.**

1. Create a virtual environment:

   ```sh
   python -m venv .venv
   ```

2. Install the engine dependencies using that environment:

   PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e .
   ```

   Bash/sh:

   ```sh
   .venv/bin/python -m pip install -e .
   ```

3. Launch directly in local mode:

   ```sh
   python breviabook-cli --runtime local --formats epub,md
   ```

The launcher prefers the repository's `.venv`; otherwise it uses the Python running the
launcher. It checks engine dependencies before prompting for keys or starting paid work.
If PDF libraries are unavailable, it offers to continue with the other requested formats.

## Resume or redo

**Terminal → launcher's book menu.**

1. Select a book marked **Resume** to continue with its saved provider, model, ratio,
   formats, and concurrency; enter the key again.
2. Select a **Completed** book and choose **Redo summary** or **Cancel**.
3. For **Existing files (completion unknown)**, choose resume, redo, or cancel;
   use the original provider and model when resuming an older command-line run.
4. Press Ctrl+C to stop processing; relaunch and select the book to resume.
5. Select either runtime when resuming; Docker and local mode share the same checkpoints.

Redo creates a separate `run-002`, `run-003`, etc. directory and preserves previous results.
Changing the provider or model while resuming also starts a separate attempt.

## Change defaults

**Terminal → repository root.**

1. Set options for new jobs as needed:

   ```sh
   python breviabook-cli --target-ratio 0.4 --formats epub,md --concurrency 2
   ```

2. Offer a model as the default at the prompt:

   ```sh
   python breviabook-cli --model publisher/model-name
   ```

3. Run from another directory by using the launcher's absolute path:

   ```powershell
   python Z:\github\ccpu\breviabook\breviabook-cli
   ```

4. Skip runtime selection by passing `--runtime local` or `--runtime docker`; explicit
   Docker mode reports an error if Docker is unavailable.

## Verify

**File manager → repository's `output` folder; terminal → repository root.**

1. Open the output directory printed by the launcher, normally
   `output/book-<title>-<identity>/run-001/`, and check the requested summary files.
2. Relaunch and confirm that the book is marked **Completed**.
3. Confirm the runtime menu recommends Docker when available, or reports local fallback
   when Compose or the engine is unavailable.
4. Run the launcher's tests without Docker or API calls:

   ```sh
   python -m unittest discover -s breviabook-cli/tests -v
   ```

## Gotchas

- Books are identified by relative input path and file contents; renaming or changing a book starts a new job.
- Older files directly in `output` have no reliable completion record, so the launcher asks what to do.
- Inputs sharing a filename stem use separate job directories; older shared output cannot be attributed safely and is not imported for them.
- Keep `job.json` and `.breviabook` checkpoints to resume; API keys are not stored in either by the launcher.
- Keys already set in the selected provider's environment variable can be reused by pressing Enter at the hidden prompt.
- Resume keeps the saved ratio, formats, and concurrency; CLI defaults apply to new jobs and redo.
- If a requested format is missing or empty, the book stays resumable; check the engine warnings.
- Cloud providers receive the book's content and can charge for processing; the launcher does not make free test requests.
- Local PDF output needs the `[pdf]` extra and system libraries; see [PDF output requirements](../README.md#pdf-output-requirements), or select Docker.
- Local mode has access to your host filesystem; Docker mode retains the existing Compose restrictions.
- Existing Docker setup and Ollama endpoint details are in [the Docker guide](../docs/docker.md).
- UI changes apply on the next launch; an already-running container keeps its current display.
- When input/output is redirected, use comma-separated numbers/ranges or type `all` to select books; Enter with no selection rescans input.
- Ctrl+C stops the current job and cancels the remaining queue; saved checkpoints can be resumed later.
