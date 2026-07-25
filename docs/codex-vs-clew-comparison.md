# Codex vs Clew: Сравнение на уровне исходного кода

> Независимое сравнение архитектур двух AI-агентов для кодирования:
> **Codex** (OpenAI, Rust, ~1573 файла, ~100 крейтов) vs **Clew** (Python+Rust, ~352 файла, гибридная архитектура)

---

## 1. Масштаб и технологический стек

| Характеристика | Codex (OpenAI) | Clew |
|---|---|---|
| **Язык** | Rust (100% ядра) | Python (основной) + Rust (PyO3, опционально) |
| **Файлов** | ~1 573 | ~352 |
| **Крейтов/модулей** | ~100 Rust crates | ~15 Python-модулей + 5 Rust-крейтов |
| **Фронтенды** | CLI (`codex exec`), TUI, VS Code Extension, Web | Qt GUI (PySide6), TUI (Textual), Headless CLI |
| **LLM API** | Exclusively OpenAI Responses API | 15+ провайдеров (OpenAI, Anthropic, xAI, Cerebras, Ollama, ...) |
| **Сборка** | Bazel + Cargo | pip/maturin (PyO3) |
| **Возраст** | Зрелый продукт, 41 миграция БД | v2.0, активный рефакторинг |

**Впечатление:** Codex — это промышленный монолит с годами эволюции. Clew — компактная, но архитектурно изощрённая система, которая уже на v2.0 демонстрирует зрелые инженерные решения. Разница в масштабе (4.5x по файлам) объясняется не «больше кода = лучше», а разной стратегией: Codex покрывает все платформы и edge cases из коробки, Clew делает ставку на модульность и extensibility.

---

## 2. Архитектура агента: как устроен цикл

### Codex: Многоуровневая конвейерная архитектура

```
CLI / VS Code / WebSocket
  ↓ JSON-RPC
MessageProcessor (~70+ типов запросов)
  ↓
ThreadRequestProcessor
  ↓
ThreadManager → CodexThread → Session
  ↓
build_prompt() → Responses API (streaming)
  ↓
ResponseEvent stream → ToolExecutor.handle()
  ↓ (цикл)
Rollout recording → Compact (при необходимости)
```

Ключевые черты:
- **5 уровней абстракции** между пользователем и LLM-вызовом. Каждый уровень заменяем.
- **In-process client**: CLI-режим `codex exec` не использует реальный JSON-RPC — он запускает `app-server` в том же процессе через mpsc/broadcast каналы.
- **Request Serialization Queues**: thread-scoped сериализация запросов для предотвращения race conditions.
- **ConnectionRpcGate**: семафор для ограничения одновременных RPC на соединение.
- **Проприетарное ядро**: модули `session`, `agent`, `guardian`, `tasks` (самое ценное) **отсутствуют в публичном экстракте** — они содержат логику оборота агента, диспетчеризацию инструментов и управление состоянием.

### Clew: Двухуровневая система с Actor Model

```
Qt GUI / TUI / CLI
  ↓
ClewBridge / AgentRuntimeV2
  ↓
ChatStateActor (asyncio Actor, единственный владелец состояния)
  ↓
TurnLoop (stateless executor, инъекция зависимостей)
  ↓
LLM Provider (через Circuit Breaker)
  ↓
ToolScheduler (параллельное исполнение с детекцией конфликтов)
  ↓ (цикл)
CompactionEngine (3 стратегии)
```

Ключевые черты:
- **Actor Model** (`ChatStateActor`): единственная asyncio.Task владеет conversation state. Все мутации через FIFO-очередь. Никаких мьютексов.
- **Stateless TurnLoop**: цикл оборота не владеет состоянием — все зависимости инжектируются (llm_call_fn, tool_execute_fn, parse_tool_calls_fn).
- **Builder-паттерн** для конфигурации: `.with_compaction()`, `.with_sandbox()`, `.with_circuit_breaker()`.
- **Wrapper/Decorator**: v2 оборачивает legacy v1 runtime через `from_legacy()`, не переписывая его.

