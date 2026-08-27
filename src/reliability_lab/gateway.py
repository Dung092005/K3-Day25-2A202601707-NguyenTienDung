from __future__ import annotations

from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback.

        Pipeline:
        1. CACHE CHECK: If hit, return immediately (0ms, $0)
        2. PROVIDER CHAIN: Try each provider via its circuit breaker
           - On success: cache response, return with route="primary" or "fallback"
           - On error (ProviderError / CircuitOpenError): record error, continue to next
        3. STATIC FALLBACK: If all fail, return degraded message
        """
        # 1. Cache check
        if self.cache is not None:
            cached_text, score = self.cache.get(prompt)
            if cached_text is not None:
                return GatewayResponse(
                    text=cached_text,
                    route=f"cache_hit:{score:.2f}",
                    provider=None,
                    cache_hit=True,
                    latency_ms=0.0,
                    estimated_cost=0.0,
                    error=None,
                )

        # 2. Provider fallback chain
        last_error: str | None = None
        for idx, provider in enumerate(self.providers):
            breaker = self.breakers.get(provider.name)
            try:
                if breaker is not None:
                    resp: ProviderResponse = breaker.call(provider.complete, prompt)
                else:
                    resp = provider.complete(prompt)

                if self.cache is not None:
                    self.cache.set(prompt, resp.text, {"provider": provider.name})

                route = "primary" if idx == 0 else "fallback"
                return GatewayResponse(
                    text=resp.text,
                    route=route,
                    provider=provider.name,
                    cache_hit=False,
                    latency_ms=resp.latency_ms,
                    estimated_cost=resp.estimated_cost,
                    error=None,
                )
            except (ProviderError, CircuitOpenError) as exc:
                last_error = str(exc)
                continue

        # 3. Static fallback
        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            provider=None,
            cache_hit=False,
            latency_ms=0.0,
            estimated_cost=0.0,
            error=last_error,
        )
