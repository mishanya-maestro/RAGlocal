import os
from pathlib import Path

from dotenv import load_dotenv

_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv()

# Кэш HuggingFace / Whisper на диск проекта (обычно D:), а не в %USERPROFILE% на C:,
# где часто заканчивается место.
HF_CACHE_DIR = Path(
    os.environ.get("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface"))
)
HF_HUB_CACHE = Path(
    os.environ.get(
        "HUGGINGFACE_HUB_CACHE",
        str(HF_CACHE_DIR / "hub"),
    )
)
try:
    HF_HUB_CACHE.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_HUB_CACHE))
# ctranslate2 / faster-whisper тоже смотрят на эти переменные через huggingface_hub.

FULLTEXT_DB = Path(os.environ.get("FULLTEXT_DB", str(PROJECT_ROOT / "fulltext.db")))
CHROMA_PERSIST_DIR = Path(
    os.environ.get("CHROMA_PERSIST_DIR", str(PROJECT_ROOT / "chroma_db"))
)
ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
# Режим: "local" или "api"
# Для обратной совместимости поддерживаются старые значения из окружения:
# - split -> api
# - ollama -> local
# - openrouter -> api
_RAW_MODE = os.environ.get("RAG_MODE", "api").strip().lower()
if _RAW_MODE == "split":
    MODE = "api"
elif _RAW_MODE == "ollama":
    MODE = "local"
elif _RAW_MODE == "openrouter":
    MODE = "api"
else:
    MODE = _RAW_MODE if _RAW_MODE in ("local", "api") else "api"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_EMBEDDING_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
OPENROUTER_LLM_MODEL = os.environ.get("OPENROUTER_LLM_MODEL", "openai/gpt-oss-120b:free")

# --- Retrieval (фиксированы: одна векторная БД / один корпус) ---
RETRIEVER_TOP_K = 7
RERANKER_TOP_K = 3
VECTOR_TOP_K = 100
FTS_TOP_K = 60
RRF_K = 60
RERANK_POOL_SIZE = 15

VECTOR_WEIGHT = 1.0
FTS_WEIGHT = 1.4

USE_RERANK = os.environ.get("USE_RERANK", "1") not in ("0", "false", "False", "")

RERANK_MAX_TOKENS = 400
RERANKER_MODEL = os.environ.get(
    "RERANKER_MODEL",
    "awenleven/Qwen3-Reranker-4B:Q4_K_M",
)
RERANK_DOC_MAX_CHARS = int(os.environ.get("RERANK_DOC_MAX_CHARS", "1800"))
RERANK_TIMEOUT_SEC = float(os.environ.get("RERANK_TIMEOUT_SEC", "60"))
RERANK_NUM_PREDICT = int(os.environ.get("RERANK_NUM_PREDICT", "16"))

SEARCH_TOP_K = VECTOR_TOP_K

GENERATOR_TEMP = float(os.environ.get("GENERATOR_TEMP", "0.1"))
GENERATOR_MAX_TOKENS = int(os.environ.get("GENERATOR_MAX_TOKENS", "1800"))

# Chunk sizes фиксированы — не зависят от мощности ПК.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_LLM_MODEL = os.environ.get("OLLAMA_LLM_MODEL", "qwen3.5:4b")
ASR_MODEL = os.environ.get("ASR_MODEL", "whisper-medium")
DEFAULT_LOCAL_EMBEDDING = os.environ.get("EMBEDDING_MODEL", "qwen3-embedding:4b")

if MODE == "local":
    LLM_MODEL = OLLAMA_LLM_MODEL
    BASE_URL = OLLAMA_BASE_URL + "/chat/completions"
    BASE_URL1 = OLLAMA_BASE_URL + "/chat/completions"
    EMBEDDING_MODEL = DEFAULT_LOCAL_EMBEDDING
    HEADERS = {"Content-Type": "application/json"}
    HEADERS1 = HEADERS
    COLLECTION_NAME = "electoral_local"
elif MODE == "api":
    print("mode:api")
    BASE_URL = OLLAMA_BASE_URL + "/chat/completions"
    BASE_URL1 = OPENROUTER_BASE_URL
    LLM_MODEL = OPENROUTER_LLM_MODEL
    EMBEDDING_MODEL = DEFAULT_LOCAL_EMBEDDING
    HEADERS = {"Content-Type": "application/json"}
    _auth = OPENROUTER_API_KEY.strip()
    HEADERS1 = {
        "Authorization": f"Bearer {_auth}" if _auth else "Bearer ",
        "Content-Type": "application/json",
    }
    COLLECTION_NAME = "electoral_local"
else:
    LLM_MODEL = OPENROUTER_LLM_MODEL
    BASE_URL = OPENROUTER_BASE_URL
    BASE_URL1 = OPENROUTER_BASE_URL
    _auth = OPENROUTER_API_KEY.strip()
    HEADERS = {
        "Authorization": f"Bearer {_auth}" if _auth else "Bearer ",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "RAG Agent",
    }
    HEADERS1 = HEADERS
    EMBEDDING_MODEL = OPENROUTER_EMBEDDING_MODEL
    COLLECTION_NAME = "electoral_laws"
