# MTEB Model Discovery Report

> **Data Freshness**: MTEB results dataset last updated on `2026-06-23`.

## Top Embedding Models for Code Search

> **Speed note**: CPU speed is estimated from parameter count and architecture. **Encoder** models (BERT/ModernBERT-based) process tokens in parallel and are significantly faster on CPU than **Decoder** models (LLM-based), which process tokens sequentially. A decoder model of the same parameter count can be 3–10× slower on CPU.

### Tier: Micro (< 50M)
| Model                                     |   Code Score |   General Score |   Params (M) | Architecture   | CPU Speed   |
|-------------------------------------------|--------------|-----------------|--------------|----------------|-------------|
| lightonai/LateOn-Code-edge                |     0.816549 |      nan        |       17     | Encoder        | Very Fast   |
| lightonai/LateOn-Code-edge-pretrain       |     0.791693 |      nan        |       16.798 | Encoder        | Very Fast   |
| thenlper/gte-small                        |     0.781565 |        0.479423 |       33     | Encoder        | Very Fast   |
| avsolatorio/GIST-small-Embedding-v0       |     0.772521 |        0.480646 |       33.36  | Encoder        | Very Fast   |
| avsolatorio/NoInstruct-small-Embedding-v0 |     0.770071 |        0.488884 |       33.36  | Encoder        | Very Fast   |
| abhinand/MedEmbed-small-v0.1              |     0.766076 |        0.52863  |       33.36  | Encoder        | Very Fast   |
| BAAI/bge-small-en-v1.5                    |     0.75267  |        0.514409 |       33     | Encoder        | Very Fast   |
| Snowflake/snowflake-arctic-embed-s        |     0.672949 |        0.493245 |       33     | Encoder        | Very Fast   |


### Tier: Small (< 150M)
| Model                                             |   Code Score |   General Score |   Params (M) | Architecture   | CPU Speed   |
|---------------------------------------------------|--------------|-----------------|--------------|----------------|-------------|
| lightonai/LateOn-Code                             |     0.851318 |      nan        |      149     | Encoder        | Fast        |
| lightonai/LateOn-Code-pretrain                    |     0.832574 |      nan        |      149.016 | Encoder        | Fast        |
| ibm-granite/granite-embedding-97m-multilingual-r2 |     0.799971 |        0.446515 |       97     | Encoder        | Fast        |
| avsolatorio/GIST-Embedding-v0                     |     0.78981  |        0.503411 |      109.482 | Encoder        | Fast        |
| thenlper/gte-base                                 |     0.789403 |        0.496155 |      109     | Encoder        | Fast        |
| ibm-granite/granite-embedding-english-r2          |     0.773404 |        0.501664 |      149.014 | Unknown        | Unknown     |
| BAAI/bge-base-en-v1.5                             |     0.767726 |        0.531966 |      109     | Encoder        | Fast        |
| nomic-ai/nomic-embed-text-v1.5                    |     0.716379 |        0.49881  |      137     | Encoder        | Fast        |


### Tier: Medium (< 500M)
| Model                                     |   Code Score |   General Score |   Params (M) | Architecture   | CPU Speed      |
|-------------------------------------------|--------------|-----------------|--------------|----------------|----------------|
| geevec-ai/geevec-embeddings-1.0-lite      |     0.92365  |        0.53474  |      366     | Encoder        | Moderate       |
| jinaai/jina-embeddings-v5-text-nano       |     0.90384  |        0.535934 |      239     | Encoder        | Moderate       |
| microsoft/harrier-oss-v1-270m             |     0.89605  |        0.425505 |      270     | Decoder        | Slow (Decoder) |
| Shuu12121/CodeSearch-ModernBERT-Crow-Plus |     0.892957 |      nan        |      151.668 | Encoder        | Fast           |
| codefuse-ai/F2LLM-v2-330M                 |     0.842182 |        0.475202 |      334     | Decoder        | Slow (Decoder) |
| google/embeddinggemma-300m                |     0.838689 |        0.459    |      302.863 | Unknown        | Unknown        |
| Shuu12121/NightOwl-CodeEmbedding          |     0.831063 |      nan        |      150.779 | Unknown        | Unknown        |
| codefuse-ai/C2LLM-0.5B                    |     0.828636 |      nan        |      497.252 | Unknown        | Unknown        |


### Tier: Large (> 500M)
| Model                            |   Code Score |   General Score |   Params (M) | Architecture   | CPU Speed      |
|----------------------------------|--------------|-----------------|--------------|----------------|----------------|
| microsoft/harrier-oss-v1-27b     |     0.96994  |        0.483455 |     27009.3  | Decoder        | Slow (Decoder) |
| Octen/Octen-Embedding-8B-INT8    |     0.967965 |      nan        |      7567.3  | Decoder        | Slow (Decoder) |
| nvidia/llama-embed-nemotron-8b   |     0.96586  |        0.51917  |      7504.92 | Unknown        | Unknown        |
| Octen/Octen-Embedding-4B-INT8    |     0.96369  |      nan        |      4022.88 | Unknown        | Unknown        |
| bflhc/MoD-Embedding              |     0.96368  |      nan        |      4021.77 | Unknown        | Unknown        |
| Octen/Octen-Embedding-4B         |     0.96236  |      nan        |      4021.77 | Unknown        | Unknown        |
| Octen/Octen-Embedding-8B         |     0.9597   |        0.505307 |      7567.3  | Unknown        | Unknown        |
| Mira190/Euler-Legal-Embedding-V1 |     0.95635  |        0.51144  |      8188.52 | Decoder        | Slow (Decoder) |


---
## Snowflake Arctic Embed Family — Baseline Reference

These encoder-based models are included as baseline references and span the full Snowflake Arctic size range. The `xs` variant is the **default model** in `cocoindex-code`. All variants use an encoder architecture and are fast on CPU. Scores below come from the live MTEB dataset where available.

| Model                                    | Code Score   | General Score   |   Params (M) | Architecture   | CPU Speed   |
|------------------------------------------|--------------|-----------------|--------------|----------------|-------------|
| Snowflake/snowflake-arctic-embed-xs      | 0.6661       | 0.4721          |           22 | Encoder        | Very Fast   |
| Snowflake/snowflake-arctic-embed-s       | 0.6729       | 0.4932          |           33 | Encoder        | Very Fast   |
| Snowflake/snowflake-arctic-embed-m       | 0.7003       | 0.5197          |          109 | Encoder        | Fast        |
| Snowflake/snowflake-arctic-embed-l       | 0.6976       | 0.5314          |          334 | Encoder        | Moderate    |
| Snowflake/snowflake-arctic-embed-2-m     | N/A          | N/A             |          305 | Encoder        | Moderate    |
| Snowflake/snowflake-arctic-embed-2-large | N/A          | N/A             |          568 | Encoder        | Moderate    |


---
## How to Regenerate this Report
This report was generated using the `find_best_models.py` script. To update it with the latest live data from MTEB, run:
```bash
uv run scripts/find_best_models.py --clear-cache --output MTEB-RANKINGS.md
```
