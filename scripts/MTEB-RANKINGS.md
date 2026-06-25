# MTEB Model Discovery Report

> **Data Freshness**: MTEB results dataset last updated on `2026-06-15`.

## Top Embedding Models for Code Search

### Tier: Micro (< 50M)

| Model                                     |   Code Search Score |   General Retrieval Score |   Params (M) |
|-------------------------------------------|---------------------|---------------------------|--------------|
| lightonai/LateOn-Code-edge                |            0.816549 |                nan        |       17     |
| lightonai/LateOn-Code-edge-pretrain       |            0.791693 |                nan        |       16.798 |
| thenlper/gte-small                        |            0.781565 |                  0.479423 |       33     |
| avsolatorio/GIST-small-Embedding-v0       |            0.772521 |                  0.480646 |       33.36  |
| avsolatorio/NoInstruct-small-Embedding-v0 |            0.770071 |                  0.488884 |       33.36  |

### Tier: Small (< 150M)

| Model                                             |   Code Search Score |   General Retrieval Score |   Params (M) |
|---------------------------------------------------|---------------------|---------------------------|--------------|
| lightonai/LateOn-Code                             |            0.851318 |                nan        |      149     |
| lightonai/LateOn-Code-pretrain                    |            0.832574 |                nan        |      149.016 |
| ibm-granite/granite-embedding-97m-multilingual-r2 |            0.799971 |                  0.446515 |       97     |
| avsolatorio/GIST-Embedding-v0                     |            0.78981  |                  0.503411 |      109.482 |
| thenlper/gte-base                                 |            0.789403 |                  0.496155 |      109     |

### Tier: Medium (< 500M)

| Model                                     |   Code Search Score |   General Retrieval Score |   Params (M) |
|-------------------------------------------|---------------------|---------------------------|--------------|
| geevec-ai/geevec-embeddings-1.0-lite      |            0.92365  |                  0.53474  |      366     |
| jinaai/jina-embeddings-v5-text-nano       |            0.90384  |                  0.535934 |      239     |
| microsoft/harrier-oss-v1-270m             |            0.89605  |                  0.425505 |      270     |
| Shuu12121/CodeSearch-ModernBERT-Crow-Plus |            0.892957 |                nan        |      151.668 |
| codefuse-ai/F2LLM-v2-330M                 |            0.842182 |                  0.475202 |      334     |

### Tier: Large (> 500M)

| Model                                    |   Code Search Score |   General Retrieval Score |   Params (M) |
|------------------------------------------|---------------------|---------------------------|--------------|
| voyageai/voyage-4-large                  |            0.97726  |                nan        |        nan   |
| voyageai/voyage-4-large (embed_dim=2048) |            0.97719  |                nan        |        nan   |
| google/gemini-embedding-2-preview        |            0.972905 |                nan        |        nan   |
| microsoft/harrier-oss-v1-27b             |            0.96994  |                  0.483455 |      27009.3 |
| Octen/Octen-Embedding-8B-INT8            |            0.967965 |                nan        |       7567.3 |

---

## How to Regenerate this Report

This report was generated using the `find_best_models.py` script. To update it with the latest live data from MTEB, run:

```bash
uv run scripts/find_best_models.py --clear-cache --output MTEB-RANKINGS.md
```
