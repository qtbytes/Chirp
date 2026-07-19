"""
The moderation queue -- the consumer side of reporting.

Access is the operator-granted ``is_moderator`` flag; anyone else gets 404 so
the surface stays undisclosed. Moderators judge the *post*: dismissing or
taking it down closes every open report about it together. A takedown hides
the post from every read path but keeps the row, so it is reversible and a
taken-down tweet's thread stays reachable through a tombstone.
"""

from conftest import TestingSessionLocal
from fastapi.testclient import TestClient
from main import app


def register(username: str) -> tuple[TestClient, int]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return client, response.json()["id"]


def make_moderator(user_id: int) -> None:
    """The operator path (deploy/set_moderator.py), minus the CLI."""
    from app.models.user import User

    with TestingSessionLocal() as db:
        db.get(User, user_id).is_moderator = True
        db.commit()


def register_moderator(username: str = "mod") -> tuple[TestClient, int]:
    client, user_id = register(username)
    make_moderator(user_id)
    return client, user_id


def _post(client: TestClient, content: str) -> int:
    return client.post("/api/v1/tweets", json={"content": content}).json()["id"]


def _report(client: TestClient, post_id: int, reason: str = "spam") -> None:
    response = client.post(
        f"/api/v1/reports/posts/{post_id}", json={"reason": reason}
    )
    assert response.status_code == 201, response.text


# --- access ----------------------------------------------------------------


def test_non_moderator_gets_404_everywhere() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "hello")

    assert alice.get("/api/v1/moderation/reports").status_code == 404
    assert alice.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss").status_code == 404
    assert alice.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").status_code == 404
    assert alice.post(f"/api/v1/moderation/posts/{tweet_id}/restore").status_code == 404


def test_own_profile_shows_the_flag_but_nobody_elses_does() -> None:
    mod, _ = register_moderator("modesty")
    alice, _ = register("alice")

    assert mod.get("/api/v1/users/modesty/profile").json()["is_moderator"] is True
    # The roster is not browsable: another viewer sees False regardless.
    assert alice.get("/api/v1/users/modesty/profile").json()["is_moderator"] is False


# --- the queue -------------------------------------------------------------


def test_queue_groups_reports_by_post() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    carol, _ = register("carol")
    mod, _ = register_moderator()

    tweet_id = _post(carol, "widely reported")
    _report(alice, tweet_id, "spam")
    _report(bob, tweet_id, "hate")

    page = mod.get("/api/v1/moderation/reports").json()
    assert len(page["items"]) == 1, "two reports on one post are one queue item"
    item = page["items"][0]
    assert item["post"]["id"] == tweet_id
    assert item["report_count"] == 2
    assert {r["reason"] for r in item["reports"]} == {"spam", "hate"}
    assert {r["reporter"]["username"] for r in item["reports"]} == {"alice", "bob"}


def test_queue_ignores_the_moderators_own_blocks() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "reported across a block")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/blocks/{bob_id}")

    page = mod.get("/api/v1/moderation/reports").json()
    assert [item["post"]["id"] for item in page["items"]] == [tweet_id]


def test_queue_paginates_by_latest_report() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_ids = [_post(bob, f"post {n}") for n in range(3)]
    for tweet_id in tweet_ids:
        _report(alice, tweet_id)

    first = mod.get("/api/v1/moderation/reports", params={"limit": 2}).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    second = mod.get(
        "/api/v1/moderation/reports",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    seen = [item["post"]["id"] for item in first["items"] + second["items"]]
    assert sorted(seen) == sorted(tweet_ids)
    assert len(seen) == len(set(seen)), "pages must not overlap"


# --- dismiss ---------------------------------------------------------------


def test_dismiss_closes_the_reports_and_changes_nothing_else() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "innocent")
    _report(alice, tweet_id)

    body = mod.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss").json()
    assert body["resolved_reports"] == 1
    assert body["taken_down"] is False

    assert mod.get("/api/v1/moderation/reports").json()["items"] == []
    # The post is untouched for everyone.
    assert alice.get(f"/api/v1/tweets/{tweet_id}").json()["taken_down"] is False

    resolved = mod.get(
        "/api/v1/moderation/reports", params={"status": "resolved"}
    ).json()
    assert [item["post"]["id"] for item in resolved["items"]] == [tweet_id]
    assert resolved["items"][0]["reports"][0]["status"] == "dismissed"


