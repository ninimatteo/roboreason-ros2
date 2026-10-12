"""cancel_planning: lets the GUI stop a plan request that is still being computed.

Each /plan_task request gets its own cancellation flag for its lifetime; the
cancel_planning service sets the flags of every request in progress. The
planning loop checks its flag between reasoning steps (see run_plan_loop): a
model call already in flight can't be interrupted, but no further one starts.
Per request rather than one node-wide flag, so a new request arriving while a
cancelled one is still finishing its last model call can't un-cancel it.
"""

import threading

from std_srvs.srv import Trigger

from robo_reason_bringup.interfaces import names


class PlanningCancelled(Exception):
    """Raised by the planning loop when its request was cancelled."""


class PlanningCancellation:
    def __init__(self, node, callback_group):
        self._logger = node.get_logger()
        self._lock = threading.Lock()
        self._active = set()
        node.create_service(Trigger, names.cancel_planning, self._on_cancel,
                            callback_group=callback_group)

    def begin(self) -> threading.Event:
        """Start of one /plan_task request; returns the flag to pass to run_plan_loop."""
        flag = threading.Event()
        with self._lock:
            self._active.add(flag)
        return flag

    def end(self, flag: threading.Event) -> None:
        """End of the request begun with ``flag``, however it ended."""
        with self._lock:
            self._active.discard(flag)

    def _on_cancel(self, _request, response):
        with self._lock:
            for flag in self._active:
                flag.set()
            count = len(self._active)
        self._logger.warn(f'Planning cancel requested: {count} request(s) in progress.')
        response.success = True
        response.message = f'cancelled {count} planning request(s)'
        return response
