import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.resolver import Quote
from app.state_machine import ItemState
from app.vision import Identification


def _mock_db():
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("user-uuid-1",)

    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__.return_value = mock_cur
    mock_cursor_cm.__exit__.return_value = False

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_cm

    mock_conn_cm = MagicMock()
    mock_conn_cm.__enter__.return_value = mock_conn
    mock_conn_cm.__exit__.return_value = False

    mock_get_conn = MagicMock(return_value=mock_conn_cm)
    return mock_get_conn, mock_cur


async def test_handle_photo_message_creates_and_updates_item():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="serum bottle",
        brand="Minimalist",
        product="Salicylic Acid 2% Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"],
        confidence=0.95,
        reasoning="Clear front label with brand and concentration visible.",
        missing_info=None,
        suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ) as mock_download,
        patch("app.orchestrator.archive_photo", return_value="path.jpg") as mock_archive,
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)) as mock_identify,
        patch("app.orchestrator.compose_and_send", AsyncMock()) as mock_compose,
        patch("app.orchestrator.resolve", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()),
    ):
        item_id = await handle_photo_message(
            settings.demo_user_phone, "chat1", "https://x/y.jpg"
        )

    assert item_id
    assert mock_get_conn.call_count == 3
    mock_download.assert_awaited_once_with("https://x/y.jpg")
    mock_archive.assert_called_once_with(item_id, b"bytes")
    assert mock_identify.await_args.args[0] == b"bytes"
    assert "Beauty & Personal Care/Skin Care" in mock_identify.await_args.args[1]
    mock_compose.assert_awaited_once_with("chat1", ItemState.IDENTIFIED, fake_result)

    calls = mock_cur.execute.call_args_list
    assert calls[0][0][0].strip().startswith("SELECT id FROM users")
    assert calls[0][0][1] == (settings.demo_user_phone,)

    open_item_sql, open_item_params = calls[1][0]
    assert "state = 'NEEDS_ANGLE'" in open_item_sql
    assert open_item_params == ("user-uuid-1",)

    insert_sql, insert_params = calls[2][0]
    assert "INSERT INTO items" in insert_sql
    assert "IDENTIFYING" in insert_sql
    assert insert_params[0] == item_id
    assert insert_params[1] == "user-uuid-1"

    update_sql, update_params = calls[3][0]
    assert "UPDATE items" in update_sql
    assert "state = %s" in update_sql
    assert update_params[:7] == (
        "Minimalist",
        "Salicylic Acid 2% Serum",
        "30ml",
        "Beauty & Personal Care/Skin Care",
        0.95,
        "path.jpg",
        "IDENTIFIED",
    )
    assert update_params[7] == item_id

    event_sql, event_params = calls[4][0]
    assert "INSERT INTO events" in event_sql
    assert "identified" in event_sql
    assert event_params[0] == item_id
    event_payload = json.loads(event_params[1])
    assert event_payload["brand"] == "Minimalist"
    assert event_payload["product"] == "Salicylic Acid 2% Serum"
    assert event_payload["confidence"] == 0.95


async def test_handle_photo_message_replies_when_user_is_unknown():
    from app.orchestrator import handle_photo_message

    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.return_value = None
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        item_id = await handle_photo_message("+910000000000", "chat1", "https://x/y.jpg")

    assert item_id is None
    mock_send.assert_awaited_once_with(
        "chat1", "Couldn't read that one — try a photo of the front label?"
    )


async def test_handle_photo_message_replies_when_identification_is_missing():
    from app.orchestrator import handle_photo_message

    mock_get_conn, mock_cur = _mock_db()
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        item_id = await handle_photo_message(
            settings.demo_user_phone, "chat1", "https://x/y.jpg"
        )

    assert item_id is None
    mock_send.assert_awaited_once_with(
        "chat1", "Couldn't read that one — try a photo of the front label?"
    )
    assert mock_cur.execute.call_count == 2


async def test_low_confidence_reaches_needs_angle_state():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="bottle",
        brand="Minimalist",
        product="Serum",
        variant=None,
        category="Beauty & Personal Care/Skin Care",
        search_terms=["serum"],
        confidence=0.4,
        reasoning="blurry",
        missing_info="concentration",
        suggested_photo="front label",
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.compose_and_send", AsyncMock()) as mock_compose,
    ):
        await handle_photo_message("+910000000000", "chat1", "https://x/y.jpg")

    mock_compose.assert_awaited_once_with("chat1", ItemState.NEEDS_ANGLE, fake_result)
    update_params = mock_cur.execute.call_args_list[3][0][1]
    assert update_params[6] == "NEEDS_ANGLE"