# --- takedown --------------------------------------------------------------


def test_takedown_hides_the_tweet_from_feeds_and_tombstones_its_detail() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")
    mod, _ = register_moderator()

    alice.post(f"/api/v1/follows/{bob_id}")
    tweet_id = _post(bob, "the offending tweet")
    keeper_id = _post(bob, "a fine tweet")
    _report(alice, tweet_id)

    body = mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").json()
    assert body["taken_down"] is True
    assert body["resolved_reports"] == 1

    # Gone from the home timeline; the author's other post survives.
    feed = alice.get(
        "/api/v1/timeline/home", params={"strategy": "read", "limit": 20}
    ).json()
    feed_ids = [item["id"] for item in feed["items"]]
    assert tweet_id not in feed_ids
    assert keeper_id in feed_ids

    # Gone from the author's profile feed too.
    profile = alice.get("/api/v1/users/bob/tweets").json()
    assert tweet_id not in [item["id"] for item in profile["items"]]

    # The detail endpoint answers with a tombstone, not the content.
    detail = alice.get(f"/api/v1/tweets/{tweet_id}").json()
    assert detail["taken_down"] is True
    assert detail["content"] == ""

    # No further interaction with a removed post.
    assert alice.post(f"/api/v1/tweets/{tweet_id}/likes").status_code == 404
    assert (
        alice.post(
            f"/api/v1/tweets/{tweet_id}/comments", json={"content": "hi"}
        ).status_code
        == 404
    )
    assert (
        alice.post(
            "/api/v1/tweets", json={"content": "q", "quoted_post_id": tweet_id}
        ).status_code
        == 404
    )
    assert (
        alice.post(
            f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"}
        ).status_code
        == 404
    )


def test_takedown_of_a_comment_drops_it_from_the_thread_with_its_subtree() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(alice, "root")
    bad = bob.post(
        f"/api/v1/tweets/{tweet_id}/comments", json={"content": "abusive"}
    ).json()
    child = alice.post(
        f"/api/v1/comments/{bad['id']}/comments", json={"content": "reply to it"}
    ).json()
    _report(alice, bad["id"], "abuse")

    mod.post(f"/api/v1/moderation/posts/{bad['id']}/takedown")

    comments = alice.get(f"/api/v1/tweets/{tweet_id}/comments").json()
    listed = [comment["id"] for comment in comments]
    assert bad["id"] not in listed
    assert child["id"] not in listed, "the subtree goes with its parent"
    assert alice.get(f"/api/v1/comments/{bad['id']}").status_code == 404


def test_quote_embeds_of_a_taken_down_post_render_as_no_embed() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    quoted_id = _post(bob, "soon to be removed")
    quote = alice.post(
        "/api/v1/tweets", json={"content": "look", "quoted_post_id": quoted_id}
    ).json()
    _report(alice, quoted_id)

    mod.post(f"/api/v1/moderation/posts/{quoted_id}/takedown")

    detail = alice.get(f"/api/v1/tweets/{quote['id']}").json()
    assert detail["quoted_post"] is None, "a removed quote renders like a deleted one"


def test_takedown_is_idempotent_and_restore_reverses_it() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "borderline")
    _report(alice, tweet_id)

    assert (
        mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").json()[
            "resolved_reports"
        ]
        == 1
    )
    # A second takedown resolves nothing further and stays 200.
    assert (
        mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").json()[
            "resolved_reports"
        ]
        == 0
    )

    body = mod.post(f"/api/v1/moderation/posts/{tweet_id}/restore").json()
    assert body["taken_down"] is False

    detail = alice.get(f"/api/v1/tweets/{tweet_id}").json()
    assert detail["taken_down"] is False
    assert detail["content"] == "borderline"
    # The judgement stays recorded: reports do not reopen on restore.
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []


