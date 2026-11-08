"""Streamlit web app for Chess Coach. Run locally: streamlit run app.py"""
import base64
import os
import shutil

import chess
import chess.engine
import chess.svg
import requests
import streamlit as st

# Bridge Streamlit Secrets into environment variables BEFORE importing coach,
# because coach.py reads its LLM settings from the environment at import time.
for _k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    try:
        if _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k]).strip()
    except Exception:
        pass  # no secrets configured (e.g. running locally)

import coach

st.set_page_config(page_title="Chess Coach", page_icon="♟️", layout="centered")

# Keep a public demo cheap and safe: hard limits on work per request.
MAX_GAMES, MAX_DEPTH, MAX_TOP = 5, 12, 5

STOCKFISH = (
    os.environ.get("STOCKFISH_PATH")
    or shutil.which("stockfish")
    or next((p for p in ["/usr/games/stockfish", "/usr/bin/stockfish"] if os.path.exists(p)), None)
)


@st.cache_data(ttl=3600, show_spinner=False)
def analyze(username: str, n_games: int, depth: int):
    games = coach.fetch_games(username, n_games)
    mistakes = []
    with chess.engine.SimpleEngine.popen_uci(STOCKFISH) as engine:
        for g in games:
            mistakes += coach.find_mistakes(g, username, engine, depth, 150)
    mistakes.sort(key=lambda m: m["loss"], reverse=True)
    return len(games), mistakes


@st.cache_data(ttl=86400, show_spinner=False)
def explain_cached(m: dict) -> str:
    return coach.explain(m)


def board_svg(m: dict) -> str:
    board = chess.Board(m["fen"])
    played = board.parse_san(m["played"])
    best = board.parse_san(m["best"])
    arrows = [
        chess.svg.Arrow(played.from_square, played.to_square, color="#cc3333cc"),
        chess.svg.Arrow(best.from_square, best.to_square, color="#2e9e4fcc"),
    ]
    svg = chess.svg.board(
        board,
        arrows=arrows,
        orientation=chess.WHITE if m["color"] == "White" else chess.BLACK,
        size=340,
    )
    b64 = base64.b64encode(svg.encode()).decode()
    return f'<img src="data:image/svg+xml;base64,{b64}" width="340"/>'


st.title("♟️ Chess Coach")
st.write(
    "Enter a Chess.com username. Stockfish finds your biggest mistakes in recent games, "
    "and an LLM explains each one. **Red arrow** = what you played, **green arrow** = the engine's choice."
)

if not STOCKFISH:
    st.error("Stockfish is not installed on this server.")
    st.stop()

username = st.text_input("Chess.com username").strip()
c1, c2, c3 = st.columns(3)
n_games = c1.slider("Recent games", 1, MAX_GAMES, 3)
depth = c2.slider("Engine depth", 8, MAX_DEPTH, 10)
top = c3.slider("Mistakes to show", 1, MAX_TOP, 3)

use_llm = bool(coach.LLM_API_KEY and coach.LLM_MODEL)
if not use_llm:
    missing = [k for k, v in [("LLM_API_KEY", coach.LLM_API_KEY), ("LLM_MODEL", coach.LLM_MODEL)] if not v]
    st.info(f"LLM not configured (missing: {', '.join(missing)}). Showing engine analysis only.")

if st.button("Analyze my games", type="primary") and username:
    try:
        with st.spinner("Fetching games and analyzing with Stockfish (can take a minute)..."):
            total, mistakes = analyze(username, n_games, depth)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            st.error("Username not found on Chess.com.")
        else:
            st.error(f"Chess.com API error: {e}")
        st.stop()
    except requests.RequestException as e:
        st.error(f"Network error: {e}")
        st.stop()

    if total == 0:
        st.warning("No games found for this user.")
        st.stop()

    st.success(f"Analyzed {total} games and found {len(mistakes)} significant mistakes.")
    for n, m in enumerate(mistakes[:top], 1):
        st.subheader(f"{n}. Move {m['move_no']} as {m['color']}: {coach.headline(m)}")
        left, right = st.columns([1, 1])
        left.markdown(board_svg(m), unsafe_allow_html=True)
        right.markdown(
            f"**You played:** {m['played']}  \n**Engine preferred:** {m['best']}  \n"
            f"**Best line:** `{m['best_line']}`  \n**Then the opponent:** `{m['reply_line']}`"
        )
        if m["url"]:
            right.markdown(f"[Open game on Chess.com]({m['url']})")
        if use_llm:
            with st.spinner("Coach is thinking..."):
                text = explain_cached(m)
            st.write(text)
            warning = coach.check_explanation(m, text)
            if warning:
                st.caption(f"⚠️ Auto-check: {warning}. Trust the engine line above over the text.")
            else:
                st.caption("✓ Auto-check passed: explanation matches the engine's findings.")
        st.divider()

st.caption("Built with the Chess.com public API, Stockfish, python-chess and an LLM. Engine facts are verified; the LLM only explains them.")