async def test_handle_photo_message_reuses_open_needs_angle_item():
    from app.orchestrator import handle_photo_message

    existing_item_id = "existing-item-uuid"
    fake_result = Identification(
        object_type="serum bottle",
        brand="Minimalist",
        product="Salicylic Acid 2% Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"],
        confidence=0.95,
        reasoning="Clear front label with brand and concentration visible.",
        missing_info=None,
        suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), (existing_item_id,)]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ) as mock_download,
        patch("app.orchestrator.archive_photo", return_value="path.jpg") as mock_archive,
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)) as mock_identify,
        patch("app.orchestrator.compose_and_send", AsyncMock()) as mock_compose,
        patch("app.orchestrator.resolve", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()),
    ):
        item_id = await handle_photo_message(
            settings.demo_user_phone, "chat1", "https://x/y.jpg"
        )

    assert item_id == existing_item_id
    assert mock_get_conn.call_count == 3
    mock_download.assert_awaited_once_with("https://x/y.jpg")
    mock_archive.assert_called_once_with(existing_item_id, b"bytes")
    assert mock_identify.await_args.args[0] == b"bytes"
    assert "Beauty & Personal Care/Skin Care" in mock_identify.await_args.args[1]
    mock_compose.assert_awaited_once_with("chat1", ItemState.IDENTIFIED, fake_result)

    calls = mock_cur.execute.call_args_list
    assert not any("INSERT INTO items" in c[0][0] for c in calls)

    open_item_sql, open_item_params = calls[1][0]
    assert "state = 'NEEDS_ANGLE'" in open_item_sql
    assert open_item_params == ("user-uuid-1",)

    update_sql, update_params = calls[2][0]
    assert "UPDATE items" in update_sql
    assert update_params[7] == existing_item_id


async def test_unknown_category_reaches_unbuyable_state():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="laptop",
        brand="Acme",
        product="Notebook",
        variant=None,
        category="Computers/Laptops",
        search_terms=["acme notebook"],
        confidence=0.95,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.compose_and_send", AsyncMock()) as mock_compose,
    ):
        await handle_photo_message("+910000000000", "chat1", "https://x/y.jpg")

    mock_compose.assert_awaited_once_with("chat1", ItemState.UNBUYABLE, fake_result)


async def test_typing_starts_before_download_and_stops_before_composing():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="bottle", brand="Minimalist", product="Serum", variant="30ml",
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.95, reasoning="clear", missing_info=None, suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    calls = []

    async def record_typing(_chat_id, on):
        calls.append(("typing", on))

    async def record_download(_media_url):
        calls.append(("download", None))
        return b"bytes"

    async def record_compose(*_args):
        calls.append(("compose", None))

    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.send_typing", side_effect=record_typing),
        patch("app.orchestrator.download_media", side_effect=record_download),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.compose_and_send", side_effect=record_compose),
        patch("app.orchestrator.resolve", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()),
    ):
        await handle_photo_message(settings.demo_user_phone, "chat1", "https://x/y.jpg")

    assert calls.index(("typing", True)) < calls.index(("download", None))
    assert calls.index(("typing", False)) < calls.index(("compose", None))


async def test_typing_failure_does_not_prevent_successful_reply():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="bottle", brand="Minimalist", product="Serum", variant="30ml",
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.95, reasoning="clear", missing_info=None, suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.send_typing", AsyncMock(side_effect=RuntimeError("Linq down"))),
        patch("app.orchestrator.download_media", AsyncMock(return_value=b"bytes")),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.compose_and_send", AsyncMock()) as mock_compose,
        patch("app.orchestrator.resolve", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()),
    ):
        item_id = await handle_photo_message(
            settings.demo_user_phone, "chat1", "https://x/y.jpg"
        )

    assert item_id is not None
    mock_compose.assert_awaited_once_with("chat1", ItemState.IDENTIFIED, fake_result)


async def test_identified_item_reaches_quoted_with_shopify_price():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="bottle",
        brand="Minimalist",
        product="Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["serum"],
        confidence=0.95,
        reasoning="clear",
        missing_info=None,
        suggested_photo=None,
    )
    quote = Quote(
        merchant="beminimalist.co",
        shopify_variant_id="123",
        price_paise=54900,
        handle="serum",
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.download_media", AsyncMock(return_value=b"bytes")),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.send_typing", AsyncMock()),
        patch("app.orchestrator.compose_and_send", AsyncMock()),
        patch("app.orchestrator.resolve", AsyncMock(return_value=quote)),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        await handle_photo_message(settings.demo_user_phone, "chat1", "https://x/y.jpg")

    quoted_sql, quoted_params = mock_cur.execute.call_args_list[-1][0]
    assert "state = 'QUOTED'" in quoted_sql
    assert quoted_params == (
        "beminimalist.co",
        "123",
        54900,
        mock_cur.execute.call_args_list[2][0][1][0],
    )
    mock_send.assert_awaited_once_with("chat1", "Minimalist Serum · 30ml · ₹549")