def test_re_reporting_after_a_judgement_reopens_the_queue_item() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "still at it")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss")
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []

    # The reporter insists: the amended report reopens, not stays buried.
    _report(alice, tweet_id, "abuse")
    reopened = mod.get("/api/v1/moderation/reports").json()
    assert [item["post"]["id"] for item in reopened["items"]] == [tweet_id]
    assert reopened["items"][0]["reports"][0]["reason"] == "abuse"


def test_unknown_post_is_404_for_actions() -> None:
    mod, _ = register_moderator()
    assert mod.post("/api/v1/moderation/posts/999999/takedown").status_code == 404


# --- author deletes vs the moderation record ---------------------------------


def _report_rows_for_post(post_id: int) -> list:
    from app.models.report import Report
    from sqlalchemy import select

    with TestingSessionLocal() as db:
        return list(db.scalars(select(Report).where(Report.post_id == post_id)))


def test_author_cannot_delete_a_taken_down_post() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "the evidence")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown")

    # The row is the moderation record; its author may not destroy it.
    response = bob.delete(f"/api/v1/tweets/{tweet_id}")
    assert response.status_code == 403
    assert "moderation" in response.json()["detail"]

    # The record survives and the judgement stays reversible.
    assert mod.post(f"/api/v1/moderation/posts/{tweet_id}/restore").status_code == 200
    assert bob.get(f"/api/v1/tweets/{tweet_id}").json()["content"] == "the evidence"
    # Once restored, the author's delete right returns.
    assert bob.delete(f"/api/v1/tweets/{tweet_id}").status_code == 204


def test_author_cannot_delete_a_taken_down_comment() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(alice, "root")
    bad = bob.post(
        f"/api/v1/tweets/{tweet_id}/comments", json={"content": "abusive"}
    ).json()
    _report(alice, bad["id"], "abuse")
    mod.post(f"/api/v1/moderation/posts/{bad['id']}/takedown")

    assert bob.delete(f"/api/v1/comments/{bad['id']}").status_code == 403


def test_deleting_a_reported_post_discards_its_reports() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "reported, then thought better of it")
    _report(alice, tweet_id)

    assert bob.delete(f"/api/v1/tweets/{tweet_id}").status_code == 204

    # The author removed the content themselves -- the complaint is answered.
    # No orphaned rows linger, and the queue never shows a target-less item.
    assert _report_rows_for_post(tweet_id) == []
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []


def test_deleting_a_thread_discards_reports_on_its_replies() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "root")
    reply = alice.post(
        f"/api/v1/tweets/{tweet_id}/comments", json={"content": "reported reply"}
    ).json()
    _report(bob, reply["id"], "abuse")

    # Deleting the root sweeps the subtree; the reply's reports go with it.
    assert bob.delete(f"/api/v1/tweets/{tweet_id}").status_code == 204
    assert _report_rows_for_post(reply["id"]) == []
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []


