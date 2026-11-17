# DealerCenter Agent — Phase 1: Foundation & Tool Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the config layer, pluggable browser-driver abstraction, dialect-agnostic DB models, and the four deterministic tools (Login, SearchInvoice, CostAdd, Verify) — the zero-LLM-cost core everything else (queue, LangGraph recovery, API) plugs into.

**Architecture:** Tools depend only on a `BrowserDriver` ABC, never on Playwright directly, so the engine is swappable later. Tool decision logic (what counts as a match, what counts as success) is tested against a `FakeBrowserDriver` test double — fast, deterministic, no real browser or DealerCenter access needed. The `PlaywrightDriver`'s tricky DOM-handling logic (duplicate hidden submit buttons, async Kendo modals — both hit live in the POC) is tested against small local HTML fixtures, not the live site, so CI never needs DealerCenter credentials.

**Tech Stack:** Python 3.13, Playwright (sync API), SQLAlchemy 2.x + Alembic, Pydantic v2 / pydantic-settings, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-dealercenter-agent-design.md`

## Global Constraints

- No tool in this phase calls an LLM. (Spec: "zero LLM cost on the happy path.")
- Tools depend on `automation.base.BrowserDriver`, never on `playwright.sync_api` directly. (Spec: pluggable browser engine seam.)
- DB models use only dialect-agnostic SQLAlchemy types (no Postgres-only JSONB) so the same models/migrations work against Supabase, Postgres, and MySQL via `.env`-selected `DBConfig.driver`. (Spec: DB dialect seam.)
- Every tool call is a `ToolRun` row with `duration_ms` and `llm_cost_usd` (0 for every tool in this phase). (Spec: cost/time per tool call.)
- `CostAddTool` and `VerifyTool` must implement the skill's exact decision tree: memo-already-has-invoice# → `exists` (no write); otherwise add row + upload + Save, then re-check the Files grid before reporting `created`. (Carried over from the existing `dealercenter-cost-adds` skill.)
- No test in this phase hits `app.dealercenter.net` or uses real credentials.

## Review Focus

- **Duplicate aria-hidden submit button inside the same `<form>`** (hit live in the POC on both the login and MFA screens) — `PlaywrightDriver.click_submit_near` must pick the visible one, not `.first`.
- **Asynchronously-rendered modal appearing after an `is_visible()`-style check already passed** (hit live, crashed `do_search` twice) — `dismiss_blocking_modals` must poll (`wait_for(state="visible")`), not snapshot-check once.
- **Invoice number already in Cost Adds memo** — `CostAddTool` must return `exists` and perform zero writes (no Add New, no Save) — a reasonable person would be alarmed by a duplicate cost-add entry.
- **Save succeeds but the PDF never appears in the Files grid** — `VerifyTool` must report `failed("PDF not listed")`, not `created`, or invoices silently go unbilled-for.
- **No Inventory row and no Deal row match the VIN/PO, or more than one of either matches** — `SearchInvoiceTool` must distinguish "not found" from "ambiguous, don't guess" as two different failure reasons, since the skill explicitly forbids guessing on ambiguous matches.

---

## File Structure

```
dealercenter_agent/
  config.py                        # LLMConfig, BrowserConfig, DBConfig, QueueConfig, AppConfig
  automation/
    __init__.py
    base.py                        # BrowserDriver ABC
    playwright_driver.py           # PlaywrightDriver(BrowserDriver)
  tools/
    __init__.py
    base.py                        # ToolStatus, ToolResult, StepFailure, Tool ABC
    login_tool.py                  # LoginTool
    search_invoice_tool.py         # SearchInvoiceTool
    cost_add_tool.py               # CostAddTool
    verify_tool.py                 # VerifyTool
  db/
    __init__.py
    engine.py                      # engine/session factory from DBConfig
    models.py                      # Tenant, Invoice, ToolRun, RecoveryAttempt, Escalation, AuthSession
  alembic/                         # migration scaffold (Task 3)
tests/
  conftest.py                      # FakeBrowserDriver fixture
  fixtures/
    login_form.html                # mimics live Auth0 markup: duplicate submit button
    modal_chain.html                # mimics live Kendo markup: trust-browser + generic dialog + overlay
  test_config.py
  automation/test_playwright_driver.py
  db/test_models.py
  tools/test_login_tool.py
  tools/test_search_invoice_tool.py
  tools/test_cost_add_tool.py
  tools/test_verify_tool.py
