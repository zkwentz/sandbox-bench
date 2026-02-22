"""Provider-specific sandbox pricing."""

# Cost per second of sandbox runtime, sourced from published pricing pages.
# These are approximate rates for the most common tier/instance type.
PROVIDER_RATES: dict[str, float] = {
    "e2b": 0.0001,        # ~$0.36/hr  (e2b.dev/pricing)
    "daytona": 0.00015,    # ~$0.54/hr  (daytona.io/pricing)
    "modal": 0.000164,     # ~$0.59/hr  (modal.com/pricing)
    "codesandbox": 0.000278,  # ~$1.00/hr  (codesandbox.io/pricing)
    "fly": 0.0000095,      # ~$0.034/hr (fly.io/pricing)
    "docker-image": 0.0,   # Local, no cost
    "microvm": 0.0,        # Local, no cost
}

# Default rate when a provider is not in the table
DEFAULT_RATE = 0.0001  # ~$0.36/hr


def estimate_sandbox_cost(provider_name: str, duration_seconds: float) -> float:
    """Estimate the cost of running a sandbox for a given duration.

    Args:
        provider_name: Name of the sandbox provider.
        duration_seconds: Total wall-clock seconds the sandbox was alive.

    Returns:
        Estimated cost in USD.
    """
    rate = PROVIDER_RATES.get(provider_name, DEFAULT_RATE)
    return round(rate * duration_seconds, 6)
