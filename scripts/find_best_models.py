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
import os
import sys
from pathlib import Path

import pandas as pd
from datasets import load_dataset
from huggingface_hub import HfApi, dataset_info
from tabulate import tabulate

CACHE_DIR = Path(".cocoindex_code/cache")
CACHE_FILE = CACHE_DIR / "mteb_results.parquet"


def get_model_params(model_id, api):
    """Estimate model parameters from metadata or tags."""
    try:
        info = api.model_info(model_id)
        if info.safetensors and info.safetensors.get("total"):
            return info.safetensors["total"] / 1_000_000
        for tag in info.tags:
            if tag.endswith("B") and tag[:-1].replace(".", "").isdigit():
                return float(tag[:-1]) * 1000
            if tag.endswith("M") and tag[:-1].replace(".", "").isdigit():
                return float(tag[:-1])
        return None
    except Exception:
        return None


def main():
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

    results = model_scores.merge(
        code_scores, on="model_name", how="outer", suffixes=("_general", "_code")
    )

    # Only show public models
    public_models = df[df["is_public"]]["model_name"].unique()
    results = results[results["model_name"].isin(public_models)]

    # Hardcoded sizes for common models to speed up analysis
    known_sizes = {
        "Snowflake/snowflake-arctic-embed-xs": 22,
        "Snowflake/snowflake-arctic-embed-s": 33,
        "Snowflake/snowflake-arctic-embed-m": 109,
        "Snowflake/snowflake-arctic-embed-l": 334,
        "mixedbread-ai/mxbai-embed-large-v1": 335,
        "nomic-ai/nomic-embed-text-v1.5": 137,
        "BAAI/bge-small-en-v1.5": 33,
        "BAAI/bge-base-en-v1.5": 109,
        "BAAI/bge-large-en-v1.5": 335,
        "intfloat/multilingual-e5-large": 560,
        "jinaai/jina-embeddings-v5-text-nano": 239,
        "ibm-granite/granite-embedding-97m-multilingual-r2": 97,
        "geevec-ai/geevec-embeddings-1.0-lite": 366,
    }

    def categorize(size):
        if size is None:
            return "Unknown"
        if size < 50:
            return "Micro (< 50M)"
        if size < 150:
            return "Small (< 150M)"
        if size < 500:
            return "Medium (< 500M)"
        return "Large (> 500M)"

    print("Analyzing top candidates to determine hardware tiers...", file=sys.stderr)
    results["max_score"] = results[["score_general", "score_code"]].max(axis=1)
    results = results.sort_values(by="max_score", ascending=False).head(500)

    results["size_mb"] = results["model_name"].map(known_sizes)

    # Fetch metadata for top unknown models
    missing_size = results[results["size_mb"].isna()]["model_name"].tolist()
    size_map = {}
    for i, name in enumerate(missing_size[:100]):
        size_map[name] = get_model_params(name, api)

    results.loc[results["size_mb"].isna(), "size_mb"] = results["model_name"].map(size_map)
    results["tier"] = results["size_mb"].apply(categorize)

    report.append("\n## Top Embedding Models for Code Search\n")

    tiers = ["Micro (< 50M)", "Small (< 150M)", "Medium (< 500M)", "Large (> 500M)"]
    for tier in tiers:
        report.append(f"### Tier: {tier}")
        tier_df = (
            results[results["tier"] == tier].sort_values(by="score_code", ascending=False).head(5)
        )
        if tier_df.empty:
            report.append("_No models found in this tier._\n")
            continue

        display = tier_df[["model_name", "score_code", "score_general", "size_mb"]]
        display.columns = ["Model", "Code Search Score", "General Retrieval Score", "Params (M)"]
        report.append(tabulate(display, headers="keys", tablefmt="github", showindex=False))
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

    final_output = "\n".join(report)

    if args.output:
        with open(args.output, "w") as f:
            f.write(final_output)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(final_output)


if __name__ == "__main__":
    main()
