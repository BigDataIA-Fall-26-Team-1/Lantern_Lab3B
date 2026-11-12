"""Pydantic schemas for tool inputs and outputs. MCP turns these into the JSON
schemas the model sees: inputs from the annotated parameters, outputs from the
return types."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ---- inputs (used as parameter annotations in server.py)
Scope = Annotated[str, Field(description="'public' for BDC data, or the ACTIVE mandate id for client data")]
MandateScope = Annotated[str, Field(description="the ACTIVE mandate id; never another mandate, never 'public'")]
Adsh = Annotated[str, Field(pattern=r"^\d{10}-\d{2}-\d{6}$", description="SEC accession number")]
Recipients = Annotated[list[str], Field(min_length=1, description="only this mandate's contacts or a BDC's IR address")]
BdcName = Annotated[str | None, Field(description="exact BDC name as in the panel; omit for all BDCs")]


# ---- outputs
class MandateList(BaseModel):
    principal: str
    mandates: list[str]


class Holding(BaseModel):
    adsh: str
    name: str
    identifier: str
    fair_value: float | None
    principal: float | None
    pik_rate: float | None = Field(description="decimal, e.g. 0.025 is 2.5%")
    source_rows: str


class Signal(BaseModel):
    adsh: str = Field(description="the filing: evidence")
    name: str
    rule: Literal["MARKDOWN", "PIK_UP", "EXTENDED"]
    identifier: str
    row: str = Field(description="current-period row: evidence")
    prior_row: str = Field(description="prior fiscal year-end row in the same filing: evidence")
    before: float | str
    after: float | str


class Filing(BaseModel):
    adsh: str
    cik: str
    name: str
    form: str
    period: str
    filed: str
    url: str


class MandateProfile(BaseModel):
    mandate: str
    client: str
    positions: dict[str, float] = Field(description="BDC name -> USD millions")


class WatchlistEntry(BaseModel):
    bdc: str
    reason: str
    added: str


# ---- outputs of the tools declared now and built in Part 4 (memory) and Part 6 (actions)
class MemoryHit(BaseModel):
    text: str
    source: str = Field(description="provenance, e.g. log/2026-10-01.md#1")


class ProposeWriteResult(BaseModel):
    result: Literal["written", "proposed"]
    origin: Literal["principal", "email", "model"] = Field(
        description="decided by the channel's authentication, never by the caller")


class WatchlistWriteResult(BaseModel):
    entry_id: str
    status: str


class DraftResult(BaseModel):
    draft_id: str
    payload_hash: str


class ApprovalRequest(BaseModel):
    draft_id: str
    payload_hash: str
    status: str


class SendResult(BaseModel):
    message_id: str
    status: str