.env.example
```

---

### Task 1: Config layer

**Files:**
- Create: `dealercenter_agent/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `LLMConfig(provider: Literal["openai","anthropic","google"], model: str, system_prompt_path: str, api_key: SecretStr)`, `BrowserConfig(engine: Literal["playwright"], headless: bool, auth_state_path: Path)`, `DBConfig(driver: Literal["supabase","postgresql","mysql"], host: str, port: int, user: str, password: SecretStr, name: str)` with computed property `DBConfig.url -> str`, `QueueConfig(redis_url: str)`, `AppConfig(llm: LLMConfig, browser: BrowserConfig, db: DBConfig, queue: QueueConfig)`. All are `pydantic_settings.BaseSettings` reading from `.env` with field prefixes (`LLM_`, `BROWSER_`, `DB_`, `QUEUE_`).

- [ ] **Step 1: Write the failing test for `DBConfig.url`**

```python
def test_db_url_postgresql(monkeypatch):
    monkeypatch.setenv("DB_DRIVER", "postgresql")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_USER", "app")
    monkeypatch.setenv("DB_PASSWORD", "secret")
    monkeypatch.setenv("DB_NAME", "dealercenter")
    cfg = DBConfig()
    assert cfg.url == "postgresql+psycopg://app:secret@localhost:5432/dealercenter"

def test_db_url_mysql(monkeypatch):
    monkeypatch.setenv("DB_DRIVER", "mysql")
    # ... same fields
    assert cfg.url.startswith("mysql+pymysql://")

def test_db_url_supabase_uses_postgres_dialect(monkeypatch):
    monkeypatch.setenv("DB_DRIVER", "supabase")
    # ... same fields
    assert cfg.url.startswith("postgresql+psycopg://")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ImportError` / `NameError` (module doesn't exist yet)

- [ ] **Step 3: Implement `config.py`**

`DBConfig.url` maps `driver` → SQLAlchemy dialect prefix (`supabase` and `postgresql` both → `postgresql+psycopg://`, `mysql` → `mysql+pymysql://`), then formats the rest of the URL from the other fields. Each `*Config` class loads from `.env` via its own `model_config = SettingsConfigDict(env_prefix=..., env_file=".env")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Create `.env.example`** listing every env var the four config classes read (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_SYSTEM_PROMPT_PATH`, `LLM_API_KEY`, `BROWSER_ENGINE`, `BROWSER_HEADLESS`, `BROWSER_AUTH_STATE_PATH`, `DB_DRIVER`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `QUEUE_REDIS_URL`) with placeholder values and one comment line each.

- [ ] **Step 6: Commit**

```bash
git add dealercenter_agent/config.py tests/test_config.py .env.example
git commit -m "feat: add config layer with driver-swappable DB URL"
```

---

### Task 2: BrowserDriver abstraction + PlaywrightDriver

**Files:**
- Create: `dealercenter_agent/automation/base.py`
- Create: `dealercenter_agent/automation/playwright_driver.py`
- Create: `tests/fixtures/login_form.html`, `tests/fixtures/modal_chain.html`
- Test: `tests/automation/test_playwright_driver.py`

**Interfaces:**
- Consumes: `BrowserConfig` from Task 1.
- Produces: `BrowserDriver` ABC with methods `goto(url: str) -> None`, `current_url() -> str`, `fill(selector: str, value: str) -> None`, `click(selector: str) -> None`, `click_submit_near(field_selector: str) -> None` (the form-scoped, aria-hidden-excluding submit click proven in the POC), `wait_visible(selector: str, timeout_ms: int = 2000) -> bool` (polls via `wait_for(state="visible")`, never a single-shot `is_visible()`), `dismiss_blocking_modals(max_rounds: int = 5) -> None`, `get_text(selector: str) -> str`, `save_session() -> dict`, `load_session(state: dict) -> None`, `screenshot(path: str) -> None`. `PlaywrightDriver(BrowserDriver)` implements all of them, ported from `poc_dealercenter_login.py`'s `submit_button_for`/`click_when_enabled`/`wait_visible`/`dismiss_blocking_modals`.

- [ ] **Step 1: Write `tests/fixtures/login_form.html`**