**Что понравилось больше:**
- **Codex** — безупречная слоистая архитектура с заменяемыми транспортами. `app-server` может быть in-process, daemon, или удалённым — ядро не знает разницы.
- **Clew** — элегантность Actor Model. Отсутствие мьютексов и race conditions «по конструкции» — это красиво. Stateless `TurnLoop` с DI — образец тестируемости.

---

## 3. Система инструментов (Tools)

### Codex: Иерархическая система с 4 уровнями видимости

```rust
pub enum ToolExposure {
    Direct,          // Виден модели сразу + доступен в code-mode
    Deferred,        // Зарегистрирован, но виден через tool_search
    DirectModelOnly, // Виден модели, но не в nested code-mode
    Hidden,          // Для диспетчеризации, но невидим модели
}

pub enum ToolSpec {
    Function(ResponsesApiTool),
    Namespace(ResponsesApiNamespace),
    ToolSearch { ... },
    WebSearch { ... },
    Freeform(FreeformTool),
}
```

- **`ToolExecutor<Invocation>` trait** — единый интерфейс для всех инструментов.
- **Мульти-окружения**: один вызов инструмента может работать с несколькими средами (разные CWD, разные sandbox-контексты).
- **tool_search** — специальный инструмент для ленивого поиска других инструментов.
- **Параллельные вызовы**: `supports_parallel_tool_calls()` на уровне каждого инструмента.
- **ExtensionTurnItem**: инструменты-расширения могут публиковать видимые элементы в жизненный цикл хода.
- **Dynamic tools**: `parse_dynamic_tool()` для runtime-регистрации.

### Clew: Progressive Tool Disclosure + Conflict-Aware Scheduler

```python
class ToolScheduler:
    max_parallel: int  # По умолчанию 6
    # Детекция конфликтов по файловым ресурсам:
    # Write+write = конфликт, Read+read = никогда не конфликтует
    # Recursive path overlap detection
```

- **Progressive Disclosure**: вместо отправки всех ~40 инструментов, отправляется компактный каталог + meta-tool `select_tools`. Полные определения загружаются по требованию.
- **`<tools_added>` / `<tools_removed>` теги** в истории для отслеживания загруженных инструментов. Самовосстанавливающийся при компакции.
- **Conflict detection**: параллельное исполнение с анализом файловых доступов (READ/WRITE/READWRITE/SEARCH).
- **Thread-based**: выполнение в daemon threads с `done_event.wait()` для упорядочивания результатов.

**Что понравилось больше:**
- **Codex** — `ToolExposure` с 4 уровнями — это продуманная система. `Deferred` для MCP-инструментов (загрузка по требованию) экономит токены. `ToolSearch` как встроенный meta-tool элегантен.
- **Clew** — conflict detection в `ToolScheduler` — это то, чего у Codex я не увидел. Если два параллельных tool call пишут в один файл, Clew это обнаружит и сериализует. У Codex параллельность есть, но детекции конфликтов по файлам не видно.

---

## 4. Безопасность и песочница (Sandboxing)

### Codex: Кроссплатформенный промышленный sandbox

```rust
pub enum SandboxType {
    None,
    MacosSeatbelt,         // sandbox-exec + SBPL профили
    LinuxSeccomp,          // Landlock + Bubblewrap
    WindowsRestrictedToken // Windows Restricted Token
}
```

- **3 полноценные платформенные реализации** с единым интерфейсом `SandboxManager::transform()`.
- **Сетевая изоляция**: управляемый MITM-прокси (`NetworkProxy`, `ManagedNetworkSandboxContext`).
- **`PermissionProfile`**: `FileSystemSandboxPolicy` + `NetworkSandboxPolicy` + `AdditionalPermissionProfile`.
- **SandboxablePreference**: `Auto | Require | Forbid`.
- **WSL1 detection** с fallback.
- **Windows-specific**: `resolve_windows_restricted_token_filesystem_overrides()`.
- **SBPL-профили**: отдельные файлы для базовой, сетевой и read-only политик.

