from __future__ import annotations

import time

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from geoagent.config import settings
from geoagent.db.models import ChatMessage, ChatSession
from geoagent.db.session import db_session
from geoagent.services import runs as run_service

HISTORY_TURNS = 8


def get_or_create_session(session_id: str | None, channel: str = "web") -> ChatSession:
    with db_session() as db:
        if session_id:
            s = db.get(ChatSession, session_id)
            if s:
                return s
        s = ChatSession(channel=channel)
        db.add(s)
        db.flush()
        db.refresh(s)
        return s


def history(session_id: str, limit: int = HISTORY_TURNS * 2) -> list[ChatMessage]:
    with db_session() as db:
        rows = db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc()).limit(limit)
        ).scalars().all()
        return list(reversed(rows))


def _add(session_id: str, role: str, content: str, run_id: str | None = None, latency_ms: int | None = None) -> ChatMessage:
    with db_session() as db:
        m = ChatMessage(session_id=session_id, role=role, content=content, run_id=run_id, latency_ms=latency_ms)
        db.add(m)
        s = db.get(ChatSession, session_id)
        if s and not s.title:
            s.title = content[:80]
        db.flush()
        db.refresh(m)
        return m


def _fallback_answer(question: str, aoi: dict | None) -> tuple[str, list[str]]:
    """No LLM key: run the deterministic pipeline on the AOI in context."""
    if not aoi:
        return ("No LLM provider is configured and no AOI was supplied, so I can't run anything. "
                "Add GROQ_API_KEY (or another provider) to .env, or select an AOI on the dashboard "
                "and ask again.", [])
    run = run_service.create_run(aoi, requested_by="chat")
    run = run_service.execute_now(run.id)
    if run.error:
        return f"The monitoring run failed: {run.error}", [run.id]
    return (run.narrative or "Run completed."), [run.id]


def ask(session_id: str, question: str, aoi: dict | None = None,
        provider: str | None = None, model: str | None = None) -> dict:
    _add(session_id, "user", question)
    started = time.perf_counter()

    context = question
    if aoi:
        context += (f"\n\n[Dashboard AOI context] minx={aoi['minx']} miny={aoi['miny']} "
                    f"maxx={aoi['maxx']} maxy={aoi['maxy']} days_back_t1={aoi.get('days_back_t1', 60)} "
                    f"days_back_t2={aoi.get('days_back_t2', 15)} window_days={aoi.get('window_days', 20)} "
                    f"threshold={aoi.get('threshold', settings.DEFAULT_CHANGE_THRESHOLD)} "
                    f"buffer_m={aoi.get('buffer_m', settings.DEFAULT_BUFFER_METERS)}")

    if settings.llm_configured or provider:
        from geoagent.agent.graph import run_agent

        past = [
            HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content)
            for m in history(session_id)[:-1]
        ]
        try:
            answer, run_ids = run_agent(context, history=past, provider=provider, model=model)
        except Exception as exc:  # noqa: BLE001
            answer, run_ids = f"The agent hit an error: {exc}", []
    else:
        answer, run_ids = _fallback_answer(question, aoi)

    latency = int((time.perf_counter() - started) * 1000)
    msg = _add(session_id, "assistant", answer, run_id=run_ids[-1] if run_ids else None, latency_ms=latency)
    return {"session_id": session_id, "message_id": msg.id, "answer": answer,
            "run_ids": run_ids, "latency_ms": latency}
