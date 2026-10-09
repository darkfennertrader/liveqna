"""Thin REST/WS client wrapping calls to the capture and stt services."""

from __future__ import annotations

import os

import httpx

CAPTURE_URL = os.environ.get("CAPTURE_URL", "http://capture:8081")
STT_URL = os.environ.get("STT_URL", "http://stt:8082")
SPEAKER_ID_URL = os.environ.get("SPEAKER_ID_URL", "http://speaker-id:8083")


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


async def configure_speaker_id(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{SPEAKER_ID_URL}/configure", json=payload)
        resp.raise_for_status()
        return resp.json()


async def reset_speaker_id() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{SPEAKER_ID_URL}/reset")
        resp.raise_for_status()
        return resp.json()


async def merge_speaker(source_id: str, target_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{SPEAKER_ID_URL}/merge",
            json={"source_id": source_id, "target_id": target_id},
        )
        resp.raise_for_status()
        return resp.json()


async def enroll_speaker(name: str, audio_base64: str, source_label: str = "",
                          start_sec: float | None = None, end_sec: float | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{SPEAKER_ID_URL}/enroll", json={
            "name": name,
            "audio": audio_base64,
            "source_label": source_label,
            "start_sec": start_sec,
            "end_sec": end_sec,
        })
        return resp.json()


async def list_enrolled_speakers() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{SPEAKER_ID_URL}/enrolled")
        resp.raise_for_status()
        return resp.json().get("speakers", [])


async def delete_enrolled_speaker(name: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(f"{SPEAKER_ID_URL}/enroll/{name}")
        resp.raise_for_status()
        return resp.json()