### Clew: OS-level sandbox + Command whitelist

```python
# Профили: off, workspace, read-only, strict (read-only + network block)
# Реализация: Landlock (Linux) или Seatbelt (macOS)
# КРИТИЧЕСКИ: необратим — однажды применённый, sandbox нельзя снять
```

- **Два слоя**: OS-level sandbox (Landlock/Seatbelt) + command whitelist (`command_policy.py`).
- **Pending grants**: project-level расширения capabilities должны быть явно одобрены пользователем.
- **3 конфигурационных слоя** для команд: BASE_ALLOWED → user-global → project-scoped.
- **Необратимость** — защита от социальной инженерии модели (нельзя попросить «сними sandbox»).

**Что понравилось больше:**
- **Codex** — масштаб и зрелость. Три платформы, сетевая изоляция через MITM-прокси, `PermissionProfile` с тонкой настройкой. Это уровень enterprise-продукта.
- **Clew** — **необратимость sandbox** — это гениальное решение. В Codex я такого не встретил (хотя возможно это в проприетарных модулях). Идея, что модель не может «уговорить» снять ограничения, даже если она скомпрометирована — это defense-in-depth в чистом виде.

---

## 5. Управление контекстом и компакция

### Codex: Многоуровневая система

- **Context Fragments**: XML-маркеры (`<external_{key}>...</external_{key}>`) для внедрения контекста с возможностью последующего распознавания и фильтрации.
- **`compact_remote` / `compact_remote_v2`**: удалённая компактация через более дешёвую модель.
- **`compact_token_budget`**: бюджет токенов для компактации.
- **`retain_tail_from_last_n_user_messages()`**: удержание хвоста.
- **`truncate_assistant_output_text_to_token_budget()`**: обрезка ответов.
- **Marker-based фильтрация**: при компактации фрагменты удаляются по маркерам, не разбирая содержимое.

### Clew: Трёхуровневая стратегия (порт из Grok Build)

| Стратегия | Поведение | Когда используется |
|---|---|---|
| **code** (full-replace) | Суммаризировать ВСЮ историю, пересобрать | Самая агрессивная, при критическом переполнении |
| **intra** (tail-keep) | Суммаризировать tool-call текущего turn, сохранить последние N сообщений | Стандартная компакция |
| **inter** (chunked) | Разбить на чанки, каждый суммаризировать отдельно | Баланс между детализацией и экономией |

- **Порог по умолчанию**: 85% контекстного окна.
- **Rust-нативная или Python-fallback** реализация.
- **Auto-compaction** в начале каждого turn при превышении порога.

**Что понравилось больше:**
- **Codex** — marker-based фрагменты — изящный подход. Возможность удалять injected контент по маркерам, не парся его содержимое — это масштабируемо.
- **Clew** — **три стратегии компакции** — это явное преимущество. Codex, судя по коду, имеет более плоскую модель (compact_remote vs compact_token_budget). У Clew выбор стратегии зависит от ситуации, что даёт лучший баланс между полнотой контекста и экономией токенов.

---

## 6. Мультиагентность и sub-агенты

### Codex: Мультиагентный протокол с версионированием

- **`MultiAgentVersion`** — версионирование мультиагентных протоколов.
- **`AgentGraphStore`** / **`LocalAgentGraphStore`** — граф связей между агентами.
- **`codex_delegate`** — делегирование задач подагентам.
- **Guardian agent** — «сторожевой» агент для ревью/одобрения действий (создаётся через `Arc::new_cyclic` — циклическая ссылка на ThreadManager).
- **`SubAgentSource`** — источник подагента.
- **Collaboration Modes**: Default, Plan, Execute, Pair Programming — переключаемые developer instructions через тег `<collaboration_mode>`.

### Clew: Sub-agents с read-only by construction

