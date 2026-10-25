"""CROP_SOWN_RECORDS — the root register and the hub of the ERD.

Every crop line (planning, cultivation, sowing, production, harvesting,
infestation, cluster) links straight back to this record; the ERD draws no
intermediate level.

The farmer is **identified, not owned**: this registry is not the system of
record for farmers, so the record carries their identifiers — and mirrors the
Fayda FAN into the platform's `link_foundational_id`, which exists for exactly
this "belongs to a person held elsewhere" case.

The land is **not** held here. Matching the Odoo registry, one record can span
several plots, so each crop line carries the plot it was worked on — `land_id`
and its attributes live on the line, not the header. What the header holds is
where the farmer is: the administrative address (region / zone / woreda /
kebele) and a GPS reading.
"""

from typing import Optional

from openg2p_registry_core.models.g2p_intake_form import G2PIntakeForm
from openg2p_registry_core.models import (
    G2PRegister, G2PRegisterHistory, G2PGeo, G2PGeoHistory
)
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..services import G2PRegisterDomainServiceCropSown


class G2PCropSown:
    # Popup FIN evidence, distinct from the approved reference UID/FAN16.
    fayda_fin: Mapped[str] = mapped_column(String, nullable=True)
    identity_verification_id: Mapped[str] = mapped_column(String, nullable=True)
    last_authentication_status: Mapped[str] = mapped_column(String, nullable=True)
    last_authenticated_on: Mapped[str] = mapped_column(String, nullable=True)
    authentication_expiry_date: Mapped[str] = mapped_column(String, nullable=True)


    # ── Farmer (identified, held in the farmer registry) ──────────────────────
    farmer_uuid: Mapped[str] = mapped_column(String, nullable=True)
    farmer_id: Mapped[str] = mapped_column(String, nullable=True)
    fayda_fan_id: Mapped[str] = mapped_column(String, nullable=True)
    farmer_name: Mapped[str] = mapped_column(String, nullable=True)
    land_id: Mapped[str] = mapped_column(String, nullable=True)
    # farmer_photo_upload: Mapped[str] = mapped_column(String, nullable=True)
    record_image_document_id: Mapped[str] = mapped_column(String, nullable=True)
    # ── Address: the admin hierarchy, from the master-data catalog ────────────
    region: Mapped[str] = mapped_column(String, nullable=True)                # Attribute lookup (REGION)
    zone: Mapped[str] = mapped_column(String, nullable=True)                  # Attribute lookup (ZONE)
    woreda: Mapped[str] = mapped_column(String, nullable=True)                # Attribute lookup (WOREDA)
    kebele: Mapped[str] = mapped_column(String, nullable=True)                # Attribute lookup (KEBELE)
    latitude: Mapped[str] = mapped_column(String, nullable=True)
    longitude: Mapped[str] = mapped_column(String, nullable=True)
    altitude: Mapped[str] = mapped_column(String, nullable=True)
    # Denormalised admin names. The register search returns stored values as-is —
    # it does not join g2p_attribute_values — so a tree column bound to `region`
    # would print REGION_ET11. These carry the display name for those columns and
    # are refreshed from the lookup whenever the record is approved.
    region_name: Mapped[str] = mapped_column(String, nullable=True)
    zone_name: Mapped[str] = mapped_column(String, nullable=True)
    woreda_name: Mapped[str] = mapped_column(String, nullable=True)
    kebele_name: Mapped[str] = mapped_column(String, nullable=True)
    # The geo-hierarchy widget stores the root->leaf chain it selected here; the
    # individual level columns above are filled from it on approval.
    address_hierarchy: Mapped[str] = mapped_column(Text, nullable=True)

    # ── Record lifecycle & field staff ────────────────────────────────────────
    status: Mapped[str] = mapped_column(String, nullable=True)                # Attribute lookup (APPROVAL_STATUS)
    crop_year: Mapped[str] = mapped_column(String, nullable=True)
    # A registration covers one cropping season, and the season is part of the
    # functional id (REG/S1/2026/00001), so it has to live on the root record —
    # the id is minted before any crop line exists to derive it from.
    production_season: Mapped[str] = mapped_column(String, nullable=True)                # Attribute lookup (CROP_SEASON)
    lifecycle_stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Rejection tracking ──────────────────────────────────────────────────
    rejection_reason: Mapped[str] = mapped_column(Text, nullable=True)
    edit_count: Mapped[int] = mapped_column(Integer, nullable=True)


# All Register classes should have the prefix G2PRegister
class G2PRegisterCropSown(G2PRegister, G2PCropSown, G2PGeo):
    __tablename__ = "g2p_register_crop_sowns"
    __table_args__ = {"extend_existing": True}

    latitude: Mapped[str] = mapped_column(String, nullable=True)
    longitude: Mapped[str] = mapped_column(String, nullable=True)
    altitude: Mapped[str] = mapped_column(String, nullable=True)

    def get_search_text_fields(self) -> str:
        """Return crop sown fields used to build search_text."""
        return G2PRegisterDomainServiceCropSown().construct_search_text(self.to_dict())

    def get_record_name_fields(self) -> str:
        """Return crop sown record_name from domain service implementation."""
        return G2PRegisterDomainServiceCropSown().construct_record_name(self.to_dict())


# All Register History classes should have the prefix G2PRegisterHistory
class G2PRegisterHistoryCropSown(G2PRegisterHistory, G2PCropSown, G2PGeoHistory):
    __tablename__ = "g2p_register_history_crop_sowns"
    __table_args__ = {"extend_existing": True}

    latitude: Mapped[str] = mapped_column(String, nullable=True)
    longitude: Mapped[str] = mapped_column(String, nullable=True)
    altitude: Mapped[str] = mapped_column(String, nullable=True)


# All Intake Form classes should have the prefix G2PIntakeForm
class G2PIntakeFormCropSown(G2PIntakeForm, G2PRegister, G2PCropSown, G2PGeo):
    __tablename__ = "g2p_intake_form_crop_sowns"
    __table_args__ = {"extend_existing": True}

    latitude: Mapped[str] = mapped_column(String, nullable=True)
    longitude: Mapped[str] = mapped_column(String, nullable=True)
    altitude: Mapped[str] = mapped_column(String, nullable=True)

    def get_search_text_fields(self) -> str:
        """Return crop sown fields used to build search_text."""
        return G2PRegisterDomainServiceCropSown().construct_search_text(self.to_dict())

    def get_record_name_fields(self) -> str:
        """Return crop sown record_name from domain service implementation."""
        return G2PRegisterDomainServiceCropSown().construct_record_name(self.to_dict())
