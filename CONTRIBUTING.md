# Contributing

Thanks for helping. songforge is small on purpose: a FastAPI server, one worker, one HTML file.

## Setup

```bash
git clone https://github.com/Arthur031221/songforge
cd songforge
uv sync
uv run pytest
```

The test suite uses a fake engine and runs in seconds without weights. To work on the UI without a model, start the studio with the fake engine:

```bash
SONGFORGE_HOME=/tmp/sf SONGFORGE_FAKE_DELAY=4 uv run songforge serve --engine fake
```

## Before you open a pull request

- `uv run ruff check . && uv run ruff format --check .`
- `uv run pytest`
- Keep the UI in `src/songforge/static/index.html`. No build step, no framework.
- Keep new dependencies out of the server unless there is no reasonable alternative.
- If you change engine behavior, run one real song with `songforge bench` and paste the table in the PR.

## Benchmarks from your Mac

Numbers from other Macs are welcome. Run `songforge bench --json` and open an issue with the output and your Mac model.