```python
class SubagentDefinition:
    name: str
    tools: list[str]   # Whitelist — инструмент просто НЕ ПРЕДСТАВЛЕН если не в списке
    isolation_mode: str

# Три встроенных:
# explore — ТОЛЬКО read-only инструменты
# plan — архитектурное планирование (read-only)
# general-purpose — все инструменты
```

- **Read-only by construction**: инструмент буквально НЕ ПРЕДСТАВЛЕН модели в toolset schema. Не whitelist на этапе dispatch, а отсутствие в schema.
- **Projected History**: дочерний агент получает compaction summary + последние 4 сообщения (усечённые до 2000 символов).
- **Batch scheduling**: rate-limited, resumable (до 5 задач немедленно, +1 каждые 700ms, exponential backoff при rate limit).
- **Swarm Mode**: рой параллельных сессий в отдельных git checkout (`.clew-swarm-{agent_id}`).
- **Пользовательские sub-агенты** из `.clew/agents/*.md` (YAML frontmatter + markdown body).

**Что понравилось больше:**
- **Codex** — **Guardian agent**. Идея отдельного «сторожевого» агента, который ревьюрует действия перед выполнением — это уровень безопасности, которого у Clew нет. И `Arc::new_cyclic` для создания циклической ссылки — технически красивое решение.
- **Clew** — **read-only by construction**. Удаление инструментов из schema, а не фильтрация на этапе dispatch — это конструктивно надёжнее. Кроме того, **Swarm Mode** с git checkout на каждую сессию — это продвинутая изоляция, которой у Codex я не заметил.

---

## 7. Маршрутизация моделей и провайдеров

### Codex: Тесная интеграция с OpenAI

- Эксклюзивно OpenAI Responses API.
- **`ModelsManager`** с автоматическим обновлением списка моделей.
- **`model-provider-info`** — метаданные провайдеров.
- Collaboration Modes переключают developer instructions, но не провайдера.

### Clew: Multi-provider AutoRouter

```python
class TaskComplexity(Enum):
    TRIVIAL → SIMPLE → MODERATE → COMPLEX → EXPERT

# ~40 ModelTier для 15+ провайдеров
# Классификация по: длине prompt, кол-ву файловых ссылок, ключевым словам
# Фильтрация по capabilities, доступности (TTL), бюджету
```

- **15+ провайдеров** с автоматической классификацией сложности.
- **Live pricing** через OpenRouter API.
- **Token Tracker**: запись каждого вызова с временными метками, расчёт стоимости, burn rate, бюджетные проекции.
- **Quota Tracker**: дневные лимиты по секциям (Heavy Code: 10 запросов/день).

**Что понравилось больше:**
- **Codex** — тесная интеграция с одной экосистемой гарантирует максимальное качество. Responses API (не Chat Completions) даёт Codex доступ к функциям, недоступным через стандартный API.
- **Clew** — **AutoRouter** — это одно из главных конкурентных преимуществ. Автоматический выбор модели по сложности задачи, live pricing, бюджетные проекции — это делает Clew прагматичным инструментом для реального использования с контролем затрат.

---

## 8. Персистентность и состояние

### Codex: Двойной слой + SQLite

- **JSONL rollout'ы** — полная история всех событий сессии.
- **SQLite** с 41 миграцией: потоки, цели, логи, фоновые задачи, удалённое управление.
- **ThreadStore**: InMemory + Local/FS.
- **`MemoryStore`** (удалён в последних миграциях — `0035_drop_memory_tables`).
- **Graceful degradation**: fallback на backup БД при corruption.

### Clew: Файловая персистентность + file locking

- **Memory Service**: append-only Markdown (`clew_memory.md`) с JSON metadata headers.
- **Token Tracker**: append-only JSONL с atomic rewrite.
- **Quota Tracker**: append-only JSONL с rotation за 30 дней.
- **Cross-process file locking** (fcntl/msvcrt) для конкурентной записи.

