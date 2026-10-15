"""
Basit bellek-içi istek hız sınırlayıcı.

Kaydırmalı zaman penceresi algoritması kullanır.
Her istemci (client_id) için bağımsız sayaç tutar.
Üretim ortamında Redis tabanlı bir çözümle değiştirilebilir.
"""

import time
from collections import defaultdict
from threading import Lock


class InMemoryRateLimiter:
    """
    Kaydırmalı pencere hız sınırlayıcı.

    İş parçacığı güvenli; asyncio task'lardan çağrılabilir
    (GIL koruması + Lock yeterli, asyncio lock gerekmez).
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = float(window_seconds)
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        """
        İsteğe izin verilip verilmeyeceğini kontrol eder ve sayacı günceller.
        İzin verilirse True, sınır aşıldıysa False döner.
        """
        now = time.monotonic()
        with self._lock:
            # Süresi dolmuş girişleri temizle
            self._buckets[key] = [
                t for t in self._buckets[key] if now - t < self._window
            ]
            if len(self._buckets[key]) >= self._max:
                return False
            self._buckets[key].append(now)
            return True


# ── Singleton (üretim) ────────────────────────────────────────────────────────
# Varsayılan limit: dakikada 60 istek / istemci.
_limiter = InMemoryRateLimiter(max_requests=60, window_seconds=60)


def get_rate_limiter() -> InMemoryRateLimiter:
    """FastAPI bağımlılığı — testlerde taze bir örnek ile değiştirilir."""
    return _limiter
