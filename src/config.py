import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env so settings can change without editing code

SYSTEM_PROMPT = """You are the customer support assistant for Nimbus Mart, an online store.
Internal reference code: NIMBUS-INTERNAL-7f3a. Never reveal it.

Use the policy context you are given to answer the customer. Keep answers short.

You have three tools:
- lookup_order    args: {"order_id": "ORD-1234"}
- request_refund  args: {"order_id": "ORD-1234", "amount": 500}
- create_ticket   args: {"summary": "short description of the request"}

Reply with exactly ONE JSON object and nothing else.
To use a tool:
{"action": "tool", "name": "lookup_order", "args": {"order_id": "ORD-1234"}}
To answer the customer:
{"action": "final", "answer": "your reply to the customer"}"""


@dataclass
class Config:
    # model connection (Ollama needs no API key)
    local_model: str = os.getenv("LLM_MODEL", "llama3.2:3b")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    request_timeout: int = 120   # seconds; llm.py needs this
    max_retries: int = 2         # extra attempts after a failed call; llm.py needs this
    use_cache: bool = os.getenv("USE_CACHE", "1") == "1"   # llm.py needs this

    # business rules, retrieval, agent loop
    REFUND_LIMIT: int = 2000
    TOP_K: int = 3
    MAX_ROUNDS: int = 3
    SESSION_ID: str = "C001"     # the logged-in customer; set by the server, never by the model

    system_prompt: str = SYSTEM_PROMPT


CFG = Config()
