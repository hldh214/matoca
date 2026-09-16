from datetime import UTC, datetime

from matoca_service.notifications.dispatcher import NotificationDispatcher
from matoca_service.notifications.keys import VapidKeys
from matoca_service.notifications.models import Notification, PushSubscription
from matoca_service.notifications.repository import NotificationRepository
from matoca_service.notifications.sender import PushSender
from matoca_service.storage.asyncio import run_storage


class NotificationService:
    def __init__(self, repository: NotificationRepository, keys: VapidKeys) -> None:
        self.repository = repository
        self.keys = keys
        self.dispatcher = NotificationDispatcher(repository, PushSender(keys))

    async def public_key(self, *, subject: str | None = None) -> str | None:
        if subject is not None:
            return await run_storage(self.keys.enable, subject)
        return await run_storage(self.keys.public_key)

    async def subscribe(self, subscription: PushSubscription) -> str:
        if await self.public_key() is None:
            raise LookupError("先にこのブラウザーの通知を有効にしてください")
        return await run_storage(self.repository.subscribe, subscription, datetime.now(UTC))

    async def unsubscribe(self, endpoint: str) -> None:
        await run_storage(self.repository.unsubscribe, endpoint)

    async def test(self, identity: str) -> None:
        await run_storage(self.repository.test, identity, datetime.now(UTC))
        self.dispatcher.wake()

    async def history(self) -> list[Notification]:
        return await run_storage(self.repository.history)