def test_queue_pagination_survives_legacy_orphaned_reports() -> None:
    """
    Rows from before delete_post cascaded reports point at missing posts.
    They must be excluded in SQL: if they merely got skipped after the fetch
    they would consume the LIMIT window and end pagination early, stranding
    real queue items beyond it.
    """
    from app.models.post import Post
    from sqlalchemy import delete as sa_delete

    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_ids = [_post(bob, f"real {n}") for n in range(2)]
    for tweet_id in tweet_ids:
        _report(alice, tweet_id)
    # Reported last, so the orphaned group is the *newest* -- the position
    # where, merely skipped after the fetch, it would consume the whole
    # LIMIT-window of the first page and strand the older real item.
    orphan_id = _post(bob, "will become an orphaned report")
    _report(alice, orphan_id)

    # Simulate the legacy hard delete: remove the post, keep its report.
    with TestingSessionLocal() as db:
        db.execute(sa_delete(Post).where(Post.id == orphan_id))
        db.commit()

    seen: list[int] = []
    cursor = None
    for _ in range(4):
        params: dict = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = mod.get("/api/v1/moderation/reports", params=params).json()
        seen.extend(item["post"]["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert sorted(seen) == sorted(tweet_ids), (
        "every real queue item must stay reachable past the orphan"
    )


# --- media files under takedown and deletion ---------------------------------
#
# /uploads is a static mount with no per-request gating: hiding a post's row
# does nothing to its files. Takedown must quarantine them (reversibly), and a
# hard delete must unlink them -- except files another surviving post shares.


def _upload_png(client: TestClient) -> str:
    import io

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360000002000100057b8e9b0000000049454e44ae426082"
    )
    response = client.post(
        "/api/v1/media", files={"file": ("pic.png", io.BytesIO(png), "image/png")}
    )
    assert response.status_code == 201, response.text
    return response.json()["url"]


def test_takedown_quarantines_media_and_restore_serves_it_again() -> None:
    from pathlib import Path

    from app.core.config import settings

    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    url = _upload_png(bob)
    tweet_id = bob.post(
        "/api/v1/tweets", json={"content": "bad picture", "media_urls": [url]}
    ).json()["id"]
    _report(alice, tweet_id, "sensitive")
    assert alice.get(url).status_code == 200

    mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown")
    # The URL stops resolving for everyone...
    assert alice.get(url).status_code == 404
    # ...but the file survives as evidence, outside the static mount.
    uploads = Path(settings.uploads_dir)
    quarantine = uploads.with_name(uploads.name + "_quarantine") / "media"
    assert (quarantine / Path(url).name).exists()

    mod.post(f"/api/v1/moderation/posts/{tweet_id}/restore")
    assert alice.get(url).status_code == 200


def test_deleting_a_post_unlinks_its_media_files() -> None:
    from pathlib import Path

    from app.core.config import settings

    bob, _ = register("bob")
    url = _upload_png(bob)
    tweet_id = bob.post(
        "/api/v1/tweets", json={"content": "gone soon", "media_urls": [url]}
    ).json()["id"]

    assert bob.delete(f"/api/v1/tweets/{tweet_id}").status_code == 204
    assert bob.get(url).status_code == 404
    assert not (Path(settings.uploads_dir) / "media" / Path(url).name).exists()


def test_shared_media_survives_deleting_one_referencing_post() -> None:
    bob, _ = register("bob")
    url = _upload_png(bob)
    first = bob.post(
        "/api/v1/tweets", json={"content": "one", "media_urls": [url]}
    ).json()["id"]
    bob.post("/api/v1/tweets", json={"content": "two", "media_urls": [url]})

    bob.delete(f"/api/v1/tweets/{first}")
    # The other post still references the file; it must keep resolving.
    assert bob.get(url).status_code == 200


# --- judgement notifications ------------------------------------------------


def _notification_types(client: TestClient) -> list[str]:
    page = client.get("/api/v1/notifications").json()
    return [item["type"] for item in page["items"]]


def test_takedown_notifies_the_author_and_each_open_reporter() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    carol, _ = register("carol")
    mod, _ = register_moderator()

    tweet_id = _post(carol, "reported by two people")
    _report(alice, tweet_id)
    _report(bob, tweet_id, "abuse")

    mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown")

    assert "post_removed" in _notification_types(carol)
    assert "report_actioned" in _notification_types(alice)
    assert "report_actioned" in _notification_types(bob)

    # The notice must not name the moderator: the actor is the recipient.
    page = alice.get("/api/v1/notifications").json()
    actioned = next(
        item for item in page["items"] if item["type"] == "report_actioned"
    )
    assert actioned["actor"]["username"] == "alice"
    # And the removed content must not leak through the preview.
    assert actioned["preview"] is None


