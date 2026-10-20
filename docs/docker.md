# Run BreviaBook with Docker

Build the CLI and PDF libraries inside Docker, read books from `input`, and save results in
`output` on your machine. Run commands from the repository root in PowerShell or Bash/sh;
Docker and Ollama commands work in both, with shell-specific setup shown separately.

For book selection, hidden API-key prompts, and automatic resume detection, use the
[interactive Python launcher](../breviabook-cli/README.md): `python breviabook-cli`.
It recommends Docker when available and falls back to local Python when Docker is unavailable.

## Requirements

**Your host machine → Docker and Ollama; terminal → any directory.**

Docker is required for every run. Steps 2–4 are for local Ollama runs; cloud providers only
need Docker and their API key.

1. Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/) with
   Linux containers, or use Docker Engine with the Compose plugin on Linux.
2. Install [Ollama](https://ollama.com/download) on your host ([Windows installer](https://ollama.com/download/windows)),
   start it, and open a new terminal.
3. Download this project's default model:

   ```sh
   ollama pull gemma4:e4b
   ```

4. Check the installed model and test a response:

   ```sh
   ollama list
   ollama run gemma4:e4b "Reply with OK."
   ```

   The list should include `gemma4:e4b`, and the test should return a response.

Ollama manages the downloaded model on your host; `ollama pull` works from any directory.
The BreviaBook container connects to that running Ollama server.

## Build

**[Docker Desktop](https://www.docker.com/products/docker-desktop/) → Linux containers, or Docker Engine with Compose on Linux; terminal → repository root.**

1. Start Docker Desktop or your Docker Engine.
2. Create the input and output folders; Compose requires these host mount folders to exist.

   PowerShell:

   ```powershell
   New-Item -ItemType Directory -Force input, output | Out-Null
   ```

   Bash/sh (Linux or macOS):

   ```sh
   mkdir -p input output
   ```

3. On Linux, use your regular user's IDs so the container can write to the output mount:

   ```sh
   export DOCKER_UID="$(id -u)"
   export DOCKER_GID="$(id -g)"
   ```

4. Build the image:

   ```sh
   docker compose build
   ```

5. Copy your book into `input`, for example `input/book.epub`.

## Use local Ollama

**Ollama on your host; terminal → repository root.**

1. Estimate the work without calling the model:

   ```sh
   docker compose run --rm breviabook condense /input/book.epub --dry-run
   ```

2. Condense the book and write EPUB, PDF, and Markdown files:

   ```sh
   docker compose run --rm breviabook condense /input/book.epub --provider ollama --model gemma4:e4b --out /output
   ```

3. Resume an interrupted run with the same options and `--resume`:

   ```sh
   docker compose run --rm breviabook condense /input/book.epub --provider ollama --model gemma4:e4b --out /output --resume
   ```

## Use a cloud provider

**Terminal → repository root.**

1. Set the selected provider's key in this shell, or in the project's `.env` file:

   PowerShell:

   ```powershell
   $env:OPENAI_API_KEY = "your-key"
   ```

   Bash/sh:

   ```sh
   export OPENAI_API_KEY="your-key"
   ```

2. Run with the provider and model you use:

   ```sh
   docker compose run --rm breviabook condense /input/book.epub --provider openai --model YOUR_MODEL --out /output
   ```

3. Remove the key from this shell when finished:

   PowerShell:

   ```powershell
   Remove-Item Env:OPENAI_API_KEY
   ```

   Bash/sh:

   ```sh
   unset OPENAI_API_KEY
   ```

Compose passes only the three supported provider-key variables plus the model and Ollama
endpoint; it does not mount `.env` or copy it into the image. Cloud runs send book content to
the selected provider and can incur charges.

## Translate

**Terminal → repository root.**

1. Translate without condensing:

   ```sh
   docker compose run --rm breviabook translate /input/book.epub --to Spanish --provider ollama --model gemma4:e4b --out /output
   ```

Put glossary or manual TOC JSON files in `input` and refer to them using `/input/...`.

## Verify

**Terminal → repository root; file manager → repository's `output` folder.**

1. Check the installed CLI:

   ```sh
   docker compose run --rm breviabook version
   ```

2. After condensing `book.epub`, check `output/book-condensed.epub`,
   `output/book-condensed.pdf`, and `output/book-condensed.md`; images are in `output/images`.
3. Keep `output/.breviabook` to resume work; removing a container with `--rm` keeps these host files.

## Gotchas

- Directory creation is only needed when the folders are missing; PowerShell's `-Force` and Bash/sh's `-p` allow reruns, while `Out-Null` only hides PowerShell's directory listing.
- Docker reduces host access; it does not fix the application or dependency vulnerabilities found in the security review. Treat generated files as untrusted.
- The Compose service runs as a non-root user, drops capabilities, uses a read-only root filesystem, and mounts only `input` (read-only) and `output` (writable); use Compose to get these runtime restrictions.
- Only place books you intend to process in `input`; the container can read that entire folder and modify everything in `output`.
- Network access remains enabled for Ollama and cloud APIs; no ports are published and the Docker socket is not mounted.
- Ollama must accept connections from Docker at `host.docker.internal:11434`; if needed, bind Ollama to a host interface reachable from Docker and restrict port 11434 to trusted local traffic with your firewall.
- To use another Ollama server, set `$env:DOCKER_OLLAMA_ENDPOINT = "http://YOUR_SERVER:11434"` in PowerShell or `export DOCKER_OLLAMA_ENDPOINT="http://YOUR_SERVER:11434"` in Bash/sh before running Compose.
- To skip PDF generation, append `--formats epub,md`; PDF libraries are already included in the image.
- On Linux, repeat the `DOCKER_UID` and `DOCKER_GID` exports when opening a new shell; Windows Docker Desktop handles bind-mount permissions.
- Processing is limited to 4 GB RAM, 2 CPUs, 256 processes, and 256 MB temporary storage; adjust `compose.yaml` for large books. The host Ollama model has separate resource requirements.
- Builds use the checked-in lockfile with `uv sync --locked`; upgrading vulnerable dependencies requires updating that lockfile and rebuilding.
- Use an up-to-date Docker Desktop; container isolation also depends on your Docker engine and host security.
- The interactive launcher detects a missing Buildx endpoint and uses the legacy builder for that run before starting a fresh container for the selected book; it preserves global Docker settings.
- For manual builds reporting `docker endpoint for "default" not found`, temporarily set `DOCKER_BUILDKIT=0` and `COMPOSE_DOCKER_CLI_BUILD=0` in your shell and retry `docker compose build`; repair Docker contexts or update Docker Desktop before switching back to BuildKit.
