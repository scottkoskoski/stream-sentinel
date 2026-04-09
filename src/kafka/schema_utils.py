"""
Avro Schema Registry utilities for Stream-Sentinel.

Provides optional Avro serialisation/deserialisation for Kafka messages
using the Confluent Schema Registry.  When the Schema Registry is
unreachable or the ``confluent_kafka.schema_registry`` package is not
installed the helpers fall back to plain JSON -- the rest of the system
keeps working, just without schema enforcement.

Typical usage
-------------

Producer side::

    from kafka.schema_utils import get_schema_helper

    helper = get_schema_helper()
    serializer = helper.get_avro_serializer("transaction")
    # serializer is None when schema registry is unavailable
    if serializer:
        payload = serializer(transaction_dict, ctx)
    else:
        payload = json.dumps(transaction_dict).encode("utf-8")

Consumer side::

    deserializer = helper.get_avro_deserializer("transaction")
    if deserializer:
        record = deserializer(raw_bytes, ctx)
    else:
        record = json.loads(raw_bytes)
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("stream_sentinel.schema_utils")

# ---------------------------------------------------------------------------
# Attempt to import Schema Registry support -- completely optional.
# ---------------------------------------------------------------------------
try:
    from confluent_kafka.schema_registry import SchemaRegistryClient
    from confluent_kafka.schema_registry.avro import AvroDeserializer, AvroSerializer
    from confluent_kafka.serialization import MessageField, SerializationContext

    SCHEMA_REGISTRY_AVAILABLE = True
except ImportError:
    SCHEMA_REGISTRY_AVAILABLE = False
    logger.info(
        "confluent_kafka.schema_registry not installed -- "
        "Avro schema validation disabled; messages will be plain JSON"
    )


# ---------------------------------------------------------------------------
# Schema file paths (relative to repository root)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA_DIR = _REPO_ROOT / "schemas"

SCHEMA_FILES: Dict[str, str] = {
    "transaction": "transaction.avsc",
    "fraud_alert": "fraud_alert.avsc",
    "fraud_score": "fraud_score.avsc",
}


# ---------------------------------------------------------------------------
# Helper class
# ---------------------------------------------------------------------------


class SchemaHelper:
    """
    Manages Avro schemas and provides serializer / deserializer instances.

    All public methods are safe to call even when the Schema Registry is
    not available -- they return ``None`` and log a warning instead of
    raising.
    """

    def __init__(self, schema_registry_url: Optional[str] = None):
        self._schemas: Dict[str, str] = {}
        self._serializers: Dict[str, Any] = {}
        self._deserializers: Dict[str, Any] = {}
        self._sr_client: Optional[Any] = None
        self._available = False

        # Load raw Avro schema strings from the filesystem.
        self._load_schema_files()

        # Try to connect to the Schema Registry.
        if SCHEMA_REGISTRY_AVAILABLE:
            url = (
                schema_registry_url
                or os.getenv("SCHEMA_REGISTRY_URL")
                or os.getenv("SCHEMA_REGISTRY_STAGING")
                or "http://localhost:8081"
            )
            self._init_registry_client(url)

    # ------------------------------------------------------------------
    # Schema loading
    # ------------------------------------------------------------------

    def _load_schema_files(self) -> None:
        """Read ``.avsc`` files into memory."""
        for name, filename in SCHEMA_FILES.items():
            path = _SCHEMA_DIR / filename
            if path.exists():
                self._schemas[name] = path.read_text()
                logger.debug("Loaded schema '%s' from %s", name, path)
            else:
                logger.warning("Schema file not found: %s", path)

    def get_schema_str(self, name: str) -> Optional[str]:
        """Return the raw Avro schema JSON string, or ``None``."""
        return self._schemas.get(name)

    # ------------------------------------------------------------------
    # Registry client
    # ------------------------------------------------------------------

    def _init_registry_client(self, url: str) -> None:
        """Attempt to create a SchemaRegistryClient."""
        try:
            self._sr_client = SchemaRegistryClient({"url": url})
            # Quick connectivity check -- list subjects.
            self._sr_client.get_subjects()
            self._available = True
            logger.info("Connected to Schema Registry at %s", url)
        except Exception as exc:
            self._sr_client = None
            self._available = False
            logger.warning(
                "Schema Registry at %s is not reachable (%s) -- "
                "Avro validation disabled, falling back to plain JSON",
                url,
                exc,
            )

    @property
    def is_available(self) -> bool:
        """``True`` when a working Schema Registry connection exists."""
        return self._available

    # ------------------------------------------------------------------
    # Serializer / Deserializer factories
    # ------------------------------------------------------------------

    def get_avro_serializer(self, schema_name: str) -> Optional[Any]:
        """
        Return an ``AvroSerializer`` for *schema_name*, or ``None`` if
        the Schema Registry is not available.
        """
        if not self._available:
            return None

        if schema_name in self._serializers:
            return self._serializers[schema_name]

        schema_str = self._schemas.get(schema_name)
        if schema_str is None:
            logger.warning("No schema loaded for '%s'", schema_name)
            return None

        try:
            serializer = AvroSerializer(
                self._sr_client,
                schema_str,
            )
            self._serializers[schema_name] = serializer
            logger.info("Created AvroSerializer for '%s'", schema_name)
            return serializer
        except Exception as exc:
            logger.warning("Failed to create AvroSerializer for '%s': %s", schema_name, exc)
            return None

    def get_avro_deserializer(self, schema_name: str) -> Optional[Any]:
        """
        Return an ``AvroDeserializer`` for *schema_name*, or ``None`` if
        the Schema Registry is not available.
        """
        if not self._available:
            return None

        if schema_name in self._deserializers:
            return self._deserializers[schema_name]

        schema_str = self._schemas.get(schema_name)
        if schema_str is None:
            logger.warning("No schema loaded for '%s'", schema_name)
            return None

        try:
            deserializer = AvroDeserializer(
                self._sr_client,
                schema_str,
            )
            self._deserializers[schema_name] = deserializer
            logger.info("Created AvroDeserializer for '%s'", schema_name)
            return deserializer
        except Exception as exc:
            logger.warning(
                "Failed to create AvroDeserializer for '%s': %s",
                schema_name,
                exc,
            )
            return None


# ---------------------------------------------------------------------------
# Convenience helpers for producing / consuming
# ---------------------------------------------------------------------------


def serialize_message(
    schema_helper: SchemaHelper,
    schema_name: str,
    data: Dict[str, Any],
    topic: str,
) -> bytes:
    """
    Serialize *data* using Avro if the Schema Registry is available,
    otherwise fall back to JSON encoding.
    """
    serializer = schema_helper.get_avro_serializer(schema_name)
    if serializer is not None:
        try:
            ctx = SerializationContext(topic, MessageField.VALUE)
            return serializer(data, ctx)
        except Exception as exc:
            logger.warning(
                "Avro serialization failed for '%s', falling back to JSON: %s",
                schema_name,
                exc,
            )
    return json.dumps(data, default=str).encode("utf-8")


def deserialize_message(
    schema_helper: SchemaHelper,
    schema_name: str,
    raw: bytes,
    topic: str,
) -> Dict[str, Any]:
    """
    Deserialize *raw* bytes using Avro if the Schema Registry is
    available, otherwise fall back to JSON decoding.
    """
    deserializer = schema_helper.get_avro_deserializer(schema_name)
    if deserializer is not None:
        try:
            ctx = SerializationContext(topic, MessageField.VALUE)
            return deserializer(raw, ctx)
        except Exception as exc:
            logger.warning(
                "Avro deserialization failed for '%s', falling back to JSON: %s",
                schema_name,
                exc,
            )
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: Optional[SchemaHelper] = None
_instance_lock = threading.Lock()


def get_schema_helper(schema_registry_url: Optional[str] = None) -> SchemaHelper:
    """Return a module-level singleton ``SchemaHelper``."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = SchemaHelper(schema_registry_url=schema_registry_url)
        return _instance
