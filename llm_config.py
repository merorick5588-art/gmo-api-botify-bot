import os

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
MARKET_REASONING_EFFORT = os.getenv("OPENAI_MARKET_REASONING_EFFORT", "medium")
MANAGEMENT_REASONING_EFFORT = os.getenv("OPENAI_MANAGEMENT_REASONING_EFFORT", "medium")
BATCH_ANALYSIS_ENABLED = os.getenv("OPENAI_BATCH_ANALYSIS", "true").strip().lower() not in {
    "0", "false", "no", "off"
}
try:
    BATCH_MAX_SYMBOLS = max(1, int(os.getenv("OPENAI_BATCH_MAX_SYMBOLS", "6")))
except ValueError:
    BATCH_MAX_SYMBOLS = 6


def market_max_output_tokens(symbol_count: int) -> int:
    # max_output_tokens には reasoning token も含まれる。medium reasoning では
    # 1銘柄でも数百tokenを内部推論に使うため、JSON本体を書き切れる余白を確保する。
    return max(1600, 550 * max(1, symbol_count) + 700)


def management_max_output_tokens(symbol_count: int) -> int:
    # Managementはreasoning+Structured Outputの合計が500tokenを超えやすい。
    # 上限を大きくしても課金は実使用tokenのみなので、切断防止を優先する。
    return max(1400, 500 * max(1, symbol_count) + 700)


def log_usage(response, label: str) -> None:
    usage = getattr(response, "usage", None)
    if not usage:
        return
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached = getattr(input_details, "cached_tokens", None) if input_details else None
    reasoning = getattr(output_details, "reasoning_tokens", None) if output_details else None
    parts = [f"input={input_tokens}", f"output={output_tokens}", f"total={total_tokens}"]
    if cached is not None:
        parts.append(f"cached={cached}")
    if reasoning is not None:
        parts.append(f"reasoning={reasoning}")
    print(f"[OpenAI usage:{label}] " + " ".join(parts))