A minimal static page reproducing the exact structure hit live: one `<form>` containing an `<input id="username">`, `<input id="password">`, and **two** `<button type="submit">Continue</button>` — the first with `aria-hidden="true" tabindex="-1"`, the second plain. (This is the structure captured directly from `auth.dealercenter.net` earlier in this project.)

- [ ] **Step 2: Write `tests/fixtures/modal_chain.html`**

A page with a button that, on page load after a `setTimeout`, injects a `.k-overlay` + `.k-dialog` containing a button with text "Yes, trust this browser" — reproducing the async-render race that broke `is_visible()` live.

- [ ] **Step 3: Write the failing tests**

```python
def test_click_submit_near_skips_aria_hidden_button(playwright_page, login_form_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(login_form_fixture_url)
    driver.fill("#username", "x")
    driver.fill("#password", "y")
    driver.click_submit_near("#password")
    assert playwright_page.evaluate("window.__submitted_button_aria_hidden__") == "false"

def test_wait_visible_polls_for_async_modal(playwright_page, modal_chain_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(modal_chain_fixture_url)  # modal injects after 1.5s delay
    assert driver.wait_visible("button:has-text('trust this browser')", timeout_ms=3000) is True

def test_dismiss_blocking_modals_clears_overlay(playwright_page, modal_chain_fixture_url):
    driver = PlaywrightDriver(playwright_page)
    driver.goto(modal_chain_fixture_url)
    driver.dismiss_blocking_modals()
    assert driver.wait_visible(".k-overlay", timeout_ms=500) is False
```

(`playwright_page` and the fixture-URL fixtures are added to `tests/conftest.py` in this task: a session-scoped Playwright browser launching headless, a `file://` URL per HTML fixture.)

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/automation/test_playwright_driver.py -v`
Expected: FAIL (module/class not implemented)

- [ ] **Step 5: Implement `automation/base.py` and `automation/playwright_driver.py`**

Port the four proven functions from `poc_dealercenter_login.py` (`submit_button_for` → `click_submit_near`, `click_when_enabled` folded into the same method, `wait_visible`, `dismiss_blocking_modals`) onto the `PlaywrightDriver` class, parameterized by `self.page` instead of a bare `page` argument. `login_form.html`'s fixture button sets `window.__submitted_button_aria_hidden__` via an inline `onclick` so the test can assert which button actually received the click.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/automation/test_playwright_driver.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add dealercenter_agent/automation tests/automation tests/fixtures tests/conftest.py
git commit -m "feat: add BrowserDriver abstraction with Playwright implementation"
```

---

### Task 3: DB models + engine factory

**Files:**
- Create: `dealercenter_agent/db/models.py`
- Create: `dealercenter_agent/db/engine.py`
- Create: `alembic/` scaffold (`alembic init`, then one initial migration)
- Test: `tests/db/test_models.py`

**Interfaces:**
- Consumes: `DBConfig` from Task 1.
- Produces: SQLAlchemy declarative models — `Tenant(id, name, created_at)`; `Invoice(id, tenant_id, invoice_number, vendor_name, grand_total, invoice_date, vin_last6, po_number, status, payload_json, local_pdf_path, callback_url, created_at, updated_at)` with `status` constrained to `queued|processing|exists|created|failed`; `ToolRun(id, invoice_id, tool_name, input_json, output_json, status, duration_ms, llm_cost_usd, started_at, finished_at)`; `RecoveryAttempt(id, invoice_id, tool_run_id, classification, llm_model, prompt_tokens, completion_tokens, cost_usd, action_taken, outcome, langsmith_run_id, created_at)`; `Escalation(id, invoice_id, reason, notified_at, resolved_at, resolved_by, resolution_note)`; `AuthSession(id, tenant_id, storage_state_json, device_trusted, last_used_at, expires_at)`. `engine.py` exposes `get_engine(cfg: DBConfig) -> Engine` and `get_session_factory(cfg: DBConfig) -> sessionmaker`.

- [ ] **Step 1: Write the failing test**