def test_dismiss_notifies_nobody() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "harmless")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss")

    assert "report_actioned" not in _notification_types(alice)
    assert "post_removed" not in _notification_types(bob)


def test_takedown_restore_takedown_does_not_re_notify() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "borderline again")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown")
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/restore")
    _report(alice, tweet_id)  # reopens
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown")

    assert _notification_types(bob).count("post_removed") == 1
    assert _notification_types(alice).count("report_actioned") == 1


# --- suspension -------------------------------------------------------------


def test_suspend_freezes_the_account_and_unsuspend_restores_it() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    mod, _ = register_moderator()

    alice.post(f"/api/v1/follows/{bob_id}")
    tweet_id = _post(bob, "soon suspended")

    assert mod.post(f"/api/v1/moderation/users/{bob_id}/suspend").json() == {
        "user_id": bob_id,
        "suspended": True,
        "resolved_reports": 0,
    }

    # Their session is revoked and login is refused with the reason.
    assert bob.get("/api/v1/auth/me").status_code == 401
    login = bob.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "password123"},
    )
    assert login.status_code == 403
    assert "suspended" in login.json()["detail"]

    # Their content leaves the timeline and search; discovery skips them.
    feed = alice.get(
        "/api/v1/timeline/home", params={"strategy": "read", "limit": 20}
    ).json()
    assert tweet_id not in [item["id"] for item in feed["items"]]
    discovery = alice.get("/api/v1/users", params={"q": "bob"}).json()
    assert all(user["username"] != "bob" for user in discovery)

    # The profile still answers, flagged, so the state is explicable.
    profile = alice.get("/api/v1/users/bob/profile").json()
    assert profile["is_suspended"] is True

    mod.post(f"/api/v1/moderation/users/{bob_id}/unsuspend")

    assert (
        bob.post(
            "/api/v1/auth/login",
            json={"username": "bob", "password": "password123"},
        ).status_code
        == 200
    )
    # A different limit dodges the cached first page (the key includes it):
    # suspension accepts the same TTL-bounded cache staleness as a takedown.
    feed = alice.get(
        "/api/v1/timeline/home", params={"strategy": "read", "limit": 19}
    ).json()
    assert tweet_id in [item["id"] for item in feed["items"]], (
        "unsuspending must bring the content back"
    )


# --- user reports in the queue ----------------------------------------------


def _report_user(client: TestClient, user_id: int, reason: str = "abuse") -> None:
    response = client.post(
        f"/api/v1/reports/users/{user_id}", json={"reason": reason}
    )
    assert response.status_code == 201, response.text


def test_queue_mixes_post_and_user_items() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    carol, carol_id = register("carol")
    mod, _ = register_moderator()

    tweet_id = _post(carol, "reported post")
    _report(alice, tweet_id, "spam")
    _report_user(alice, carol_id, "abuse")
    _report_user(bob, carol_id, "spam")

    page = mod.get("/api/v1/moderation/reports").json()
    assert len(page["items"]) == 2, "the post and the account are separate items"

    user_item = next(item for item in page["items"] if item["reported_user"])
    post_item = next(item for item in page["items"] if item["post"])
    assert user_item["post"] is None
    assert post_item["reported_user"] is None
    assert user_item["reported_user"]["username"] == "carol"
    assert user_item["report_count"] == 2
    assert {r["reason"] for r in user_item["reports"]} == {"abuse", "spam"}
    assert post_item["post"]["id"] == tweet_id


