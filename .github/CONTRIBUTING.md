# Contributing to OnClock

First off, thank you for considering contributing! 🎉

## Code of Conduct

This project and everyone participating in it is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## How Can I Contribute?

### 🐛 Reporting Bugs

Before creating bug reports, please check the issue tracker to avoid duplicates. When you create a bug report, include as many details as possible:

- Use a clear and descriptive title
- Describe the exact steps to reproduce the problem
- Include screenshots if possible
- Mention your browser, device, and OS

### 💡 Suggesting Features

Feature requests are welcome! Tell us what you'd like to see and why it would be useful.

### 🔧 Pull Requests

1. Fork the repo and create your branch from `main`
2. If you've added code, add tests
3. Ensure the test suite passes
4. Make sure your code lints
5. Issue that pull request!

## Development Setup

```bash
# Clone
git clone https://github.com/LoneTraderRishi/onclock.git
cd onclock/backend

# Install dependencies
pip install -r requirements.txt

# Install dev dependencies
pip install pytest pytest-asyncio httpx

# Copy env config
cp .env.example .env
# Edit .env with your Supabase credentials

# Run the server
python main.py
```

## Project Structure

```
onclock/
├── backend/
│   ├── main.py           # FastAPI app — all routes
│   ├── database.py       # Supabase client
│   ├── models.py         # Pydantic models
│   ├── tests/            # Test suite
│   └── frontend/         # Static HTML/CSS/JS
├── screenshots/          # Screenshots for README
└── .github/              # GitHub templates & CI
```

## Style Guide

- Python: Follow PEP 8
- HTML/CSS: Keep it clean, no build step needed
- No external frontend frameworks — keep it vanilla for zero-dependency deployment

## License

By contributing, you agree that your contributions will be licensed under the project's MIT License.
