"""Chess Coach: find your biggest mistakes with Stockfish, explain them with an LLM.

Usage:
    python coach.py YOUR_CHESSCOM_USERNAME --games 5 --depth 14 --top 5
"""
import argparse
import io
import os
import shutil
import sys

import chess
import chess.engine
import chess.pgn
import requests

API = "https://api.chess.com/pub/player"
# Chess.com asks API users to identify themselves. Put your own contact here.
HEADERS = {"User-Agent": "chess-coach/0.1 (contact: your-email@example.com)"}
# Any OpenAI-compatible provider works (Gemini, Groq, OpenRouter all have free tiers).
# Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL as environment variables.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")


# ---------- 1. Data: Chess.com public API ----------
def fetch_games(username: str, max_games: int) -> list[dict]:
    """Return the most recent games (newest first) from the monthly archives."""
    r = requests.get(f"{API}/{username}/games/archives", headers=HEADERS, timeout=15)
    r.raise_for_status()
    games: list[dict] = []
    for url in reversed(r.json()["archives"]):
        month = requests.get(url, headers=HEADERS, timeout=15)
        month.raise_for_status()
        for g in reversed(month.json()["games"]):
            if "pgn" in g:
                games.append(g)
            if len(games) >= max_games:
                return games
    return games


# ---------- 2. Analysis: Stockfish via python-chess ----------
def cp(info: dict, color: chess.Color) -> int:
    """Engine score in centipawns from `color`'s point of view."""
    return info["score"].pov(color).score(mate_score=10000)


def find_mistakes(game: dict, username: str, engine, depth: int, min_loss: int):
    pgn = chess.pgn.read_game(io.StringIO(game["pgn"]))
    if pgn is None:
        return []
    is_white = pgn.headers.get("White", "").lower() == username.lower()
    color = chess.WHITE if is_white else chess.BLACK
    limit = chess.engine.Limit(depth=depth)

    board = pgn.board()
    found = []
    for move in pgn.mainline_moves():
        if board.turn != color:
            board.push(move)
            continue

        before = engine.analyse(board, limit)
        best_move = before["pv"][0]
        best_cp = cp(before, color)
        best_mate = before["score"].pov(color).mate()
        fen = board.fen()
        move_no = board.fullmove_number
        played_san = board.san(move)
        best_san = board.san(best_move)
        best_line = board.variation_san(before["pv"][:4])

        board.push(move)
        if board.is_game_over():
            continue
        after = engine.analyse(board, limit)
        played_cp = cp(after, color)
        after_mate = after["score"].pov(color).mate()
        reply_line = board.variation_san(after["pv"][:4])

        loss = best_cp - played_cp
        already_decided = best_cp > 800 and played_cp > 800
        if loss >= min_loss and not already_decided:
            found.append(
                {
                    "fen": fen,
                    "move_no": move_no,
                    "color": "White" if is_white else "Black",
                    "played": played_san,
                    "best": best_san,
                    "best_line": best_line,
                    "reply_line": reply_line,
                    "mate_in": best_mate if best_mate and best_mate > 0 else None,
                    "allowed_mate": -after_mate if after_mate and after_mate < 0 else None,
                    "loss": loss,
                    "eval_before": best_cp,
                    "eval_after": played_cp,
                    "url": game.get("url", ""),
                }
            )
    return found


# ---------- 3. LLM explanation (grounded in engine facts) ----------
SYSTEM = (
    "You are a friendly chess coach talking directly to the player (say 'you'). "
    "The Stockfish facts you receive are the only source of truth. Rules: "
    "(1) Only mention pieces and squares that appear in the facts. "
    "(2) 'Played' and 'Best move' are both moves by the player; the opponent's reply "
    "line shows what happens after the played move. "
    "(3) If a forced mate is listed, say so clearly. "
    "(4) Never invent variations or evaluations. "
    "Write 3-4 sentences, then one sentence starting with 'Tip:' that the player can reuse."
)


def headline(m: dict) -> str:
    """Human-readable size of the mistake (mate scores make raw centipawns look silly)."""
    if m.get("mate_in"):
        return f"missed a forced checkmate in {m['mate_in']}"
    if m.get("allowed_mate"):
        return f"allowed a forced checkmate in {m['allowed_mate']}"
    return f"lost about {m['loss'] / 100:.1f} pawns of advantage"


def fmt_eval(centipawns: int) -> str:
    if abs(centipawns) >= 9000:
        return "a forced mate for the player" if centipawns > 0 else "a forced mate for the opponent"
    return f"{centipawns / 100:+.1f} pawns"