def test_queue_paginates_across_mixed_targets() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    carol, carol_id = register("carol")
    mod, _ = register_moderator()

    tweet_ids = [_post(bob, f"post {n}") for n in range(2)]
    for tweet_id in tweet_ids:
        _report(alice, tweet_id)
    _report_user(alice, bob_id)
    _report_user(alice, carol_id)

    seen: list[tuple] = []
    cursor = None
    for _ in range(4):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = mod.get("/api/v1/moderation/reports", params=params).json()
        for item in page["items"]:
            if item["post"]:
                seen.append(("post", item["post"]["id"]))
            else:
                seen.append(("user", item["reported_user"]["id"]))
        cursor = page["next_cursor"]
        if cursor is None:
            break

    expected = {("post", tweet_ids[0]), ("post", tweet_ids[1]), ("user", bob_id), ("user", carol_id)}
    assert set(seen) == expected
    assert len(seen) == len(set(seen)), "pages must not overlap"


def test_dismiss_user_reports_closes_them_and_changes_nothing_else() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    mod, _ = register_moderator()

    _report_user(alice, bob_id)

    body = mod.post(f"/api/v1/moderation/users/{bob_id}/dismiss").json()
    assert body["resolved_reports"] == 1
    assert body["suspended"] is False

    assert mod.get("/api/v1/moderation/reports").json()["items"] == []
    # The account is untouched.
    assert bob.get("/api/v1/auth/me").status_code == 200

    resolved = mod.get(
        "/api/v1/moderation/reports", params={"status": "resolved"}
    ).json()
    item = resolved["items"][0]
    assert item["reported_user"]["id"] == bob_id
    assert item["reports"][0]["status"] == "dismissed"


def test_suspend_resolves_open_user_reports_as_actioned() -> None:
    alice, _ = register("alice")
    _, bob_id = register("bob")
    mod, _ = register_moderator()

    _report_user(alice, bob_id)

    body = mod.post(f"/api/v1/moderation/users/{bob_id}/suspend").json()
    assert body["suspended"] is True
    assert body["resolved_reports"] == 1

    assert mod.get("/api/v1/moderation/reports").json()["items"] == []
    resolved = mod.get(
        "/api/v1/moderation/reports", params={"status": "resolved"}
    ).json()
    item = resolved["items"][0]
    assert item["reported_user"]["is_suspended"] is True
    assert item["reports"][0]["status"] == "actioned"

    # A repeat suspension has nothing left to resolve.
    assert (
        mod.post(f"/api/v1/moderation/users/{bob_id}/suspend").json()[
            "resolved_reports"
        ]
        == 0
    )

    # Deliberately no notification: the dedupe key has no user-target column,
    # and the suspension is already public on the profile.
    assert "report_actioned" not in _notification_types(alice)


def test_unsuspend_leaves_user_reports_resolved_until_re_reported() -> None:
    alice, _ = register("alice")
    _, bob_id = register("bob")
    mod, _ = register_moderator()

    _report_user(alice, bob_id)
    mod.post(f"/api/v1/moderation/users/{bob_id}/suspend")
    mod.post(f"/api/v1/moderation/users/{bob_id}/unsuspend")

    # The judgement stands; lifting it reopens nothing.
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []

    # The reporter insists: the amended report re-enters the queue.
    _report_user(alice, bob_id, "spam")
    reopened = mod.get("/api/v1/moderation/reports").json()
    assert [item["reported_user"]["id"] for item in reopened["items"]] == [bob_id]


def test_user_report_moderation_is_a_moderator_only_surface() -> None:
    alice, _ = register("alice")
    _, bob_id = register("bob")
    assert alice.post(f"/api/v1/moderation/users/{bob_id}/dismiss").status_code == 404


def test_a_moderator_cannot_be_suspended() -> None:
    mod, _ = register_moderator("modone")
    _, other_mod_id = register_moderator("modtwo")

    response = mod.post(f"/api/v1/moderation/users/{other_mod_id}/suspend")
    assert response.status_code == 400


def test_suspend_is_a_moderator_only_surface() -> None:
    alice, _ = register("alice")
    _, bob_id = register("bob")
    assert alice.post(f"/api/v1/moderation/users/{bob_id}/suspend").status_code == 404
    assert alice.post(f"/api/v1/moderation/users/{bob_id}/unsuspend").status_code == 404
