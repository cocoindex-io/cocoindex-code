# /// script
# dependencies = [
#   "pandas",
#   "datasets",
#   "huggingface_hub",
#   "tabulate",
#   "pyarrow",
# ]
# ///

"""
find_best_models.py

This script queries the live MTEB (Massive Text Embedding Benchmark) results
dataset on Hugging Face to find the highest-performing embedding models.

Usage:
    uv run scripts/find_best_models.py [--output REPORT.md] [--clear-cache]

--- CUSTOMIZATION GUIDE ---
You can fine-tune this script to find models for specific tastes:

1. NON-ENGLISH / MULTILINGUAL:
   - Change 'retrieval_tasks' to include multilingual datasets like:
     ['mMARCO-NL', 'NQ-PL', 'TRECCOVID-VN', 'ArguAna-VN']
   - Or filter the dataframe by the 'language' column:
     df = df[df['language'].apply(lambda x: 'spa-Latn' in x)] # Example for Spanish

2. SPECIFIC PROGRAMMING LANGUAGES:
   - Filter 'df_code' for specific task subsets:
     df_code = df_code[df_code['subset'] == 'python']

3. CLUSTERING OR CLASSIFICATION:
   - Update the 'task_name' filter to include tasks from other categories
     (e.g., 'Banking77' for Classification).
---------------------------
"""

import argparse
import math
import os
import sys
from pathlib import Path

import pandas as pd
from datasets import load_dataset
from huggingface_hub import HfApi, ModelInfo, dataset_info
from tabulate import tabulate

CACHE_DIR = Path(".cocoindex_code/cache")
CACHE_FILE = CACHE_DIR / "mteb_results.parquet"

# Known architectures for common embedding models.
# Encoder (BERT-based) models process tokens in parallel — fast on CPU.
# Decoder (LLM-based) models process tokens sequentially — much slower on CPU,
# typically 3-10× slower than an encoder of the same parameter count.
known_architectures: dict[str, str] = {
    # --- Encoder models (BERT/RoBERTa/ModernBERT-based) ---
    "Snowflake/snowflake-arctic-embed-xs": "Encoder",
    "Snowflake/snowflake-arctic-embed-s": "Encoder",
    "Snowflake/snowflake-arctic-embed-m": "Encoder",
    "Snowflake/snowflake-arctic-embed-l": "Encoder",
    "Snowflake/snowflake-arctic-embed-2-m": "Encoder",
    "Snowflake/snowflake-arctic-embed-2-large": "Encoder",
    "lightonai/LateOn-Code-edge": "Encoder",
    "lightonai/LateOn-Code-edge-pretrain": "Encoder",
    "lightonai/LateOn-Code": "Encoder",
    "lightonai/LateOn-Code-pretrain": "Encoder",
    "ibm-granite/granite-embedding-97m-multilingual-r2": "Encoder",
    "Shuu12121/CodeSearch-ModernBERT-Crow-Plus": "Encoder",
    "jinaai/jina-embeddings-v5-text-nano": "Encoder",
    "geevec-ai/geevec-embeddings-1.0-lite": "Encoder",
    "thenlper/gte-small": "Encoder",
    "thenlper/gte-base": "Encoder",
    "BAAI/bge-small-en-v1.5": "Encoder",
    "BAAI/bge-base-en-v1.5": "Encoder",
    "BAAI/bge-large-en-v1.5": "Encoder",
    "nomic-ai/nomic-embed-text-v1.5": "Encoder",
    "nomic-ai/CodeRankEmbed": "Encoder",
    "mixedbread-ai/mxbai-embed-large-v1": "Encoder",
    "avsolatorio/GIST-small-Embedding-v0": "Encoder",
    "avsolatorio/GIST-Embedding-v0": "Encoder",
    "avsolatorio/NoInstruct-small-Embedding-v0": "Encoder",
    "intfloat/multilingual-e5-large": "Encoder",
    "intfloat/e5-small-v2": "Encoder",
    "intfloat/e5-base-v2": "Encoder",
    "intfloat/e5-large-v2": "Encoder",
    # --- Decoder models (significantly slower on CPU without GPU) ---
    "microsoft/harrier-oss-v1-270m": "Decoder",
    "microsoft/harrier-oss-v1-27b": "Decoder",
    "codefuse-ai/F2LLM-v2-330M": "Decoder",
    "Octen/Octen-Embedding-8B-INT8": "Decoder",
    # --- Cloud APIs (no local inference) ---
    "voyageai/voyage-4-large": "Cloud API",
    "google/gemini-embedding-2-preview": "Cloud API",
}


