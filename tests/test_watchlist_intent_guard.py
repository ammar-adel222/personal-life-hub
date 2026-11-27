"""Regression tests ensuring spoken 'I will watch...' statements never get misrouted to Playlists/Watchlist unless explicitly requested."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app import agent, ai, db, tools, watchlist
from app.config import save_settings


def _isolate_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIFEHUB_DISABLE_AUTO_SYNC", "1")
    db._initialized_db_paths.clear()
    db.init_db()


def test_explicit_watchlist_intent_detection():
    # Casual "I will watch ..." and task plans must NOT trigger Watchlist/Playlist routing
    assert watchlist.has_explicit_watchlist_intent("I will watch Inception tonight") is False
    assert (
        watchlist.has_explicit_watchlist_intent(
            "I want to watch the first five lectures of the Stanford AI course"
        )
        is False
    )
    assert (
        watchlist.has_explicit_watchlist_intent(
            "For today's task I should watch lecture 1 of the Stanford Agentic AI course"
        )
        is False
    )
    assert (
        watchlist.has_explicit_watchlist_intent(
            "for the next 10 days I want to add a task to watch the first five videos"
        )
        is False
    )
    assert (
        watchlist.has_explicit_watchlist_intent(
            "Don't put this in the playlist tab, just remind me to watch lecture 2"
        )
        is False
    )
    assert watchlist.has_explicit_watchlist_intent("Note: read https://example.com/blog") is False

    # Explicit Playlist / Watchlist requests and pasted links MUST return True
    assert (
        watchlist.has_explicit_watchlist_intent(
            "Add two playlists: Stanford Transformers Course and Stanford Agentic AI Course"
        )
        is True
    )
    assert watchlist.has_explicit_watchlist_intent("Put this video in my playlist tab") is True
    assert watchlist.has_explicit_watchlist_intent("Save CS229 to my watchlist") is True
    assert (
        watchlist.has_explicit_watchlist_intent(
            "watchlist: https://www.youtube.com/playlist?list=PLxyz Rust Course"
        )
        is True
    )
    assert (
        watchlist.has_explicit_watchlist_intent("https://www.youtube.com/playlist?list=PLabc")
        is True
    )

    # Multi-turn confirmation after the assistant asked about Watchlist/Playlist
    prior = [
        {"role": "assistant", "content": "Want me to add Stanford CS229 to your Watchlist tab?"}
    ]
    assert watchlist.has_explicit_watchlist_intent("Yes, add it", prior_messages=prior) is True
    assert (
        watchlist.has_explicit_watchlist_intent("No, don't put it on the playlist", prior_messages=prior)
        is False
    )


def test_offline_agent_fallback_never_misroutes_watch_plans_to_watchlist(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)

    r1, _, _ = agent._offline_agent_fallback(
        "for the next 10 days I want to watch the first five lectures of the Stanford AI course"
    )
    r2, _, _ = agent._offline_agent_fallback(
        "For today's task I should watch lecture 1 of the Stanford Agentic AI course"
    )
    r3, _, _ = agent._offline_agent_fallback("I will watch a movie tonight")

    assert "watchlist" not in r1.lower()
    assert "watchlist" not in r2.lower()
    assert "watchlist" not in r3.lower()
    assert db.list_watchlist() == []
    assert db.list_notes() == []
    assert len(db.list_tasks()) == 3

    # Explicit playlist command in offline mode still adds to Watchlist
    r4, actions4, _ = agent._offline_agent_fallback("add to my playlist: Stanford CS229")
    assert any(a["domain"] == "watchlist" for a in actions4)
    assert "Stanford CS229" in r4
    items = db.list_watchlist()
    assert len(items) == 1
    assert items[0]["title"] == "Stanford CS229"


def test_online_agent_blocks_unsolicited_watchlist_tool_and_recovers(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    save_settings({"groq_api_key": "gsk_testkey123456", "groq_model": "openai/gpt-oss-120b"})

    # Case 1: Model attempts unsolicited upsert_watchlist_item -> runtime guard blocks -> model recovers with create_task
    round_state = {"calls": 0, "saw_blocked_error": False}

    async def fake_step_unsolicited(messages, api_key, model, use_tools=True):
        round_state["calls"] += 1
        if round_state["calls"] == 1:
            return (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_wl_1",
                            "function": {
                                "name": "upsert_watchlist_item",
                                "arguments": json.dumps(
                                    {"title": "Stanford AI Lecture 1", "content_type": "playlist"}
                                ),
                            },
                        }
                    ],
                },
                model,
            )
        if round_state["calls"] == 2:
            last_tool_payload = json.loads(messages[-1]["content"])
            assert last_tool_payload.get("blocked") is True
            round_state["saw_blocked_error"] = True
            return (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_task_1",
                            "function": {
                                "name": "create_task",
                                "arguments": json.dumps(
                                    {
                                        "title": "Watch Lecture 1 of Stanford AI",
                                        "list_type": "daily",
                                        "due_date": db.today_str(),
                                        "timeline": "Today",
                                    }
                                ),
                            },
                        }
                    ],
                },
                model,
            )
        return (
            {
                "role": "assistant",
                "content": "Added today's task to watch Lecture 1 of Stanford AI.",
                "tool_calls": [],
            },
            model,
        )

    monkeypatch.setattr(agent, "_call_groq_agent_step", fake_step_unsolicited)
    res1 = asyncio.run(agent.run_agent_turn("I will watch lecture 1 of Stanford AI today"))
    assert round_state["saw_blocked_error"] is True
    assert db.list_watchlist() == []
    tasks = db.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Watch Lecture 1 of Stanford AI"
    assert not any(a.get("domain") == "watchlist" for a in res1["actions"])

    # Case 2: Explicit playlist request IS allowed
    wl_calls = {"calls": 0}

    async def fake_step_explicit(messages, api_key, model, use_tools=True):
        wl_calls["calls"] += 1
        if wl_calls["calls"] == 1:
            return (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_wl_ok",
                            "function": {
                                "name": "upsert_watchlist_item",
                                "arguments": json.dumps(
                                    {"title": "Stanford CS229", "content_type": "playlist"}
                                ),
                            },
                        }
                    ],
                },
                model,
            )
        return (
            {"role": "assistant", "content": "Added Stanford CS229 to your Watchlist.", "tool_calls": []},
            model,
        )

    monkeypatch.setattr(agent, "_call_groq_agent_step", fake_step_explicit)
    res2 = asyncio.run(agent.run_agent_turn("Add Stanford CS229 to my playlist tab"))
    assert len(db.list_watchlist()) == 1
    assert db.list_watchlist()[0]["title"] == "Stanford CS229"
    assert any(a.get("domain") == "watchlist" for a in res2["actions"])

    # Case 3: Multi-turn confirmation ("Yes, add it") after assistant asked about Watchlist IS allowed
    sess = db.create_agent_session("Confirm Watchlist")
    sid = int(sess["id"])
    db.add_agent_message(sid, "user", "Check MIT 6.S191 lectures")
    db.add_agent_message(
        sid, "assistant", "Do you want me to add MIT 6.S191 to your Watchlist tab?"
    )
    confirm_calls = {"calls": 0}

    async def fake_step_confirm(messages, api_key, model, use_tools=True):
        confirm_calls["calls"] += 1
        if confirm_calls["calls"] == 1:
            return (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_wl_confirm",
                            "function": {
                                "name": "upsert_watchlist_item",
                                "arguments": json.dumps(
                                    {"title": "MIT 6.S191", "content_type": "course"}
                                ),
                            },
                        }
                    ],
                },
                model,
            )
        return (
            {"role": "assistant", "content": "Added MIT 6.S191 to your Watchlist.", "tool_calls": []},
            model,
        )

    monkeypatch.setattr(agent, "_call_groq_agent_step", fake_step_confirm)
    asyncio.run(agent.run_agent_turn("Yes, add it", session_id=sid))
    titles = {w["title"] for w in db.list_watchlist()}
    assert "MIT 6.S191" in titles


def test_smart_capture_auto_blocks_unsolicited_watchlist_action(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    save_settings({"groq_api_key": "gsk_testkey123456", "groq_model": "openai/gpt-oss-120b"})

    async def fake_chat(messages, api_key, model, json_mode=False, temperature=0.2):
        return (
            json.dumps(
                {
                    "summary": "Saved item",
                    "actions": [{"type": "watchlist", "title": "Watch lecture 1"}],
                }
            ),
            model,
        )

    monkeypatch.setattr(ai, "_call_groq_chat", fake_chat)

    res_auto = asyncio.run(ai.smart_capture("I will watch lecture 1 tonight", mode="auto"))
    assert db.list_watchlist() == []
    tasks = db.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Watch lecture 1"
    assert res_auto["results"][0]["type"] == "task"

    # Explicit watchlist mode still saves directly to Watchlist
    res_explicit = asyncio.run(ai.smart_capture("CS229 video", mode="watchlist"))
    assert len(db.list_watchlist()) == 1
    assert res_explicit["results"][0]["type"] == "watchlist"


def test_tool_schemas_keep_24_tools_and_nullable_optional_params():
    assert len(tools.TOOL_SCHEMAS) == 24
    by_name: dict[str, dict[str, Any]] = {
        entry["function"]["name"]: entry["function"] for entry in tools.TOOL_SCHEMAS
    }

    assert by_name["create_task"]["parameters"]["properties"]["due_date"]["type"] == [
        "string",
        "null",
    ]
    assert (
        by_name["create_tasks_batch"]["parameters"]["properties"]["tasks"]["items"]["properties"][
            "due_date"
        ]["type"]
        == ["string", "null"]
    )
    assert by_name["update_task"]["parameters"]["properties"]["start_date"]["type"] == [
        "string",
        "null",
    ]
    assert by_name["log_study_session"]["parameters"]["properties"]["date"]["type"] == [
        "string",
        "null",
    ]
    # Enum fields keep string type untouched
    assert by_name["create_task"]["parameters"]["properties"]["list_type"]["type"] == "string"

    desc = by_name["upsert_watchlist_item"]["description"].lower()
    assert "explicitly" in desc and "never" in desc
