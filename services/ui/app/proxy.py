"""Thin REST/WS client wrapping calls to the capture and stt services."""

from __future__ import annotations

import os

import httpx

CAPTURE_URL = os.environ.get("CAPTURE_URL", "http://capture:8081")
STT_URL = os.environ.get("STT_URL", "http://stt:8082")


async def get_sources() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{CAPTURE_URL}/sources")
        resp.raise_for_status()
        return resp.json().get("sources", [])


async def configure_capture(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{CAPTURE_URL}/configure", json=payload)
        resp.raise_for_status()
        return resp.json()


async def start_capture() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{CAPTURE_URL}/start")
        resp.raise_for_status()
        return resp.json()


async def stop_capture() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{CAPTURE_URL}/stop")
        resp.raise_for_status()
        return resp.json()


async def capture_status() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{CAPTURE_URL}/status")
        resp.raise_for_status()
        return resp.json()


async def configure_stt(payload: dict) -> dict:
    # Model loading (local backend) can take a few seconds on first use.
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{STT_URL}/configure", json=payload)
        resp.raise_for_status()
        return resp.json()


async def stt_health() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{STT_URL}/health")
        resp.raise_for_status()
        return resp.json()