async def test_identified_item_without_quote_reaches_unbuyable():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="bottle",
        brand="Minimalist",
        product="Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["serum"],
        confidence=0.95,
        reasoning="clear",
        missing_info=None,
        suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.download_media", AsyncMock(return_value=b"bytes")),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.send_typing", AsyncMock()),
        patch("app.orchestrator.compose_and_send", AsyncMock()),
        patch("app.orchestrator.resolve", AsyncMock(return_value=None)),
        patch("app.orchestrator.find_substitute", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        await handle_photo_message(settings.demo_user_phone, "chat1", "https://x/y.jpg")

    unbuyable_sql, unbuyable_params = mock_cur.execute.call_args_list[-1][0]
    assert "state = 'UNBUYABLE'" in unbuyable_sql
    assert len(unbuyable_params) == 1
    mock_send.assert_awaited_once_with(
        "chat1",
        "Couldn't get Minimalist Serum — none of my merchants stock it.",
    )


async def test_identified_item_without_quote_offers_substitute():
    from app.luna import VariantMatch
    from app.orchestrator import handle_photo_message
    from app.substitution import SubstituteOffer

    fake_result = Identification(
        object_type="bottle",
        brand="Dove",
        product="Moisturizer",
        variant="50ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["moisturizer"],
        confidence=0.95,
        reasoning="clear",
        missing_info=None,
        suggested_photo=None,
    )
    offer = SubstituteOffer(
        merchant="beminimalist.co",
        handle="moisturizer-50ml",
        shopify_variant_id="var-1",
        price_paise=39900,
        title="Moisturizer 50ml",
        brand="Minimalist",
        match=VariantMatch(
            best_match_handle="moisturizer-50ml",
            similarity=0.75,
            shared_attributes=["moisturizer"],
            differences=["different brand"],
            one_line_pitch="Minimalist Moisturizer 50ml",
        ),
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("user-uuid-1",), None]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.download_media", AsyncMock(return_value=b"bytes")),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)),
        patch("app.orchestrator.send_typing", AsyncMock()),
        patch("app.orchestrator.compose_and_send", AsyncMock()),
        patch("app.orchestrator.resolve", AsyncMock(return_value=None)),
        patch("app.orchestrator.find_substitute", AsyncMock(return_value=offer)),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        await handle_photo_message(settings.demo_user_phone, "chat1", "https://x/y.jpg")

    sub_sql, sub_params = mock_cur.execute.call_args_list[-1][0]
    assert "state = 'SUBSTITUTE_OFFERED'" in sub_sql
    assert sub_params == (
        "Minimalist",
        "Moisturizer 50ml",
        "beminimalist.co",
        "var-1",
        39900,
        mock_cur.execute.call_args_list[2][0][1][0],
    )
    mock_send.assert_awaited_once()
    msg = mock_send.await_args.args[1]
    assert "Can't get Dove one" in msg
    assert "Minimalist Moisturizer 50ml" in msg
    assert "different brand" in msg
    assert "Want it?" in msg


async def test_handle_text_message_creates_session_and_sends_price_then_approval_link():
    from app.orchestrator import handle_text_message
    from app.prava import Session

    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [
        None,  # no SUBSTITUTE_OFFERED item
        (
            "item-uuid-1",
            "beminimalist.co",
            "variant-123",
            54900,
            "Minimalist",
            "Salicylic Acid Serum",
        ),
    ]
    session = Session(
        session_id="prava-session-1", approval_url="https://prava.example/approve"
    )
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.create_session",
            AsyncMock(return_value=session),
        ) as mock_create_session,
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
        patch(
            "app.routes.prava_callback._finalize_payment",
            AsyncMock(),
        ),
    ):
        await handle_text_message(settings.demo_user_phone, "chat1", "buy it")

    mock_create_session.assert_awaited_once_with(
        amount_paise=54900,
        merchant="beminimalist.co",
        line_items=[
            {
                "name": "Minimalist Salicylic Acid Serum",
                "shopify_variant_id": "variant-123",
                "price": 549.0,
            }
        ],
    )
    assert mock_get_conn.call_count == 3
    calls = mock_cur.execute.call_args_list
    assert "SUBSTITUTE_OFFERED" in calls[0][0][0]
    assert "UPDATE items" in calls[1][0][0]
    assert "state = 'QUOTED'" in calls[1][0][0]
    assert calls[1][0][1] == (settings.demo_user_phone,)
    assert "INSERT INTO purchases" in calls[2][0][0]
    assert calls[2][0][1] == ("item-uuid-1", "prava-session-1", 54900)
    assert "INSERT INTO events" in calls[3][0][0]
    assert json.loads(calls[3][0][1][1]) == {"chat_id": "chat1"}
    assert mock_send.await_args_list[0].args == (
        "chat1",
        "₹549 for Minimalist Salicylic Acid Serum. Sending the approval link now.",
    )
    assert mock_send.await_args_list[1].args == (
        "chat1",
        "https://prava.example/approve",
    )


