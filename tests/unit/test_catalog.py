import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from matoca_service.catalog import CatalogState, MerchantCatalog, ShopCatalogStore
from matoca_service.matoca.models import Shop


def test_catalog_is_reusable_for_24_hours() -> None:
    refreshed_at = datetime(2026, 9, 10, tzinfo=UTC)
    catalog = MerchantCatalog(
        refreshed_at=refreshed_at,
        shops=[Shop(id=3272, name="synthetic merchant")],
    )

    assert catalog.fresh_for(refreshed_at + timedelta(hours=23, minutes=59))
    assert not catalog.fresh_for(refreshed_at + timedelta(hours=24))


def test_catalog_store_round_trips_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "shop_catalog.json"
    store = ShopCatalogStore(path)
    state = CatalogState(
        merchants={
            "sawayaka": MerchantCatalog(
                refreshed_at=datetime(2026, 9, 10, tzinfo=UTC),
                shops=[Shop(id=3272, name="synthetic merchant")],
            )
        }
    )

    store.save(state)

    assert store.load() == state
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
