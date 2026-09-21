from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

Mode = Literal["web", "no_web"]


class Brand(BaseModel):
    id: str
    name: str
    aliases: list[str] = []
    domains: list[str] = []
    url_patterns: list[str] = []  # substrings of marketplace listing URLs, e.g. "nykaa.com/dot-key"
    ambiguous: bool = False       # name is also a common word; dictionary-only hits are ignored
    is_client: bool = False


class Workspace(BaseModel):
    id: str
    category: str
    market: str = "IN"
    brands: list[Brand]

    @model_validator(mode="after")
    def _check_brands(self):
        clients = [b for b in self.brands if b.is_client]
        if len(clients) != 1:
            raise ValueError(f"workspace {self.id} needs exactly one is_client brand, found {len(clients)}")
        ids = [b.id for b in self.brands]
        if len(ids) != len(set(ids)):
            raise ValueError(f"workspace {self.id} has duplicate brand ids")
        return self

    @property
    def client(self) -> Brand:
        return next(b for b in self.brands if b.is_client)


class EngineCfg(BaseModel):
    name: Literal["gemini", "groq", "nova"]
    model: str
    modes: list[Mode]
    min_interval_s: float
    temperature: float = 0.7
    region: str | None = None       # AWS region, nova only
    max_prompts: int | None = None  # only the top-N prompts (priority order), to fit a free-tier daily cap

    @model_validator(mode="after")
    def _check_engine(self):
        if self.name == "groq" and "web" in self.modes:
            raise ValueError("groq has no web search tool; use modes: [no_web]")
        if self.name == "nova" and not self.region:
            raise ValueError("nova needs a region (Bedrock web grounding is US-only)")
        return self


class ExtractorCfg(BaseModel):
    engine: Literal["gemini", "nova"] = "gemini"
    model: str
    version: str
    region: str | None = None  # AWS region, nova only


class SearxngCfg(BaseModel):
    url: str
    engines: list[str]
    probe_top_n: int = 20
    max_queries_per_response: int = 3
    min_interval_s: float = 1.5


class Settings(BaseModel):
    system_prompt: str
    engines: list[EngineCfg]
    extractor: ExtractorCfg
    joiner_version: str
    searxng: SearxngCfg
    eligible_top_n: int = 10
    domain_types: dict[str, list[str]] = {}


def load_settings(path: str | Path = "config/config.yaml") -> Settings:
    return Settings.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_workspace(workspace_id: str, root: str | Path = "config/workspaces") -> Workspace:
    text = (Path(root) / f"{workspace_id}.yaml").read_text(encoding="utf-8")
    return Workspace.model_validate(yaml.safe_load(text))
