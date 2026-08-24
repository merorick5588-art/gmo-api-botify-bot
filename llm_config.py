import os

# GPT-5.6 Luna: FX分析専用。
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
MARKET_REASONING_EFFORT = os.getenv("OPENAI_MARKET_REASONING_EFFORT", "low")
BATCH_ANALYSIS_ENABLED = os.getenv("OPENAI_BATCH_ANALYSIS", "true").strip().lower() not in {
    "0", "false", "no", "off"
}
MARKET_MAX_OUTPUT_TOKENS_PER_SYMBOL = int(
    os.getenv("OPENAI_MARKET_MAX_OUTPUT_TOKENS_PER_SYMBOL", "700")
)


def market_max_output_tokens(symbol_count: int) -> int:
    """バッチ件数に応じて出力上限を確保する。上限値自体は課金トークンではない。"""
    return max(700, MARKET_MAX_OUTPUT_TOKENS_PER_SYMBOL * max(1, symbol_count))


def log_usage(response, label: str) -> None:
    """GitHub Actions上でトークン消費を追えるようにする。"""
    usage = getattr(response, "usage", None)
    if not usage:
        return

    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(details, "cached_tokens", None) if details else None

    parts = [f"input={input_tokens}", f"output={output_tokens}", f"total={total_tokens}"]
    if cached_tokens is not None:
        parts.append(f"cached={cached_tokens}")
    print(f"[OpenAI usage:{label}] " + " ".join(parts))
