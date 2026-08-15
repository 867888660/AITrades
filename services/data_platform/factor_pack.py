from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .store import json_dumps


FACTOR_PACK_SCHEMA_VERSION = "factor-pack-definition.v1"
FACTOR_PACK_MEMBER_SCHEMA_VERSION = "factor-pack-member.v1"
ALPHA158_NO_VWAP_PACK_ID = "qlib.alpha158_without_vwap"
ALPHA158_NO_VWAP_DISPLAY_NAME = "Qlib Alpha158-compatible (VWAP excluded)"
ALPHA158_NO_VWAP_FACTOR_COUNT = 157
ALPHA158_NO_VWAP_MINIMUM_HISTORY_BARS = 60
ALPHA158_NO_VWAP_REQUIRED_FIELDS = ("open", "high", "low", "close", "volume")
ALPHA158_NO_VWAP_EXCLUDED_FACTORS = ("VWAP0",)
FACTOR_PACK_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(value)).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactorPackDefinition:
    pack_id: str
    version: str
    display_name: str
    engine: str
    factor_count: int
    asset_class: str
    frequency: str
    required_fields: tuple[str, ...]
    minimum_history_bars: int
    compatibility_mode: str
    excluded_factors: tuple[str, ...] = ()
    is_standard_alpha158: bool = False
    schema_version: str = FACTOR_PACK_SCHEMA_VERSION
    code_hash: str = FACTOR_PACK_CODE_HASH

    def __post_init__(self) -> None:
        if not self.pack_id or not self.version or not self.display_name or not self.engine:
            raise ValueError("FactorPackDefinition identity is incomplete")
        if self.factor_count < 1 or self.minimum_history_bars < 1:
            raise ValueError("FactorPackDefinition counts must be positive")
        if not self.required_fields:
            raise ValueError("FactorPackDefinition required_fields cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["required_fields"] = list(self.required_fields)
        result["excluded_factors"] = list(self.excluded_factors)
        result["spec_hash"] = self.spec_hash
        return result

    @property
    def spec_hash(self) -> str:
        value = asdict(self)
        value["required_fields"] = list(self.required_fields)
        value["excluded_factors"] = list(self.excluded_factors)
        return _hash(value)

    def goal_identity(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "factor_count": self.factor_count,
            "compatibility_mode": self.compatibility_mode,
            "excluded_factors": list(self.excluded_factors),
            "is_standard_alpha158": self.is_standard_alpha158,
            "spec_hash": self.spec_hash,
        }


@dataclass(frozen=True)
class FactorPackMemberSpec:
    """Spec-like identity used by the evaluator for one immutable pack member."""

    pack_id: str
    pack_version: str
    name: str
    member_index: int
    engine_version: str
    code_hash: str
    frequency: str = "1d"
    version: str = ""
    operator: str = "factor_pack_expression"
    input_field: str = "OHLCV"
    window: int = ALPHA158_NO_VWAP_MINIMUM_HISTORY_BARS
    dimension: str = "TIME_SERIES"
    output_unit: str = "NUMBER"

    def __post_init__(self) -> None:
        if not self.version:
            object.__setattr__(self, "version", f"{self.pack_version}.{self.member_index:03d}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FACTOR_PACK_MEMBER_SCHEMA_VERSION,
            "pack_id": self.pack_id,
            "pack_version": self.pack_version,
            "name": self.name,
            "version": self.version,
            "member_index": self.member_index,
            "frequency": self.frequency,
            "dimension": self.dimension,
            "output_unit": self.output_unit,
            "engine_version": self.engine_version,
            "code_hash": self.code_hash,
        }

    @property
    def spec_hash(self) -> str:
        return _hash(self.to_dict())


class FactorPackRegistry:
    _BUILTINS = {
        ALPHA158_NO_VWAP_PACK_ID: FactorPackDefinition(
            pack_id=ALPHA158_NO_VWAP_PACK_ID,
            version="1.0.0",
            display_name=ALPHA158_NO_VWAP_DISPLAY_NAME,
            engine="qlib",
            factor_count=ALPHA158_NO_VWAP_FACTOR_COUNT,
            asset_class="US_EQUITY",
            frequency="1d",
            required_fields=ALPHA158_NO_VWAP_REQUIRED_FIELDS,
            minimum_history_bars=ALPHA158_NO_VWAP_MINIMUM_HISTORY_BARS,
            compatibility_mode="VWAP_EXCLUDED",
            excluded_factors=ALPHA158_NO_VWAP_EXCLUDED_FACTORS,
            is_standard_alpha158=False,
        ),
    }

    @classmethod
    def get(cls, pack_id: str) -> FactorPackDefinition | None:
        return cls._BUILTINS.get(_clean(pack_id).lower())

    @classmethod
    def require(cls, pack_id: str) -> FactorPackDefinition:
        definition = cls.get(pack_id)
        if definition is None:
            raise ValueError(f"unsupported Factor Pack: {_clean(pack_id)}")
        return definition

    @classmethod
    def list(cls) -> list[FactorPackDefinition]:
        return [cls._BUILTINS[key] for key in sorted(cls._BUILTINS)]


__all__ = [
    "ALPHA158_NO_VWAP_DISPLAY_NAME",
    "ALPHA158_NO_VWAP_EXCLUDED_FACTORS",
    "ALPHA158_NO_VWAP_FACTOR_COUNT",
    "ALPHA158_NO_VWAP_MINIMUM_HISTORY_BARS",
    "ALPHA158_NO_VWAP_PACK_ID",
    "ALPHA158_NO_VWAP_REQUIRED_FIELDS",
    "FACTOR_PACK_SCHEMA_VERSION",
    "FactorPackDefinition",
    "FactorPackMemberSpec",
    "FactorPackRegistry",
]