def describe_position(fen: str) -> str:
    board = chess.Board(fen)
    lines = []
    for color, name in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        pieces = sorted(
            p.symbol().upper() + chess.square_name(sq)
            for sq, p in board.piece_map().items()
            if p.color == color
        )
        lines.append(f"{name}: {', '.join(pieces)}")
    return "\n".join(lines)


def check_explanation(m: dict, text: str) -> str | None:
    """Cheap automatic eval. Returns a warning string, or None if checks pass."""
    t = text.lower()
    if m["best"].rstrip("+#").lower() not in t:
        return "the explanation never mentions the engine's best move"
    says_mate = "mate" in t
    if says_mate and not (m.get("mate_in") or m.get("allowed_mate")):
        return "it mentions checkmate but the engine found none"
    if (m.get("mate_in") or m.get("allowed_mate")) and not says_mate:
        return "the engine found a forced mate but the explanation does not say so"
    return None


def explain(m: dict) -> str:
    if not LLM_API_KEY or not LLM_MODEL:
        sys.exit("Set LLM_API_KEY and LLM_MODEL (see README), or run with --no-llm.")

    facts = [
        f"The player is {m['color']}. It is move {m['move_no']}.",
        f"Pieces on the board:\n{describe_position(m['fen'])}",
        f"Move the player played: {m['played']}",
        f"Stockfish best move: {m['best']}  (line: {m['best_line']})",
        f"After the played move, the opponent's best reply line: {m['reply_line']}",
        f"Evaluation for the player before the move: {fmt_eval(m['eval_before'])}; "
        f"after the played move: {fmt_eval(m['eval_after'])}",
    ]
    if m.get("mate_in"):
        facts.append(f"The player had a forced checkmate in {m['mate_in']} with {m['best']}.")
    if m.get("allowed_mate"):
        facts.append(f"The played move allows the opponent a forced checkmate in {m['allowed_mate']}.")
    prompt = "\n".join(facts)
    payload = {
        "model": LLM_MODEL,
        "max_tokens": 1500,  # reasoning models spend tokens "thinking" first
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    if "gpt-oss" in LLM_MODEL:
        payload["reasoning_effort"] = "low"  # faster, cheaper, enough for this task
    resp = requests.post(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        json=payload,
        timeout=60,
    )
    if resp.status_code == 429:
        return "(rate limited by the free tier; wait a minute and rerun)"
    resp.raise_for_status()
    text = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    return text or "(the model returned an empty answer; try again)"


# ---------- 4. CLI ----------
def main() -> None:
    ap = argparse.ArgumentParser(description="LLM chess coach")
    ap.add_argument("username")
    ap.add_argument("--games", type=int, default=5)
    ap.add_argument("--depth", type=int, default=14)
    ap.add_argument("--top", type=int, default=5, help="mistakes to explain")
    ap.add_argument("--min-loss", type=int, default=150, help="centipawns")
    ap.add_argument("--stockfish", default=shutil.which("stockfish"))
    ap.add_argument("--no-llm", action="store_true", help="engine output only")
    args = ap.parse_args()

    if not args.stockfish:
        sys.exit("Stockfish not found. Install it and pass --stockfish /path/to/stockfish")

    print(f"Fetching last {args.games} games for {args.username}...")
    games = fetch_games(args.username, args.games)
    if not games:
        sys.exit("No games found.")

    all_mistakes = []
    with chess.engine.SimpleEngine.popen_uci(args.stockfish) as engine:
        for i, g in enumerate(games, 1):
            print(f"Analyzing game {i}/{len(games)}...")
            all_mistakes += find_mistakes(g, args.username, engine, args.depth, args.min_loss)

    all_mistakes.sort(key=lambda m: m["loss"], reverse=True)
    top = all_mistakes[: args.top]

    print(f"\n# Coach report for {args.username}")
    print(f"Found {len(all_mistakes)} significant mistakes; showing the {len(top)} biggest.\n")
    for n, m in enumerate(top, 1):
        print(f"## {n}. Move {m['move_no']} as {m['color']} (lost ~{m['loss']} cp)")
        print(f"Played **{m['played']}**, engine preferred **{m['best']}**  ({m['best_line']})")
        print(f"Game: {m['url']}")
        if not args.no_llm:
            print(f"\n{explain(m)}")
        print()


if __name__ == "__main__":
    main()
