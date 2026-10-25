"""Domain service for the CropSown Farmer register.

Mirrors the Livestock registry's Farmer domain service: builds the farmer's
search_text and record_name, and fills ``farmer_name`` from name parts when the
intake form only captured first/middle/last.
"""
import logging
from datetime import date

from openg2p_registry_core.services import G2PRegisterDomainService
from ...farmer_reference.profile import FarmerReferenceError, validate_identity_fields
from .domain_validation_utils import validation_error

_logger = logging.getLogger("g2p-register-domain-service-farmer")

_SEARCH_FIELDS = [
    "functional_record_id", "farmer_id", "fayda_fan_id", "farmer_name",
    "first_name", "middle_name", "last_name", "mobile_number", "status",
    "region", "zone", "woreda", "kebele",
]


class G2PRegisterDomainServiceFarmer(G2PRegisterDomainService):

    async def pre_approve(self, change_request, session):
        from ...farmer_reference.native import guard_farmer_approval
        await guard_farmer_approval(change_request, session)

    async def post_approve(self, change_request, session):
        from ...farmer_reference.native import record_native_decision
        await record_native_decision(session, change_request.change_request_id, True)
        # Stored Crop mirrors require their own domain approval. Farmer approval
        # changes only this Farmer and releases already approved waiting intakes.

    async def post_ingest(self, register_id, register_row, session):
        raise FarmerReferenceError("FARMER_REFERENCE_CHANGE_REQUEST_REQUIRED")

    async def validate_domain_attributes(self, records: list[dict], session=None, **kwargs):
        from .field_rules import validate_record_fields
        await validate_record_fields(records)
        for rec in records or []:
            try:
                validate_identity_fields(rec)
            except FarmerReferenceError as error:
                validation_error(str(error))
            self._fill_farmer_name(rec)
            if not rec.get("registration_date"):
                rec["registration_date"] = date.today().isoformat()
        return

    def _fill_farmer_name(self, payload: dict) -> None:
        if payload.get("farmer_name"):
            return
        parts = [payload.get("first_name"), payload.get("middle_name"), payload.get("last_name")]
        name = " ".join(p for p in parts if p).strip()
        if name:
            payload["farmer_name"] = name

    def construct_search_text(self, payload: dict, extra: list[str] = None) -> str:
        self._fill_farmer_name(payload)
        values = [str(payload.get(k)) for k in _SEARCH_FIELDS if payload.get(k)]
        if extra:
            values.extend(str(e) for e in extra if e)
        return " ".join(values).strip()

    def construct_record_name(self, payload: dict, extra: list[str] = None) -> str:
        self._fill_farmer_name(payload)
        values = [str(payload.get(k)) for k in ["farmer_name", "farmer_id"] if payload.get(k)]
        return " ".join(values).strip() or str(payload.get("functional_record_id") or "")