**Что понравилось больше:**
- **Codex** — SQLite с 41 миграцией — это индустриальный стандарт. Индексированные запросы, ACID-транзакции, graceful corruption handling.
- **Clew** — простота и прозрачность. Markdown-файлы читаемы человеком, JSONL — легко парсить. Cross-process locking — правильное решение для multi-instance. Но для масштабирования это упрётся в потолок.

---

## 9. Промпты и системные инструкции

### Codex: Структурированные prompt-шаблоны

- **Идентичность**: «You are Codex, based on GPT-5».
- **4 Collaboration Modes** как сменные developer instructions:
  - **Default** — действия вместо вопросов, `request_user_input` только при необходимости.
  - **Plan** — 3 фазы (Ground → Intent → Implementation), только не-мутирующие действия, финальный план в `<proposed_plan>`.
  - **Execute** — автономное выполнение, «assumptions-first», «think ahead».
  - **Pair Programming** — пользователь «рядом в терминале», проверка выравнивания.
- **Marker-based контекст**: XML-теги для идентификации и последующей фильтрации.

### Clew: Шаблоны для sub-агентов + CLEW.md

- **CLEW.md/CLAUDE.md** — иерархия проектных инструкций (глобальные → проектные → дополнительные).
- **Skill Loader**: `.clew/skills/*/SKILL.md` с YAML frontmatter.
- **Sub-agent templates**: структурированные промпты для explore/plan/general-purpose.
- **Compaction prompts**: отдельные системные промпты для каждой стратегии компакции.
- **Interjection frame**: шаблон для mid-turn пользовательских сообщений.

---

## 10. Обработка ошибок и отказоустойчивость

### Codex
- **Централизованный `CodexErr`** с вариантами: InvalidRequest, UnsupportedOperation, Fatal.
- **SQLite corruption detection** с автоматическим восстановлением.
- **Model fallback** — fallback на более дешёвые модели.
- **`TurnAbortReason`** + `CancellationToken` для cooperative cancellation.

### Clew
- **Circuit Breaker** с 3 состояниями (Closed → Open → Half-Open), sliding window, exponential backoff.
- **CancelToken** с parent→child цепочкой (native Rust или Python fallback).
- **Interjection Buffer** — пользователь может отправить сообщение mid-turn, оно буферизируется.
- **CircuitBreakerRegistry** per (provider, model) — отдельный breaker для каждого провайдера.

**Что понравилось больше:**
- **Clew** — **Circuit Breaker** — это зрелый паттерн, которого у Codex я не увидел в явном виде. Отдельный breaker на каждого провайдера — это правильно для multi-provider архитектуры. Interjection Buffer — тоже уникальная фича: пользователь не обязан ждать завершения хода.

---

## 11. Уникальные сильные стороны каждого

### Что Codex делает лучше

1. **Архитектурная чистота слоёв** — 5 уровней абстракции, каждый заменяем. Транспорт (JSON-RPC, in-process, WebSocket) отделён от бизнес-логики.
2. **Промышленный sandbox** — 3 платформы, сетевая изоляция через MITM-прокси, `PermissionProfile` с тонкой настройкой.
3. **Guardian agent** — отдельный агент для ревью действий перед выполнением.
4. **Tool Search** — встроенный meta-tool для ленивого обнаружения инструментов.
5. **Context Fragments** — marker-based внедрение и фильтрация контекста.
6. **Responses API** — доступ к проприетарным возможностям OpenAI (structured outputs, reasoning tokens и т.д.).
7. **SQLite персистентность** — 41 миграция, ACID, graceful corruption handling.
8. **Extension API** — плагины, скиллы, MCP, расширения через единую архитектуру.
9. **Мульти-окружения** — один tool call может работать с несколькими CWD/sandbox-контекстами.

### Что Clew делает лучше

