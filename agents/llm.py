"""
Free-tool LLM abstraction.

LLM_PROVIDER=ollama     -> local model via Ollama, $0 forever, needs `ollama serve`
                           running and the model pulled: `ollama pull qwen3:4b`
LLM_PROVIDER=gemini     -> Google Gemini API free tier, needs GEMINI_API_KEY
LLM_PROVIDER=groq       -> Groq API free tier (fast hosted inference), needs GROQ_API_KEY
LLM_PROVIDER=anthropic  -> Claude models, paid per token, needs ANTHROPIC_API_KEY
LLM_PROVIDER=openai     -> OpenAI models, paid per token, needs OPENAI_API_KEY
LLM_PROVIDER=openrouter -> one key in front of ~400 models with cross-provider
                           failover, needs OPENROUTER_API_KEY. Note its free
                           tier is capped by REQUEST COUNT per day, not tokens
                           -- see config.py's OPENROUTER_MODEL comment.

Every agent calls get_llm() instead of instantiating a provider directly, so
swapping providers is a one-line .env change.
"""
import re

import config


# Models that emit a <think> block before answering, and bill it against the
# same output budget. Matched on the family, not the exact tag, because the
# tag carries a size and a quantisation ("qwen3:4b", "deepseek-r1:8b-q4").
_THINKING_FAMILIES = ("qwen3", "deepseek-r1", "qwq", "magistral")


def _is_thinking_model(model: str) -> bool:
    return any(family in (model or "").lower() for family in _THINKING_FAMILIES)


def estimate_tokens(text: str) -> int:
    """A deliberately pessimistic token count.

    ~3.2 characters per token rather than the usual 4: the prompts here are
    JSON and CV text, which tokenise denser than prose, and the cost of
    guessing low is a silently truncated prompt while the cost of guessing
    high is a little more KV cache on a local model.
    """
    return int(len(text or "") / 3.2) + 1


def context_for(prompt_text: str, max_tokens: int | None) -> int:
    """How much context this call actually needs, within the configured cap.

    Ollama counts prompt AND completion against num_ctx and truncates the
    prompt silently when it does not fit -- no error, no warning, just a model
    that never saw the end of the job description and an empty or nonsense
    answer.
    """
    needed = estimate_tokens(prompt_text) + (max_tokens or 1024) + 256
    return max(config.OLLAMA_NUM_CTX, min(needed, config.OLLAMA_NUM_CTX_MAX))


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)


def visible_content(resp) -> str:
    """The model's ANSWER, with any reasoning removed.

    A thinking model can return its chain of thought in three shapes, and only
    one of them is handled by reading `.content`:

      1. `<think>…</think>` then the answer -- strip the block.
      2. `<think>` with no close, because the output budget ran out mid-thought
         -- everything after the tag is reasoning and there is no answer at
         all, so what comes back is "".
      3. reasoning in `.content` with no tags whatsoever, which is what qwen3:4b
         did on a real rewrite: 13,000 characters of "We are given the
         candidate profile... but wait, the problem says..." and no CV. Nothing
         here can detect that from the text alone, which is why the callers
         that expect JSON must VALIDATE rather than trust -- see
         cv_rewriter_agent._ask_for_document.

    Separate `reasoning_content` / `thinking` fields, where a provider supplies
    them, are ignored on purpose: they are not the answer.
    """
    content = (getattr(resp, "content", "") or "")
    content = _THINK_BLOCK.sub("", content)
    if _THINK_OPEN.search(content):
        content = _THINK_OPEN.split(content, maxsplit=1)[0]
    return content.strip()


def no_think_prefix() -> str:
    """Qwen3's soft switch, for the USER turn.

    Qwen documents `/no_think` as a control token in the conversation, and
    which turn it is honoured in depends on the chat template a build ships.
    Sending it on both turns is harmless -- for a model that does not know the
    token it is a stray word, and for one that does it says the same thing
    twice -- and it is the only lever available when the installed
    langchain-ollama predates the `reasoning` argument.
    """
    if config.LLM_PROVIDER != "ollama" or config.OLLAMA_THINKING:
        return ""
    return "/no_think\n" if _is_thinking_model(config.OLLAMA_MODEL) else ""


