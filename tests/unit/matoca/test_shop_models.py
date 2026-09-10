from matoca_service.matoca.models import Shop


def test_shop_detail_parses_ticketing_rules_and_wait_estimate() -> None:
    shop = Shop.model_validate(
        {
            "id": 3272,
            "name": "synthetic merchant",
            "forms": {
                "is_ticketing_only": False,
                "is_confirm_tel": False,
                "min_adult": 1,
                "max_adult": 10,
                "default_value_adult": 2,
                "min_child": 0,
                "max_child": 10,
                "default_value_child": 0,
                "is_confirm_child": True,
            },
            "options": {
                "waiting": {
                    "enabled": True,
                    "show_waiting": True,
                    "unit": "groups",
                    "is_display_waiting_group": True,
                    "is_display_waiting_time": True,
                }
            },
            "waiting_time": {"minutes": 90, "is_more": True},
            "is_issuable": True,
            "is_open": True,
            "is_issuable_area": True,
            "unknown_future_field": "preserved",
        }
    )

    assert shop.forms is not None
    assert shop.forms.default_value_adult == 2
    assert shop.options.waiting is not None
    assert shop.options.waiting.is_display_waiting_time is True
    assert shop.waiting_time is not None
    assert shop.waiting_time.minutes == 90
    assert shop.model_extra == {"unknown_future_field": "preserved"}
