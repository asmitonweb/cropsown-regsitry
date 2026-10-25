import logging
import re
from datetime import date

from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from openg2p_registry_core.models import G2PRegisterChangeRequest
from openg2p_registry_core.services import G2PRegisterDomainService

from ...farmer_reference.profile import FarmerReferenceError, normalize_uid

from .domain_validation_utils import as_float, as_int, validation_error, get_row_field

_logger = logging.getLogger("g2p-register-domain-service")


_MOBILE_NUMBER_PATTERN = re.compile(r"^(\+251[79]\d{8}|0[79]\d{8})$")

# Farmer ids are issued by the farmer registry as FR- followed by twelve digits.
_FARMER_ID_PATTERN = re.compile(r"^FR-[0-9]{12}$")

_SECTION_LIFECYCLE_STAGE_MAP = {
    "cs_planning_details": "PLANNING_APPROVED",
    "cs_cluster_details": "PLANNING_APPROVED",
    "cs_cultivation_details": "CULTIVATION_APPROVED",
    "cs_cultivation_cluster_details": "CULTIVATION_APPROVED",
    "cs_sowing_details": "SOWING_APPROVED",
    "cs_production_details": "SOWING_APPROVED",
    "cs_infestation_details": "SOWING_APPROVED",
    "cs_harvest_details": "HARVESTING_APPROVED",
}

