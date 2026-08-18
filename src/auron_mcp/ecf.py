"""Fiscal domain types for AURON MCP — NCF, e-CF and DGII integration.

These dataclasses define the schemas returned by fiscal read tools. They map
directly to the backend's ``pos_api`` models (``NCFSequence``, ``Sale``) and
provide a clean MCP-facing contract independent of Django ORM details.

DGII e-CF submission is NOT yet implemented in the backend. When it is, the
``ECFDocument`` dataclass and the corresponding write tools (``auron_submit_ecf``,
``auron_ecf_status``) will be wired through the same controlled-write boundary
used for appointments.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NCFInfo:
    """Status of a single NCF sequence for the authenticated tenant."""

    id: str
    type: str  # "01" (Credito Fiscal), "02" (Consumidor Final), "14", "15"
    prefix: str  # "B"
    start_sequence: int
    end_sequence: int
    current_sequence: int
    expiration_date: str  # ISO date
    is_active: bool
    remaining: int  # end - current + 1 (or 0 if exhausted)
    exhausted: bool
    expired: bool  # True if expiration_date < today


@dataclass(frozen=True)
class NCFSequenceSummary:
    """Aggregate view of all NCF sequences for the tenant."""

    sequences: list[NCFInfo] = field(default_factory=list)
    total_remaining: int = 0
    sequences_near_exhaustion: int = 0  # remaining < 100
    sequences_expired: int = 0


@dataclass(frozen=True)
class FiscalDaySummary:
    """Today's fiscal summary: sales count by NCF type, tax totals."""

    date: str  # ISO date
    total_sales: int = 0
    total_revenue: str = "0.00"
    sales_by_ncf_type: dict[str, int] = field(default_factory=dict)
    total_itbis: str = "0.00"
    total_propina: str = "0.00"
    voided_sales: int = 0
    refunded_sales: int = 0


@dataclass(frozen=True)
class SaleFiscalDetail:
    """Fiscal detail of a single sale for DGII reporting."""

    sale_id: str
    ncf: str
    ncf_type: str
    rnc: str
    company_name: str
    date_time: str  # ISO datetime
    subtotal: str
    itbis: str
    propina: str
    total: str
    status: str  # draft / confirmed / void / refunded
    payment_method: str
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ECFTemplate:
    """Schema for a future DGII e-CF document.

    This is a structural placeholder. When the backend implements DGII
    submission, this dataclass will be populated with the XML generation
    fields required by DGII's e-CF schema v1.1.
    """

    sale_id: str
    ncf: str
    ncf_type: str
    rnc_emisor: str  # Tenant's RNC
    rnc_receptor: str  # Client's RNC (required for B01)
    fecha_emision: str  # ISO datetime
    itens: list[dict[str, Any]] = field(default_factory=list)
    total_itbis: str = "0.00"
    total_sin_itbis: str = "0.00"
    monto_total: str = "0.00"
    tipo_pago: str = "efectivo"
    estado: str = "pending"  # pending / submitted / accepted / rejected


@dataclass(frozen=True)
class ECFSubmissionResult:
    """Result of submitting an e-CF to DGII (future)."""

    sale_id: str
    track_id: str  # DGII tracking number
    estado: str  # accepted / rejected / pending
    fecha_aceptacion: str | None = None
    errores: list[str] = field(default_factory=list)
