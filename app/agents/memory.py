"""Shared agent memory — preferences, feedback, and performance trends."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
MEMORY_FILE = DATA_DIR / "agent_memory.json"

MAX_FEEDBACK = 50
MAX_TRENDS = 12

VALID_AGENTS = ("atlas", "mango", "olive", "hermes")


def _load_all() -> dict:
    if not MEMORY_FILE.exists():
        return {}
    return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))


def _save_all(data: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    MEMORY_FILE.write_text(
        json.dumps(data, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


def _agent_section(data: dict, agent: str) -> dict:
    if agent not in data:
        data[agent] = {
            "feedback": [],
            "preferences": [],
            "blocked_topics": [],
        }
    return data[agent]


def load_memory(agent: str) -> dict:
    """Load memory for a specific agent."""
    data = _load_all()
    return _agent_section(data, agent)


def save_feedback(agent: str, note: str, feedback_type: str = "general") -> None:
    """Append feedback to an agent's log."""
    data = _load_all()
    section = _agent_section(data, agent)

    section["feedback"].append({
        "date": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "type": feedback_type,
    })
    section["feedback"] = section["feedback"][-MAX_FEEDBACK:]

    if feedback_type == "preference":
        add_preference(agent, note, _data=data, _save=False)
    elif feedback_type == "blocked":
        add_blocked_topic(agent, note, _data=data, _save=False)

    _save_all(data)
    logger.info(f"Saved {feedback_type} feedback for {agent}: {note[:50]}")


def add_preference(agent: str, pref: str, _data: dict | None = None, _save: bool = True) -> None:
    """Add a preference for an agent."""
    data = _data or _load_all()
    section = _agent_section(data, agent)
    if pref not in section["preferences"]:
        section["preferences"].append(pref)
    if _save:
        _save_all(data)


def remove_preference(agent: str, pref: str) -> None:
    """Remove a preference."""
    data = _load_all()
    section = _agent_section(data, agent)
    section["preferences"] = [p for p in section["preferences"] if p != pref]
    _save_all(data)


def add_blocked_topic(agent: str, topic: str, _data: dict | None = None, _save: bool = True) -> None:
    """Add a blocked topic for an agent."""
    data = _data or _load_all()
    section = _agent_section(data, agent)
    if topic not in section["blocked_topics"]:
        section["blocked_topics"].append(topic)
    if _save:
        _save_all(data)


def remove_blocked_topic(agent: str, topic: str) -> None:
    """Remove a blocked topic."""
    data = _load_all()
    section = _agent_section(data, agent)
    section["blocked_topics"] = [t for t in section["blocked_topics"] if t != topic]
    _save_all(data)


def save_performance_trend(market: str, gsc_data: dict) -> None:
    """Save weekly performance snapshot for Atlas."""
    data = _load_all()
    section = _agent_section(data, "atlas")

    if "performance_trends" not in section:
        section["performance_trends"] = []

    now = datetime.now(timezone.utc)
    week = now.strftime("%Y-W%V")

    queries = gsc_data.get("queries", [])
    pages = gsc_data.get("pages", [])

    total_clicks = sum(q["clicks"] for q in queries)
    total_impressions = sum(q["impressions"] for q in queries)
    avg_position = (
        sum(q["position"] * q["impressions"] for q in queries) / max(total_impressions, 1)
        if queries else 0
    )

    # Compute delta vs previous entry for same market
    prev = None
    for t in reversed(section["performance_trends"]):
        if t.get("market") == market and t.get("week") != week:
            prev = t
            break

    click_delta = ""
    if prev and prev.get("clicks", 0) > 0:
        pct = ((total_clicks - prev["clicks"]) / prev["clicks"]) * 100
        click_delta = f"{pct:+.0f}%"

    top_queries = [
        {"query": q["query"], "position": q["position"], "clicks": q["clicks"]}
        for q in queries[:5]
    ]

    entry = {
        "week": week,
        "date": now.isoformat(),
        "market": market,
        "clicks": total_clicks,
        "impressions": total_impressions,
        "avg_position": round(avg_position, 1),
        "click_delta": click_delta,
        "top_queries": top_queries,
    }

    # Replace existing entry for same week+market, or append
    section["performance_trends"] = [
        t for t in section["performance_trends"]
        if not (t.get("week") == week and t.get("market") == market)
    ]
    section["performance_trends"].append(entry)
    section["performance_trends"] = section["performance_trends"][-MAX_TRENDS:]

    _save_all(data)


def build_memory_prompt(agent: str) -> str:
    """Build a prompt section from an agent's memory.

    Returns empty string if no memory exists.
    """
    mem = load_memory(agent)

    sections = []

    prefs = mem.get("preferences", [])
    if prefs:
        lines = "\n".join(f"- {p}" for p in prefs)
        sections.append(f"USER PREFERENCES (follow these strictly):\n{lines}")

    blocked = mem.get("blocked_topics", [])
    if blocked:
        lines = "\n".join(f"- {t}" for t in blocked)
        sections.append(f"DO NOT suggest or mention these topics:\n{lines}")

    feedback = mem.get("feedback", [])
    recent = [f for f in feedback if f.get("type") == "general"][-5:]
    if recent:
        lines = "\n".join(f"- [{f['date'][:10]}] \"{f['note']}\"" for f in recent)
        sections.append(f"Recent feedback from the user:\n{lines}")

    if not sections:
        return ""

    return "\n\n" + "\n\n".join(sections) + "\n"


def build_trends_prompt(market: str) -> str:
    """Build week-over-week performance trends for Atlas's prompt."""
    mem = load_memory("atlas")
    trends = mem.get("performance_trends", [])

    market_trends = [t for t in trends if t.get("market") == market][-4:]
    if not market_trends:
        return ""

    lines = []
    for t in market_trends:
        delta = f" ({t['click_delta']})" if t.get("click_delta") else ""
        top = ""
        if t.get("top_queries"):
            top_q = t["top_queries"][0]
            top = f", top query: \"{top_q['query']}\" at #{top_q['position']}"
        lines.append(
            f"- {t['week']}: {t['clicks']} clicks{delta}, "
            f"{t['impressions']} impressions, avg position {t['avg_position']}{top}"
        )

    return (
        f"\nWEEK-OVER-WEEK PERFORMANCE TRENDS ({market}, last {len(market_trends)} weeks):\n"
        + "\n".join(lines)
        + "\nUse these trends to identify what's working and double down.\n"
    )