1. **Actor Model** — нет мьютексов, нет race conditions «по конструкции».
2. **Read-only by construction** — инструменты удалены из schema, а не фильтруются.
3. **Необратимый sandbox** — защита от социальной инженерии модели.
4. **Conflict-aware Tool Scheduler** — детекция файловых конфликтов при параллельном выполнении.
5. **Трёхуровневая компакция** — 3 стратегии (code/intra/inter) vs плоская модель.
6. **Circuit Breaker** — зрелый паттерн с per-provider изоляцией.
7. **Multi-provider AutoRouter** — 15+ провайдеров, автоматическая классификация сложности.
8. **Progressive Tool Disclosure** — компактный каталог + загрузка по требованию.
9. **Interjection Buffer** — mid-turn пользовательские сообщения.
10. **Swarm Mode** — параллельные сессии в отдельных git checkout.
11. **Token Intelligence** — live pricing, burn rate, бюджетные проекции.
12. **Открытость** — весь код доступен, нет проприетарных модулей.

---

## 12. Что стоит добавить в Clew из Codex для усиления

### Высокий приоритет (сильное влияние на качество)

#### 12.1. Guardian Agent (агент-ревьюер)

**Что взять:** Концепцию отдельного агента, который ревьюрует действия основного агента перед выполнением. В Codex это реализовано через `guardian`-модуль с `Arc::new_cyclic` для циклической ссылки на ThreadManager.

**Почему это важно:** Это последний рубеж обороны. Перед выполнением `rm -rf`, `git push --force` или перезаписью критичных файлов — guardian-агент проверяет действие. Это не просто confirm dialog, а полноценный LLM-ревью с пониманием контекста.

**Как реализовать в Clew:**
```
- Добавить optional GuardianConfig в AgentRuntimeV2
- При каждом tool call с высоким risk score → отправить в guardian-агента
- Guardian получает: tool_name, args, контекст (последние N сообщений)
- Guardian возвращает: APPROVE / REJECT / MODIFY (с альтернативными args)
- Пользователь может настроить порог: off / dangerous-only / all
```

#### 12.2. Marker-Based Context Fragments

**Что взять:** Систему XML-маркеров для внедрения контекста с возможностью последующего распознавания и фильтрации при компакции.

**Почему это важно:** Сейчас при компакции Clew теряет структуру injected контекста (project context, skill instructions, CLEW.md). Marker-based подход позволяет точно идентифицировать и сохранить/переформулировать нужные фрагменты.

**Как реализовать в Clew:**
```
- Определять маркеры для каждого типа injected контекста:
  <clew_project_context>...</clew_project_context>
  <clew_skill name="...">...</clew_skill>
  <clew_memory>...</clew_memory>
- В CompactionEngine: при обработке истории, распознавать маркеры
- Сохранять содержимое помеченных фрагментов в summary (а не удалять)
- Это особенно важно для code-compaction (full-replace)
```

#### 12.3. SQLite для персистентности

**Что взять:** Переход от файловой персистентности (Markdown/JSONL) к SQLite.

**Почему это важно:** Append-only файлы с file locking — это хорошо для прототипа, но не масштабируется. При сотнях сессий и тысячах записей поиск, фильтрация, агрегация становятся медленными. SQLite даёт индексированные запросы, ACID, и предсказуемую производительность.

**Как реализовать в Clew:**
```
- Использовать встроенный sqlite3 (стандартная библиотека Python)
- Начать с 3 таблиц: sessions, token_usage, memories
- Миграции — простой SQL-файлы в порядке нумерации (как у Codex)
- Сохранить JSONL rollout'ы как append-only лог (двойной слой как у Codex)
- File locking заменить на WAL mode (конкурентные чтения без блокировок)
```

### Средний приоритет (улучшение архитектуры)

#### 12.4. Collaboration Modes

**Что взять:** Сменные developer instructions через тег `<collaboration_mode>`.

**Почему это важно:** Разные задачи требуют разного поведения агента. Plan-режим (только чтение) для архитектурных решений, Execute-режим (автономность) для рутинных задач, Pair Programming для совместной работы.

**Как реализовать в Clew:**
```
- Добавить 3-4 режима в AgentRuntimeV2
- Каждый режим = отдельный developer message в system prompt
- Команда /mode plan|execute|pair для переключения
- В Plan-режиме: фильтровать tools по read-only (как explore sub-agent)
- В Execute-режиме: autonomy=full, не спрашивать
```

