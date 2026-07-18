"""
Direct messages: 1:1 chat, the one-unanswered-message anti-spam rule, the
dm_policy privacy setting, and the block/deletion gates.
"""

from fastapi.testclient import TestClient
from main import app


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201
    return response.json()


def send(client: TestClient, username: str, content: str):
    return client.post(f"/api/v1/dm/with/{username}/messages", json={"content": content})


def test_send_and_receive_roundtrip() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    assert send(alice, "bob", "hi bob").status_code == 201

    # Bob sees the conversation with an unread message.
    conversations = bob.get("/api/v1/dm/conversations").json()["items"]
    assert len(conversations) == 1
    assert conversations[0]["other_user"]["username"] == "alice"
    assert conversations[0]["last_message"]["content"] == "hi bob"
    assert conversations[0]["unread_count"] == 1
    assert bob.get("/api/v1/dm/unread-count").json()["count"] == 1

    # Opening the chat and marking read clears the badge.
    chat = bob.get("/api/v1/dm/with/alice").json()
    assert [m["content"] for m in chat["messages"]] == ["hi bob"]
    assert bob.post("/api/v1/dm/with/alice/read").status_code == 204
    assert bob.get("/api/v1/dm/unread-count").json()["count"] == 0

    # Bob replies; both sides now see both messages (newest first).
    assert send(bob, "alice", "hi alice").status_code == 201
    chat = alice.get("/api/v1/dm/with/bob").json()
    assert [m["content"] for m in chat["messages"]] == ["hi alice", "hi bob"]


def test_one_message_until_reply() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    assert send(alice, "bob", "first").status_code == 201

    # Second message before any reply is refused, and the chat reports it.
    second = send(alice, "bob", "second")
    assert second.status_code == 403
    assert "wait for a reply" in second.json()["detail"]
    chat = alice.get("/api/v1/dm/with/bob").json()
    assert chat["can_send"] is False
    assert chat["cannot_send_reason"] == "await_reply"

    # Bob's reply opens the conversation: alice can now send freely.
    assert send(bob, "alice", "ok").status_code == 201
    assert send(alice, "bob", "second").status_code == 201
    assert send(alice, "bob", "third").status_code == 201
    chat = alice.get("/api/v1/dm/with/bob").json()
    assert chat["can_send"] is True


def test_recipient_first_message_is_not_capped_by_own_streak() -> None:
    # Bob never sent anything: his first message to alice is fine even though
    # alice already used her opener.
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    assert send(alice, "bob", "opener").status_code == 201
    assert send(bob, "alice", "reply").status_code == 201


def test_dm_policy_gates_new_conversations() -> None:
    alice = TestClient(app)
    alice_id = register(alice, "alice")["id"]
    bob = TestClient(app)
    register(bob, "bob")
    carol = TestClient(app)
    carol_id = register(carol, "carol")["id"]

    # none: nobody may start a chat.
    assert bob.patch("/api/v1/users/me", json={"dm_policy": "none"}).status_code == 200
    refused = send(alice, "bob", "hello?")
    assert refused.status_code == 403
    assert "don't accept" in refused.json()["detail"]
    chat = alice.get("/api/v1/dm/with/bob").json()
    assert chat["can_send"] is False
    assert chat["cannot_send_reason"] == "policy"

    # following: only people bob follows may message him.
    assert (
        bob.patch("/api/v1/users/me", json={"dm_policy": "following"}).status_code
        == 200
    )
    assert send(alice, "bob", "hello?").status_code == 403
    assert bob.post(f"/api/v1/follows/{alice_id}").status_code == 200
    assert send(alice, "bob", "hello!").status_code == 201

    # An established conversation survives a policy change: carol chats with
    # bob while policy allows, bob replies, then bob shuts DMs off -- carol
    # can still send.
    assert bob.post(f"/api/v1/follows/{carol_id}").status_code == 200
    assert send(carol, "bob", "hey bob").status_code == 201
    assert send(bob, "carol", "hey carol").status_code == 201
    assert bob.patch("/api/v1/users/me", json={"dm_policy": "none"}).status_code == 200
    assert send(carol, "bob", "still works").status_code == 201


def test_dm_hidden_across_blocks_and_self_and_unknown() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    bob_id = register(bob, "bob")["id"]

    assert send(alice, "bob", "hi").status_code == 201
    assert send(bob, "alice", "hi back").status_code == 201

    assert alice.get("/api/v1/dm/with/ghost").status_code == 404
    assert alice.get("/api/v1/dm/with/alice").status_code == 400
    assert send(alice, "alice", "me myself").status_code == 400

    # A block hides the chat and the conversation row in both directions.
    alice.post(f"/api/v1/blocks/{bob_id}")
    assert alice.get("/api/v1/dm/with/bob").status_code == 404
    assert send(alice, "bob", "hello?").status_code == 404
    assert send(bob, "alice", "hello?").status_code == 404
    assert alice.get("/api/v1/dm/conversations").json()["items"] == []
    assert bob.get("/api/v1/dm/conversations").json()["items"] == []
    assert bob.get("/api/v1/dm/unread-count").json()["count"] == 0

    # Unblocking restores the history.
    alice.delete(f"/api/v1/blocks/{bob_id}")
    assert len(alice.get("/api/v1/dm/with/bob").json()["messages"]) == 2


def test_message_pagination_and_validation() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    assert send(alice, "bob", "opener").status_code == 201
    assert send(bob, "alice", "reply").status_code == 201
    for index in range(4):
        assert send(alice, "bob", f"msg {index}").status_code == 201

    page = alice.get("/api/v1/dm/with/bob", params={"limit": 4}).json()
    assert len(page["messages"]) == 4
    assert page["next_cursor"]
    page2 = alice.get(
        "/api/v1/dm/with/bob",
        params={"limit": 4, "before_id": page["next_cursor"]},
    ).json()
    assert [m["content"] for m in page2["messages"]] == ["reply", "opener"]
    assert page2["next_cursor"] is None

    assert send(alice, "bob", "   ").status_code == 422
    assert send(alice, "bob", "x" * 1001).status_code == 422


def test_dm_policy_round_trips_on_own_profile_only() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    assert alice.get("/api/v1/users/alice/profile").json()["dm_policy"] == "everyone"
    alice.patch("/api/v1/users/me", json={"dm_policy": "following"})
    assert alice.get("/api/v1/users/alice/profile").json()["dm_policy"] == "following"
    # Another user's view never exposes the setting.
    assert bob.get("/api/v1/users/alice/profile").json()["dm_policy"] is None
    assert (
        alice.patch("/api/v1/users/me", json={"dm_policy": "sometimes"}).status_code
        == 422
    )
