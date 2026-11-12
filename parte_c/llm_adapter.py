"""
Adaptador de LLM (geração de TEXTO). Não tem relação com o modelo de embeddings da busca.

Provedores:
  - ollama (PRINCIPAL, padrão): modelo local e gratuito servido pelo Ollama em http://localhost:11434.
      * modelo padrão `qwen2.5:7b` (testado na máquina do projeto: RTX 4050, 16 GB de RAM);
      * saída JSON garantida pelo campo `format` (JSON schema); temperatura 0 (respostas reprodutíveis);
      * janela de contexto fixada em OLLAMA_NUM_CTX (o padrão do Ollama, 4096, cortaria o prompt).
  - anthropic (alternativa OPCIONAL, paga): SDK oficial `anthropic` (instalar à parte); modelo padrão
    `claude-opus-5-5`; credencial ANTHROPIC_API_KEY no .env, nunca logada; JSON por
    `output_config.format`; esforço por LLM_EFFORT; fallback do servidor em caso de recusa
    (LLM_SERVER_FALLBACK=0 desliga).
  - fake (testes): respostas programadas, sem rede.

LLM_PROVIDER=none -> `get_llm()` devolve None e o sistema funciona em modo "retrieval_only"
(mostra evidências, sem gerar texto). Se o Ollama estiver desligado, as chamadas falham com
LLMUnavailable e o serviço também cai para "retrieval_only", com aviso.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from parte_c.config import Settings

DEFAULT_MODELS = {"ollama": "qwen2.5:7b", "anthropic": "claude-opus-5-5"}


class LLMError(RuntimeError):
    """Falha na chamada ao LLM (mensagem já sanitizada, sem chaves)."""


class LLMUnavailable(LLMError):
    """Provedor não configurado, sem credencial ou inacessível."""


class LLMRefusal(LLMError):
    """O modelo recusou responder (stop_reason=refusal)."""


@dataclass
class LLMResult:
    text: str
    data: Optional[dict] = None          # JSON já decodificado quando json_schema foi pedido
    model: str = ""
    provider: str = ""
    ms: float = 0.0
    usage: dict = field(default_factory=dict)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError("o LLM não devolveu JSON")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON inválido do LLM: {e.msg}") from None


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.model = settings.llm_model or DEFAULT_MODELS["anthropic"]
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMUnavailable("pacote 'anthropic' não instalado (provedor opcional: pip install anthropic)") from e
            client = anthropic.Anthropic(timeout=settings.llm_timeout_s, max_retries=2)
        self.client = client

    def complete(self, system: str, user: str, json_schema: Optional[dict] = None,
                 max_tokens: Optional[int] = None) -> LLMResult:
        import anthropic

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        output_config: dict[str, Any] = {}
        if self.settings.llm_effort:
            output_config["effort"] = self.settings.llm_effort
        if json_schema:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        t0 = time.perf_counter()
        try:
            if self.settings.llm_server_fallback:
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise LLMUnavailable("credencial da Anthropic ausente ou inválida (ANTHROPIC_API_KEY)") from None
        except anthropic.PermissionDeniedError:
            raise LLMUnavailable("a chave não tem permissão para este modelo") from None
        except anthropic.NotFoundError:
            raise LLMError(f"modelo não encontrado: {self.model}") from None
        except anthropic.RateLimitError:
            raise LLMError("limite de requisições da Anthropic atingido; tente novamente") from None
        except anthropic.BadRequestError as e:
            raise LLMError(f"requisição recusada pela API: {getattr(e, 'message', 'bad request')[:200]}") from None
        except anthropic.APIConnectionError:
            raise LLMUnavailable("sem conexão com a API da Anthropic") from None
        except anthropic.APIStatusError as e:
            raise LLMError(f"erro da API da Anthropic (HTTP {e.status_code})") from None
        ms = (time.perf_counter() - t0) * 1000
        if resp.stop_reason == "refusal":
            raise LLMRefusal("o modelo recusou responder a esta pergunta")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens" and json_schema:
            raise LLMError("resposta do LLM truncada (aumente LLM_MAX_TOKENS)")
        usage = {"input_tokens": getattr(resp.usage, "input_tokens", None),
                 "output_tokens": getattr(resp.usage, "output_tokens", None)}
        return LLMResult(text=text, data=_parse_json(text) if json_schema else None,
                         model=getattr(resp, "model", self.model), provider=self.provider, ms=ms, usage=usage)


class OllamaLLM:
    provider = "ollama"

    def __init__(self, settings: Settings, http=None):
        self.settings = settings
        self.model = settings.llm_model or DEFAULT_MODELS["ollama"]
        if http is None:
            import httpx
            http = httpx.Client(timeout=settings.llm_timeout_s)
        self.http = http

    def complete(self, system: str, user: str, json_schema: Optional[dict] = None,
                 max_tokens: Optional[int] = None) -> LLMResult:
        # O Ollama usa janela de contexto de 4096 tokens por padrão e CORTA o início do prompt sem avisar
        # (onde ficam as regras e a pergunta). Por isso a janela é fixada explicitamente (OLLAMA_NUM_CTX)
        # e o tamanho do pedido é conferido antes do envio (~3 caracteres por token em português).
        num_predict = min(max_tokens or self.settings.llm_max_tokens, 2048)
        num_ctx = self.settings.ollama_num_ctx
        estimated = (len(system) + len(user)) // 3 + num_predict
        if estimated > num_ctx:
            raise LLMError(f"pedido (~{estimated} tokens) maior que a janela do Ollama ({num_ctx}); "
                           f"aumente OLLAMA_NUM_CTX ou reduza MAX_CONTEXT_CHARS")
        body: dict[str, Any] = {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0, "num_predict": num_predict, "num_ctx": num_ctx},
        }
        if json_schema:
            body["format"] = json_schema
        t0 = time.perf_counter()
        try:
            r = self.http.post(f"{self.settings.ollama_url}/api/chat", json=body)
        except Exception:  # noqa: BLE001
            raise LLMUnavailable(f"Ollama inacessível em {self.settings.ollama_url} (rode `ollama serve`)") from None
        if r.status_code != 200:
            raise LLMError(f"Ollama devolveu HTTP {r.status_code}")
        text = r.json().get("message", {}).get("content", "")
        return LLMResult(text=text, data=_parse_json(text) if json_schema else None, model=self.model,
                         provider=self.provider, ms=(time.perf_counter() - t0) * 1000)


Responder = Union[str, dict, Exception, Callable[[str, str, Optional[dict]], Union[str, dict]]]


class FakeLLM:
    """LLM programável para testes. `responses` é consumida em ordem; cada item pode ser
    str (texto), dict (JSON), Exception (é levantada) ou função (system, user, schema) -> str|dict."""
    provider = "fake"

    def __init__(self, responses: Optional[list[Responder]] = None, model: str = "fake-llm"):
        self.responses = list(responses or [])
        self.model = model
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, json_schema: Optional[dict] = None,
                 max_tokens: Optional[int] = None) -> LLMResult:
        self.calls.append({"system": system, "user": user, "schema": json_schema})
        if not self.responses:
            raise LLMError("FakeLLM sem respostas programadas")
        item = self.responses.pop(0)
        if callable(item) and not isinstance(item, (str, dict)):
            item = item(system, user, json_schema)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return LLMResult(text=json.dumps(item, ensure_ascii=False), data=item, model=self.model,
                             provider=self.provider)
        return LLMResult(text=item, data=_parse_json(item) if json_schema else None, model=self.model,
                         provider=self.provider)


def get_llm(settings: Optional[Settings] = None):
    """Cria o cliente do provedor configurado; None se LLM_PROVIDER=none (modo só-recuperação)."""
    settings = settings or Settings.from_env()
    p = settings.llm_provider
    if p in ("", "none", "off"):
        return None
    if p == "ollama":
        return OllamaLLM(settings)
    if p == "anthropic":
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE")
                or os.path.exists(os.path.expanduser("~/.config/anthropic"))):
            raise LLMUnavailable("LLM_PROVIDER=anthropic, mas ANTHROPIC_API_KEY não está definida no .env")
        return AnthropicLLM(settings)
    raise LLMUnavailable(f"LLM_PROVIDER desconhecido: {p!r} (use ollama, anthropic ou none)")
