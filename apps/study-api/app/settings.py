from dataclasses import dataclass, field
import os
from pathlib import Path


def env_bool(name, default):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass
class Settings:
    data: Path = field(default_factory=lambda: Path(os.getenv('STUDY_DATA_PATH', './data')))
    library: Path = field(default_factory=lambda: Path(os.getenv('STUDY_LIBRARY_PATH', './library')))
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL', ''), repr=False)
    database_worker_url: str = field(default_factory=lambda: os.getenv('DATABASE_WORKER_URL', ''), repr=False)
    # Where originals and processing artifacts live. Local disk now; the adapter is the seam for S3.
    objects: Path = field(default_factory=lambda: Path(os.getenv('STUDY_OBJECTS_PATH', '')) if os.getenv('STUDY_OBJECTS_PATH') else None)
    docling: str = field(default_factory=lambda: os.getenv('DOCLING_URL', 'http://localhost:5001').rstrip('/'))
    searxng: str = field(default_factory=lambda: os.getenv('SEARXNG_URL', 'http://localhost:8081').rstrip('/'))
    ollama: str = field(default_factory=lambda: os.getenv('OLLAMA_URL', 'http://localhost:11435').rstrip('/'))
    embedding_provider: str = field(default_factory=lambda: os.getenv('EMBEDDING_PROVIDER', 'ollama'))
    embedding_model: str = field(default_factory=lambda: os.getenv('EMBEDDING_MODEL', 'embeddinggemma:300m'))
    llm_url: str = field(default_factory=lambda: os.getenv('LLM_BASE_URL', '').strip().rstrip('/'))
    llm_key: str = field(default_factory=lambda: os.getenv('LLM_API_KEY', '').strip(), repr=False)
    llm_model: str = field(default_factory=lambda: os.getenv('LLM_MODEL', '').strip())
    tutor_provider: str = field(default_factory=lambda: os.getenv('TUTOR_PROVIDER', 'auto').strip())
    ollama_chat_model: str = field(default_factory=lambda: os.getenv('OLLAMA_CHAT_MODEL', '').strip())
    ollama_chat_urls: str = field(default_factory=lambda: os.getenv('OLLAMA_CHAT_URLS', '').strip())
    # Base64 32-byte key for provider secrets. Rotating means adding a new version.
    secret_key: str = field(default_factory=lambda: os.getenv('STUDYHUB_SECRET_KEY', '').strip(), repr=False)
    cookie_secure: bool = field(default_factory=lambda: env_bool('STUDYHUB_COOKIE_SECURE', False))
    session_days: int = field(default_factory=lambda: int(os.getenv('STUDYHUB_SESSION_DAYS', '30')))
    trash_retention_days: int = field(default_factory=lambda: int(os.getenv('STUDYHUB_TRASH_RETENTION_DAYS', '30')))
    account_deletion_grace_days: int = field(default_factory=lambda: int(os.getenv('STUDYHUB_ACCOUNT_DELETION_GRACE_DAYS', '30')))
    # Self-hosted installs reach Ollama on a private network. Hosted installs should turn this off.
    allow_private_provider_urls: bool = field(default_factory=lambda: env_bool('STUDYHUB_ALLOW_PRIVATE_PROVIDER_URLS', True))
    signup_open: bool = field(default_factory=lambda: env_bool('STUDYHUB_SIGNUP_OPEN', True))
    chunk_chars: int = 1400

    def __post_init__(self):
        if self.objects is None:
            self.objects = self.data/'objects'

    def llm_status(self):
        # Never return the key or make a completion request for a health check.
        missing = [name for name, value in [('LLM_BASE_URL', self.llm_url),
                   ('LLM_API_KEY', self.llm_key), ('LLM_MODEL', self.llm_model)] if not value]
        return {'configured': not missing, 'key_present': bool(self.llm_key),
                'model': self.llm_model or None, 'missing': missing, 'endpoint_checked': False}
