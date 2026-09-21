"""Chainlit console for GeoAgent Monitor.

A thin client over the FastAPI /api/chat endpoint, so the same sessions, runs
and database are shared with the Next.js dashboard. Run with:

    chainlit run app.py --port 8501
"""
from __future__ import annotations

import os

import chainlit as cl
import httpx

API = os.getenv("GEOAGENT_API_URL", "http://127.0.0.1:8000")
WEB = os.getenv("GEOAGENT_WEB_URL", "http://localhost:3000")
TIMEOUT = httpx.Timeout(600.0, connect=10.0)


async def _presets() -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{API}/api/presets")
        r.raise_for_status()
        return r.json()


def _aoi_from_preset(p: dict) -> dict:
    return {"minx": p["minx"], "miny": p["miny"], "maxx": p["maxx"], "maxy": p["maxy"],
            "days_back_t1": 60, "days_back_t2": 15, "window_days": 20,
            "threshold": 0.35, "buffer_m": 200, "label": p["name"]}


@cl.on_chat_start
async def start():
    try:
        presets = await _presets()
    except Exception as exc:  # noqa: BLE001
        await cl.Message(content=f"Cannot reach the GeoAgent API at {API}: {exc}").send()
        return

    cl.user_session.set("presets", presets)
    cl.user_session.set("aoi", _aoi_from_preset(presets[0]) if presets else None)
    cl.user_session.set("session_id", None)

    actions = [
        cl.Action(name="pick_aoi", payload={"name": p["name"]}, label=p["name"])
        for p in presets
    ]
    await cl.Message(
        content=(
            "Ask me to monitor an area for recent construction or land-cover change. "
            f"Current area: **{presets[0]['name'] if presets else 'none'}** — pick another below, "
            "or give me a bounding box (west, south, east, north) in the message."
        ),
        actions=actions,
    ).send()


@cl.action_callback("pick_aoi")
async def pick_aoi(action: cl.Action):
    presets = cl.user_session.get("presets") or []
    p = next((x for x in presets if x["name"] == action.payload["name"]), None)
    if p:
        cl.user_session.set("aoi", _aoi_from_preset(p))
        await cl.Message(content=f"Area set to **{p['name']}** ({p['minx']}, {p['miny']} → {p['maxx']}, {p['maxy']}).").send()


@cl.on_message
async def on_message(message: cl.Message):
    body = {"message": message.content, "session_id": cl.user_session.get("session_id")}
    aoi = cl.user_session.get("aoi")
    if aoi:
        body["aoi"] = aoi

    async with cl.Step(name="GeoAgent pipeline", type="tool") as step:
        step.input = message.content
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(f"{API}/api/chat", json=body)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as exc:
            step.output = exc.response.text
            await cl.Message(content=f"The API returned an error: {exc.response.text}").send()
            return
        except Exception as exc:  # noqa: BLE001
            step.output = str(exc)
            await cl.Message(content=f"Request failed: {exc}").send()
            return
        step.output = f"{data['latency_ms']} ms · runs {data['run_ids'] or 'none'}"

    cl.user_session.set("session_id", data["session_id"])

    elements = []
    if data["run_ids"]:
        rid = data["run_ids"][-1]
        elements.append(cl.Text(name="Open on map", content=f"{WEB}/runs/{rid}", display="inline"))
        for name in ("t2", "change"):
            elements.append(cl.Image(name=name, url=f"{API}/api/runs/{rid}/preview/{name}.png", display="inline"))
    await cl.Message(content=data["answer"], elements=elements).send()