#### 12.5. Tool Search (meta-tool)

**Что взять:** Встроенный инструмент для динамического поиска и загрузки других инструментов.

**Почему это важно:** У Clew уже есть Progressive Disclosure (select_tools), но он не умный — пользователь или модель должны знать имя инструмента. Tool Search позволяет модели искать по описанию и загружать нужные инструменты по контексту задачи.

**Как реализовать в Clew:**
```
- Обогатить TOOL_CATALOG: добавить keywords, categories, examples
- Создать tool_search инструмент: принимает query → возвращает top-5 + auto-loads
- Интегрировать с select_tools: tool_search возвращает имена для select_tools
- Использовать embedding-based поиск (или простой TF-IDF) для релевантности
```

#### 12.6. Request Serialization Queues

**Что взять:** Механизм сериализации запросов по ключу для предотвращения race conditions.

**Почему это важно:** При Swarm Mode и параллельных sub-агентах возможны race conditions на общих ресурсах (файловая система, конфигурация). Queues гарантируют, что операции на одном ресурсе сериализуются.

**Как реализовать в Clew:**
```
- Добавить RequestQueue в ToolScheduler (или отдельный модуль)
- Ключи: (operation_type, file_path) или (resource_type, resource_id)
- Asyncio semaphore per key
- При конфликте — ждать в очереди, не блокировать другие операции
```

### Низкий приоритет (nice-to-have)

#### 12.7. Сетевая изоляция через прокси

У Clew есть `strict` sandbox-профиль (блокировка сети), но нет MITM-прокси для перехвата и контроля сетевого трафика. У Codex это реализовано через `NetworkProxy` + `ManagedNetworkSandboxContext`. Это позволяет агенту делать HTTP-запросы, но контролировать их (блокировать утечки данных).

#### 12.8. Мульти-окружения для tool calls

У Codex один tool call может работать с несколькими CWD. Это полезно для monorepo-проектов, где нужно читать из одного пакета и писать в другой. У Clew это можно добавить через параметр `working_dir` в tool call.

#### 12.9. Attestation (криптографическая верификация)

У Codex есть `AttestationProvider` — криптографическое подтверждение того, что результат получен от доверенного агента. Для Clew это less relevant (локальный агент), но может быть полезно для swarm-режима с shared state.

---

## 13. Итоговая оценка

### Codex — что впечатлило

Codex — это продукт с годами инженерии. 5 уровней абстракции, 100 крейтов, 3 платформы sandbox, 41 миграция БД. Архитектура рассчитана на годы эволюции. Но самое важное — **проприетарное ядро** (session, agent, guardian, tasks) недоступно. Это значит, что самые ценные архитектурные решения мы видим только по их отражению в публичном коде.

### Clew — что впечатлило

Clew — это **настоящий вызов**. При 4.5x меньшем масштабе, он демонстрирует:
- Actor Model для состояния — чище, чем любой мьютексный подход в Codex
- Conflict-aware Tool Scheduler — уникальная фича, absent в Codex
- Необратимый sandbox — gениальная защита от социальной инженерии
- Multi-provider AutoRouter — прагматичное решение для реального мира
- Circuit Breaker с per-provider изоляцией — зрелая отказоустойчивость
- Трёхуровневая компакция — гибче, чем у Codex

### Вердикт

**Clew — солидный соперник Codex.** Не в смысле «догнал по функциональности» (Codex покрывает больше платформ и edge cases), а в смысле **архитектурной зрелости и инновационности**. Несколько решений Clew (Actor Model, read-only by construction, conflict-aware scheduler, irreversble sandbox) — это идеи, которых у Codex нет в явном виде.

Если Clew реализует Guardian Agent, Marker-Based Context Fragments и SQLite-персистентность из списка выше — он станет не просто соперником, а **архитектурным лидером** в открытом сегменте AI-агентов для кодирования.
