from dealercenter_agent.config import BrowserConfig, DBConfig, LLMConfig, StorageConfig


def test_db_config_reads_database_url_verbatim(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@host:5432/db")
    monkeypatch.setenv("DB_BACKEND", "supabase")
    cfg = DBConfig(_env_file=None)
    assert cfg.database_url == "postgresql+psycopg://user:pw@host:5432/db"
    assert cfg.db_backend == "supabase"


def test_db_config_accepts_mysql_backend(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://user:pw@host:3306/db")
    monkeypatch.setenv("DB_BACKEND", "mysql")
    cfg = DBConfig(_env_file=None)
    assert cfg.db_backend == "mysql"


def test_storage_config_reads_local_path(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "/tmp/pdfs")
    cfg = StorageConfig(_env_file=None)
    assert str(cfg.storage_local_path) == "/tmp/pdfs"


def test_llm_config_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    cfg = LLMConfig(_env_file=None)
    assert cfg.provider == "anthropic"


def test_llm_config_reads_provider_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    cfg = LLMConfig(_env_file=None)
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5"


def test_browser_config_defaults_headless_true(monkeypatch):
    monkeypatch.delenv("BROWSER_HEADLESS", raising=False)
    cfg = BrowserConfig(_env_file=None)
    assert cfg.headless is True