```python
def test_invoice_round_trip(sqlite_session):  # in-memory SQLite stands in for "any dialect" here
    tenant = Tenant(name="Tailgate Motor Co")
    sqlite_session.add(tenant)
    sqlite_session.flush()
    inv = Invoice(tenant_id=tenant.id, invoice_number="110011350",
                   vendor_name="Jensen", grand_total=250.00,
                   invoice_date=date(2026, 11, 17), vin_last6="G16336",
                   status="queued", payload_json={}, callback_url="https://x")
    sqlite_session.add(inv)
    sqlite_session.commit()
    fetched = sqlite_session.get(Invoice, inv.id)
    assert fetched.status == "queued"
    assert fetched.tenant_id == tenant.id

def test_tool_run_zero_cost_by_default(sqlite_session):
    run = ToolRun(invoice_id=1, tool_name="LoginTool", input_json={}, output_json={},
                  status="success", duration_ms=120)
    assert run.llm_cost_usd == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/db/test_models.py -v`
Expected: FAIL (module not implemented)

- [ ] **Step 3: Implement `db/models.py`**

Use only dialect-agnostic column types (`String`, `Integer`, `Numeric`, `Date`, `DateTime`, `JSON` — not `JSONB`) so the same models work unmodified against SQLite (tests), Supabase/Postgres (dev), and MySQL (prod). `llm_cost_usd` defaults to `0`.

- [ ] **Step 4: Implement `db/engine.py`**

`get_engine` builds from `DBConfig.url`; `get_session_factory` wraps it in a `sessionmaker`. `tests/conftest.py` adds a `sqlite_session` fixture using `sqlite:///:memory:` through the same `get_engine`/`get_session_factory` functions, proving the models are dialect-agnostic by construction rather than by inspection.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/db/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Generate the initial Alembic migration**

Run: `alembic revision --autogenerate -m "initial schema"` then inspect the generated file for dialect-specific constructs and remove any found.

- [ ] **Step 7: Commit**

```bash
git add dealercenter_agent/db alembic tests/db
git commit -m "feat: add dialect-agnostic DB models and engine factory"
```

---

### Task 4: Tool base + LoginTool

**Files:**
- Create: `dealercenter_agent/tools/base.py`
- Create: `dealercenter_agent/tools/login_tool.py`
- Test: `tests/tools/test_login_tool.py`

**Interfaces:**
- Consumes: `BrowserDriver` from Task 2, `AuthSession` model from Task 3.
- Produces: `ToolStatus(str, Enum)` with values `SUCCESS, EXISTS, FAILED`; `ToolResult(status: ToolStatus, data: dict = {}, reason: str | None = None)` (Pydantic model); `StepFailure(Exception)` with attrs `tool_name: str, reason: str, screenshot_path: str | None, dom_snapshot: str | None`; `Tool(ABC)` with abstract `name: str` and `run(self, driver: BrowserDriver, **kwargs) -> ToolResult`. `LoginTool.run(driver, username: str, password: str, saved_state: dict | None, mfa_code_provider: Callable[[], str]) -> ToolResult` — `mfa_code_provider` is a no-arg callable returning the 6-digit code; the real CLI/queue callers decide how to source it (terminal `input()` today, a human-escalation callback later), `LoginTool` itself never calls `input()` directly.

- [ ] **Step 1: Write the failing tests against a `FakeBrowserDriver`**

```python
def test_login_reuses_valid_saved_session(fake_driver_already_logged_in):
    result = LoginTool().run(fake_driver_already_logged_in, username="u", password="p",
                              saved_state={"cookies": [...]}, mfa_code_provider=lambda: "000000")
    assert result.status == ToolStatus.SUCCESS
    assert fake_driver_already_logged_in.login_form_submitted is False

def test_login_detects_mfa_and_uses_provided_code(fake_driver_requires_mfa):
    result = LoginTool().run(fake_driver_requires_mfa, username="u", password="p",
                              saved_state=None, mfa_code_provider=lambda: "123456")
    assert result.status == ToolStatus.SUCCESS
    assert fake_driver_requires_mfa.mfa_code_submitted == "123456"

def test_login_raises_step_failure_when_mfa_code_rejected(fake_driver_always_rejects_mfa):
    with pytest.raises(StepFailure) as exc:
        LoginTool().run(fake_driver_always_rejects_mfa, username="u", password="p",
                         saved_state=None, mfa_code_provider=lambda: "000000")
    assert exc.value.tool_name == "LoginTool"
```

