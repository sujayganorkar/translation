# Contributing to chinese-pdf-translator

Thank you for your interest in contributing! This is a focused tool for translating Chinese technical PDFs to English using Claude + OpenRouter. Contributions are welcome.

## Ways to Contribute

- **Bug reports** — Open a GitHub Issue with steps to reproduce, the command you ran, and relevant error output.
- **Feature requests** — Open a GitHub Issue describing the use case.
- **Pull requests** — See the workflow below.

## Development Setup

```bash
git clone https://github.com/sujayganorkar/translation.git
cd translation
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add your API keys to .env
```

## Pull Request Guidelines

1. **Keep it focused** — One feature or fix per PR.
2. **Test your change** — Run the affected pipeline step against a short PDF (even 10 pages) to verify it works end-to-end.
3. **Don't commit data** — The `.gitignore` excludes all generated files (`*.json`, `page_images/`, `*.pdf`, `*.log`). Keep it that way.
4. **Don't commit `.env`** — API keys must never be committed. Use `.env.example` for documenting new variables.
5. **Update README** if you add or change a command, flag, or environment variable.

## Code Style

- Python 3.10+
- Follow the existing style (no formatter enforced yet, but keep it readable)
- Add docstrings to new functions
- Keep the resumable / idempotent design: every step should be safe to interrupt and restart

## Reporting Security Issues

If you find a security issue (e.g., API key exposure, unsafe file handling), please **do not open a public issue**. Email the maintainer directly or use GitHub's private security advisory feature.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
