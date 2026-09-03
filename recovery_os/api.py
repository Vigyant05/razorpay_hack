"""FastAPI app declaration. No live webhook logic this phase (invariant: stubs only)."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__

app = FastAPI(title="Recovery OS", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# Razorpay webhook receiver, run endpoints, etc. land in a later phase.