(`FakeBrowserDriver` and its three pre-scripted variants are added to `tests/conftest.py` in this task: a `BrowserDriver` subclass whose methods return scripted values/record calls instead of touching a real page.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tools/test_login_tool.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `tools/base.py` and `tools/login_tool.py`**

`LoginTool.run` ports the control flow already proven in `poc_dealercenter_login.py`'s `main`/`do_login`/`handle_mfa`: try `load_session` + `looks_logged_in`-equivalent check first; on failure, fill credentials, `click_submit_near`, detect MFA, call `mfa_code_provider()`, fill + submit, `dismiss_blocking_modals`; raise `StepFailure("LoginTool", ...)` on any unrecoverable state instead of printing and returning `False` as the POC script did.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tools/test_login_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dealercenter_agent/tools/base.py dealercenter_agent/tools/login_tool.py tests/tools/test_login_tool.py
git commit -m "feat: add Tool base types and LoginTool"
```

---

### Task 5: SearchInvoiceTool

**Files:**
- Create: `dealercenter_agent/tools/search_invoice_tool.py`
- Test: `tests/tools/test_search_invoice_tool.py`

**Interfaces:**
- Consumes: `Tool`, `ToolResult`, `StepFailure`, `BrowserDriver` from Tasks 2/4.
- Produces: `SearchInvoiceTool.run(driver, vin_last6: str | None, po_number: str | None, stock_number: str | None) -> ToolResult` — `data={"match_type": "inventory"|"deal", "handle": <opaque driver-specific locator token>}` on success.

- [ ] **Step 1: Write the failing tests**

```python
def test_search_finds_single_inventory_match(fake_driver_one_inventory_match):
    result = SearchInvoiceTool().run(fake_driver_one_inventory_match, vin_last6="G16336",
                                      po_number=None, stock_number=None)
    assert result.status == ToolStatus.SUCCESS
    assert result.data["match_type"] == "inventory"

def test_search_falls_back_to_deal_block_when_no_inventory_row(fake_driver_only_deal_match):
    result = SearchInvoiceTool().run(fake_driver_only_deal_match, vin_last6="G16336",
                                      po_number=None, stock_number=None)
    assert result.data["match_type"] == "deal"

def test_search_fails_distinctly_on_no_match(fake_driver_no_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(fake_driver_no_matches, vin_last6="ZZZZZZ", po_number=None, stock_number=None)
    assert exc.value.reason == "not found in inventory"

def test_search_fails_distinctly_on_ambiguous_match(fake_driver_two_inventory_matches):
    with pytest.raises(StepFailure) as exc:
        SearchInvoiceTool().run(fake_driver_two_inventory_matches, vin_last6="G16336",
                                 po_number=None, stock_number=None)
    assert "ambiguous" in exc.value.reason or "multiple" in exc.value.reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tools/test_search_invoice_tool.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `tools/search_invoice_tool.py`**

Query priority is VIN-last-6, else PO, else stock number (per the existing skill's rule). Read the Inventory block first; if empty, read the Deal block in the same dropdown. Exactly one match in either block → success; zero in both → `StepFailure(reason="not found in inventory")`; more than one in either block → a distinctly-worded ambiguous-match failure (never guesses).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tools/test_search_invoice_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dealercenter_agent/tools/search_invoice_tool.py tests/tools/test_search_invoice_tool.py
git commit -m "feat: add SearchInvoiceTool"
```

---

### Task 6: CostAddTool

**Files:**
- Create: `dealercenter_agent/tools/cost_add_tool.py`
- Test: `tests/tools/test_cost_add_tool.py`

**Interfaces:**
- Consumes: `Tool`, `ToolResult`, `StepFailure` from Task 4; the `handle` from `SearchInvoiceTool`'s `ToolResult.data`.
- Produces: `CostAddTool.run(driver, handle, invoice_number: str, grand_total: Decimal, invoice_date: date, vendor_name: str, line_labels: list[str]) -> ToolResult` with `status` one of `EXISTS` (memo already has `invoice_number`) or `SUCCESS` (row added, file uploaded, Save clicked, grid re-checked by the caller via `VerifyTool` — Task 7).

- [ ] **Step 1: Write the failing tests**

```python
def test_cost_add_returns_exists_without_writing_when_memo_already_present(fake_driver_memo_has_invoice):
    result = CostAddTool().run(fake_driver_memo_has_invoice, handle="veh-1", invoice_number="110011350",
                                grand_total=Decimal("250.00"), invoice_date=date(2026, 11, 17),
                                vendor_name="Jensen Automotive", line_labels=["oil change"])
    assert result.status == ToolStatus.EXISTS
    assert fake_driver_memo_has_invoice.add_new_clicked is False

def test_cost_add_classifies_repair_vs_parts(fake_driver_memo_empty):
    CostAddTool().run(fake_driver_memo_empty, handle="veh-1", invoice_number="X",
                       grand_total=Decimal("10"), invoice_date=date(2026, 1, 1),
                       vendor_name="Acme", line_labels=["labor", "brake pad"])
    assert fake_driver_memo_empty.last_category == "Repair"  # any labor/lube/etc line -> Repair

def test_cost_add_fails_when_vendor_has_no_close_match(fake_driver_memo_empty_no_vendor_match):
    with pytest.raises(StepFailure) as exc:
        CostAddTool().run(fake_driver_memo_empty_no_vendor_match, handle="veh-1", invoice_number="X",
                           grand_total=Decimal("10"), invoice_date=date(2026, 1, 1),
                           vendor_name="TotallyUnknownVendorXYZ", line_labels=["part"])
    assert "vendor" in exc.value.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tools/test_cost_add_tool.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `tools/cost_add_tool.py`**

Memo-check first, exact match ignoring spaces, per the existing skill's rule — `EXISTS` short-circuits before any write. Category/Description is `Repair` if any `line_labels` entry matches `{labor, lube, oil-change service, rotation, inspection, install, diagnostic}` (case-insensitive substring), else `Parts`. Vendor resolves to the first word of `vendor_name`, matched against the dropdown's options for the closest legal name; no match → `StepFailure` naming the vendor, never a guessed/invented vendor. Cost is `grand_total` only, Date is `invoice_date` formatted `MM/DD/YYYY`, Memo is `invoice_number` only — these four are pinned values, not configurable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tools/test_cost_add_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dealercenter_agent/tools/cost_add_tool.py tests/tools/test_cost_add_tool.py
git commit -m "feat: add CostAddTool"
```

---

### Task 7: VerifyTool

**Files:**
- Create: `dealercenter_agent/tools/verify_tool.py`
- Test: `tests/tools/test_verify_tool.py`

**Interfaces:**
- Consumes: `Tool`, `ToolResult`, `StepFailure` from Task 4.
- Produces: `VerifyTool.run(driver, handle, invoice_number: str, pdf_filename: str) -> ToolResult` — `status=SUCCESS` with `reason=None` only if the Files grid lists the PDF; otherwise `status=FAILED, reason="PDF not listed on Files"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_verify_succeeds_when_pdf_listed(fake_driver_pdf_in_files_grid):
    result = VerifyTool().run(fake_driver_pdf_in_files_grid, handle="veh-1",
                               invoice_number="110011350", pdf_filename="Jensen 110011350.pdf")
    assert result.status == ToolStatus.SUCCESS

def test_verify_fails_when_pdf_missing_from_grid(fake_driver_pdf_not_in_files_grid):
    result = VerifyTool().run(fake_driver_pdf_not_in_files_grid, handle="veh-1",
                               invoice_number="110011350", pdf_filename="Jensen 110011350.pdf")
    assert result.status == ToolStatus.FAILED
    assert result.reason == "PDF not listed on Files"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tools/test_verify_tool.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `tools/verify_tool.py`**

Reads the Files tab's grid text/rows via `driver.get_text`; a `FAILED` result here is a normal, expected outcome (not a raised `StepFailure`) — it's the signal the caller uses to decide whether to call `finish_invoice`-equivalent with `created` or hand off to the recovery layer.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/tools/test_verify_tool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dealercenter_agent/tools/verify_tool.py tests/tools/test_verify_tool.py
git commit -m "feat: add VerifyTool"
```

---

## What Phase 1 deliberately does not include

Queue/RQ wiring, the webhook API, the LangGraph recovery graph, LangSmith integration, and Azure deployment are separate subsystems with their own plans, written after this one lands — each depends on the `Tool`/`ToolResult`/`StepFailure`/`BrowserDriver` interfaces locked in here, so getting those names and shapes right in review now is what keeps the later plans from needing rework.
