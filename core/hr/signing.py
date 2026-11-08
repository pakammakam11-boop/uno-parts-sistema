"""Pasirašymo adapteris pagal Dokobit Universal API formą. Demo naudoja DemoSigningProvider (imitacija).

Tikras Dokobit: žr. .flow/2026-11-07-personalas-atostogos/RESEARCH.md. Pasirenkama aplinkos kintamuoju HR_SIGNING
(demo | dokobit), numatytai demo.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import select

from core.hr import db
from core.hr.schema import hr_documents, hr_signers

DEMO_METHOD = "Smart-ID (demo)"


@dataclass(frozen=True)
class Signer:
    id: str
    name: str
    email: str
    sequence: int


@dataclass(frozen=True)
class SignerState:
    id: str
    status: Literal["pending", "signed", "declined"]


@dataclass(frozen=True)
class SigningStatus:
    status: Literal["pending", "completed", "declined"]
    signers: list[SignerState]


class SigningProvider(Protocol):
    def create_signing(self, *, name: str, pdf: bytes, signers: list[Signer], postback_url: str) -> str: ...

    def signer_url(self, token: str, signer_id: str) -> str: ...

    def status(self, token: str) -> SigningStatus: ...

    def download(self, token: str) -> bytes: ...


class DemoSigningProvider:
    """Imituoja Dokobit pusę. Būsena lieka mūsų DB (hr_documents/hr_signers), nes Vercel funkcijos be atminties."""

    def create_signing(self, *, name: str, pdf: bytes, signers: list[Signer], postback_url: str) -> str:
        return secrets.token_urlsafe(24)

    def signer_url(self, token: str, signer_id: str) -> str:
        return f"/hr/sign/{token}/{signer_id}"

    def status(self, token: str) -> SigningStatus:
        with db.get_engine().connect() as conn:
            doc = conn.execute(select(hr_documents).where(hr_documents.c.signing_token == token)).mappings().first()
            if doc is None:
                raise KeyError(token)
            rows = conn.execute(select(hr_signers).where(hr_signers.c.document_id == doc["id"])
                                .order_by(hr_signers.c.sequence)).mappings().all()
        overall = doc["status"] if doc["status"] in ("completed", "declined") else "pending"
        return SigningStatus(overall, [SignerState(r["signer_id"], r["status"]) for r in rows])

    def download(self, token: str) -> bytes:
        with db.get_engine().connect() as conn:
            doc = conn.execute(select(hr_documents).where(hr_documents.c.signing_token == token)).mappings().first()
        if doc is None or doc["signed_pdf"] is None:
            raise KeyError(token)
        return bytes(doc["signed_pdf"])


class DokobitProvider:
    """Tikras Dokobit Universal API (demo nerealizuota, žr. RESEARCH.md)."""

    def create_signing(self, *, name: str, pdf: bytes, signers: list[Signer], postback_url: str) -> str:
        raise NotImplementedError("Dokobit integracija nerealizuota (RESEARCH.md)")

    def signer_url(self, token: str, signer_id: str) -> str:
        raise NotImplementedError("Dokobit integracija nerealizuota (RESEARCH.md)")

    def status(self, token: str) -> SigningStatus:
        raise NotImplementedError("Dokobit integracija nerealizuota (RESEARCH.md)")

    def download(self, token: str) -> bytes:
        raise NotImplementedError("Dokobit integracija nerealizuota (RESEARCH.md)")


def get_provider() -> SigningProvider:
    return DokobitProvider() if os.environ.get("HR_SIGNING", "demo").lower() == "dokobit" else DemoSigningProvider()


def control_code(token: str, signer_id: str) -> str:
    """4 skaitmenų kontrolinis kodas (kaip Smart-ID), išvedamas iš pasirašymo žetono ir pasirašančiojo."""
    digest = hashlib.sha256(f"{token}:{signer_id}".encode()).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 10000:04d}"
