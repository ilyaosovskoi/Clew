# Contributing to Clew

Thanks for your interest in making Clew better. This is a small project — a few practical rules to keep things smooth.

## Quick start

```bash
git clone https://github.com/OpenSynapseLabs/Clew.git
cd Clew
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Run the test suite
pytest clew/ -v

# Run a specific test module
pytest clew/test_lsp.py -v
pytest clew/test_code_chunker.py -v

# Launch Clew
clew
```

## Project conventions

These invariants are documented inline and MUST be preserved in any PR:

1. **Thread safety** — every shared-state mutation is guarded by an `RLock`. If you add a new shared field on a class, wrap it in a lock or document why it doesn't need one.
2. **No `shell=True`** — all subprocess calls use `shlex.split()` + `shell=False`. The terminal panel falls back to `bash -c` only when `shlex.split` fails on unbalanced quotes (and that fallback is opt-in, not the default).
3. **No telemetry** — Clew does not phone home. Ever. No analytics, no crash reporting, no "anonymous usage stats." If your PR adds a network call, it must be (a) opt-in and (b) in response to a user action.
4. **Graceful degradation** — every optional dependency has a fallback path. `sentence-transformers` not installed? Random vectors. `faiss` missing? Brute-force search. `python-lsp-server` missing? No autocomplete, but the editor still works. Preserve this in any new integration.
5. **Local-first** — local model inference is the default. API providers (OpenAI, Anthropic, OpenRouter) are an opt-in escape hatch, not the primary path. Don't make any feature that *requires* a cloud model.
6. **Theme centralization** — all colors live in `clew/theme.py`. Don't hardcode hex codes in panel stylesheets. Use `from clew.theme import COLORS, FONTS, RADII`.

## What to work on

The highest-leverage contributions right now (in rough priority order):

1. **The REMAINING Known Issues** in [README.md](README.md#known-issues):
   - `lsp_client.py` stderr drain thread (medium-difficulty threading work)
   - Lazy file-tree population in `FileExplorer._populate_tree` (QTreeWidgetItem lazy-load refactor)
   - `QFileSystemWatcher` on `set_root()` (easy)
   - `.gitignore` support in `_ripgrep_search` / `_python_search` (easy)
2. **Git integration** — branch status, diff viewer, commit UI, blame. The biggest missing IDE feature.
3. **Inline "Apply" button** in chat → diff preview → patch the editor. Infrastructure exists, needs UI wiring.
4. **`.dmg` notarization** — if you have an Apple Developer Program membership and want to sponsor or co-sign the build, please open an issue.
5. **Windows / Linux builds** — currently macOS Apple Silicon only. The llama.cpp backend works everywhere; the polished experience needs porting.

## Reporting bugs

Open a [GitHub Issue](https://github.com/OpenSynapseLabs/Clew/issues) with:

1. Clew version (from **Settings → About** or `clew --version`)
2. macOS version (`sw_vers -productVersion`)
3. Mac model (e.g. "M2 Air, 16GB")
4. Model you were running (e.g. "Arche Codium 3B Q4")
5. What you expected
6. What actually happened (paste the traceback if there's one in the terminal)
7. Steps to reproduce

## Pull requests

1. Fork the repo, create a feature branch (`git checkout -b feature/my-thing`)
2. Make your changes. Keep commits focused — one logical change per commit.
3. Run `pytest clew/ -v` and `black clew/` before pushing.
4. Open a PR against `main`. Reference any issues it closes (`Closes #123`).
5. Be patient — this is a small project, review may take a few days.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0 (see [LICENSE](LICENSE)).

---

Built with care by **Open Synapse Labs**. Questions? opensynapselabs@proton.me.
