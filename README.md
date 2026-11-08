# Chess Coach

Finds the biggest mistakes in your recent Chess.com games with Stockfish, then uses an LLM to explain each one in plain language.

**Pipeline:** Chess.com PubAPI → python-chess (parse PGN) → Stockfish (centipawn loss per move) → LLM explanation grounded in the engine's facts.

## Setup

```bash
pip install python-chess requests
# install Stockfish: https://stockfishchess.org/download/ (or `brew install stockfish`, `apt install stockfish`)
# free LLM (pick one provider, see below)
export LLM_BASE_URL=https://api.groq.com/openai/v1
export LLM_API_KEY=your_free_key
export LLM_MODEL=model_name_from_provider_docs
python coach.py YOUR_USERNAME --games 5 --depth 14 --top 5
```

Use `--no-llm` to see engine output only (no API key needed).

## Free LLM options (OpenAI-compatible)

| Provider | LLM_BASE_URL |
|---|---|
| Groq | https://api.groq.com/openai/v1 |
| Google Gemini (AI Studio) | https://generativelanguage.googleapis.com/v1beta/openai |
| OpenRouter (free models) | https://openrouter.ai/api/v1 |

Free tiers and model names change, so check the provider's docs for a current free model.

## Design notes

- The LLM never judges the position by itself. It only receives verified Stockfish evals and the best line, which reduces hallucinated analysis.
- Positions that were already decided (huge advantage before and after) are skipped to cut noise.
- Uses the official public API, not scraping.

## Ideas to extend

- Simple evals: check that explanations mention the engine's best move
- Group mistakes by theme (missed tactics, endgames, time trouble)
- Web UI (Streamlit) and a live deploy

## Web app

```bash
pip install -r requirements.txt
streamlit run app.py
```

Deploy free on Streamlit Community Cloud: push this folder to GitHub, create an app at share.streamlit.io pointing to `app.py`, and add `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` under Advanced settings > Secrets. `packages.txt` installs Stockfish on the server. Never commit your API key.
