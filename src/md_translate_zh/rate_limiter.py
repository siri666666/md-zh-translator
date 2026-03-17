from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque


@dataclass
class TokenReservation:
    timestamp: float
    estimated_tokens: int


class SlidingWindowRateLimiter:
    """Simple sliding-window limiter for RPM and TPM."""

    def __init__(self, max_rpm: int | None, max_tpm: int | None) -> None:
        self.max_rpm = max_rpm
        self.max_tpm = max_tpm
        self._request_times: Deque[float] = deque()
        self._token_records: Deque[tuple[float, int]] = deque()

    def acquire(self, estimated_tokens: int = 0) -> TokenReservation:
        while True:
            now = time.monotonic()
            self._prune(now)
            wait_seconds = self._next_wait_seconds(now, estimated_tokens)
            if wait_seconds <= 0:
                self._request_times.append(now)
                if self.max_tpm and estimated_tokens > 0:
                    self._token_records.append((now, estimated_tokens))
                return TokenReservation(timestamp=now, estimated_tokens=estimated_tokens)
            time.sleep(min(wait_seconds, 5.0))

    def add_positive_delta(self, extra_tokens: int) -> None:
        if not self.max_tpm:
            return
        if extra_tokens <= 0:
            return
        now = time.monotonic()
        self._prune(now)
        self._token_records.append((now, extra_tokens))

    def _prune(self, now: float) -> None:
        threshold = now - 60.0
        while self._request_times and self._request_times[0] <= threshold:
            self._request_times.popleft()
        while self._token_records and self._token_records[0][0] <= threshold:
            self._token_records.popleft()

    def _next_wait_seconds(self, now: float, estimated_tokens: int) -> float:
        wait_seconds = 0.0

        if self.max_rpm and len(self._request_times) >= self.max_rpm:
            wait_seconds = max(wait_seconds, 60.0 - (now - self._request_times[0]))

        if self.max_tpm and estimated_tokens > 0:
            used_tokens = sum(tokens for _, tokens in self._token_records)
            overflow = used_tokens + estimated_tokens - self.max_tpm
            if overflow > 0:
                released = 0
                for ts, tokens in self._token_records:
                    released += tokens
                    if released >= overflow:
                        wait_seconds = max(wait_seconds, 60.0 - (now - ts))
                        break

        return max(wait_seconds, 0.0)
