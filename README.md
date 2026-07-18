<div align="center">

<img src=".clew-source/assets/logo.png" alt="Clew Logo" width="180"/>

<br/>

# 🧵 Clew — Native AI Coding IDE

### The next-generation free AI coding tool for everyone. **15 providers** · **ReAct agents** · **MCP support** · **Zero telemetry**.

**Build with Claude, GPT, Gemini, DeepSeek, z.ai, or run 100% offline with local models.**
<br/>

**Native desktop app for macOS, Windows & Linux. Your code never leaves your machine unless you choose otherwise.**

<br/>

<h3>

⭐ Star the repo if Clew helps you build better software with AI.

</h3>

[![Stars](https://img.shields.io/github/stars/ilyaosovskoi/Clew?style=social)](https://github.com/ilyaosovskoi/Clew)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-blue.svg)]()

</br>

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![Qt](https://img.shields.io/badge/Qt-PySide6-green?style=for-the-badge&logo=qt)](https://www.qt.io/)
[![Privacy](https://img.shields.io/badge/Privacy-Local--First-orange?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)]()

<br/>

**[✨ Features](#-key-features)** · **[🤖 Providers](#-ai-providers--15-supported)** · **[🛠️ Tech Stack](#-tech-stack)** · **[🤝 Contributing](#-contributing)**

</div>

<br/>

<div align="center">

# 💥 The Promise

</div>

> **Clew = Cursor + LM Studio + Multi-API Gateway, all in one app.**
> No Python dependencies to install. No telemetry. Absolute privacy for local models, with the flexibility to plug in cloud APIs. Your workflow, supercharged by AI.

<table>
  <tr>
    <td width="33%" valign="top"><b>🔒 Privacy-First</b><br/><sub>Local models run 100% on-device. No telemetry, no tracking, no cloud unless you want it.</sub></td>
    <td width="33%" valign="top"><b>🧠 15 AI Providers</b><br/><sub>Anthropic, OpenAI, Gemini, DeepSeek, Groq, xAI, z.ai, or run Llama/Mistral/Qwen locally via Ollama or LM Studio.</sub></td>
    <td width="33%" valign="top"><b>⚡ Native Performance</b><br/><sub>Qt-powered desktop app with real-time streaming, instant file search, live token tracking.</sub></td>
  </tr>
  <tr>
    <td width="33%" valign="top"><b>🤖 ReAct Agent Runtime</b><br/><sub>Autonomous coding agents that read, write, run code, and iterate until the task is done.</sub></td>
    <td width="33%" valign="top"><b>🧩 MCP Protocol Support</b><br/><sub>Model Context Protocol integration — connect external tools and knowledge bases.</sub></td>
    <td width="33%" valign="top"><b>📂 Smart Context</b><br/><sub>Intelligent file selection, git-aware filtering, live project indexing.</sub></td>
  </tr>
</table>

<br/>

<div align="center">

# 🤔 Why Clew?

</div>

> **Stop juggling terminal tabs, API keys, and slow web UIs.**

| ❌ The daily pain                                     | ✅ How Clew fixes it                                                      |
| ----------------------------------------------------- | ------------------------------------------------------------------------- |
| 🌐 Web UIs are slow and clunky                        | **Native desktop app** — instant startup, real-time streaming             |
| 🔐 Privacy concerns with cloud-only tools             | **Local-first** — your code stays on your machine, zero telemetry         |
| 🧩 Switching providers means switching tools          | **15 providers in one UI** — Anthropic, OpenAI, Gemini, local models      |
| 🤖 Manually copying code between AI and your editor   | **Autonomous agents** — they read, write, and run code for you            |
| 📂 Context management is manual and error-prone       | **Smart context** — intelligent file selection, git-aware, auto-indexed   |
| 💸 Locked into one provider's ecosystem               | **Multi-provider** — switch models mid-conversation, compare outputs      |

<br/>

<div align="center">

# ✨ What Makes Clew Different

</div>

| Feature                            | Clew                                                                 | Cursor / Cline | Windsurf | Claude Desktop |
| ---------------------------------- | -------------------------------------------------------------------- | -------------- | -------- | -------------- |
| 🖥️ **Native desktop app**         | **Qt/PySide6-based, fast, offline-capable**                          | Web/Electron   | Electron | Electron       |
| 🔒 **Local-first privacy**         | **Zero telemetry, 100% offline mode**                                | Partial        | Partial  | Cloud-only     |
| 🤖 **Autonomous agents**           | **ReAct loop with JSON tool calling**                                | ✅             | ✅       | Limited        |
| 🧠 **Provider count**              | **15 native + 200+ via OpenRouter**                                  | 5–10           | 3–5      | 1 (Anthropic)  |
| 🧩 **MCP Protocol**                | **Built-in MCP client & manager**                                    | Plugin         | No       | ✅             |
| 📂 **Smart context**               | **Git-aware, relevance-ranked, token-budgeted, live indexing**        | ✅             | ✅       | Limited        |
| 🤖 **Auto-Router**                 | **Complexity-based provider/model selection with fallback chains**   | No             | No       | No             |
| 🧩 **Plugin System**               | **Custom providers, API routes, JS/CSS injection**                   | No             | No       | No             |
| 💬 **Live token tracking**         | **Real-time usage display, per-provider quotas**                     | Basic          | No       | No             |
| 🎨 **Themes**                      | **Light, Dark, System with ambient neural synapse effects**          | ✅             | ✅       | ✅             |
| 🔓 **Open source**                 | **MIT**                                                              | Proprietary    | Partial  | Proprietary    |

<br/>

<div align="center">

# 🤖 AI Providers — 15 Supported

</div>

> **Switch between cloud and local models seamlessly. Route through OpenRouter for 200+ additional models.**

<div align="center">

### 🏢 Cloud Providers — Through one interface

<table>
  <tr>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/claude-color.svg" width="44" alt="Anthropic"/><br/><sub><b>Anthropic</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/openai.svg" width="44" alt="OpenAI"/><br/><sub><b>OpenAI</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/gemini-color.svg" width="44" alt="Gemini"/><br/><sub><b>Google Gemini</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/deepseek-color.svg" width="44" alt="DeepSeek"/><br/><sub><b>DeepSeek</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/grok.svg" width="44" alt="xAI"/><br/><sub><b>xAI Grok</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/groq.svg" width="44" alt="Groq"/><br/><sub><b>Groq</b></sub></td>
  </tr>
  <tr>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/mistral-color.svg" width="44" alt="Mistral"/><br/><sub><b>Mistral</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/cerebras-color.svg" width="44" alt="Cerebras"/><br/><sub><b>Cerebras</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/together-color.svg" width="44" alt="Together"/><br/><sub><b>Together</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/fireworks-color.svg" width="44" alt="Fireworks"/><br/><sub><b>Fireworks</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/openrouter-color.svg" width="44" alt="OpenRouter"/><br/><sub><b>OpenRouter</b></sub></td>
    <td align="center" width="100"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/siliconflow-color.svg" width="44" alt="SambaNova"/><br/><sub><b>SambaNova</b></sub></td>
  </tr>
  <tr>
    <td align="center" width="100" colspan="3"><br/><b>z.ai</b><br/><sub>GLM models via z.ai API</sub></td>
  </tr>
</table>

<sub>…and 200+ more models via OpenRouter gateway with a single API key</sub>

<br/>

### 🖥️ Local Models — 100% Private, No Internet Required

<table>
  <tr>
    <td align="center" width="150"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/ollama-color.svg" width="44" alt="Ollama"/><br/><b>Ollama</b><br/><sub>Run Llama, Mistral, Qwen<br/>locally via Ollama</sub></td>
    <td align="center" width="150"><img src="https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.91.0/icons/meta-color.svg" width="44" alt="LM Studio"/><br/><b>LM Studio</b><br/><sub>GGUF model server<br/>with GPU acceleration</sub></td>
  </tr>
</table>

**Supported local models:**
- **Llama 3.1 / 3.2** (Meta) — Excellent general-purpose coding and reasoning
- **Mistral / Mixtral** (Mistral AI) — Fast, efficient, great at following instructions
- **Qwen 2.5 Coder** (Alibaba) — Outstanding coding performance per parameter size
- **DeepSeek Coder V2** — Top-tier code generation
- **Phi-3 / Phi-3.5** (Microsoft) — Incredible speed and logic for small sizes

</div>

<br/>

<div align="center">

# 🚀 Quick Start

</div>

**Download** the latest release for your platform from [Releases](https://github.com/ilyaosovskoi/Clew/releases) — `Clew.dmg` (macOS), `Clew-Setup.exe` (Windows), or `Clew.AppImage` (Linux). Requires Python 3.11+.

**Configure** — open Settings, add an API key (Anthropic / OpenAI / Gemini / z.ai) or install [Ollama](https://ollama.ai) / [LM Studio](https://lmstudio.ai) for local models — Clew auto-detects them.

**Open a project** — `File → Open Folder…`, pick a template and a Skill, describe your task in natural language, and watch the agent read, write, and execute code autonomously.

**From source:**
```bash
git clone https://github.com/ilyaosovskoi/Clew.git
cd Clew && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,macos]"   # remove macos on Windows/Linux
clew
```

<br/>

<div align="center">

# ✨ Key Features

</div>

### 🤖 Autonomous ReAct Agent Runtime

Clew's agent system uses the **ReAct** (Reasoning + Acting) pattern to autonomously complete coding tasks:

- **Plan → Read → Write → Execute → Observe** loop
- **JSON tool calling**: `read_file`, `write_file`, `str_replace`, `execute_command`, `search_project`, `git_status`, `git_diff`, `git_stage`, `git_commit`, `get_skill`, `call_mcp_tool`
- **Sandboxed execution**: Commands run safely with a whitelist (`python`, `git`, `npm`, `pytest`, etc.) — dangerous metacharacters and interpreter escape flags are blocked
- **Diff review before write**: Agent pauses and asks UI for apply/reject before modifying files
- **Configurable autonomy levels**: `never_ask`, `new_files_only`, `always_ask`
- **Live UI trace**: Watch the agent's thoughts and actions in real-time
- **Self-correction**: If a tool fails, the agent diagnoses and retries

**Example workflow:**
```
User: "Add error handling to api.py"
Agent:
  1. 🧠 Plan: Read api.py, identify try-catch gaps, add handlers
  2. 📖 read_file(api.py) → analyzes current error handling
  3. ✍️ str_replace() → injects try-except blocks
  4. ▶️ execute_command("python -m pytest tests/test_api.py") → verifies
  5. ✅ Final answer: "Added error handling with 3 try-except blocks, all tests pass"
```

### 🧩 Model Context Protocol (MCP) Support

Built-in **MCP manager** lets Clew connect to external tools and knowledge bases:

- **Multi-server management**: Configure multiple MCP servers in `~/.clew/mcp.json`
- **Unified tool catalog**: MCP tools appear alongside built-in tools in the agent's system prompt
- **Meta-tool routing**: Agent calls `call_mcp_tool(server, tool, args)` — the manager routes to the right MCP client
- **Secure**: Explicit user approval for sensitive operations

### 🧠 Multi-Provider Auto-Router

Automatically selects the best provider/model for each task based on complexity analysis, cost constraints, and speed requirements:

- **Task complexity classification**: Trivial → Simple → Moderate → Complex → Expert
- **Model tier catalog**: Maps each provider and model to a cost/speed/capability tier
- **Fallback chains**: If the primary provider fails, the router automatically falls back to the next best option
- **Covers all 15 registered providers** — no provider is left out of auto-routing

### 📂 Smart Context Management

Clew intelligently selects which files to include in the AI's context:

- **Git-aware filtering**: Ignores `.git/`, `node_modules/`, `__pycache__/`, build artifacts, and 15+ other common directories
- **Relevance ranking**: Uses heuristics and information-density weights per file extension
- **Token budget**: Automatically fits context within the model's limit
- **Live indexing**: File changes are detected and re-indexed; agent-created files are tracked automatically

### 🎨 Premium UI with Dark Mode

Beautiful, distraction-free interface built with an embedded HTML5 frontend (QWebEngineView):

- **Themes**: Light, Dark, System (follows OS)
- **Ambient synapse effects**: Neural pathway animations (toggleable)
- **Integrated Code Viewer**: Right-panel file explorer with syntax highlighting, file search, and change watching
- **High-DPI support**: Retina / 4K displays
- **Native OS integration**: Transparent titlebar on macOS, polished typography (Inter + JetBrains Mono)

### 💬 Live Token Tracking & Quota System

- **Per-message tracking**: Input tokens, output tokens, total cost
- **Daily quota tracking**: Per-section daily limits with persistent on-disk storage
- **Cost estimation**: Know the cost before sending (based on provider pricing)
- **Context window visualization**: Bar chart shows how much context is used

### 🔧 Plugin System

Extend Clew without modifying the source code:

- **Auto-discovery**: Drop `.py` files in `~/.clew/plugins/` — loaded at startup
- **Register custom providers**: Add your own AI provider backends
- **Custom API routes**: Expose new HTTP endpoints from the local API server
- **Frontend injection**: Inject custom JavaScript and CSS into the HTML UI

### 📋 Skill System (SKILL.md)

Reusable instruction packages that guide the agent's behavior:

- **YAML frontmatter**: Skill name, description, tags, and activation criteria
- **Multi-location loading**: Project-level (`.clew/skills/`) and user-level (`~/.clew/skills/`)
- **Skill catalog**: Descriptions injected into the system prompt; agent requests full skill text via `get_skill` tool

### 🗂️ Git Integration

Built-in Git service — no external tool needed:

- **Agent tools**: `git_status`, `git_diff`, `git_stage`, `git_commit`
- **Branch management** and **commit history**
- **Diff viewer**: Unified diffs with hunk-level accept/reject

### 💾 Cross-Chat Memory

- **Structured metadata**: Each session records project root, provider, files touched, chat ID, and tags
- **Search** prior sessions by keyword, file path, project root, or tag
- **Context brief**: Compact summary of relevant prior sessions injected into system prompts

### 🔍 LSP Integration

Language Server Protocol client for Python via `python-lsp-server` / `jedi`:

- **Autocomplete**, **hover**, **go-to-definition**, **diagnostics**, **signature help**
- Non-blocking: all operations run in a dedicated QThread

### 🔄 Auto-Updater

- **GitHub Releases API**: Checks for newer versions on startup (no external dependency)
- **Configurable**: Can be disabled or overridden with a custom repo URL

### 🛡️ Security & Privacy

- **Local-first**: Your code never leaves your machine unless you use a cloud API
- **Zero telemetry**: No tracking, no analytics, no crash reports
- **Sandboxed execution**: Command whitelist, dangerous metacharacters and interpreter escape flags blocked
- **Path sandboxing**: Code Viewer restricts file access to the project root

<br/>

<div align="center">

# 🗺️ Roadmap

</div>

- [x] **Multi-provider support** (15 native providers + OpenRouter gateway)
- [x] **ReAct agent runtime** with JSON tool calling
- [x] **MCP protocol integration** (multi-server manager)
- [x] **Smart context management** with git-aware filtering
- [x] **Live token tracking** and cost estimation
- [x] **Git integration** (status, diff, stage, commit)
- [x] **Diff service** with hunk-level accept/reject
- [x] **Plugin system** (custom providers, routes, JS/CSS injection)
- [x] **Skill system** (SKILL.md format with catalog)
- [x] **Cross-chat memory** with structured metadata
- [x] **Auto-Router** (complexity-based provider selection)
- [x] **LSP client** (Python autocomplete, hover, diagnostics)
- [x] **Auto-updater** (GitHub Releases integration)
- [x] **Daily quota system** per section
- [ ] **Multi-agent "Heavy Code" mode** — spawn multiple agents for complex refactors
- [ ] **Voice mode** — speak your tasks, hear code explanations
- [ ] **Web UI** — browser-based version for remote access
- [ ] **Team collaboration** — shared sessions, real-time co-editing

<br/>

<div align="center">

# 🛠️ Tech Stack

</div>

- **Language**: Python 3.11 / 3.12
- **UI Framework**: PySide6 (Qt 6.6+) — native desktop with QWebEngineView (HTML5 frontend)
- **Frontend**: HTML5, JavaScript, CSS (embedded web UI in `clew/web/`)
- **Agent Runtime**: Custom ReAct loop with JSON-based tool calling (QThread for non-blocking)
- **Auto-Router**: Complexity analysis + model tier catalog + fallback chains
- **Context Protocol**: MCP (Model Context Protocol) client & multi-server manager
- **Providers**: 15 native via OpenAI-compatible API or raw urllib (Anthropic, Gemini, z.ai)
- **LSP**: python-lsp-server / jedi for Python language features
- **Git**: Pure subprocess — no gitpython dependency
- **Database**: SQLite for chat history, settings, memory
- **Plugin System**: Dynamic module loading from `~/.clew/plugins/`
- **Platforms**: macOS (M-series + Intel), Windows 10+, Linux (Qt6-supported distros)

**Zero external runtime dependencies for core features** — no Docker, no Redis, no Node.js. Just Python + Qt.

<br/>

<div align="center">

# 🤝 Contributing

</div>

We welcome contributions! Here's how to get started:

1. **Fork** the repository on GitHub
2. **Clone**: `git clone https://github.com/YOUR_USERNAME/Clew.git`
3. **Branch**: `git checkout -b feature/amazing-feature`
4. **Commit**: `git commit -m 'Add amazing feature'`
5. **Push**: `git push origin feature/amazing-feature`
6. **Open a Pull Request** on the main repository

**Ideas:** add a provider in `clew/providers/`, improve the agent in `agent_runtime.py`, build a plugin in `~/.clew/plugins/`, create a SKILL.md, fix a bug on [Issues](https://github.com/ilyaosovskoi/Clew/issues).

<br/>

<div align="center">

# 📧 Support

</div>

- 🐙 **GitHub**: [github.com/ilyaosovskoi/Clew](https://github.com/ilyaosovskoi/Clew)
- 🐛 **Issues**: [Report a bug or request a feature](https://github.com/ilyaosovskoi/Clew/issues)
- 📥 **Releases**: [Download the latest version](https://github.com/ilyaosovskoi/Clew/releases)

<br/>

<div align="center">

## 📄 License

</div>

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

<sub>Clew v1.1.0 · Python ≥3.11 · Qt 6.6+ · MIT License</sub>

</div>
