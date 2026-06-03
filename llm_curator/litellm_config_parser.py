"""Read-only parser for litellm_config.yaml.

Produces a normalised view of the current routing config so the curator can
compare its eval-based recommendations against what's actually deployed.

Why not mutate here: round-tripping YAML cleanly (preserving comments + key
order) requires `ruamel.yaml`. For Phase 3 we only READ — proposals are
surfaced as structured diffs, not applied automatically.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

DEFAULT_CONFIG_PATH = os.getenv(
    "LITELLM_CONFIG_PATH",
    "",  # no default — set LITELLM_CONFIG_PATH in .env to enable proposal comparison
)


@dataclass
class AliasEntry:
    """One model_list entry — what LiteLLM exposes as a callable model_name."""
    alias: str                                # e.g. "deepseek-chat"
    model: str                                # e.g. "deepseek/deepseek-v4-flash"
    api_base: str | None = None
    timeout: int | None = None
    reasoning_flag: bool = False              # has `<<: *reasoning_flag` mixin
    fallbacks: list[str] = field(default_factory=list)


@dataclass
class ParsedConfig:
    aliases: dict[str, AliasEntry]            # keyed by alias name
    path: str

    def alias_for_model(self, model_str: str) -> str | None:
        for a in self.aliases.values():
            if a.model == model_str:
                return a.alias
        return None

    def to_snapshot(self) -> list[dict]:
        """Serializable form for storing in llm_proposals.current_snapshot."""
        return [
            {
                "alias":     a.alias,
                "model":     a.model,
                "api_base":  a.api_base,
                "timeout":   a.timeout,
                "fallbacks": a.fallbacks,
            }
            for a in self.aliases.values()
        ]


def parse(path: str = DEFAULT_CONFIG_PATH) -> ParsedConfig:
    """Parse the LiteLLM YAML at `path` into a normalised structure."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    aliases: dict[str, AliasEntry] = {}
    for entry in raw.get("model_list", []):
        alias = entry.get("model_name")
        if not alias:
            continue
        lp = entry.get("litellm_params", {}) or {}
        aliases[alias] = AliasEntry(
            alias=alias,
            model=lp.get("model", ""),
            api_base=lp.get("api_base"),
            timeout=lp.get("timeout"),
            # PyYAML resolves the `<<: *reasoning_flag` merge into the dict, so
            # we can't see the anchor itself; detect via model_info instead.
            reasoning_flag=bool(
                (entry.get("model_info") or {}).get("supports_system_prompts") is False
            ),
        )

    # Fallback chains live under router_settings.fallbacks (list of single-key dicts)
    fb_list = (raw.get("router_settings") or {}).get("fallbacks") or []
    for item in fb_list:
        for src_alias, chain in (item or {}).items():
            if src_alias in aliases:
                aliases[src_alias].fallbacks = list(chain or [])

    return ParsedConfig(aliases=aliases, path=path)


if __name__ == "__main__":
    # Sanity print
    cfg = parse()
    print(f"Parsed {len(cfg.aliases)} aliases from {cfg.path}")
    for a in cfg.aliases.values():
        rf = " [reasoning]" if a.reasoning_flag else ""
        fb = f"  fallbacks→ {','.join(a.fallbacks)}" if a.fallbacks else ""
        print(f"  {a.alias:<24} → {a.model}{rf}{fb}")
