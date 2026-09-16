from typing import Any

import pywebpush  # type: ignore[import-untyped]
import requests
from py_vapid import Vapid02  # type: ignore[import-untyped]

from matoca_service.notifications.keys import VapidKeys
from matoca_service.notifications.models import Delivery


class NoRedirectSession(requests.Session):
    def post(self, url: str | bytes, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs["allow_redirects"] = False
        return super().post(url, *args, **kwargs)


class PushSender:
    def __init__(self, keys: VapidKeys) -> None:
        self.keys = keys

    def send(self, delivery: Delivery) -> str:
        try:
            credentials = self.keys.credentials()
            if credentials is None:
                return "rejected"
            with NoRedirectSession() as session:
                pywebpush.webpush(
                    subscription_info=delivery.subscription.model_dump(),
                    data=delivery.notification.model_dump_json(exclude={"created_at", "kind"}),
                    vapid_private_key=Vapid02.from_string(credentials.private_key),
                    vapid_claims={"sub": credentials.subject},
                    timeout=10,
                    ttl=900,
                    verbose=False,
                    requests_session=session,
                )
            return "sent"
        except pywebpush.WebPushException as error:
            response = error.response
            status = response.status_code if response is not None else None
            if status in {404, 410}:
                return "expired"
            return "transient" if status is None or status == 429 or status >= 500 else "rejected"
        except Exception:
            # Never log exception strings: transport and encryption errors may contain capabilities.
            return "transient"
