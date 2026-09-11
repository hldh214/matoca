import asyncio
from collections.abc import Callable


async def run_storage[T, **P](operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Keep ownership of worker-thread I/O until it has actually finished.

    Cancelling to_thread alone abandons the await, not the transaction. Shield
    and drain so merchant admission cannot reopen while an old write is running.
    """
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve any exception before propagating cancellation.
        if not worker.cancelled():
            worker.exception()
        raise asyncio.CancelledError
    return worker.result()
