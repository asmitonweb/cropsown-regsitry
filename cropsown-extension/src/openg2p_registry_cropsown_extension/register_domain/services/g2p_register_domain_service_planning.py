import logging
from datetime import date

from openg2p_registry_core.services import G2PRegisterDomainService

from .domain_compute_utils import (
    compute_season_parts,
    is_date_in_season,
    compute_ec_date
)

from .domain_validation_utils import as_float, parse_date, validation_error

_logger = logging.getLogger("g2p-register-domain-service")


class G2PRegisterDomainServicePlanning(G2PRegisterDomainService):
    async def validate_domain_attributes(self, records: list[dict], session=None, **kwargs):
        from .field_rules import validate_record_fields
        await validate_record_fields(records, table=True)
        _logger.warning("VALIDATION RECORDS PAYLOAD: %s", records)
        from .domain_validation_utils import is_record_deleted
        active_records = [r for r in records if not is_record_deleted(r)]
        _logger.warning("ACTIVE RECORDS PAYLOAD: %s", active_records)
        for record in active_records:

            from .domain_validation_utils import validate_alphabetical_name, validate_mobile_number
            validate_alphabetical_name(record.get("farmer_name"), "Farmer Name")
            validate_alphabetical_name(record.get("da_name"), "DA Name")
            validate_alphabetical_name(record.get("supervisor_name"), "Supervisor Name")
            validate_alphabetical_name(record.get("local_name"), "Local Name")
            validate_alphabetical_name(record.get("scientific_name"), "Scientific Name")
            validate_mobile_number(record.get("da_mobile_number"), "DA Mobile Number")
            validate_mobile_number(record.get("supervisor_mobile_number"), "Supervisor Mobile Number")
            if not str(record.get("land_id") or "").strip():
                validation_error("Land ID is required in Crop Planning.")
            if not str(record.get("season") or "").strip():
                validation_error("Season is required in Crop Planning.")
            if not str(record.get("commodity") or "").strip():
                validation_error("Crop is required in Crop Planning.")

            from .domain_validation_utils import resolve_production_season
            prod_season = await resolve_production_season(record, session)

            season = record.get("season")
            if prod_season and season:
                p_clean = str(prod_season).replace("CROP_SEASON_", "").strip().upper()
                s_clean = str(season).replace("CROP_SEASON_", "").strip().upper()
                if p_clean != s_clean:
                    validation_error(
                        f"Season '{season}' in Crop Planning Details does not match the Production Season '{prod_season}' specified in Farmer Identity."
                    )


            compute_season_parts(record)
            self._validate_date_in_season(record, "planned_date")
            self._validate_planned_area(record)
            self._validate_planned_date(record)
            compute_ec_date(record, "planned_date", "planned_date_ec")
        self._validate_cumulative_planned_area(active_records)
        self._validate_no_duplicate_commodity_season(active_records)

    def _validate_planned_area(self, record: dict) -> None:
        planned_area = as_float(record.get("planned_area"))
        if planned_area is not None and planned_area <= 0:
            validation_error("planned_area must be greater than zero when provided")

        land_area = as_float(record.get("land_area"))
        if planned_area is not None and land_area is not None and land_area > 0:
            if planned_area > land_area:
                validation_error(f"Planned Crop Area ({planned_area} ha) cannot be greater than Total Land Area ({land_area} ha).")

    def _validate_cumulative_planned_area(self, records: list[dict]) -> None:
        by_land: dict[str, list[dict]] = {}
        for record in records:
            land_id = str(record.get("land_id") or "").strip()
            if land_id:
                by_land.setdefault(land_id, []).append(record)

        for land_id, land_records in by_land.items():
            total_planned = sum(as_float(r.get("planned_area")) or 0.0 for r in land_records)
            land_area = next((as_float(r.get("land_area")) for r in land_records if as_float(r.get("land_area")) is not None and as_float(r.get("land_area")) > 0), None)
            if land_area is not None and land_area > 0 and total_planned > land_area + 1e-6:
                validation_error(
                    f"Total Planned Crop Area ({total_planned:g} ha) across all records for Land ID '{land_id}' "
                    f"exceeds Total Land Area ({land_area:g} ha)."
                )

    def _validate_planned_date(self, record: dict) -> None:
        planned_date = parse_date(record.get("planned_date"))
        if planned_date is not None and planned_date.year > date.today().year + 1:
            validation_error("planned_date must not be more than one season ahead")

    def _validate_no_duplicate_commodity_season(self, records: list[dict]) -> None:
        seen: set[tuple[str, str, str]] = set()
        for record in records:
            commodity = str(record.get("commodity") or "").strip()
            season = str(record.get("season") or "").strip()
            land_id = str(record.get("land_id") or "").strip()
            if not commodity:
                continue
            key = (commodity, season, land_id)
            if key in seen:
                validation_error(
                    "Duplicate commodity entries for the same season and land are not allowed"
                )
            seen.add(key)

    def _validate_date_in_season(self, record: dict, field: str) -> None:
        """Odoo: `_check_season_crop_required` — the date must fall inside the
        season window carried on the record."""
        value = parse_date(record.get(field))
        if value is None:
            return
        if not is_date_in_season(value, record.get("start_month"), record.get("start_day"),
                                 record.get("end_month"), record.get("end_day")):
            validation_error(f"{field} falls outside the season window on this record")

    def construct_search_text(self, payload: dict, extra: list[str] = None) -> str:
        _logger.info("Constructing search text for planning")

        keys = [
            "functional_record_id",
            "land_id",
            "season",
            "commodity",
            "crop_variety",
            "crop_category",
            "local_name",
            "scientific_name",
            "plot_category",
            "cropping_system",
            "planned_area",
            "seed_class",
            "seed_source",
            "seed_variety",
            "planned_fertilizer_type",
            "water_source",
            "water_source_method",
            "water_source_frequency",
            "cluster_status",
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
        _logger.info("Constructing record name for planning")

        keys = ["commodity", "season", "planned_area"]
        record_name = []
        if extra:
            record_name.extend(str(item).strip() for item in extra if str(item).strip())
        record_name.extend(
            str(payload.get(key) or "").strip()
            for key in keys
            if str(payload.get(key) or "").strip()
        )

        return " ".join(record_name).strip()