def _extract_params(info: ModelInfo) -> float | None:
    """Estimate model parameters from HF model info metadata or tags."""
    if info.safetensors and info.safetensors.get("total"):
        return float(info.safetensors["total"]) / 1_000_000
    for tag in info.tags or []:
        if tag.endswith("B") and tag[:-1].replace(".", "").isdigit():
            return float(tag[:-1]) * 1000
        if tag.endswith("M") and tag[:-1].replace(".", "").isdigit():
            return float(tag[:-1])
    return None


def _extract_architecture(info: ModelInfo) -> str:
    """Try to detect encoder vs decoder architecture from HF model info."""
    tags = [t.lower() for t in (info.tags or [])]
    if any(t in tags for t in ["encoder-only", "bert", "roberta", "xlm-roberta"]):
        return "Encoder"
    if any(t in tags for t in ["decoder-only", "causal-lm", "text-generation"]):
        return "Decoder"
    if hasattr(info, "transformers_info") and info.transformers_info:
        auto_model = getattr(info.transformers_info, "auto_model", "") or ""
        if any(x in auto_model for x in ("CausalLM", "GPTNeo", "LlamaFor")):
            return "Decoder"
        if any(x in auto_model for x in ("MaskedLM", "BertModel", "RobertaModel")):
            return "Encoder"
    return "Unknown"


def fetch_model_metadata(model_id: str, api: HfApi) -> tuple[float | None, str]:
    """Fetch a model's param count and architecture with a single HF API call."""
    try:
        info = api.model_info(model_id)
    except Exception:
        return None, "Unknown"
    return _extract_params(info), _extract_architecture(info)