def prepare_system(text: str) -> str:
    """The system prompt, with thinking disabled where that needs saying.

    Two mechanisms, because which one works depends on the installed
    langchain-ollama: the `reasoning=False` constructor argument when the
    class accepts it (see get_llm), and Qwen3's own `/no_think` control token
    otherwise. Sending both is harmless -- the token is a no-op for a model
    that does not know it, and for one that does it says the same thing twice.
    """
    if config.LLM_PROVIDER != "ollama" or config.OLLAMA_THINKING:
        return text
    if not _is_thinking_model(config.OLLAMA_MODEL):
        return text
    return f"{text}\n\n/no_think"


def get_llm(temperature: float = 0.0, max_tokens: int | None = None,
            prompt_text: str = ""):
    """`max_tokens` caps the OUTPUT budget for one call.

    It matters more than it looks on metered providers, because they bill the
    reservation, not the usage: Groq's free tier counts input + max_tokens
    against its 8,000 tokens-per-minute limit and rejects the request outright
    with a 413 before the model runs. So a structured-extraction call that
    emits ~1,200 tokens of JSON but reserves the 4,096 default can fail on a
    prompt that would otherwise have fit comfortably. Callers that know their
    output is small should say so.
    """
    if config.LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        if not config.GEMINI_API_KEY:
            raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set in .env")
        return ChatGoogleGenerativeAI(
            model="gemini-1.5-flash",
            google_api_key=config.GEMINI_API_KEY,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

    if config.LLM_PROVIDER == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic needs the langchain-anthropic package. "
                "Run: pip install -r requirements.txt"
            ) from exc
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set in .env")
        # Anthropic requires max_tokens on every request; no hidden reasoning
        # is billed unless extended thinking is turned on, which it isn't.
        return ChatAnthropic(model=config.ANTHROPIC_MODEL, api_key=config.ANTHROPIC_API_KEY,
                             temperature=temperature, max_tokens=max_tokens or 4096)

    if config.LLM_PROVIDER == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "LLM_PROVIDER=openai needs the langchain-openai package. "
                "Run: pip install -r requirements.txt"
            ) from exc
        if not config.OPENAI_API_KEY:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set in .env")
        kwargs = {"model": config.OPENAI_MODEL, "api_key": config.OPENAI_API_KEY,
                  "max_tokens": max_tokens or 4096}
        if _is_openai_reasoning_model(config.OPENAI_MODEL):
            # gpt-5 / o-series reason before answering and bill it as output,
            # like gpt-oss on Groq (see below); "low" keeps the budget for the
            # answer. They also accept only the default temperature.
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["temperature"] = temperature
        return ChatOpenAI(**kwargs)

    if config.LLM_PROVIDER == "openrouter":
        try:
            from langchain_openrouter import ChatOpenRouter
        except ImportError as exc:
            raise RuntimeError(
                "LLM_PROVIDER=openrouter needs the langchain-openrouter package. "
                "Run: pip install -r requirements.txt  (or: pip install langchain-openrouter)."
            ) from exc
        if not config.OPENROUTER_API_KEY:
            raise RuntimeError("LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set in .env")
        kwargs = {
            "model": config.OPENROUTER_MODEL,
            "openrouter_api_key": config.OPENROUTER_API_KEY,
            "temperature": temperature,
            # Same cap and reasoning: same reason as the Groq branch below --
            # a full CV plus a full job description is a long prompt, and an
            # uncapped request can exhaust its budget before emitting content.
            "max_tokens": max_tokens or 4096,
            # OpenRouter attributes traffic to an app for its public rankings;
            # naming this one keeps that honest rather than anonymous.
            "app_title": "Job Application Agent",
        }
        # gpt-oss bills hidden reasoning as completion tokens whichever
        # provider serves it -- see the Groq branch for the measurement.
        if "gpt-oss" in config.OPENROUTER_MODEL:
            kwargs["model_kwargs"] = {"reasoning_effort": "low"}
        return ChatOpenRouter(**kwargs)

    if config.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        if not config.GROQ_API_KEY:
            raise RuntimeError("LLM_PROVIDER=groq but GROQ_API_KEY is not set in .env")
        kwargs = {
            "model": config.GROQ_MODEL,
            "api_key": config.GROQ_API_KEY,
            "temperature": temperature,
            # Without an explicit cap, a long prompt (full CV + job description)
            # can hit Groq's default max_tokens before any actual content is
            # emitted -- see reasoning_effort note below. 4096 leaves plenty of
            # room for a full CV rewrite even after reasoning overhead.
            "max_tokens": max_tokens or 4096,
        }
        # "gpt-oss" models reason internally before producing output, and that
        # reasoning is billed as completion tokens same as the visible content.
        # Verified live: a trivial 2-line rewrite spent ~85% of its completion
        # tokens on hidden reasoning, and a token-capped request came back with
        # content="" + finish_reason="length" (reasoning consumed the whole
        # budget, no error raised). reasoning_effort="low" cuts that overhead
        # so more of the token budget -- and the account's shared daily Groq
        # quota -- goes to the actual CV text instead.
        if "gpt-oss" in config.GROQ_MODEL:
            kwargs["reasoning_effort"] = "low"
        return ChatGroq(**kwargs)

    # default: ollama (local, free)
    chat_class = _chat_ollama_class()
    kwargs = {
        "model": config.OLLAMA_MODEL,
        "base_url": config.OLLAMA_BASE_URL,
        "temperature": temperature,
        # Sized from the actual prompt when the caller passes one. Left at the
        # configured floor otherwise. Unset, Ollama uses the model's own
        # default (commonly 4096) and silently truncates anything longer --
        # no error, just a model that never saw the end of the job description.
        "num_ctx": context_for(prompt_text, max_tokens) if prompt_text
                   else config.OLLAMA_NUM_CTX,
        # Local and unmetered, so a cap only helps if one was asked for --
        # unlike the hosted providers there is no reservation being billed.
        "num_predict": max_tokens,
    }
    # Turn thinking off at the API level where the installed langchain-ollama
    # supports it. Older versions do not have the argument at all, and passing
    # it would be a TypeError, so the signature decides -- the /no_think token
    # in prepare_system() covers the rest.
    if (not config.OLLAMA_THINKING and _is_thinking_model(config.OLLAMA_MODEL)
            and _accepts(chat_class, "reasoning")):
        kwargs["reasoning"] = False
    return chat_class(**kwargs)