class G2PRegisterDomainServiceCropSown(G2PRegisterDomainService):
    async def post_ingest(self, register_id, register_row, session):
        from .farmer_name_projection import refresh_crop_name_on_ingest
        await refresh_crop_name_on_ingest(session, register_row)

    async def validate_domain_attributes(self, records: list[dict], session=None, **kwargs):
        from .identity_verification import OANRegistrantAuthenticationService
        verifier = OANRegistrantAuthenticationService.get_component()
        if verifier:
            problems = await verifier.verify_records(records, {}, "CropSown")
            if problems:
                validation_error("; ".join(problems))
        section_id = kwargs.get("section_id")
        for record in records:
            sec_id = section_id or record.get("section_id") or record.get("_section_id")

            if "latitude" in record and record["latitude"] is not None:
                record["latitude"] = str(record["latitude"])
            if "longitude" in record and record["longitude"] is not None:
                record["longitude"] = str(record["longitude"])

            if session:
                await self._validate_immutability_of_season_and_year(record, session, **kwargs)

            await self._sync_address_names_on_record(record, session)

            # Address & Survey Personnel section payloads: do not validate Farmer Identity header attributes
            address_keys = (
                "geo_lowest_level_value_id", "geo_code_hierarchy_json", "latitude", "longitude",
                "region", "zone", "woreda", "kebele", "sub_kebele", "agro_ecological_zone"
            )
            personnel_keys = ("da_name", "da_mobile_number", "supervisor_name", "supervisor_mobile_number")

            is_address = (
                sec_id == "cropsown_cropsown_location_section_03"
                or any(ak in record for ak in address_keys)
                or any(isinstance(k, str) and (k.startswith("geo_") or any(ak in k for ak in address_keys)) for k in record)
            )
            is_personnel = (
                sec_id == "cropsown_survey_personnel_section_02"
                or any(ak in record for ak in personnel_keys)
            )

            if is_address or is_personnel:
                continue

            # Child table records: do not validate Farmer Identity here
            if any(k in record for k in ("land_id", "infestation_id", "records", "cs_planning_details_table", "cs_cultivation_details_table", "cs_sowing_details_table", "cs_harvest_details_table")):
                self._validate_land_id(record)
                continue

            # If the record has no farmer identity fields and is not explicitly the farmer identity section, do not validate as Farmer Identity
            farmer_identity_keys = ("crop_year", "production_season", "farmer_id", "fayda_fan_id", "fayda_id", "farmer_name")
            is_farmer_identity = (
                sec_id in (
                    "cropsown_cropsown_record_section_01",
                    "cropsown_common_intake_record_section_01",
                    "cropsown_intake_record_section_01",
                )
                or any(k in record for k in farmer_identity_keys)
                or any(isinstance(k, str) and any(k.endswith(f".{fk}") for fk in farmer_identity_keys) for k in record)
            )
            if not is_farmer_identity:
                continue

            from .domain_validation_utils import validate_alphabetical_name
            farmer_name = get_row_field(record, "farmer_name")
            if farmer_name:
                validate_alphabetical_name(farmer_name, "Farmer Name")

            is_common_intake = (sec_id == "cropsown_common_intake_record_section_01")

            self._validate_crop_year(record)
            self._validate_production_season(record)
            if not is_common_intake:
                self._validate_farmer_id(record)
            self._validate_fayda_fan_id(record)

            if session and (record.get("fayda_fan_id") or record.get("fayda_id")):
                await self._validate_fayda_season_and_year(record, session)

    async def _validate_immutability_of_season_and_year(self, record: dict, session, **kwargs) -> None:
        if not session:
            return

        internal_id = (
            record.get("internal_record_id")
            or record.get("link_internal_record_id")
            or record.get("record_id")
            or record.get("subject_internal_record_id")
            or kwargs.get("internal_record_id")
            or kwargs.get("link_internal_record_id")
            or kwargs.get("record_id")
            or kwargs.get("subject_internal_record_id")
        )
        if not internal_id and record.get("submission_id"):
            from sqlalchemy import text
            try:
                res_cr = await session.execute(
                    text("SELECT COALESCE(internal_record_id, link_internal_record_id) FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND (internal_record_id IS NOT NULL OR link_internal_record_id IS NOT NULL) LIMIT 1"),
                    {"sub_id": str(record.get("submission_id"))}
                )
                row_cr = res_cr.fetchone()
                if row_cr and row_cr[0]:
                    internal_id = row_cr[0]
            except Exception:
                pass

        existing_year = None
        existing_season = None
        found_rec_id = None

        from sqlalchemy import text

        if internal_id:
            res = await session.execute(
                text("SELECT internal_record_id, crop_year, production_season FROM g2p_register_crop_sowns WHERE internal_record_id = :rec_id"),
                {"rec_id": str(internal_id)}
            )
            existing = res.fetchone()
            if existing:
                found_rec_id, existing_year, existing_season = existing[0], existing[1], existing[2]

        if not found_rec_id:
            return

        new_season = (
            record.get("production_season")
            or record.get("6b06a95a-9a6c-5a33-a33d-c1625716c59c.production_season")
            or record.get("season")
        )
        if not new_season:
            for k, v in record.items():
                if isinstance(k, str) and (k.endswith("production_season") or k.endswith("season")) and v:
                    new_season = v
                    break

        if new_season and existing_season:
            p_clean = str(existing_season).replace("CROP_SEASON_", "").strip().upper()
            n_clean = str(new_season).replace("CROP_SEASON_", "").strip().upper()
            if p_clean != n_clean:
                validation_error(
                    f"Production Season cannot be changed once established for a Crop Sown record (Existing: '{existing_season}', Attempted: '{new_season}')."
                )

        if new_season and found_rec_id:
            n_clean = str(new_season).replace("CROP_SEASON_", "").strip().upper()
            child_tables = [
                ("g2p_register_plannings", "season", "Crop Planning Details"),
                ("g2p_register_cultivations", "season", "Cultivation Details"),
                ("g2p_register_cultivation_clusters", "season", "Cultivation Cluster Details"),
                ("g2p_register_clusters", "season", "Cluster Information Details"),
                ("g2p_register_sowings", "COALESCE(season, cluster_season)", "Sowing Details"),
                ("g2p_register_productions", "season", "Production Details"),
            ]
            for tbl, col, section_label in child_tables:
                res_child = await session.execute(
                    text(f"SELECT {col} FROM {tbl} WHERE link_internal_record_id = :rec_id AND record_status = 'ACTIVE' AND {col} IS NOT NULL"),
                    {"rec_id": str(found_rec_id)}
                )
                for r_c in res_child.fetchall():
                    if r_c[0]:
                        c_clean = str(r_c[0]).replace("CROP_SEASON_", "").strip().upper()
                        if c_clean != n_clean:
                            validation_error(
                                f"Production Season '{new_season}' does not match the Season '{r_c[0]}' established in {section_label} for this record."
                            )

        new_year = (
            record.get("crop_year")
            or record.get("6b06a95a-9a6c-5a33-a33d-c1625716c59c.crop_year")
        )
        if not new_year:
            for k, v in record.items():
                if isinstance(k, str) and k.endswith("crop_year") and v:
                    new_year = v
                    break

        if new_year and existing_year:
            if str(new_year).strip() != str(existing_year).strip():
                validation_error(
                    f"Crop Year cannot be changed once established for a Crop Sown record (Existing: '{existing_year}', Attempted: '{new_year}')."
                )


    async def _validate_fayda_season_and_year(self, record: dict, session) -> None:
        """
        Validates fayda_fan_id, crop_year, and production_season.
        For Sowing and Harvest intake forms, an existing active Crop Sown record MUST exist
        for the given Fayda ID + Crop Year + Production Season before proceeding to subsequent sections.
        """
        fayda_fan_id = record.get("fayda_fan_id") or record.get("fayda_id")
        crop_year = record.get("crop_year")
        prod_season = record.get("production_season")
        submission_id = record.get("submission_id")

        if not fayda_fan_id or not session:
            return

        requires_existing_record = False
        if submission_id:
            from sqlalchemy import text
            res_form = await session.execute(
                text(
                    "SELECT d.form_mnemonic "
                    "FROM g2p_intake_form_submissions s "
                    "JOIN g2p_intake_form_definitions d ON s.form_id = d.form_id "
                    "WHERE s.submission_id = :sub_id"
                ),
                {"sub_id": submission_id},
            )
            row_form = res_form.fetchone()
            if row_form and row_form[0]:
                form_mnemonic = str(row_form[0]).lower()
                if "sowing" in form_mnemonic or "harvest" in form_mnemonic:
                    requires_existing_record = True

        if requires_existing_record:
            from sqlalchemy import text
            query = (
                "SELECT internal_record_id::text, crop_year, production_season "
                "FROM g2p_register_crop_sowns "
                "WHERE fayda_fan_id = :fayda AND record_status = 'ACTIVE'"
            )
            params = {"fayda": str(fayda_fan_id).strip()}
            if crop_year:
                query += " AND crop_year = :c_year"
                params["c_year"] = str(crop_year).strip()

            res = await session.execute(text(query), params)
            rows = res.fetchall()

            matched = False
            if prod_season:
                p_clean = str(prod_season).replace("CROP_SEASON_", "").strip().upper()
                for r in rows:
                    r_season = r[2]
                    r_clean = str(r_season or "").replace("CROP_SEASON_", "").strip().upper()
                    if p_clean == r_clean:
                        matched = True
                        break
            elif len(rows) > 0:
                matched = True

            if not matched:
                year_str = f" in Crop Year '{crop_year}'" if crop_year else ""
                season_str = f" and Season '{prod_season}'" if prod_season else ""
                validation_error(
                    f"No existing Crop Sown record found for Fayda ID '{fayda_fan_id}'{year_str}{season_str}. "
                    f"Sowing / Harvesting cannot create a new Crop Sown record. Please ensure a Cultivation/Crop Sown record already exists for this Farmer Identity."
                )

    def _validate_crop_year(self, record: dict) -> None:
        crop_year = get_row_field(record, "crop_year")
        if crop_year is None or str(crop_year).strip() in ("", "None", "null", "Select"):
            validation_error("Crop Year is required in Farmer Identity.")
        val_str = str(crop_year).strip()
        if not val_str.isdigit():
            validation_error("Crop Year must contain only numeric digits (no letters, spaces, or special characters)")
        year = as_int(crop_year)
        current_year = date.today().year
        if year is not None and year < current_year:
            validation_error("Crop Year must not be in the past")
        if year is not None and year > current_year:
            validation_error("Crop Year must not be in the future")

    def _validate_production_season(self, record: dict) -> None:
        season = get_row_field(record, "production_season") or get_row_field(record, "season")
        if season is None or str(season).strip() in ("", "None", "null", "Select"):
            validation_error("Production Season is required in Farmer Identity.")

    def _validate_farmer_id(self, record: dict) -> None:
        """Farmer ids come from the farmer registry as FR- plus twelve digits."""
        value = get_row_field(record, "farmer_id")
        if value is None or str(value).strip() in ("", "None", "null", "Select"):
            validation_error("Farmer ID is required in Farmer Identity.")
        if not _FARMER_ID_PATTERN.match(str(value).strip()):
            validation_error(
                f"farmer_id must be FR- followed by 12 digits (got '{value}')"
            )

    def _validate_fayda_fan_id(self, record: dict) -> None:
        value = get_row_field(record, "fayda_fan_id") or get_row_field(record, "fayda_id")
        if value is None or str(value).strip() == "":
            validation_error("Fayda ID (FAN): enter the farmer's 16-digit FAN, or use the FIN field when available")
        try:
            record["fayda_fan_id"] = normalize_uid(value)
        except FarmerReferenceError:
            validation_error("Fayda ID (FAN): enter exactly 16 digits, optionally spaced in four groups. Use the FIN field for a 12-digit FIN")

    def _validate_land_id(self, record: dict) -> None:
        from .domain_validation_utils import validate_land_id
        validate_land_id(record.get("land_id"), "Land ID")

    def _validate_mobile_number(self, record: dict, field: str) -> None:
        value = record.get(field)
        if value is None or str(value).strip() == "":
            return
        if not _MOBILE_NUMBER_PATTERN.match(str(value).strip()):
            validation_error(f"{field} is not a valid mobile number")

    def construct_search_text(self, payload: dict, extra: list[str] = None) -> str:
        _logger.info("Constructing search text for crop sown record")

        keys = [
            "functional_record_id",
            "farmer_name",
            "farmer_id",
            "fayda_fan_id",
            "status",
            "crop_year",
            "production_season",
            "lifecycle_stage",
            "region",
            "zone",
            "woreda",
            "kebele",
            "latitude",
            "longitude",
            "address_line_1",
            "address_line_2",
            "postal_code",
            "country_code",
        ]
        search_text = []
        if extra:
            search_text.extend(str(item).strip() for item in extra if str(item).strip())
        search_text.extend(
            str(payload.get(key) or "").strip()
            for key in keys
            if str(payload.get(key) or "").strip()
        )

        return " ".join(search_text).strip()

    def construct_record_name(self, payload: dict, extra: list[str] = None) -> str:
        _logger.info("Constructing record name for crop sown record")

        record_name = []
        if extra:
            record_name.extend(str(item).strip() for item in extra if str(item).strip())

        val = (
            payload.get("farmer_id")
            or payload.get("fayda_fan_id")
            or payload.get("fayda_id")
        )
        if val and str(val).strip():
            record_name.append(str(val).strip())

        return " ".join(record_name).strip()

    # ── Cross-record rules ──────────────────────────────────────────────────
    # `validate_domain_attributes` only sees the records being written, so rules
    # that span a record and its siblings cannot live there. `pre_approve` runs
    # on approval with a live session, which is the first point both the record
    # and the database are available — so the Odoo @api.constrains that query
    # other rows are enforced here.

    async def pre_approve(self, change_request: G2PRegisterChangeRequest, session: AsyncSession):
        await self._check_immutable_header_season_and_year(change_request, session)
        await self._check_unique_farmer_per_year(change_request, session)
        await self._check_crop_area_within_plot(change_request, session)
        await self._check_matching_land_id_across_sections(change_request, session)
        await self._check_matching_season_across_sections(change_request, session)
        await self._check_section_dates_validity(change_request, session)
        await self._recompute_ec_dates_across_sections(change_request, session)
        await self._refresh_admin_names(change_request, session)
        await self._adopt_uploaded_photo(change_request, session)

    async def _check_immutable_header_season_and_year(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        if not change_request or not change_request.internal_record_id:
            return
        if change_request.section_id not in ("cropsown_cropsown_record_section_01", "cropsown_common_intake_record_section_01"):
            return

        record = await session.get(G2PRegisterCropSown, change_request.internal_record_id)
        if not record:
            return

        new_season = None
        new_year = None

        try:
            from openg2p_registry_core.services.g2p_register_change_request_service import G2PRegisterChangeRequestService
            cr_service = G2PRegisterChangeRequestService.get_component()
            payload_obj = await cr_service._get_change_request_payload(change_request.change_request_id, session)
            if payload_obj and hasattr(payload_obj, "change_payload") and payload_obj.change_payload:
                items = (payload_obj.change_payload or {}).get("items", [])
                for item in items:
                    fname = item.get("field_name")
                    if fname == "production_season":
                        new_season = item.get("new_value")
                    elif fname == "crop_year":
                        new_year = item.get("new_value")
        except Exception:
            pass

        sub_id = getattr(change_request, "submission_id", None)
        if sub_id and (not new_season or not new_year):
            from sqlalchemy import text
            res = await session.execute(
                text("SELECT production_season, crop_year FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id"),
                {"sub_id": sub_id}
            )
            row = res.fetchone()
            if row:
                if not new_season:
                    new_season = row[0]
                if not new_year:
                    new_year = row[1]

        if new_season and record.production_season:
            p_clean = str(record.production_season).replace("CROP_SEASON_", "").strip().upper()
            n_clean = str(new_season).replace("CROP_SEASON_", "").strip().upper()
            if p_clean != n_clean:
                validation_error(
                    f"Production Season cannot be changed for an existing record via Change Request. (Existing: '{record.production_season}', Attempted: '{new_season}')"
                )

        if new_year and record.crop_year:
            if str(new_year).strip() != str(record.crop_year).strip():
                validation_error(
                    f"Crop Year cannot be changed for an existing record via Change Request. (Existing: '{record.crop_year}', Attempted: '{new_year}')"
                )

    async def _check_matching_season_across_sections(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Check that Season in Planning, Cultivation, Cluster, Sowing, and Production match Farmer Identity production_season."""
        from sqlalchemy import text
        from ..models import G2PRegisterCropSown

        rec_id = change_request.internal_record_id
        if not rec_id:
            return

        record = await session.get(G2PRegisterCropSown, rec_id)
        header_season = getattr(record, "production_season", None) if record else None
        sub_id = getattr(change_request, "submission_id", None)
        if not header_season and sub_id:
            res_sub = await session.execute(
                text("SELECT production_season FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND production_season IS NOT NULL"),
                {"sub_id": sub_id}
            )
            row_sub = res_sub.fetchone()
            if row_sub:
                header_season = row_sub[0]

        if not header_season:
            return

        header_clean = str(header_season).replace("CROP_SEASON_", "").strip().upper()

        section_tables = [
            ("g2p_register_plannings", "g2p_intake_form_plannings", "Crop Planning"),
            ("g2p_register_cultivations", "g2p_intake_form_cultivations", "Cultivation / Land Preparation"),
            ("g2p_register_cultivation_clusters", "g2p_intake_form_cultivation_clusters", "Cultivation Cluster"),
            ("g2p_register_clusters", "g2p_intake_form_clusters", "Cluster Information"),
            ("g2p_register_sowings", "g2p_intake_form_sowings", "Sowing"),
            ("g2p_register_productions", "g2p_intake_form_productions", "Production"),
        ]

        for reg_tbl, intake_tbl, section_name in section_tables:
            season_col = "COALESCE(season, cluster_season)" if "sowing" in reg_tbl else "season"
            res_reg = await session.execute(
                text(f"SELECT {season_col} FROM {reg_tbl} WHERE link_internal_record_id = :rec_id AND record_status = 'ACTIVE' AND {season_col} IS NOT NULL"),
                {"rec_id": rec_id}
            )
            for r in res_reg.fetchall():
                if r[0]:
                    r_clean = str(r[0]).replace("CROP_SEASON_", "").strip().upper()
                    if r_clean != header_clean:
                        validation_error(
                            f"Season '{r[0]}' in {section_name} does not match the Crop Season '{header_season}' specified in Farmer Identity."
                        )

            if sub_id:
                intake_season_col = "COALESCE(season, cluster_season)" if "sowing" in intake_tbl else "season"
                res_int = await session.execute(
                    text(f"SELECT {intake_season_col} FROM {intake_tbl} WHERE submission_id = :sub_id AND {intake_season_col} IS NOT NULL"),
                    {"sub_id": sub_id}
                )
                for r in res_int.fetchall():
                    if r[0]:
                        r_clean = str(r[0]).replace("CROP_SEASON_", "").strip().upper()
                        if r_clean != header_clean:
                            validation_error(
                                f"Season '{r[0]}' in {section_name} does not match the Crop Season '{header_season}' specified in Farmer Identity."
                            )

    async def _check_section_dates_validity(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Enforce date-in-season and stage chronology constraints before approval."""
        from .g2p_register_domain_service_cultivation import G2PRegisterDomainServiceCultivation
        from .g2p_register_domain_service_sowing import G2PRegisterDomainServiceSowing
        from .g2p_register_domain_service_harvest import G2PRegisterDomainServiceHarvest

        rec_id = change_request.internal_record_id
        if not rec_id:
            return

        from sqlalchemy import text

        # Load pending CR payloads if available
        cr_payload_items = []
        try:
            from openg2p_registry_core.services.g2p_register_change_request_service import G2PRegisterChangeRequestService
            cr_service = G2PRegisterChangeRequestService.get_component() or G2PRegisterChangeRequestService()
            payload_obj = await cr_service._get_change_request_payload(change_request.change_request_id, session)
            if payload_obj and payload_obj.change_payload:
                cr_payload_items = payload_obj.change_payload
        except Exception as e:
            _logger.debug("Failed loading CR payload in _check_section_dates_validity: %s", e)

        # Cultivation check
        res_c = await session.execute(
            text("SELECT land_id, actual_cultivation_date, start_gc, end_gc, season, commodity, actual_crop_area FROM g2p_register_cultivations WHERE link_internal_record_id = :rec_id AND record_status = 'ACTIVE'"),
            {"rec_id": rec_id}
        )
        c_records = [
            {
                "link_internal_record_id": rec_id,
                "land_id": row[0],
                "actual_cultivation_date": row[1],
                "start_gc": row[2],
                "end_gc": row[3],
                "season": row[4],
                "commodity": row[5],
                "actual_crop_area": row[6],
            }
            for row in res_c.fetchall()
        ]
        if change_request.section_id == 'cropsown_cultivation_details_section_01' and cr_payload_items:
            for item in cr_payload_items:
                if isinstance(item, dict):
                    rec = dict(item)
                    rec["link_internal_record_id"] = rec_id
                    c_records.append(rec)
        if c_records:
            await G2PRegisterDomainServiceCultivation().validate_domain_attributes(c_records, session=session)

        # Sowing check
        res_s = await session.execute(
            text("SELECT land_id, sowing_date, NULL AS start_gc, NULL AS end_gc, COALESCE(season, cluster_season) AS season, commodity, COALESCE(area_sown, cluster_area_sown) AS area_sown FROM g2p_register_sowings WHERE link_internal_record_id = :rec_id AND record_status = 'ACTIVE'"),
            {"rec_id": rec_id}
        )
        s_records = [
            {
                "link_internal_record_id": rec_id,
                "land_id": row[0],
                "sowing_date": row[1],
                "start_gc": row[2],
                "end_gc": row[3],
                "season": row[4],
                "commodity": row[5],
                "area_sown": row[6],
            }
            for row in res_s.fetchall()
        ]
        if change_request.section_id == 'cropsown_sowing_details_section_01' and cr_payload_items:
            for item in cr_payload_items:
                if isinstance(item, dict):
                    rec = dict(item)
                    rec["link_internal_record_id"] = rec_id
                    s_records.append(rec)
        if s_records:
            await G2PRegisterDomainServiceSowing().validate_domain_attributes(s_records, session=session)

        # Harvest check
        res_h = await session.execute(
            text("SELECT land_id, harvest_date, NULL AS start_gc, NULL AS end_gc, NULL AS season, NULL AS commodity, area_harvested FROM g2p_register_harvests WHERE link_internal_record_id = :rec_id AND record_status = 'ACTIVE'"),
            {"rec_id": rec_id}
        )
        h_records = [
            {
                "link_internal_record_id": rec_id,
                "land_id": row[0],
                "harvest_date": row[1],
                "start_gc": row[2],
                "end_gc": row[3],
                "season": row[4],
                "commodity": row[5],
                "area_harvested": row[6],
            }
            for row in res_h.fetchall()
        ]
        if change_request.section_id == 'cropsown_harvest_details_section_01' and cr_payload_items:
            for item in cr_payload_items:
                if isinstance(item, dict):
                    rec = dict(item)
                    rec["link_internal_record_id"] = rec_id
                    h_records.append(rec)
        if h_records:
            await G2PRegisterDomainServiceHarvest().validate_domain_attributes(h_records, session=session)

    async def _adopt_uploaded_photo(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Promote a photo uploaded on the form to the record's avatar.

        A file widget stores its upload as a *section document* — the submit
        flow posts `documents: [{document_id, label}]` and never touches
        `record_image_document_id`, which is the field the profile widget and
        the record tree read. So an uploaded photo is saved but invisible.

        This bridges the two: on approval, if the record has no avatar yet, the
        earliest image attached to it becomes one. Records that already have an
        avatar are left alone, so re-approving cannot swap someone's photo.
        """
        from openg2p_registry_core.models import (
            G2PRegisterSectionDocument,
            G2PRegistryDocument,
        )

        from ..models import G2PRegisterCropSown

        record = await session.get(G2PRegisterCropSown, change_request.internal_record_id)
        if record is None or record.record_image_document_id:
            return

        row = (
            await session.execute(
                select(G2PRegistryDocument)
                .join(
                    G2PRegisterSectionDocument,
                    G2PRegisterSectionDocument.document_id
                    == G2PRegistryDocument.document_id,
                )
                .where(
                    G2PRegisterSectionDocument.internal_record_id
                    == change_request.internal_record_id
                )
                .where(G2PRegistryDocument.bucket == "documents")
                .order_by(G2PRegistryDocument.created_at)
            )
        ).scalars().first()

        if row is not None and self._is_image(row.source_filename):
            record.record_image_document_id = row.document_id

    @staticmethod
    def _is_image(filename: str | None) -> bool:
        return str(filename or "").lower().endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif")
        )

    async def _sync_address_names_on_record(self, record: dict, session=None) -> None:
        """Prepare an intake/CR payload without writing any approved live row.

        Native geographic widgets carry canonical Master Data IDs. Existing
        assignments require a reviewed migration; never manufacture upper-case
        aliases or query Master Data's database from a registry extension.
        """
        import json
        if not isinstance(record, dict):
            return
        hierarchy = record.get("geo_code_hierarchy_json")
        if isinstance(hierarchy, str):
            try:
                hierarchy = json.loads(hierarchy)
            except (ValueError, TypeError):
                return
        if not isinstance(hierarchy, dict):
            return
        for item in hierarchy.get("hierarchy", []):
            if not isinstance(item, dict):
                continue
            level = str(item.get("level_mnemonic") or "").lower()
            if level not in {"region", "zone", "woreda", "kebele"}:
                continue
            value_id = item.get("level_value_id")
            if value_id:
                record[level] = value_id
            label = item.get("level_value_mnemonic") or item.get("display_name")
            if label:
                record[f"{level}_name"] = label


    async def _refresh_admin_names(self, change_request, session) -> None:
        """Copy approved hierarchy display labels without cross-database reads.

        This does not resolve approvers or change geographic identifiers. The
        geographic policy resolver remains a separate promotion requirement.
        """
        from ..models import G2PRegisterCropSown
        record = await session.get(G2PRegisterCropSown, change_request.internal_record_id)
        if record is None:
            return
        proposed = {"geo_code_hierarchy_json": record.geo_code_hierarchy_json}
        await self._sync_address_names_on_record(proposed)
        for level in ("region", "zone", "woreda", "kebele"):
            if proposed.get(level) == getattr(record, level, None):
                label = proposed.get(f"{level}_name")
                if label:
                    setattr(record, f"{level}_name", label)



    async def _check_unique_farmer_per_year(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Odoo: `_check_unique_farmer_id` — one registration per farmer per
        crop year."""
        from ..models import G2PRegisterCropSown

        record = await session.get(G2PRegisterCropSown, change_request.internal_record_id)
        if record is None or not record.farmer_id or not record.crop_year:
            return

        exclude_ids = [record.internal_record_id]
        if getattr(change_request, "subject_internal_record_id", None):
            exclude_ids.append(change_request.subject_internal_record_id)

        clash = (
            await session.execute(
                select(func.count())
                .select_from(G2PRegisterCropSown)
                .where(
                    G2PRegisterCropSown.farmer_id == record.farmer_id,
                    G2PRegisterCropSown.crop_year == record.crop_year,
                    G2PRegisterCropSown.internal_record_id.notin_(exclude_ids),
                    G2PRegisterCropSown.record_status == "ACTIVE",
                )
            )
        ).scalar_one()
        if clash:
            validation_error(
                f"Farmer {record.farmer_id} already has a crop sown record for "
                f"{record.crop_year}"
            )

    async def _check_crop_area_within_plot(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Odoo: `_check_land_area_allocation` / `_check_actual_crop_area_limits`.

        The crop area planned or worked on a plot may not exceed that plot's
        total area. Each line carries its own land_id and land_area, so the
        check groups a record's lines by plot and compares the sum against the
        area declared for it.
        """
        from ..models import (
            G2PRegisterCultivation, G2PRegisterPlanning, G2PRegisterSowing,
        )

        checks = [
            (G2PRegisterPlanning, "planned_area", "planned"),
            (G2PRegisterCultivation, "actual_crop_area", "cultivated"),
            (G2PRegisterSowing, "area_sown", "sown"),
        ]
        for model, area_field, label in checks:
            rows = (
                await session.execute(
                    select(model).where(
                        model.link_internal_record_id == change_request.internal_record_id,
                        model.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()

            by_plot: dict[str, list] = {}
            for row in rows:
                key = getattr(row, "land_id", None)
                if key:
                    by_plot.setdefault(key, []).append(row)

            for plot, plot_rows in by_plot.items():
                total = sum(as_float(getattr(r, area_field, None)) or 0.0 for r in plot_rows)
                plot_area = next(
                    (as_float(getattr(r, "land_area", None)) for r in plot_rows if getattr(r, "land_area", None)), None
                )
                if plot_area and total > plot_area + 1e-6:
                    validation_error(
                        f"Total {label} area on plot {plot} is {total:g} ha, which exceeds "
                        f"its registered area of {plot_area:g} ha"
                    )

    async def _check_matching_land_id_across_sections(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Check that Land IDs in Cluster Information sections match a Land ID specified in Crop Planning."""
        from ..models import (
            G2PRegisterPlanning, G2PRegisterCluster, G2PRegisterCultivationCluster, G2PRegisterCultivation,
            G2PIntakeFormPlanning, G2PIntakeFormCluster, G2PIntakeFormCultivationCluster, G2PIntakeFormCultivation,
            G2PRegisterSowing, G2PIntakeFormSowing, G2PRegisterInfestation, G2PIntakeFormInfestation,
            G2PRegisterHarvest, G2PIntakeFormHarvest,
        )

        planning_land_ids = set()
        cultivation_land_ids = set()

        planning_rows = (
            await session.execute(
                select(G2PRegisterPlanning).where(
                    G2PRegisterPlanning.link_internal_record_id == change_request.internal_record_id,
                    G2PRegisterPlanning.record_status == "ACTIVE",
                )
            )
        ).scalars().all()
        for r in planning_rows:
            lid = getattr(r, "land_id", None)
            if lid and str(lid).strip():
                planning_land_ids.add(str(lid).strip())

        cultivation_rows = (
            await session.execute(
                select(G2PRegisterCultivation).where(
                    G2PRegisterCultivation.link_internal_record_id == change_request.internal_record_id,
                    G2PRegisterCultivation.record_status == "ACTIVE",
                )
            )
        ).scalars().all()
        for r in cultivation_rows:
            lid = getattr(r, "land_id", None)
            if lid and str(lid).strip():
                cultivation_land_ids.add(str(lid).strip())

        sub_id = getattr(change_request, "submission_id", None)
        if sub_id:
            intake_planning = (
                await session.execute(
                    select(G2PIntakeFormPlanning).where(
                        G2PIntakeFormPlanning.submission_id == sub_id
                    )
                )
            ).scalars().all()
            for ip in intake_planning:
                lid = getattr(ip, "land_id", None)
                if lid and str(lid).strip():
                    planning_land_ids.add(str(lid).strip())

            intake_cultivation = (
                await session.execute(
                    select(G2PIntakeFormCultivation).where(
                        G2PIntakeFormCultivation.submission_id == sub_id
                    )
                )
            ).scalars().all()
            for ip in intake_cultivation:
                lid = getattr(ip, "land_id", None)
                if lid and str(lid).strip():
                    cultivation_land_ids.add(str(lid).strip())

        if planning_land_ids:
            cluster_rows = []
            c_rows = (
                await session.execute(
                    select(G2PRegisterCluster).where(
                        G2PRegisterCluster.link_internal_record_id == change_request.internal_record_id,
                        G2PRegisterCluster.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()
            cluster_rows.extend(c_rows)

            if sub_id:
                ic_rows = (
                    await session.execute(
                        select(G2PIntakeFormCluster).where(
                            G2PIntakeFormCluster.submission_id == sub_id
                        )
                    )
                ).scalars().all()
                cluster_rows.extend(ic_rows)

            for c in cluster_rows:
                lid = getattr(c, "land_id", None)
                if lid and str(lid).strip() and str(lid).strip() not in planning_land_ids:
                    validation_error(
                        f"Land ID '{lid}' in Cluster Information does not match any Land ID specified in Crop Planning."
                    )

        if cultivation_land_ids:
            cultivation_cluster_rows = []
            c_rows = (
                await session.execute(
                    select(G2PRegisterCultivationCluster).where(
                        G2PRegisterCultivationCluster.link_internal_record_id == change_request.internal_record_id,
                        G2PRegisterCultivationCluster.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()
            cultivation_cluster_rows.extend(c_rows)

            if sub_id:
                ic_rows = (
                    await session.execute(
                        select(G2PIntakeFormCultivationCluster).where(
                            G2PIntakeFormCultivationCluster.submission_id == sub_id
                        )
                    )
                ).scalars().all()
                cultivation_cluster_rows.extend(ic_rows)

            for c in cultivation_cluster_rows:
                lid = getattr(c, "land_id", None)
                if lid and str(lid).strip() and str(lid).strip() not in cultivation_land_ids:
                    validation_error(
                        f"Land ID '{lid}' in Cultivation Cluster does not match any Land ID specified in Cultivation/Land Preparation."
                    )

        if planning_land_ids:
            sowing_rows = []
            s_rows = (
                await session.execute(
                    select(G2PRegisterSowing).where(
                        G2PRegisterSowing.link_internal_record_id == change_request.internal_record_id,
                        G2PRegisterSowing.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()
            sowing_rows.extend(s_rows)

            if sub_id:
                is_rows = (
                    await session.execute(
                        select(G2PIntakeFormSowing).where(
                            G2PIntakeFormSowing.submission_id == sub_id
                        )
                    )
                ).scalars().all()
                sowing_rows.extend(is_rows)

            sowing_land_ids = set()
            for c in sowing_rows:
                lid = getattr(c, "land_id", None)
                if lid and str(lid).strip():
                    sowing_land_ids.add(str(lid).strip())
                    if str(lid).strip() not in planning_land_ids:
                        validation_error(
                            f"Land ID '{lid}' in Sowing does not match any Land ID specified in Crop Planning."
                        )

            infestation_rows = []
            i_rows = (
                await session.execute(
                    select(G2PRegisterInfestation).where(
                        G2PRegisterInfestation.link_internal_record_id == change_request.internal_record_id,
                        G2PRegisterInfestation.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()
            infestation_rows.extend(i_rows)

            if sub_id:
                ii_rows = (
                    await session.execute(
                        select(G2PIntakeFormInfestation).where(
                            G2PIntakeFormInfestation.submission_id == sub_id
                        )
                    )
                ).scalars().all()
                infestation_rows.extend(ii_rows)

            # 3. Validate Infestation Land IDs against the collected Sowing Land IDs
            for c in infestation_rows:
                lid = getattr(c, "land_id", None)
                if lid and str(lid).strip() and str(lid).strip() not in sowing_land_ids:
                    validation_error(
                        f"Land ID '{lid}' in Pest/Disease Infestation does not match any Land ID specified in Sowing."
                    )

            harvest_rows = []
            h_rows = (
                await session.execute(
                    select(G2PRegisterHarvest).where(
                        G2PRegisterHarvest.link_internal_record_id == change_request.internal_record_id,
                        G2PRegisterHarvest.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()
            harvest_rows.extend(h_rows)

            if sub_id:
                ih_rows = (
                    await session.execute(
                        select(G2PIntakeFormHarvest).where(
                            G2PIntakeFormHarvest.submission_id == sub_id
                        )
                    )
                ).scalars().all()
                harvest_rows.extend(ih_rows)

            valid_harvest_land_ids = sowing_land_ids or planning_land_ids
            for c in harvest_rows:
                lid = getattr(c, "land_id", None)
                if lid and str(lid).strip() and str(lid).strip() not in valid_harvest_land_ids:
                    validation_error(
                        f"Land ID '{lid}' in Harvest does not match any Land ID specified in Sowing or Crop Planning."
                    )

    async def _recompute_ec_dates_across_sections(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> None:
        """Ensure all GC dates on active and intake records carry their computed EC date."""
        from .domain_compute_utils import compute_ec_date
        from ..models import (
            G2PRegisterPlanning, G2PIntakeFormPlanning,
            G2PRegisterCluster, G2PIntakeFormCluster,
            G2PRegisterCultivation, G2PIntakeFormCultivation,
            G2PRegisterSowing, G2PIntakeFormSowing,
            G2PRegisterInfestation, G2PIntakeFormInfestation,
            G2PRegisterHarvest, G2PIntakeFormHarvest,
        )

        mappings = [
            (G2PRegisterPlanning, G2PIntakeFormPlanning, "planned_date", "planned_date_ec"),
            (G2PRegisterCluster, G2PIntakeFormCluster, "establishing_date", "establishing_date_ec"),
            (G2PRegisterCultivation, G2PIntakeFormCultivation, "actual_cultivation_date", "actual_cultivation_date_ec"),
            (G2PRegisterSowing, G2PIntakeFormSowing, "sowing_date", "sowing_date_ec"),
            (G2PRegisterInfestation, G2PIntakeFormInfestation, "observation_date", "observation_date_ec"),
            (G2PRegisterHarvest, G2PIntakeFormHarvest, "harvest_date", "harvest_date_ec"),
        ]

        sub_id = getattr(change_request, "submission_id", None)

        for reg_model, intake_model, gc_field, ec_field in mappings:
            rows = (
                await session.execute(
                    select(reg_model).where(
                        or_(
                            reg_model.link_internal_record_id == change_request.internal_record_id,
                            reg_model.internal_record_id == change_request.internal_record_id,
                        ),
                        reg_model.record_status == "ACTIVE",
                    )
                )
            ).scalars().all()

            if sub_id:
                i_rows = (
                    await session.execute(
                        select(intake_model).where(
                            intake_model.submission_id == sub_id
                        )
                    )
                ).scalars().all()
                rows.extend(i_rows)

            for row in rows:
                compute_ec_date(row, gc_field, ec_field)

    # ── Lifecycle ───────────────────────────────────────────────────────────
    # AWE owns the approval decision. `post_approve` runs once a change request
    # is approved and updates the lifecycle_stage on the root record.

    async def post_approve(self, change_request: G2PRegisterChangeRequest, session: AsyncSession):
        from ..models import G2PRegisterCropSown

        record = await session.get(G2PRegisterCropSown, change_request.internal_record_id)
        if record is None:
            return

        section_mnemonic = await self._resolve_section_mnemonic(change_request, session)
        from .farmer_name_projection import refresh_crop_name_on_ingest
        await refresh_crop_name_on_ingest(session, record)
        new_stage = _SECTION_LIFECYCLE_STAGE_MAP.get(section_mnemonic)
        if new_stage:
            record.lifecycle_stage = new_stage
        else:
            _logger.warning(
                "crop sown %s: section %s approved but has no lifecycle_stage mapping — "
                "lifecycle_stage unchanged (%s)",
                record.internal_record_id, section_mnemonic, record.lifecycle_stage,
            )
        record.rejection_reason = None
        await self._recompute_ec_dates_across_sections(change_request, session)
        _logger.info(
            "crop sown %s: section %s approved, lifecycle_stage -> %s",
            record.internal_record_id, section_mnemonic, record.lifecycle_stage,
        )

    async def _resolve_section_mnemonic(
        self, change_request: G2PRegisterChangeRequest, session: AsyncSession
    ) -> str:
        from openg2p_registry_core.models import G2PRegisterSection

        if not change_request.section_id:
            return ""
        section = await session.get(G2PRegisterSection, change_request.section_id)
        return getattr(section, "section_mnemonic", "") or ""
