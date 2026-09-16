import pytest

from matoca_service.service import PartyPreferences, QueueSubmission

from .fake_service import BrowserFakeService


@pytest.mark.asyncio
async def test_fake_service_mutates_only_in_memory() -> None:
    service = BrowserFakeService()
    assert await service.current_waiting("sawayaka") == []
    submission = QueueSubmission(
        shop_id=3272,
        adult_count=2,
        child_count=0,
        answer1=1,
    )

    waiting = await service.create_waiting("sawayaka", submission)

    assert waiting.id == 900000001
    assert waiting.shop_id == 3272
    assert waiting.adult_count == 2
    assert waiting.child_count == 0
    assert waiting.number == 101
    assert waiting.count == 8
    assert waiting.model_extra == {"waiting_time": {"minutes": 25, "is_more": False}}
    assert service.submissions == [submission]
    assert await service.current_waiting("sawayaka") == [waiting]

    await service.cancel_waiting("sawayaka", waiting.id)

    assert await service.current_waiting("sawayaka") == []
    assert service.submissions == [submission]


@pytest.mark.asyncio
async def test_fake_service_exposes_supported_merchants_and_shop_states() -> None:
    service = BrowserFakeService()

    merchants = service.list_merchants()
    console = await service.merchant_console("sawayaka")
    detail = await service.shop_detail("sawayaka", 3272)

    assert [(merchant.key, merchant.name) for merchant in merchants] == [
        ("sawayaka", "炭焼きレストラン さわやか"),
        ("la_ohana_yokohamahonmoku", "ラ・オハナ 横浜本牧"),
    ]
    assert all(
        merchant.cover_image_url is None or merchant.cover_image_url.startswith("/static/")
        for merchant in merchants
    )
    assert {shop.status for shop in console.shops} == {
        "available",
        "closed",
        "suspended",
        "stale",
    }
    assert all(
        shop.image_url is None or shop.image_url.startswith("/static/") for shop in console.shops
    )
    assert console.available_count == 1
    assert console.total_count == 4
    assert detail.model_dump(exclude_unset=True) == {
        "id": 3272,
        "name": "炭焼きレストラン さわやか",
        "sub_name": "浜松テスト店",
        "lat": 34.7,
        "lng": 137.7,
        "current_waiting": 8,
        "forms": {
            "min_adult": 2,
            "max_adult": 5,
            "min_child": 1,
            "max_child": 3,
            "confirm_items": [
                {
                    "enable": True,
                    "title": "注意事項を確認しましたか",
                    "sub_items": [
                        {
                            "enable": True,
                            "disabled": False,
                            "sub_item_index": 1,
                            "text": "確認しました",
                        }
                    ],
                }
            ],
        },
        "waiting_time": {"minutes": 25, "is_more": False},
        "is_issuable": True,
        "is_open": True,
    }


@pytest.mark.asyncio
async def test_fake_service_preferences_and_refresh_stay_local() -> None:
    service = BrowserFakeService()
    preferences = PartyPreferences(default_adult_count=3, default_child_count=1)

    assert await service.update_party_preferences(preferences) == preferences
    assert await service.party_preferences() == preferences
    snapshot = await service.merchant_snapshot("sawayaka", force_catalog=True)

    assert service.refresh_calls == 1
    assert snapshot.merchant.key == "sawayaka"
    assert snapshot.waiting == []