async def test_handle_text_message_only_creates_one_session_when_second_yes_loses_claim():
    from app.orchestrator import handle_text_message
    from app.prava import Session

    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [
        None,  # first yes: no substitute
        (
            "item-uuid-1",
            "beminimalist.co",
            "variant-123",
            54900,
            "Minimalist",
            "Salicylic Acid Serum",
        ),
        None,  # second yes: no substitute
        None,  # second yes: claim lost
    ]
    session = Session(
        session_id="prava-session-1", approval_url="https://prava.example/approve"
    )
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.create_session",
            AsyncMock(return_value=session),
        ) as mock_create_session,
        patch("app.orchestrator.send_text", AsyncMock()),
        patch(
            "app.routes.prava_callback._finalize_payment",
            AsyncMock(),
        ),
    ):
        await handle_text_message(settings.demo_user_phone, "chat1", "yes")
        await handle_text_message(settings.demo_user_phone, "chat1", "yes")

    mock_create_session.assert_awaited_once()
    claim_calls = [
        call
        for call in mock_cur.execute.call_args_list
        if "UPDATE items" in call[0][0] and "AND i.state = 'QUOTED'" in call[0][0]
    ]
    assert len(claim_calls) == 2


async def test_handle_text_message_accepts_substitute_and_quotes():
    from app.orchestrator import handle_text_message

    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [
        ("item-sub-1",),
        ("Minimalist", "Moisturizer 50ml", "beminimalist.co", 39900),
    ]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        await handle_text_message(settings.demo_user_phone, "chat1", "yes")

    states = [c[0][1][0] for c in mock_cur.execute.call_args_list if "SET state" in c[0][0]]
    assert "IDENTIFIED" in states
    assert any("state = 'QUOTED'" in c[0][0] for c in mock_cur.execute.call_args_list)
    mock_send.assert_awaited_once_with(
        "chat1", "Got it — Minimalist Moisturizer 50ml · ₹399. Reply 'yes' to buy."
    )


async def test_handle_text_message_declines_substitute():
    from app.orchestrator import handle_text_message

    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.side_effect = [("item-sub-1",)]
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        await handle_text_message(settings.demo_user_phone, "chat1", "no")

    assert any(
        c[0][1] == ("DECLINED_SUB", "item-sub-1") for c in mock_cur.execute.call_args_list
    )
    mock_send.assert_awaited_once_with("chat1", "No worries — logged as a miss.")


async def test_handle_text_message_refill_charges_mandate_without_approval_link():
    from app.orchestrator import handle_text_message
    from app.prava import PaymentResult
    from app.shelf import ShelfItem

    shelf = ShelfItem(
        item_id="item-uuid-1",
        brand="Bombay Shaving Company",
        product="Power Play NXT Trimmer",
        merchant="bombayshavingcompany.com",
        shopify_variant_id="44818728616090",
        last_price_paise=59900,
        mandate_id="mdt_123",
    )
    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.return_value = None  # no SUBSTITUTE_OFFERED
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.find_shelf_item", return_value=shelf),
        patch(
            "app.orchestrator.charge_mandate",
            AsyncMock(
                return_value=PaymentResult(
                    status="awaiting_result",
                    card_number="4111",
                    cvv="123",
                    expiry="12/2030",
                    txn_ref_id="txn_1",
                )
            ),
        ) as mock_charge,
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        await handle_text_message(settings.demo_user_phone, "chat1", "refill trimmer")

    mock_charge.assert_awaited_once_with("mdt_123", 59900)
    insert = next(
        c for c in mock_cur.execute.call_args_list if "INSERT INTO purchases" in c[0][0]
    )
    assert insert[0][1] == ("item-uuid-1", 59900)
    mock_send.assert_awaited_once_with(
        "chat1", "On its way. ₹599, same as last time."
    )