def _is_openai_reasoning_model(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4")) and "chat" not in m


def _accepts(cls, name: str) -> bool:
    try:
        import inspect
        return name in inspect.signature(cls).parameters
    except (TypeError, ValueError):  # pragma: no cover -- exotic class objects
        return False


def describe_llm_error(exc: Exception) -> str:
    """Turn a provider exception into something that says what to do next.

    Raw provider errors are a wall of JSON that buries the actionable part. The
    rate-limit ones especially: a 413 from Groq means the request was rejected
    before the model ran, and the fix ("use a local model, or a smaller output
    budget") is nowhere in the message.
    """
    text = str(exc)
    provider = config.LLM_PROVIDER

    if "rate_limit_exceeded" in text or "429" in text or "Error code: 413" in text:
        detail = ""
        match = re.search(r"Limit (\d+), Requested (\d+)", text)
        if match:
            detail = (f" This request needed {match.group(2)} tokens against a "
                      f"{match.group(1)}-token limit.")
        if "tokens per minute" in text or "TPM" in text:
            return (f"{provider} rejected the request: its free tier caps tokens "
                    f"PER MINUTE, counting your prompt plus the reserved output "
                    f"budget.{detail} Either switch LLM provider to Ollama on the "
                    f"Settings page (local, unmetered) or pick a Groq model with a "
                    f"higher limit. Waiting a minute will not help — the request "
                    f"is too large for the window, not merely too frequent.")
        if "tokens per day" in text or "TPD" in text:
            return (f"{provider}'s daily token budget is spent.{detail} Switch LLM "
                    f"provider to Ollama on the Settings page to keep working, or "
                    f"wait for the quota to reset.")
        return f"{provider} rate limit hit.{detail} {text}"

    if "model_not_found" in text or "does not exist" in text or "404" in text:
        return (f"{provider} does not recognise the configured model. Check the "
                f"model id on the Settings page — providers retire models "
                f"regularly. Original error: {text}")

    return text


def _chat_ollama_class():
    """ChatOllama from the dedicated langchain-ollama package.

    NOT a soft fallback to langchain-community: that package has already
    REMOVED ChatOllama (confirmed gone in langchain-community 0.4.2, after
    being deprecated in LangChain 0.3.1). Importing it there doesn't warn any
    more, it raises -- so LLM_PROVIDER=ollama was outright broken on a
    current install until langchain-ollama was added to requirements.txt.
    A bare ImportError traceback wouldn't say that, hence the explicit
    message below.
    """
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:
        raise RuntimeError(
            "LLM_PROVIDER=ollama needs the langchain-ollama package. "
            "Run: pip install -r requirements.txt  (or: pip install langchain-ollama). "
            "The old langchain-community ChatOllama has been removed upstream, "
            "so there is no fallback."
        ) from exc
    return ChatOllama
