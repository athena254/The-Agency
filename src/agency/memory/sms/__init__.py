"""SMS - Sovereign Mind System.

Athena's local memory core. Every node is independently usable so that if the
memory OS goes offline, agents continue to operate, and if SMS goes offline,
agents continue via markdown.

Nodes currently implemented:

* :mod:`agency.memory.sms.store` - SQLite-backed item store (5-tier aware)
* :mod:`agency.memory.sms.lifecycle` - tier promotion / demotion / auto-aging
* :mod:`agency.memory.sms.retrieval` - semantic (FTS5), graph (stub), hybrid
* :mod:`agency.memory.sms.secrets` - encrypted secret storage with ACLs
* :mod:`agency.memory.sms.cpr` - context compression / summarization
"""

from agency.memory.sms.cpr import ContextCompressor
from agency.memory.sms.lifecycle import TieredMemoryEngine
from agency.memory.sms.models import MemoryItem, MemoryQuery, MemoryTier
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.secrets import SecretsStore
from agency.memory.sms.store import MemoryStore

__all__ = [
    "ContextCompressor",
    "MemoryItem",
    "MemoryQuery",
    "MemoryStore",
    "MemoryTier",
    "RetrievalEngine",
    "SecretsStore",
    "TieredMemoryEngine",
]
