# Embedding Options

[`cocoindex-code`](https://github.com/cocoindex-io/cocoindex-code) supports a variety of embedding models and providers. This guide helps you choose the right path for your hardware, privacy requirements, and codebase size.

<p align="center"><a href="https://github.com/cocoindex-io/cocoindex-code"><img width="2428" alt="cocoindex code" src="https://github.com/user-attachments/assets/d05961b4-0b7b-42ea-834a-59c3c01717ca" /></a></p>

## Table of Contents

- [Which Path Should I Choose?](#which-path-should-i-choose)
- [Understanding Speed, Context, and Performance](#understanding-speed-context-and-performance)
- [The `ccc init` Wizard](#the-ccc-init-wizard)
- **[Local Sentence-Transformers](#sentence-transformers-local)**
- **[LiteLLM Remote (Cloud Providers)](#litellm-remote-cloud-providers)**
- **[LiteLLM Local](#local-litellm-providers)**
- [Choosing Based on Your Content](#choosing-based-on-your-content)
- [Pacing & Rate Limits](#pacing--rate-limits)

---

## Which Path Should I Choose?

| Path | Best For... | Key Advantage | Trade-off |
| :--- | :--- | :--- | :--- |
| **Local Sentence-Transformers** | Most users, laptops, quick setup. | **Fastest** (in-process), private, offline. | Larger initial pip install (`[full]`). |
| **Cloud LiteLLM Remote** | Large codebases, weak local hardware. | Top performance, zero local resource usage. | Per-token costs, data leaves machine. |
| **Local LiteLLM** | Power users, shared GPU resources. | Flexibility, unified model management. | Requires managing a separate server. |

---

## Understanding Speed, Context, and Performance

### Speed & Latency

- **Local Sentence-Transformers**: Typically the **fastest** option for small-to-medium models. Because it runs directly inside the `cocoindex-code` process, it avoids the network latency of Cloud APIs and the communication overhead of Local Servers (Ollama).
- **Local Servers (Ollama)**: Ideal for running **heavy models** (like `mxbai-embed-large`) on a GPU. While it has slight overhead compared to in-process execution, it is much faster than running large models on a CPU.
- **Cloud APIs**: Slower per-request due to network latency, but highly parallel. Best for the initial indexing of massive repositories.

### Does Context Size Matter?

Most local models have a **512-token** context window, while cloud models (OpenAI, Voyage) support **8k to 32k**.

In `cocoindex-code`, this matters less than you might expect due to our **Language-Aware Chunking** strategy:

- **Logical Boundaries**: The tool uses Tree-Sitter to understand code structure. It tries to split files at logical boundaries like functions, classes, or methods.
- **Target Size**: While respecting boundaries, it targets a chunk size of **~1,000 characters** (~300 tokens).
- **Compatibility**: This hybrid approach ensures code snippets are contextually coherent while remaining small enough to fit perfectly within even the smallest 512-token context windows.

### CPU vs. GPU

- The default `xs` model is optimized for **CPUs**; you likely won't see a benefit from a GPU.
- For `medium` or `large` models, a GPU is highly recommended. If you have one, you can tell Sentence-Transformers to use it by adding `device: cuda` (or `mps` for Mac) to your `global_settings.yml`.

---

## The `ccc init` Wizard

The easiest way to configure embeddings is by running `ccc init`. On first run, it will guide you through an interactive wizard. To reconfigure later, delete `~/.cocoindex_code/global_settings.yml` and re-run.

1. **Provider Selection**: Choose between `sentence-transformers` (local, free) or `litellm` (cloud/local server).
2. **Model Selection**: Enter a HuggingFace ID or a LiteLLM model string (e.g., `voyage/voyage-code-3`).
3. **Automatic Tuning**: `ccc init` will automatically apply curated defaults (like `input_type` or `prompt_name`) and test the connection.

---

## Sentence-Transformers (Local)

This option runs embedding models directly on your machine using the library.

### Recommended Models

These are based on MTEB [datasets](https://huggingface.co/datasets/mteb/results) as of 13-Jun-2026.

| Tier | Model | Params | Code Score | Best For |
| :--- | :--- | :--- | :--- | :--- |
| **Micro** | [`Snowflake/arctic-embed-xs`](https://huggingface.co/Snowflake/snowflake-arctic-embed-xs) | 22M | 0.67 | Old CPUs, minimal RAM usage. |
| **Small** | [`ibm-granite/granite-embedding-97m-multilingual-r2`](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2) | 97M | 0.80 | Modern laptops, multilingual code. |
| **Medium** | [`jinaai/jina-embeddings-v5-text-nano`](https://huggingface.co/jinaai/jina-embeddings-v5-text-nano) | 239M | **0.90** | **Performance sweet spot.** BERT-based (Fast). |
| **High** | [`geevec-ai/geevec-embeddings-1.0-lite`](https://huggingface.co/geevec-ai/geevec-embeddings-1.0-lite) | 366M | **0.92** | Maximum local accuracy (needs GPU for speed). |

#### Other Model Options

The default of `Snowflake/arctic-embed-xs` is a good choice in most situations, but if you want other options...

- **Discovery Script**: The easiest way is to run our included script to find the current best models for your hardware: `uv run scripts/find_best_models.py`.
- **MTEB v3 Leaderboard**: For manual discovery, visit the [MTEB v3 Leaderboard](https://huggingface.co/spaces/mteb/leaderboard):
    1. Go to the **Benchmarks** tab
    2. Select **Code Information Retrieval (CoIR)**.
    3. Filter **Model Type** to **Dense**.
    4. Enable the **Sentence-Transformers Compatible** toggle.
    5. Adjust **Model Size** to fit your hardware (e.g., `< 500M` for CPUs).
- **Compatibility**: Look for **Bi-encoders** with a fixed dimension size (e.g., 384, 768, 1024). Avoid "Late Interaction" (ColBERT) or "Cross-Encoders".
- **Architecture**: **Encoder** models (BERT-based) are much faster on CPUs than **Decoder** models (LLM-based).

### Installation & Configuration

Install with the `full` extra: `pip install "cocoindex-code[full]"`.

Example `global_settings.yml`:

```yaml
embedding:
  provider: sentence-transformers
  model: jinaai/jina-embeddings-v5-text-nano
  device: cpu # Use 'cuda' or 'mps' if you have a GPU
```

For more information, see the [Sentence-Transformers Documentation](https://sbert.net/).

[Back to top](#table-of-contents)

---

## LiteLLM Remote (Cloud Providers)

Use external API providers for high-quality embeddings via the LiteLLM bridge.

### Recommendations

Well ranked in the MTEB v3 benchmarks

- **Voyage AI ([`voyage-4-large`](https://docs.voyageai.com/docs/embeddings))**: Current #1 for code (Score: **0.97**).
- **Gemini ([`text-embedding-004`](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/text-embeddings))**: Top-tier performance (Score: **0.97**) with a generous free tier.
- **OpenAI ([`text-embedding-3-small`](https://platform.openai.com/docs/guides/embeddings))**: Reliable and very cost-effective for large codebases.

### Configuration

Example for Voyage AI:

```yaml
embedding:
  provider: litellm
  model: voyage/voyage-4-large
envs:
  VOYAGE_API_KEY: your-api-key-here
```

For more information, see the [LiteLLM Providers Documentation](https://docs.litellm.ai/docs/providers).

[Back to top](#table-of-contents)

---

## Local LiteLLM Providers

Connect to a local embedding server (Ollama, llama.cpp or compatible) for privacy and flexibility.

### Ollama

Ensure Ollama is running and you have pulled the model (`ollama pull jina/jina-embeddings-v5`).

**Suggested Models:**

- **Low-end**: `ollama/all-minilm`
- **Mid-range**: `ollama/jina-embeddings-v5`
- **High-end**: `ollama/mxbai-embed-large`

**Configuration:**

```yaml
embedding:
  provider: litellm
  model: ollama/jina-embeddings-v5
```

See the [Ollama Model Library](https://ollama.com/library?q=embedding&sort=popular) for more options.

---

### llama.cpp

If you prefer running `llama.cpp` directly (e.g., using `llama-server`), you can connect via the OpenAI-compatible interface.

**Configuration:**

1. Start your server: `llama-server --embedding -m your_model.gguf`
2. Configure `global_settings.yml`:

```yaml
embedding:
  provider: litellm
  model: openai/your-model-name
envs:
  OPENAI_API_BASE: http://localhost:8080/v1
  OPENAI_API_KEY: "not-needed"
```

[Back to top](#table-of-contents)

---

## Choosing Based on Your Content

- **Heavy Source Code**: Use **Jina v5 Nano** (Local) or **Voyage 4 Large** (Cloud). Both score >0.90 on code search benchmarks.
- **Large Documentation / Files**: Models with large context windows (8k+ tokens) like **Jina v5** (32k) or **OpenAI v3 Large** (8k).
- **Multilingual Projects**: **Granite 97m** (Small Local) or **Cohere Multilingual v3** (Cloud).

### Fine-Tuning with `indexing_params` and `query_params`

The `ccc init` wizard will automatically apply recommended defaults for known models.

**Example for LiteLLM (Voyage, Gemini):**

```yaml
embedding:
  provider: litellm
  model: voyage/voyage-4-large
  indexing_params:
    input_type: document
  query_params:
    input_type: query
```

**Example for Sentence-Transformers (Jina):**

```yaml
embedding:
  provider: sentence-transformers
  model: jinaai/jina-embeddings-v5-text-nano
  indexing_params:
    prompt_name: retrieval.passage
  query_params:
    prompt_name: retrieval.query
```

---

## Pacing & Rate Limits

When using cloud providers, you often encounter rate limits (number of requests per minute). `cocoindex-code` provides several mechanisms to manage this:

- **`min_interval_ms` (Pacing)**: Introduces a mandatory delay between requests (e.g., `500` for 2 req/sec).
- **Automatic Retries**: The daemon automatically retries rate-limited requests (429 errors) with exponential backoff (up to 6 times).
- **Batching**: `cocoindex-code` automatically batches up to 64 text chunks into a single API request to maximize throughput.

[Back to top](#table-of-contents)
