import logging

from openg2p_registry_core.services import G2PRegisterDomainService

from .domain_compute_utils import compute_cluster_area, compute_season_parts

from .domain_validation_utils import as_float, as_int, validation_error

_logger = logging.getLogger("g2p-register-domain-service")


class G2PRegisterDomainServiceCluster(G2PRegisterDomainService):
    async def validate_domain_attributes(self, records: list[dict], session=None, **kwargs):
        from .field_rules import validate_record_fields
        await validate_record_fields(records, table=True)
        from .domain_validation_utils import is_record_deleted
        active_records = [r for r in records if not is_record_deleted(r)]
        for record in active_records:

            from .domain_validation_utils import validate_alphabetical_name, validate_mobile_number
            validate_alphabetical_name(record.get("farmer_name"), "Farmer Name")
            validate_alphabetical_name(record.get("da_name"), "DA Name")
            validate_alphabetical_name(record.get("supervisor_name"), "Supervisor Name")
            validate_mobile_number(record.get("da_mobile_number"), "DA Mobile Number")
            validate_mobile_number(record.get("supervisor_mobile_number"), "Supervisor Mobile Number")
            from .domain_validation_utils import resolve_production_season
            prod_season = await resolve_production_season(record, session)
            if prod_season and not record.get("production_season"):
                record["production_season"] = prod_season

            season = record.get("season")
            if prod_season and season:
                p_clean = str(prod_season).replace("CROP_SEASON_", "").strip().upper()
                s_clean = str(season).replace("CROP_SEASON_", "").strip().upper()
                if p_clean != s_clean:
                    validation_error(
                        f"Season '{season}' in Cluster Information Details does not match the Production Season '{prod_season}' specified in Farmer Identity."
                    )

            from .domain_compute_utils import compute_cluster_area, compute_season_parts, compute_ec_date
            compute_ec_date(record, "establishing_date", "establishing_date_ec")

            compute_season_parts(record)
            compute_cluster_area(record)
            self._validate_cluster_area(record)
            self._validate_smallholders(record)
            self._validate_collected_land(record)
            self._validate_cluster_plan(record)
            self._validate_collected_by_combiner(record)
            if session:
                await self._validate_land_id_matches_planning(record, session=session)
        self._validate_no_duplicate_cluster(active_records)

    def _validate_no_duplicate_cluster(self, records: list[dict]) -> None:
        seen: set[tuple[str, str, str]] = set()
        for record in records:
            cluster_name = str(
                record.get("cluster_name")
                or record.get("cluster_id")
                or record.get("primary_commodity")
                or record.get("commodity")
                or ""
            ).strip()
            season = str(record.get("season") or record.get("production_season") or "").strip()
            land_id = str(record.get("land_id") or "").strip()
            if not cluster_name and not land_id:
                continue
            key = (cluster_name, season, land_id)
            if key in seen:
                validation_error(
                    "Duplicate cluster entries for the same season and land are not allowed"
                )
            seen.add(key)

    _validate_no_duplicate_commodity_season = _validate_no_duplicate_cluster

    def _validate_cluster_area(self, record: dict) -> None:
        area = as_float(record.get("cluster_area_hectare"))
        if area is not None and area < 0:
            validation_error("cluster_area_hectare must not be negative")

    def _validate_smallholders(self, record: dict) -> None:
        for field in ("number_of_smallholders",):
            value = as_int(record.get(field))
            if value is not None and value < 0:
                validation_error(f"{field} must not be negative")

    def _validate_collected_land(self, record: dict) -> None:
        area = as_float(record.get("cluster_area_hectare"))
        collected = as_float(record.get("collected_land"))
        if area is not None and collected is not None and collected > area:
            validation_error("collected_land must not exceed cluster_area_hectare")

    def _validate_cluster_plan(self, record: dict) -> None:
        timad = as_float(record.get("cluster_area_timad"))
        area = as_float(record.get("cluster_area_hectare"))
        plan = as_float(record.get("cluster_plan"))
        if area is not None and plan is not None and plan > area:
            validation_error(f"Plan Area (ha) ({plan}) must not exceed Total Cultivated Area ({area} ha, calculated from {timad} Timad)")

    def _validate_collected_by_combiner(self, record: dict) -> None:
        plan = as_float(record.get("cluster_plan"))
        combiner = as_float(record.get("collected_by_combiner"))
        if plan is not None and combiner is not None and combiner > plan:
            validation_error(f"Collected by Combiner (ha) ({combiner}) must not exceed Plan Area ({plan} ha)")

    async def _validate_land_id_matches_planning(self, record: dict, session) -> None:
        cluster_land_id = record.get("land_id")
        if not cluster_land_id or not str(cluster_land_id).strip():
            return

        submission_id = record.get("submission_id")
        link_internal_record_id = record.get("link_internal_record_id")

        if not submission_id and not link_internal_record_id:
            return

        from sqlalchemy import text

        planning_land_ids = set()
        total_planned_area = 0.0
        max_land_area = 0.0

        # 1. Fetch Land IDs and Areas saved in intake form plannings for this submission
        if submission_id:
            res = await session.execute(
                text("SELECT land_id, land_area, planned_area FROM g2p_intake_form_plannings WHERE submission_id = :sub_id"),
                {"sub_id": submission_id}
            )
            for row in res.fetchall():
                l_id = str(row[0]).strip() if row[0] else ""
                if l_id:
                    planning_land_ids.add(l_id)
                    if l_id == str(cluster_land_id).strip():
                        if row[1] is not None:
                            max_land_area = max(max_land_area, as_float(row[1]) or 0.0)
                        if row[2] is not None:
                            total_planned_area += (as_float(row[2]) or 0.0)

        # 2. Fetch Land IDs and Areas from active registered plannings (if existing record)
        if link_internal_record_id:
            res = await session.execute(
                text("SELECT land_id, land_area, planned_area FROM g2p_register_plannings WHERE link_internal_record_id = :link_id AND record_status = 'ACTIVE'"),
                {"link_id": link_internal_record_id}
            )
            for row in res.fetchall():
                l_id = str(row[0]).strip() if row[0] else ""
                if l_id:
                    planning_land_ids.add(l_id)
                    if l_id == str(cluster_land_id).strip():
                        if row[1] is not None:
                            max_land_area = max(max_land_area, as_float(row[1]) or 0.0)
                        if row[2] is not None:
                            total_planned_area += (as_float(row[2]) or 0.0)

        # 3. Validate match (if planning records exist)
        if planning_land_ids and str(cluster_land_id).strip() not in planning_land_ids:
            validation_error(
                f"Land ID '{cluster_land_id}' in Cluster Information does not match any Land ID specified in Crop Planning ({', '.join(planning_land_ids)})."
            )

        # If max_land_area was not provided in planning, fall back to land_area on cluster record
        if max_land_area == 0.0:
            max_land_area = as_float(record.get("land_area")) or 0.0

        # Calculate remaining available land area for cluster (Option A)
        remaining_land = max(0.0, max_land_area - total_planned_area) if max_land_area > 0 else 0.0

        # 4. Validate Cluster Area / Plan does not exceed remaining land area
        cluster_area = as_float(record.get("cluster_area_hectare")) or as_float(record.get("cluster_plan"))
        if cluster_area is not None and max_land_area > 0:
            if cluster_area > remaining_land + 1e-6:
                if total_planned_area > 0:
                    validation_error(
                        f"Cluster Area ({cluster_area:g} ha) for Land ID '{cluster_land_id}' exceeds the remaining available land area ({remaining_land:g} ha). "
                        f"Total Land Area is {max_land_area:g} ha and {total_planned_area:g} ha is already planned."
                    )
                else:
                    validation_error(
                        f"Cluster Area ({cluster_area:g} ha) for Land ID '{cluster_land_id}' exceeds Total Land Area ({max_land_area:g} ha)."
                    )

    def construct_search_text(self, payload: dict, extra: list[str] = None) -> str:
        _logger.info("Constructing search text for cluster")

        keys = [
            "functional_record_id",
            "cluster_name",
            "reference",
            "primary_commodity",
            "secondary_commodity",
            "fpc_type",
            "leader",
            "co_leader",
            "coordinator",
            "secretary",
            "agro_ecological_zone",
            "season",
            "cluster_area_hectare",
            "water_source",
            "water_source_method",
            "water_source_frequency",
            "latitude",
            "longitude",
            "region",
            "zone",
            "woreda",
            "kebele",
            "sub_kebele",
            "address_line_1",
            "address_line_2",
            "country_code",
            "da_name",
            "da_mobile_number",
            "supervisor_name",
            "supervisor_mobile_number",
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
        _logger.info("Constructing record name for cluster")

        keys = ["cluster_name", "season"]
        record_name = []
        if extra:
            record_name.extend(str(item).strip() for item in extra if str(item).strip())
        record_name.extend(
            str(payload.get(key) or "").strip()
            for key in keys
            if str(payload.get(key) or "").strip()
        )

        return " ".join(record_name).strip()