def speed_label(arch: str, size_mb: float | None) -> str:
    """Return a human-readable CPU indexing speed estimate."""
    if arch == "Cloud API":
        return "Cloud"
    if arch == "Decoder":
        return "Slow (Decoder)"
    if arch == "Encoder":
        if size_mb is None or math.isnan(size_mb):
            return "Fast (Encoder)"
        if size_mb < 50:
            return "Very Fast"
        if size_mb < 200:
            return "Fast"
        return "Moderate"
    return "Unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Find best embedding models from MTEB.")
    parser.add_argument("--output", "-o", help="Path to save the Markdown report.")
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Ignore cache and download fresh data.",
    )
    args = parser.parse_args()

    api = HfApi()
    report = []

    report.append("# MTEB Model Discovery Report")

    # Fetch dataset info for freshness (always live check for the date)
    try:
        info = dataset_info("mteb/results")
        last_update = info.lastModified.strftime("%Y-%m-%d")
        report.append(
            f"\n> **Data Freshness**: MTEB results dataset last updated on `{last_update}`."
        )
    except Exception:
        pass

    # Handle caching
    if args.clear_cache and CACHE_FILE.exists():
        os.remove(CACHE_FILE)

    if CACHE_FILE.exists():
        print(f"Loading cached results from {CACHE_FILE}...", file=sys.stderr)
        df = pd.read_parquet(CACHE_FILE)
    else:
        print("Loading live MTEB results from Hugging Face (mteb/results)...", file=sys.stderr)
        try:
            dataset = load_dataset("mteb/results", split="train")
            df = dataset.to_pandas()
            # Cache it
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_parquet(CACHE_FILE)
            print(f"Cached results to {CACHE_FILE}", file=sys.stderr)
        except Exception as e:
            print(f"Error loading dataset: {e}", file=sys.stderr)
            return

    # --- TASK DEFINITIONS ---
    retrieval_tasks = [
        "MSMARCO",
        "NQ",
        "HotpotQA",
        "FiQA2018",
        "ArguAna",
        "Touche2020",
        "SCIDOCS",
        "SciFact",
        "NFCorpus",
        "QuoraRetrieval",
        "DBPedia",
    ]
    code_tasks = [
        "CodeSearchNetRetrieval",
        "CosQA",
        "StackOverflowQA",
        "HumanEvalRetrieval",
        "MBPPRetrieval",
    ]

    # Filter and aggregate
    df_retrieval = df[df["task_name"].isin(retrieval_tasks)]
    df_code = df[df["task_name"].isin(code_tasks)]

    model_scores = df_retrieval.groupby("model_name")["score"].mean().reset_index()
    code_scores = df_code.groupby("model_name")["score"].mean().reset_index()

    # Keep the public-filtered results before the top-1000 head() cut, so the
    # Snowflake family section below can look up scores regardless of overall rank.
    all_results = model_scores.merge(
        code_scores, on="model_name", how="outer", suffixes=("_general", "_code")
    )

    # Only show public models
    public_models = df[df["is_public"]]["model_name"].unique()
    all_results = all_results[all_results["model_name"].isin(public_models)]

    # Hardcoded sizes for common models to speed up analysis
    known_sizes: dict[str, float | None] = {
        "Snowflake/snowflake-arctic-embed-xs": 22,
        "Snowflake/snowflake-arctic-embed-s": 33,
        "Snowflake/snowflake-arctic-embed-m": 109,
        "Snowflake/snowflake-arctic-embed-l": 334,
        "Snowflake/snowflake-arctic-embed-2-m": 305,
        "Snowflake/snowflake-arctic-embed-2-large": 568,
        "mixedbread-ai/mxbai-embed-large-v1": 335,
        "nomic-ai/nomic-embed-text-v1.5": 137,
        "BAAI/bge-small-en-v1.5": 33,
        "BAAI/bge-base-en-v1.5": 109,
        "BAAI/bge-large-en-v1.5": 335,
        "intfloat/multilingual-e5-large": 560,
        "jinaai/jina-embeddings-v5-text-nano": 239,
        "ibm-granite/granite-embedding-97m-multilingual-r2": 97,
        "geevec-ai/geevec-embeddings-1.0-lite": 366,
        "lightonai/LateOn-Code-edge": 17,
        "lightonai/LateOn-Code": 149,
        "microsoft/harrier-oss-v1-270m": 270,
        "Shuu12121/CodeSearch-ModernBERT-Crow-Plus": 151.668,
        "thenlper/gte-small": 33,
        "thenlper/gte-base": 109,
        "codefuse-ai/F2LLM-v2-330M": 334,
    }

    def categorize(size: float | None) -> str:
        if size is None or math.isnan(size):
            return "Unknown"
        if size < 50:
            return "Micro (< 50M)"
        if size < 150:
            return "Small (< 150M)"
        if size < 500:
            return "Medium (< 500M)"
        return "Large (> 500M)"

    print("Analyzing top candidates to determine hardware tiers...", file=sys.stderr)

    # Work on top 1000 for API calls (limits HF API calls for size lookups)
    results = all_results.copy()
    results["max_score"] = results[["score_general", "score_code"]].max(axis=1)
    results = results.sort_values(by="max_score", ascending=False).head(1000)

    results["size_mb"] = results["model_name"].map(known_sizes)
    results["architecture"] = results["model_name"].map(known_architectures)

    # Fetch metadata for top unknown models. One HF API call per model covers
    # both size and architecture, instead of looking each model up twice.
    needs_lookup = results[results["size_mb"].isna() | results["architecture"].isna()][
        "model_name"
    ].tolist()
    metadata_map = {name: fetch_model_metadata(name, api) for name in needs_lookup[:100]}

    results.loc[results["size_mb"].isna(), "size_mb"] = results["model_name"].map(
        {name: size for name, (size, _arch) in metadata_map.items()}
    )
    results.loc[results["architecture"].isna(), "architecture"] = results["model_name"].map(
        {name: arch for name, (_size, arch) in metadata_map.items()}
    )
    results["architecture"] = results["architecture"].fillna("Unknown")
    results["tier"] = results["size_mb"].apply(categorize)
    results["cpu_speed"] = results.apply(
        lambda row: speed_label(row["architecture"], row["size_mb"]), axis=1
    )

    report.append("\n## Top Embedding Models for Code Search\n")
    report.append(
        "> **Speed note**: CPU speed is estimated from parameter count and architecture. "
        "**Encoder** models (BERT/ModernBERT-based) process tokens in parallel and are "
        "significantly faster on CPU than **Decoder** models (LLM-based), which process "
        "tokens sequentially. A decoder model of the same parameter count can be 3–10× "
        "slower on CPU.\n"
    )

    tiers = ["Micro (< 50M)", "Small (< 150M)", "Medium (< 500M)", "Large (> 500M)"]
    for tier in tiers:
        report.append(f"### Tier: {tier}")
        tier_df = (
            results[results["tier"] == tier].sort_values(by="score_code", ascending=False).head(8)
        )
        if tier_df.empty:
            report.append("_No models found in this tier._\n")
            continue

        display = tier_df[
            ["model_name", "score_code", "score_general", "size_mb", "architecture", "cpu_speed"]
        ].copy()
        display.columns = [
            "Model",
            "Code Score",
            "General Score",
            "Params (M)",
            "Architecture",
            "CPU Speed",
        ]
        report.append(tabulate(display, headers="keys", tablefmt="github", showindex=False))
        report.append("\n")

    # --- Snowflake Arctic Family section ---
    report.append("---")
    report.append("## Snowflake Arctic Embed Family — Baseline Reference\n")
    report.append(
        "These encoder-based models are included as baseline references and span the full "
        "Snowflake Arctic size range. The `xs` variant is the **default model** in "
        "`cocoindex-code`. All variants use an encoder architecture and are fast on CPU. "
        "Scores below come from the live MTEB dataset where available.\n"
    )

    snowflake_size_order = [
        ("Snowflake/snowflake-arctic-embed-xs", 22),
        ("Snowflake/snowflake-arctic-embed-s", 33),
        ("Snowflake/snowflake-arctic-embed-m", 109),
        ("Snowflake/snowflake-arctic-embed-l", 334),
        ("Snowflake/snowflake-arctic-embed-2-m", 305),
        ("Snowflake/snowflake-arctic-embed-2-large", 568),
    ]

    snowflake_rows = []
    for model_name, size in snowflake_size_order:
        row = all_results.loc[all_results["model_name"] == model_name]
        code_score = row["score_code"].values[0] if len(row) > 0 else None
        general_score = row["score_general"].values[0] if len(row) > 0 else None
        snowflake_rows.append(
            {
                "Model": model_name,
                "Code Score": f"{code_score:.4f}" if pd.notna(code_score) else "N/A",
                "General Score": f"{general_score:.4f}" if pd.notna(general_score) else "N/A",
                "Params (M)": size,
                "Architecture": "Encoder",
                "CPU Speed": speed_label("Encoder", size),
            }
        )

    snowflake_display = pd.DataFrame(snowflake_rows)
    report.append(tabulate(snowflake_display, headers="keys", tablefmt="github", showindex=False))
    report.append("\n")

    # Add regeneration instructions
    report.append("---")
    report.append("## How to Regenerate this Report")
    report.append(
        "This report was generated using the `find_best_models.py` script. "
        "To update it with the latest live data from MTEB, run:"
    )
    report.append("```bash")
    report.append("uv run scripts/find_best_models.py --clear-cache --output MTEB-RANKINGS.md")
    report.append("```")

    final_output = "\n".join(report) + "\n"

    if args.output:
        with open(args.output, "w") as f:
            f.write(final_output)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(final_output)


if __name__ == "__main__":
    main()
