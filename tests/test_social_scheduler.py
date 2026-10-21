"""Agendador de redes sociais (Fase 5) e relógios fora dos planos (Fase 2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from veratus_agents import social_scheduler as social
from veratus_agents.instagram_comments import CommentReplyQueue, load_keyword_replies
from veratus_agents.operations import ApprovalStatus, CommandEngine, approval_status

ROOT = Path(__file__).resolve().parents[1]
REAL_CALENDAR = ROOT / "social" / "calendario-2026-10.json"
START = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
JEWELRY = ["noir-clover", "lumiere", "halo-verde", "rosa-aurea"]
WATCHES = ["ocean-blue", "black-gmt", "arctic-white", "royal-blue", "polar-blue"]


def synthetic_calendar() -> list[dict]:
    """Mesma composição do pacote: 12 feed, 19 stories, 7 vídeos, 6 bloqueados."""
    items = []
    when = START

    def add(**fields):
        nonlocal when
        when += timedelta(hours=7)
        items.append(
            {
                "id": f"out-{len(items) + 1:02d}",
                "data_hora": when.astimezone(social.SAO_PAULO).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "canais": ["instagram", "facebook"],
                "legenda": "Tom dourado para o dia a dia. Comente QUERO.",
                "texto_alternativo": "Colar em tom dourado sobre fundo escuro.",
                "palavras_chave": ["QUERO"],
                **fields,
            }
        )

    for index in range(12):
        fmt = "carrossel" if index % 4 == 0 else "feed"
        media = (
            [f"feed-{index}-a.jpg", f"feed-{index}-b.jpg"]
            if fmt == "carrossel"
            else [f"feed-{index}.jpg"]
        )
        add(formato=fmt, midias=media, produtos=[JEWELRY[index % 4]])
    for index in range(19):
        add(formato="story", midias=[f"story-{index}.jpg"], canais=["instagram"])
    for index in range(7):
        add(formato="reel", midias=[], canais=["instagram", "tiktok"])
    for index, watch in enumerate(WATCHES):
        add(
            formato="feed",
            midias=[f"relogio-{index}.jpg"],
            produtos=[watch],
            status="BLOCKED",
            motivo_bloqueio="esperando foto real",
        )
    add(
        formato="feed",
        midias=["iphone.jpg"],
        produtos=[],
        palavras_chave=["IPHONE"],
        legenda="Em breve.",
        status="BLOCKED",
    )
    return items


class _AllApproved(dict):
    """Manifesto de mídia que aprova tudo (o teste do manifesto usa o real)."""

    def get(self, key, default=None):
        return "aprovado"


@pytest.fixture(autouse=True)
def approved_media(monkeypatch):
    monkeypatch.setattr(social, "load_approved_media", lambda path=None: _AllApproved())


@pytest.fixture
def store(tmp_path):
    return social.SocialScheduleStore(tmp_path / "social.sqlite3")


@pytest.fixture
def calendar(tmp_path):
    path = tmp_path / "calendario.json"
    path.write_text(json.dumps({"itens": synthetic_calendar()}), encoding="utf-8")
    return path


@pytest.fixture
def engine():
    return CommandEngine()


def approve_all(store, engine):
    for request in social.request_weekly_approvals(store, engine):
        engine.approvals.resolve(request["approval_id"], ApprovalStatus.APPROVED)


class FakePublisher:
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def publish(self, item):
        self.calls.append(item["id"])
        if len(self.calls) <= self.fail_times:
            raise TimeoutError("meta")
        return {
            "instagram": {
                "id": f"ig-{item['id']}",
                "permalink": f"https://instagram.com/p/{item['id']}",
            }
        }


def ok_media(url):
    return 200, "image/jpeg"


# -- importação ---------------------------------------------------------------------


def test_import_creates_44_items_watches_wait_for_the_photo(store, calendar):
    result = social.import_calendar(store, calendar)

    assert result["items"] == 44
    assert result["by_status"] == {
        "PENDING_APPROVAL": 31,
        "NEEDS_VIDEO": 7,
        "NEEDS_INFO": 5,
        "BLOCKED": 1,
    }
    # Relógio sobe: espera só a foto real; o iPhone fica para a Fase 7.
    waiting = store.items(status="NEEDS_INFO")
    assert {tuple(item["products"]) for item in waiting} == {(w,) for w in WATCHES}
    assert all(item["block_reason"] == "NEEDS_REAL_PHOTO" for item in waiting)
    assert all(item["template_media"] for item in waiting)
    blocked = store.items(status="BLOCKED")
    assert "LINHA_IPHONE_DEPOIS_DO_TESTE" in blocked[0]["block_reason"]
    assert store.get("out-01")["format"] == "CAROUSEL"
    assert store.get("out-01")["media"][0].startswith(
        "https://veratus.onrender.com/social/posts/2026-10/"
    )
    again = social.import_calendar(store, calendar)
    # Itens pendentes, bloqueados e vídeos são reavaliados a cada importação.
    assert (again["unchanged"], again["updated"]) == (31, 13)


@pytest.mark.skipif(not REAL_CALENDAR.exists(), reason="pacote do fundador pendente")
def test_founder_calendar_imports_44_items(store):
    result = social.import_calendar(store, REAL_CALENDAR)

    assert result["items"] == 44
    by_status = result["by_status"]
    # 5 relógios esperando foto real e 1 iPhone, como no pacote.
    assert by_status.get("NEEDS_INFO", 0) + by_status.get("BLOCKED", 0) == 6


def test_watch_without_real_photo_waits_even_if_the_calendar_forgets(store, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "relogio-esquecido",
                    "data": "2026-10-20 10:00",
                    "formato": "feed",
                    "midias": ["ocean.jpg"],
                    "legenda": "Novo.",
                    "produtos": ["ocean-blue"],
                }
            ]
        ),
        encoding="utf-8",
    )
    social.import_calendar(store, path)

    assert store.get("relogio-esquecido")["status"] == "NEEDS_INFO"
    assert store.get("relogio-esquecido")["block_reason"] == "NEEDS_REAL_PHOTO"
    assert store.get("relogio-esquecido")["template_media"] is False


def test_quarantined_media_never_enters_the_plan(store, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "quarentena",
                    "data": "2026-10-20 10:00",
                    "formato": "feed",
                    "midias": ["landing/assets/campaign/onyx-box.webp"],
                    "legenda": "x",
                }
            ]
        ),
        encoding="utf-8",
    )
    social.import_calendar(store, path)

    assert "MEDIA_IN_QUARANTINE" in store.get("quarentena")["block_reason"]


def test_missing_calendar_reports_the_pending_package(store, tmp_path):
    with pytest.raises(social.CalendarError, match="pacote do fundador"):
        social.import_calendar(store, tmp_path / "nao-existe.json")


# -- aprovação semanal ------------------------------------------------------------


def test_one_approval_per_week_with_content_hashes(store, calendar, engine):
    social.import_calendar(store, calendar)
    requests = social.request_weekly_approvals(store, engine)

    weeks = {item["week"] for item in store.items(status="PENDING_APPROVAL")}
    assert {request["week"] for request in requests} == weeks
    payload = engine.approvals.requests[requests[0]["approval_id"]].payload
    assert all(len(item["content_hash"]) == 64 for item in payload["items"])
    assert social.request_weekly_approvals(store, engine) == []


def test_edit_after_approval_goes_back_to_pending(store, calendar, engine):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    social.sync_approvals(store, lambda rid: approval_status(engine, rid))
    assert store.get("out-02")["status"] == "APPROVED"

    social.edit_item(store, "out-02", actor="fundador", caption="Legenda nova.")

    assert store.get("out-02")["status"] == "PENDING_APPROVAL"
    assert store.get("out-02")["approval_id"] is None


# -- publicação ---------------------------------------------------------------------


def test_run_due_with_flag_off_only_previews(store, calendar, engine):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    publisher = FakePublisher()

    result = social.run_due(
        store,
        publisher=publisher,
        approval_status=lambda rid: approval_status(engine, rid),
        enabled=False,
        now=START + timedelta(days=60),
    )

    assert result["status"] == "PREVIEW"
    assert result["external_write"] is False
    assert publisher.calls == []
    ids = {item["id"] for item in result["would_publish"]}
    assert len(ids) == 31
    blocked = {
        item["id"]
        for status in ("BLOCKED", "NEEDS_INFO")
        for item in store.items(status=status)
    }
    assert not ids & blocked


def test_run_due_publishes_once_and_reads_back(store, calendar, engine):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    publisher = FakePublisher()
    now = START + timedelta(hours=8)
    kwargs = {
        "publisher": publisher,
        "approval_status": lambda rid: approval_status(engine, rid),
        "enabled": True,
        "media_check": ok_media,
        "now": now,
    }

    first = social.run_due(store, **kwargs)
    second = social.run_due(store, **kwargs)

    assert first["published"] == ["out-01"]
    assert second["published"] == []
    assert publisher.calls == ["out-01"]
    item = store.get("out-01")
    assert item["status"] == "PUBLISHED"
    assert item["permalink"] == "https://instagram.com/p/out-01"


def test_failures_retry_three_times_then_alert(store, calendar, engine):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    publisher = FakePublisher(fail_times=99)
    alerts = []
    now = START + timedelta(hours=8)
    for _ in range(3):
        social.run_due(
            store,
            publisher=publisher,
            approval_status=lambda rid: approval_status(engine, rid),
            enabled=True,
            media_check=ok_media,
            alert=alerts.append,
            now=now,
        )
        now += timedelta(hours=2)

    assert store.get("out-01")["status"] == "FAILED"
    assert store.get("out-01")["attempts"] == 3
    assert alerts and alerts[0]["item_id"] == "out-01"


def test_media_that_is_not_a_public_jpeg_is_not_published(store, calendar, engine):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    publisher = FakePublisher()

    social.run_due(
        store,
        publisher=publisher,
        approval_status=lambda rid: approval_status(engine, rid),
        enabled=True,
        media_check=lambda url: (404, "text/html"),
        now=START + timedelta(hours=8),
    )

    assert publisher.calls == []
    assert store.get("out-01")["status"] == "BLOCKED"


class FakeGraph:
    def __init__(self):
        self.posts = []

    def post(self, url, data=None, timeout=None, **kwargs):
        self.posts.append((url, dict(data or {})))
        return _Response(
            {"id": f"id{len(self.posts)}", "post_id": f"post{len(self.posts)}"}
        )

    def get(self, url, params=None, timeout=None, **kwargs):
        if "permalink_url" in (params or {}).get("fields", ""):
            return _Response({"permalink_url": "https://facebook.com/p/1"})
        if "status_code" in (params or {}).get("fields", ""):
            return _Response({"status_code": "FINISHED"})
        return _Response({"id": "m", "permalink": "https://instagram.com/p/m"})


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def test_meta_publisher_handles_carousel_story_page_and_tiktok():
    graph = FakeGraph()
    publisher = social.MetaSocialPublisher(
        graph, access_token="t", ig_user_id="IG", page_id="PAGE"
    )
    carousel = {
        "id": "c",
        "format": "CAROUSEL",
        "caption": "Legenda",
        "alt_text": "",
        "media": ["https://x/a.jpg", "https://x/b.jpg"],
        "channels": ["facebook", "instagram", "tiktok"],
    }
    result = publisher.publish(carousel)

    children = [data for url, data in graph.posts if data.get("is_carousel_item")]
    parent = [data for url, data in graph.posts if data.get("media_type") == "CAROUSEL"]
    assert len(children) == 2 and parent[0]["children"] == "id1,id2"
    assert result["instagram"]["permalink"] == "https://instagram.com/p/m"
    assert result["facebook"]["permalink"] == "https://facebook.com/p/1"
    assert result["tiktok"]["status"] == "NEEDS_MANUAL"

    story = publisher.publish(
        {**carousel, "format": "STORY", "media": ["https://x/s.jpg"]}
    )
    assert any(data.get("media_type") == "STORIES" for _, data in graph.posts)
    assert story["facebook"]["status"] == "NEEDS_MANUAL"

    # Reel: MP4 com capa no Instagram e vídeo na Página.
    reel = publisher.publish(
        {
            **carousel,
            "format": "REEL",
            "media": ["https://x/r.mp4", "https://x/capa.jpg"],
            "channels": ["facebook", "instagram"],
        }
    )
    reels = [data for _, data in graph.posts if data.get("media_type") == "REELS"]
    assert reels[0]["video_url"] == "https://x/r.mp4"
    assert reels[0]["cover_url"] == "https://x/capa.jpg"
    assert any(url.endswith("/PAGE/videos") for url, _ in graph.posts)
    assert reel["instagram"]["id"] and reel["facebook"]["id"]


# -- relógios: foto real libera a arte nova -----------------------------------------


def test_real_photo_replaces_the_template_and_goes_to_approval(
    store, calendar, tmp_path, monkeypatch
):
    social.import_calendar(store, calendar)
    photos = tmp_path / "watches"
    photos.mkdir()
    monkeypatch.setattr(social, "WATCH_PHOTO_DIR", photos)
    catalog = social._catalog()
    catalog["ocean-blue"] = {**catalog["ocean-blue"], "image_status": "REAL_PHOTO"}
    catalog["black-gmt"] = {**catalog["black-gmt"], "image_status": "REAL_PHOTO"}
    (photos / "ocean-blue.jpg").write_bytes(b"jpeg")  # black-gmt sem o JPEG
    ocean = next(
        i for i in store.items(status="NEEDS_INFO") if i["products"] == ["ocean-blue"]
    )
    black = next(
        i for i in store.items(status="NEEDS_INFO") if i["products"] == ["black-gmt"]
    )

    result = social.refresh_pending(store, catalog)

    assert ocean["id"] in result["ready"]
    ready = store.get(ocean["id"])
    assert ready["status"] == "PENDING_APPROVAL"
    assert ready["media"] == [
        "https://veratus.onrender.com/social/posts/watches/ocean-blue.jpg"
    ]
    assert ready["template_media"] is False
    assert ready["content_hash"] != ocean["content_hash"]
    assert black["id"] in result["still_pending"]
    assert store.get(black["id"])["block_reason"] == "WATCH_JPEG_MISSING:black-gmt"
    others = {i["id"] for i in store.items(status="NEEDS_INFO")} - {black["id"]}
    assert len(others) == 3  # os outros três relógios seguem esperando a foto


def test_watch_item_with_founder_art_keeps_its_media(store, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "arte-pronta",
                    "data": "2026-10-20 10:00",
                    "formato": "feed",
                    "midias": ["ocean-arte-final.jpg"],
                    "legenda": "Ocean Blue.",
                    "produtos": ["ocean-blue"],
                }
            ]
        ),
        encoding="utf-8",
    )
    catalog = social._catalog()
    catalog["ocean-blue"] = {**catalog["ocean-blue"], "image_status": "REAL_PHOTO"}

    social.import_calendar(store, path, catalog=catalog)

    item = store.get("arte-pronta")
    assert item["status"] == "PENDING_APPROVAL"
    assert item["media"][0].endswith("/social/posts/2026-10/ocean-arte-final.jpg")


# -- palavras-chave por post ------------------------------------------------------


def comment(media_id, text, comment_id):
    return {
        "object": "instagram",
        "entry": [
            {
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "text": text,
                            "media": {"id": media_id},
                            "from": {"id": "fan"},
                        },
                    }
                ]
            }
        ],
    }


def test_post_keywords_answer_only_on_their_post(store, calendar, engine, tmp_path):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    social.run_due(
        store,
        publisher=FakePublisher(),
        approval_status=lambda rid: approval_status(engine, rid),
        enabled=True,
        media_check=ok_media,
        now=START + timedelta(hours=8),
    )
    rules = social.media_reply_rules(store, load_keyword_replies())
    queue = CommentReplyQueue(tmp_path / "ig.sqlite3")

    on_post = queue.enqueue(comment("ig-out-01", "QUERO!", "c1"), [], media_rules=rules)
    elsewhere = queue.enqueue(comment("outro", "quero", "c2"), [], media_rules=rules)

    assert set(rules) == {"ig-out-01"}
    assert on_post["queued"] == 1
    assert elsewhere["ignored"] == 1


def test_keyword_replies_pass_the_gate():
    from veratus_agents.policy import hard_review_customer_reply

    replies = load_keyword_replies()
    assert set(replies) == {
        "QUERO",
        "CATÁLOGO",
        "PRESENTE",
        "TREVO",
        "CORAÇÃO",
        "LUZ",
        "IPHONE",
    }
    for reply in replies.values():
        assert hard_review_customer_reply(reply["message"]) == []


# -- métricas -----------------------------------------------------------------------


def test_insights_at_24h_and_7d_and_weekly_report(store, calendar, engine):
    social.import_calendar(store, calendar)
    approve_all(store, engine)
    published_at = START + timedelta(hours=8)
    social.run_due(
        store,
        publisher=FakePublisher(),
        approval_status=lambda rid: approval_status(engine, rid),
        enabled=True,
        media_check=ok_media,
        now=published_at,
    )
    metrics = {"reach": 1200, "saved": 30, "comments": 8, "shares": 5}

    def fetch(media_id, fmt):
        return metrics

    assert (
        social.collect_insights(store, fetch, now=published_at + timedelta(hours=1))
        == []
    )
    day = social.collect_insights(store, fetch, now=published_at + timedelta(hours=25))
    week = social.collect_insights(store, fetch, now=published_at + timedelta(days=8))

    assert [item["checkpoint"] for item in day] == ["24h"]
    assert [item["checkpoint"] for item in week] == ["7d"]
    report = social.weekly_report(store, store.get("out-01")["week"])
    assert report["top"] == ["out-01"]
    assert "salvamentos" in social.insights_csv(store).splitlines()[0]


def test_cli_reports_missing_calendar(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VERATUS_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "")

    assert social.main(["import", str(tmp_path / "x.json")]) == 2
    assert "pacote do fundador" in capsys.readouterr().out


# -- rodada social: gate, falha parcial, canais manuais, mídia e rotas -------------


def one_item_calendar(tmp_path, **fields):
    item = {
        "id": "solo",
        "data": "2026-10-19 08:00",
        "formato": "feed",
        "midias": ["solo.jpg"],
        "legenda": "Tom dourado para o dia a dia.",
        "canais": ["instagram", "facebook"],
        "produtos": ["noir-clover"],
        **fields,
    }
    path = tmp_path / "solo.json"
    path.write_text(json.dumps([item]), encoding="utf-8")
    return path


def approve_and_run(store, engine, publisher, now, **kwargs):
    approve_all(store, engine)
    return social.run_due(
        store,
        publisher=publisher,
        approval_status=lambda rid: approval_status(engine, rid),
        enabled=True,
        media_check=ok_media,
        now=now,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("caption", "issue"),
    [
        ("Colar por R$ 289,90.", "valor monetário"),
        ("Peça com garantia de procedência.", "selo ou material"),
        ("Tom dourado. #a #b #c #d #e #f", "mais de 5 hashtags"),
    ],
)
def test_caption_claims_block_the_item(store, tmp_path, caption, issue):
    social.import_calendar(store, one_item_calendar(tmp_path, legenda=caption))

    item = store.get("solo")
    assert item["status"] == "BLOCKED"
    assert f"CAPTION_CLAIM:{issue}" in item["block_reason"]


class FlakyPageGraph(FakeGraph):
    """A Página falha na primeira foto (ex.: 403 sem pages_manage_posts)."""

    def __init__(self):
        super().__init__()
        self.page_failures = 1

    def post(self, url, data=None, timeout=None, **kwargs):
        if url.endswith("/PAGE/photos") and self.page_failures:
            self.page_failures -= 1
            raise RuntimeError("403")
        return super().post(url, data=data, timeout=timeout, **kwargs)


def test_partial_failure_never_republishes_instagram(store, engine, tmp_path):
    social.import_calendar(store, one_item_calendar(tmp_path))
    graph = FlakyPageGraph()
    publisher = social.MetaSocialPublisher(
        graph, access_token="t", ig_user_id="IG", page_id="PAGE"
    )
    first = approve_and_run(store, engine, publisher, START)
    assert first["failed"][0]["error"] == "RuntimeError"
    assert store.get("solo")["published"]["instagram"]["id"]

    second = social.run_due(
        store,
        publisher=publisher,
        approval_status=lambda rid: approval_status(engine, rid),
        enabled=True,
        media_check=ok_media,
        now=START + timedelta(hours=2),
    )

    ig_publishes = [url for url, _ in graph.posts if url.endswith("/media_publish")]
    assert second["published"] == ["solo"] and len(ig_publishes) == 1
    item = store.get("solo")
    assert item["status"] == "PUBLISHED" and item["published"]["facebook"]["id"]


def test_unconfigured_page_waits_and_manual_channels_become_tasks(
    store, engine, tmp_path
):
    path = tmp_path / "c.json"
    base = {"data": "2026-10-19 08:00", "legenda": "Tom dourado.", "midias": ["a.jpg"]}
    path.write_text(
        json.dumps(
            [
                {**base, "id": "pagina", "formato": "feed", "canais": ["facebook"]},
                {
                    **base,
                    "id": "enquete",
                    "formato": "story",
                    "canais": ["instagram"],
                    "figurinha": "Enquete: tom dourado ou prateado?",
                },
                {
                    **base,
                    "id": "tiktok",
                    "formato": "feed",
                    "canais": ["instagram", "tiktok"],
                },
            ]
        ),
        encoding="utf-8",
    )
    social.import_calendar(store, path)
    publisher = social.MetaSocialPublisher(
        FakeGraph(), access_token="t", ig_user_id="IG"
    )
    tasks = []

    result = approve_and_run(store, engine, publisher, START, task=tasks.append)

    assert result["skipped"] == [
        {"id": "pagina", "reasons": ["FACEBOOK_PAGE_ID_MISSING"]}
    ]
    assert store.get("pagina")["status"] == "APPROVED"
    assert store.get("enquete")["status"] == "NEEDS_MANUAL"
    assert store.get("tiktok")["status"] == "PUBLISHED_PARTIAL"
    assert {(t["item_id"], t["channel"]) for t in tasks} == {
        ("enquete", "instagram"),
        ("tiktok", "tiktok"),
    }
    assert all(t["delivery"] == "PAINEL" and t["media"] for t in tasks)
    snapshot = social.audit_snapshot(store)
    assert len(snapshot["manual_tasks"]) == 2
    assert snapshot["by_status"]["PUBLISHED_PARTIAL"] == 1


def test_reject_one_item_keeps_the_rest_of_the_week(store, calendar, engine):
    social.import_calendar(store, calendar)
    requests = social.request_weekly_approvals(store, engine)
    payload = engine.approvals.requests[requests[0]["approval_id"]].payload
    assert payload["items"][0]["caption"] and payload["items"][0]["media"]

    social.reject_item(store, "out-02", actor="fundador", reason="trocar a arte")
    for request in requests:
        engine.approvals.resolve(request["approval_id"], ApprovalStatus.APPROVED)
    social.sync_approvals(store, lambda rid: approval_status(engine, rid))

    assert store.get("out-01")["status"] == "APPROVED"
    assert store.get("out-02")["status"] == "DRAFT"


def test_only_media_from_the_manifest_with_the_same_hash(tmp_path):
    item = {"format": "IMAGE", "media": ["https://veratus.onrender.com/social/a.jpg"]}
    jpeg = lambda url: (200, "image/jpeg", "abc")

    assert social.media_problems(item, jpeg, {}) == [
        "MEDIA_NOT_APPROVED: /social/a.jpg"
    ]
    assert social.media_problems(item, jpeg, {"/social/a.jpg": "zzz"}) == [
        "MEDIA_HASH_MISMATCH: /social/a.jpg"
    ]
    assert social.media_problems(item, jpeg, {"/social/a.jpg": "abc"}) == []
    from PIL import Image

    good, small = tmp_path / "ok.jpg", tmp_path / "small.jpg"
    Image.new("RGB", (1080, 1350)).save(good, "JPEG")
    Image.new("RGB", (600, 600)).save(small, "JPEG")
    assert social.media_qa(good, "IMAGE")["problems"] == []
    assert social.media_qa(small, "STORY")["problems"] == [
        "PROPORCAO_600x600",
        "RESOLUCAO_ABAIXO_DE_1080",
    ]


def test_metrics_by_format_survive_one_error_and_become_records(
    store, engine, tmp_path
):
    path = tmp_path / "c.json"
    base = {
        "data": "2026-10-19 08:00",
        "legenda": "Tom dourado.",
        "canais": ["instagram"],
    }
    path.write_text(
        json.dumps(
            [
                {**base, "id": "feed", "formato": "feed", "midias": ["f.jpg"]},
                {**base, "id": "story", "formato": "story", "midias": ["s.jpg"]},
            ]
        ),
        encoding="utf-8",
    )
    social.import_calendar(store, path)
    approve_and_run(store, engine, FakePublisher(), START)
    asked = []

    def fetch(media_id, fmt):
        asked.append(fmt)
        if fmt == "IMAGE":
            raise RuntimeError("400")
        return {"reach": 300, "views": 900, "replies": 2}

    story = social.collect_insights(store, fetch, now=START + timedelta(hours=21))
    feed = social.collect_insights(store, fetch, now=START + timedelta(hours=25))

    assert [(c["id"], c["checkpoint"]) for c in story] == [("story", "20h")]
    assert feed == [] and asked == ["STORY", "IMAGE"]
    assert store.events("INSIGHT_FAILED")[0]["item_id"] == "feed"
    records = social.performance_records(store)
    assert records[0]["channel"] == "instagram_organico"
    assert records[0]["impressions"] == 900 and records[0]["spend_brl"] == "0"


def test_skill_draft_is_added_with_gate_and_pt_br_lint(store):
    added = social.add_items(
        store,
        {
            "id": "rascunho",
            "data": "2026-10-25 10:00",
            "formato": "feed",
            "midias": ["r.jpg"],
            "legenda": "Uma peça incrível para a sua jornada.",
        },
        actor="ig-caption",
    )

    assert added[0]["status"] == "PENDING_APPROVAL"
    assert {"SLOP:incrível", "SLOP:jornada"} <= set(added[0]["lint"])


def test_old_cli_and_admin_routes_never_publish_with_flags_off(tmp_path, monkeypatch):
    from integrations import meta_content_publisher as meta
    from integrations import webhook

    def no_network(*args, **kwargs):
        raise AssertionError("chamada externa com as flags desligadas")

    monkeypatch.setattr("requests.Session.request", no_network)
    assert meta.main(["--publish", "--order", "1"]) == 2

    for name in ("SOCIAL_PUBLISH_ENABLED", "SOCIAL_INSIGHTS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERATUS_ADMIN_TOKEN", "admin-test-key")
    monkeypatch.setenv("VERATUS_AGENT_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("META_PAGE_ACCESS_TOKEN", "t")
    monkeypatch.setenv("META_IG_BUSINESS_ACCOUNT_ID", "IG")
    monkeypatch.setattr(webhook, "_runtime", CommandEngine)
    webhook.rate_store.clear()
    client = webhook.app.test_client()
    headers = {"X-Veratus-Admin-Token": "admin-test-key"}

    run = client.post("/os/social/run-due", json={}, headers=headers)
    insights = client.post("/os/social/insights", json={}, headers=headers)

    assert run.status_code == 200 and run.json["status"] == "PREVIEW"
    assert run.json["reasons"] == ["SOCIAL_PUBLISH_DISABLED"]
    assert (
        insights.json["status"] == "PREVIEW" and insights.json["external_read"] is False
    )
