# Contributing to pdfstruct

Thanks for your interest in contributing!

## Getting Started

```bash
git clone https://github.com/Kyros-Groupe-Ltd/pdfstruct.git
cd pdfstruct
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest --tb=short -q
```

## Code Style

We use [Ruff](https://github.com/astral-sh/ruff) for linting. Line length is 100 characters.

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Make sure all tests pass
4. Update the README if you've changed the public API
5. Open a PR with a clear description of the change

## Reporting Issues

Use [GitHub Issues](https://github.com/Kyros-Groupe-Ltd/pdfstruct/issues). Include:
- Python version
- pdfstructx version (`pdfstruct.__version__`)
- Minimal reproduction steps
- A sample PDF if possible (or describe the structure)

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
