# Clew — Quick Start from Source

## Requirements

- Python 3.11 or 3.12
- macOS 13+ (Ventura), Windows 10+, or Linux with Qt6 support
- 4–8 GB RAM

## Installation

### 1. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

**macOS:**
```bash
pip install -e ".[dev,macos]"
```

**Windows / Linux:**
```bash
pip install -e ".[dev]"
```

### 3. Run Clew

```bash
clew
# or
python -m clew
```

## Development

### Run from Source (no install)

```bash
python -m clew
```

### Run Tests

```bash
pytest
```

### Code Quality

```bash
# Format code
black clew/

# Type checking
mypy clew/
```

## Troubleshooting

### macOS: "Clew.app is damaged"

```bash
xattr -cr Clew.app
```

### Missing Qt6

```bash
pip install --upgrade PySide6 PySide6-Addons
```

### Import Errors

Make sure you're in the virtual environment:
```bash
source .venv/bin/activate
```

## Project Structure

```
clew/
├── __init__.py           # Package initialization
├── __main__.py           # Entry point
├── agent_runtime.py      # ReAct agent system
├── api_server.py         # Local API server
├── main_window.py        # Main Qt window
├── web_bridge.py         # Python ↔ JS bridge
├── providers/            # AI provider integrations
├── web/                  # HTML5 frontend
└── assets/               # Icons and images
```

## Configuration

### API Keys

Open Settings in the UI or edit `~/.clew/config.json`:

```json
{
  "providers": {
    "anthropic": {
      "api_key": "sk-ant-..."
    },
    "openai": {
      "api_key": "sk-..."
    }
  }
}
```

### Local Models

Install [Ollama](https://ollama.ai) or [LM Studio](https://lmstudio.ai), Clew auto-detects them.

## Support

- GitHub: https://github.com/clew-ide/clew
- Issues: https://github.com/clew-ide/clew/issues
