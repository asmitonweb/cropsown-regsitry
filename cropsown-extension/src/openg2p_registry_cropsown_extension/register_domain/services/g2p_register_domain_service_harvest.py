import logging
from datetime import date

from openg2p_registry_core.services import G2PRegisterDomainService

from .domain_compute_utils import compute_harvest_yield, compute_ec_date

from .domain_validation_utils import as_float, parse_date, validation_error, get_attribute_variants

_logger = logging.getLogger("g2p-register-domain-service")


class G2PRegisterDomainServiceHarvest(G2PRegisterDomainService):
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
            if not str(record.get("land_id") or "").strip():
                validation_error("Land ID is required in Harvest Details.")
            from .domain_validation_utils import resolve_production_season
            prod_season = await resolve_production_season(record, session)
            if prod_season and not record.get("production_season"):
                record["production_season"] = prod_season

            season = record.get("season") or record.get("cluster_season")
            if prod_season and season:
                p_clean = str(prod_season).replace("CROP_SEASON_", "").strip().upper()
                s_clean = str(season).replace("CROP_SEASON_", "").strip().upper()
                if p_clean != s_clean:
                    validation_error(
                        f"Season '{season}' in Harvest Details does not match the Production Season '{prod_season}' specified in Farmer Identity."
                    )

            compute_harvest_yield(record)

            self._validate_post_harvest_loss(record)
            self._validate_disposal_quantities(record)
            compute_ec_date(record, "harvest_date", "harvest_date_ec")
            if session:
                await self._validate_existing_crop_sown_record_required(record, session=session)
                await self._validate_land_id_matches_sowing(record, session=session)
                await self._validate_harvest_after_sowing(record, session=session)
                await self._validate_harvest_area_after_sowing(record, session=session)
                # Cluster harvest (cluster_harvest_date): must fall within the cluster's
                # selected season window (start_gc..end_gc), resolved from the cluster /
                # cultivation-cluster records. Independent harvest_date is blank for cluster
                # rows and has no season window, so this only affects cluster rows; if no
                # season window is found the check is skipped (no block).
                await self._validate_date_in_season_enhanced(record, "cluster_harvest_date", session=session)
        self._validate_no_duplicate_commodity_season(active_records)

    def _validate_no_duplicate_commodity_season(self, records: list[dict]) -> None:
        seen: set[tuple[str, str, str, str]] = set()
        for record in records:
            commodity = str(
                record.get("commodity")
                or ""
            ).strip()
            cluster_name = str(
                record.get("cluster_name")
                or ""
            ).strip()
            season = str(
                record.get("season")
                or record.get("cluster_season")
                or record.get("production_season")
                or ""
            ).strip()
            land_id = str(record.get("land_id") or "").strip()
            raw_cs = record.get("cluster_status")
            if isinstance(raw_cs, (list, tuple, set)):
                cs_str = " ".join(str(item) for item in raw_cs if item).upper()
            else:
                cs_str = str(raw_cs or "").upper()
            is_clustered = "CLUSTER" in cs_str and "INDEPENDENT" not in cs_str

            if not commodity and not cluster_name and not land_id:
                continue
            item_ident = cluster_name if is_clustered else commodity
            key = (item_ident, season, land_id, "CLUSTERED" if is_clustered else "INDEPENDENT")
            if key in seen:
                if is_clustered:
                    validation_error(
                        "Duplicate cluster harvest entries for the same season, land and cluster are not allowed"
                    )
                else:
                    validation_error(
                        "Duplicate commodity entries for the same season and land are not allowed"
                    )
            seen.add(key)

    async def _validate_existing_crop_sown_record_required(self, record: dict, session) -> None:
        fayda_fan_id = record.get("fayda_fan_id") or record.get("fayda_id")
        crop_year = record.get("crop_year")
        prod_season = record.get("production_season")
        submission_id = record.get("submission_id")

        if session and submission_id:
            if not fayda_fan_id or not crop_year or not prod_season:
                from sqlalchemy import text
                res_hdr = await session.execute(
                    text("SELECT fayda_fan_id, crop_year, production_season FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id"),
                    {"sub_id": submission_id}
                )
                row_hdr = res_hdr.fetchone()
                if row_hdr:
                    fayda_fan_id = fayda_fan_id or row_hdr[0]
                    crop_year = crop_year or row_hdr[1]
                    prod_season = prod_season or row_hdr[2]

        if not fayda_fan_id:
            return

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
                f"Harvesting cannot create a new Crop Sown record. Please ensure a Cultivation/Sowing/Crop Sown record already exists for this Farmer Identity."
            )

    async def _validate_harvest_area_after_sowing(self, record: dict, session) -> None:
        area_harvested = as_float(record.get("area_harvested") if record.get("area_harvested") is not None else record.get("cluster_area_harvested"))
        if area_harvested is None or area_harvested <= 0:
            return

        land_id = str(record.get("land_id") or "").strip()
        submission_id = record.get("submission_id")
        link_internal_record_id = record.get("link_internal_record_id")
        season = record.get("season")

        from sqlalchemy import text
        from .domain_validation_utils import get_attribute_variants

        season_vars = get_attribute_variants(season, "CROP_SEASON")

        max_allowed_area = None
        stage_name = "Area Sown in Sowing"

        # 1. Check current submission (Intake Form)
        if submission_id:
            query = "SELECT area_sown FROM g2p_intake_form_sowings WHERE submission_id = :sub_id AND area_sown IS NOT NULL"
            params = {"sub_id": submission_id}
            if land_id:
                query += " AND TRIM(land_id) = :land_id"
                params["land_id"] = land_id
            if season_vars:
                query += " AND season = ANY(:season_vars)"
                params["season_vars"] = season_vars
            res = await session.execute(text(query), params)
            row = res.fetchone()
            if row and row[0] is not None:
                max_allowed_area = float(row[0])

        # 2. Check registered sowing by link_internal_record_id + season
        if max_allowed_area is None and link_internal_record_id:
            query = "SELECT area_sown FROM g2p_register_sowings WHERE link_internal_record_id = :link_id AND record_status = 'ACTIVE' AND area_sown IS NOT NULL"
            params = {"link_id": str(link_internal_record_id)}
            if land_id:
                query += " AND TRIM(land_id) = :land_id"
                params["land_id"] = land_id
            if season_vars:
                query += " AND season = ANY(:season_vars)"
                params["season_vars"] = season_vars
            query += " ORDER BY created_at DESC"
            res = await session.execute(text(query), params)
            row = res.fetchone()
            if row and row[0] is not None:
                max_allowed_area = float(row[0])

        # 3. Check registered sowing by land_id + season
        if max_allowed_area is None and land_id:
            if season_vars:
                query_ls = "SELECT area_sown FROM g2p_register_sowings WHERE TRIM(land_id) = :land_id AND record_status = 'ACTIVE' AND area_sown IS NOT NULL AND season = ANY(:season_vars) ORDER BY created_at DESC"
                res_ls = await session.execute(text(query_ls), {"land_id": land_id, "season_vars": season_vars})
                row_ls = res_ls.fetchone()
                if row_ls and row_ls[0] is not None:
                    max_allowed_area = float(row_ls[0])

            if max_allowed_area is None:
                res_s = await session.execute(
                    text("SELECT area_sown FROM g2p_register_sowings WHERE TRIM(land_id) = :land_id AND record_status = 'ACTIVE' AND area_sown IS NOT NULL ORDER BY created_at DESC"),
                    {"land_id": land_id}
                )
                row_s = res_s.fetchone()
                if row_s and row_s[0] is not None:
                    max_allowed_area = float(row_s[0])

        # 4. Fallback to registered Cultivation area if Sowing is not found
        if max_allowed_area is None:
            stage_name = "Actual Crop Area in Cultivation"
            if link_internal_record_id:
                query_c = "SELECT actual_crop_area FROM g2p_register_cultivations WHERE link_internal_record_id = :link_id AND record_status = 'ACTIVE' AND actual_crop_area IS NOT NULL"
                p_c = {"link_id": str(link_internal_record_id)}
                if season_vars:
                    query_c += " AND season = ANY(:season_vars)"
                    p_c["season_vars"] = season_vars
                query_c += " ORDER BY created_at DESC"
                res_c = await session.execute(text(query_c), p_c)
                row_c = res_c.fetchone()
                if row_c and row_c[0] is not None:
                    max_allowed_area = float(row_c[0])

            if max_allowed_area is None and land_id:
                query_cl = "SELECT actual_crop_area FROM g2p_register_cultivations WHERE TRIM(land_id) = :land_id AND record_status = 'ACTIVE' AND actual_crop_area IS NOT NULL"
                p_cl = {"land_id": land_id}
                if season_vars:
                    query_cl += " AND season = ANY(:season_vars)"
                    p_cl["season_vars"] = season_vars
                query_cl += " ORDER BY created_at DESC"
                res_cl = await session.execute(text(query_cl), p_cl)
                row_cl = res_cl.fetchone()
                if row_cl and row_cl[0] is not None:
                    max_allowed_area = float(row_cl[0])

        # 5. Compare area_harvested against max_allowed_area
        if max_allowed_area is not None and area_harvested > max_allowed_area:
            validation_error(
                f"Area Harvested ({area_harvested} ha) cannot exceed {stage_name} ({max_allowed_area} ha)."
            )

    async def _validate_harvest_after_sowing(self, record: dict, session) -> None:
        harvest_date = parse_date(record.get("harvest_date") or record.get("cluster_harvest_date"))

        land_id = str(record.get("land_id") or "").strip()
        submission_id = record.get("submission_id")
        link_internal_record_id = record.get("link_internal_record_id")
        fayda_fan_id = record.get("fayda_fan_id")

        from sqlalchemy import text

        commodity = record.get("commodity")
        from .domain_validation_utils import get_attribute_variants
        commodity_vars = get_attribute_variants(commodity, "CROP_COMMODITY") if commodity else []

        raw_cs = record.get("cluster_status")
        if isinstance(raw_cs, (list, tuple, set)):
            cluster_status = " ".join(str(item) for item in raw_cs if item).upper()
        else:
            cluster_status = str(raw_cs or "").upper()
        if "INDEPENDENT" in cluster_status or "NON_CLUSTER" in cluster_status or "NON-CLUSTER" in cluster_status:
            is_clustered = False
        elif "CLUSTERED" in cluster_status or cluster_status == "CLUSTER" or "CLUSTER_STATUS_CLUSTERED" in cluster_status:
            is_clustered = True
        elif "CLUSTER" in cluster_status:
            is_clustered = True
        else:
            is_clustered = bool(record.get("cluster_harvest_date") is not None or (record.get("cluster_name") and not record.get("commodity")))

        prior_date = None
        prior_stage = None

        if is_clustered:
            if submission_id:
                query = "SELECT sowing_date FROM g2p_intake_form_sowings WHERE submission_id = :sub_id AND (UPPER(cluster_status::text) = 'CLUSTER' OR UPPER(cluster_status::text) LIKE '%CLUSTERED%' OR (UPPER(cluster_status::text) LIKE '%CLUSTER%' AND UPPER(cluster_status::text) NOT LIKE '%INDEPENDENT%') OR cluster_season IS NOT NULL)"
                params = {"sub_id": submission_id}
                if land_id:
                    query += " AND land_id = :land_id"
                    params["land_id"] = land_id
                res = await session.execute(text(query), params)
                row = res.fetchone()
                if row:
                    prior_stage = "Cluster Sowing Date"
                    if row[0]:
                        prior_date = parse_date(row[0])

                if not prior_stage:
                    query_cc = "SELECT start_gc FROM g2p_intake_form_cultivation_clusters WHERE submission_id = :sub_id"
                    res_cc = await session.execute(text(query_cc), params)
                    row_cc = res_cc.fetchone()
                    if row_cc:
                        prior_stage = "Cultivation Cluster Date"
                        if row_cc[0]:
                            prior_date = parse_date(row_cc[0])
        else:
            if not commodity or not str(commodity).strip():
                return

            if submission_id:
                query = "SELECT sowing_date, commodity FROM g2p_intake_form_sowings WHERE submission_id = :sub_id"
                params = {"sub_id": submission_id}
                if land_id:
                    query += " AND land_id = :land_id"
                    params["land_id"] = land_id
                res = await session.execute(text(query), params)
                for r in res.fetchall():
                    s_date, s_comm = r[0], r[1]
                    if s_comm and any(v.upper() == str(s_comm).strip().upper() for v in commodity_vars):
                        prior_stage = "Sowing Date"
                        if s_date:
                            prior_date = parse_date(s_date)
                        break

        master_ids = set()
        if submission_id:
            if not fayda_fan_id:
                res_f = await session.execute(
                    text("SELECT fayda_fan_id FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND fayda_fan_id IS NOT NULL"),
                    {"sub_id": submission_id}
                )
                f_row = res_f.fetchone()
                if f_row and f_row[0]:
                    fayda_fan_id = f_row[0]

            res_root = await session.execute(
                text("SELECT internal_record_id::text FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND internal_record_id IS NOT NULL"),
                {"sub_id": submission_id}
            )
            row_root = res_root.fetchone()
            if row_root and row_root[0]:
                master_ids.add(str(row_root[0]))

        internal_record_id = record.get("internal_record_id")
        if not prior_stage and link_internal_record_id:
            master_ids.add(str(link_internal_record_id))
        if not prior_stage and internal_record_id:
            res_root = await session.execute(
                text("SELECT internal_record_id::text FROM g2p_register_crop_sowns WHERE internal_record_id = :rec_id AND record_status = 'ACTIVE'"),
                {"rec_id": str(internal_record_id)}
            )
            if res_root.fetchone():
                master_ids.add(str(internal_record_id))
            else:
                res_child = await session.execute(
                    text("SELECT link_internal_record_id::text FROM g2p_register_harvests WHERE internal_record_id = :rec_id"),
                    {"rec_id": str(internal_record_id)}
                )
                row_c = res_child.fetchone()
                if row_c and row_c[0]:
                    master_ids.add(str(row_c[0]))

        crop_year = record.get("crop_year")
        prod_season = record.get("production_season")
        if submission_id and (not fayda_fan_id or not crop_year or not prod_season):
            res_f = await session.execute(
                text("SELECT fayda_fan_id, crop_year, production_season FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id"),
                {"sub_id": submission_id}
            )
            f_row = res_f.fetchone()
            if f_row:
                fayda_fan_id = fayda_fan_id or f_row[0]
                crop_year = crop_year or f_row[1]
                prod_season = prod_season or f_row[2]

        if not prior_stage and fayda_fan_id:
            query_m = "SELECT internal_record_id::text, crop_year, production_season FROM g2p_register_crop_sowns WHERE fayda_fan_id = :fayda AND record_status = 'ACTIVE'"
            params_m = {"fayda": str(fayda_fan_id).strip()}
            if crop_year:
                query_m += " AND crop_year = :c_year"
                params_m["c_year"] = str(crop_year).strip()
            res_m = await session.execute(text(query_m), params_m)
            for r in res_m.fetchall():
                r_id, r_year, r_season = r[0], r[1], r[2]
                if prod_season and r_season:
                    p_clean = str(prod_season).replace("CROP_SEASON_", "").strip().upper()
                    r_clean = str(r_season).replace("CROP_SEASON_", "").strip().upper()
                    if p_clean == r_clean:
                        master_ids.add(str(r_id))
                else:
                    master_ids.add(str(r_id))

        season_vars = get_attribute_variants(record.get("season"), "CROP_SEASON")
        commodity_vars = get_attribute_variants(record.get("commodity"), "CROP_COMMODITY")

        if not prior_stage and master_ids:
            query = "SELECT sowing_date FROM g2p_register_sowings WHERE link_internal_record_id = ANY(:m_ids) AND record_status = 'ACTIVE'"
            params = {"m_ids": list(master_ids)}
            if season_vars:
                query += " AND season = ANY(:season_vars)"
                params["season_vars"] = season_vars
            if commodity_vars:
                query += " AND commodity = ANY(:commodity_vars)"
                params["commodity_vars"] = commodity_vars
            if land_id:
                query += " AND land_id = :land_id"
                params["land_id"] = land_id
            query += " ORDER BY sowing_date DESC"
            res = await session.execute(text(query), params)
            for r in res.fetchall():
                prior_stage = "Sowing Date"
                if r[0]:
                    prior_date = parse_date(r[0])
                break

        if not prior_stage:
            crop_name = record.get("commodity") or ""
            crop_str = f" for Crop '{crop_name}'" if crop_name else ""
            validation_error(
                f"No Sowing record found{crop_str} on Land ID '{land_id}'. "
                f"A Sowing record is required before a harvest can be recorded."
            )
        if harvest_date and prior_date and harvest_date <= prior_date:
            validation_error(
                f"Harvest Date ({harvest_date.strftime('%Y-%m-%d')}) must be after the "
                f"{prior_stage or 'Sowing Date'} ({prior_date.strftime('%Y-%m-%d')})."
            )

    async def _validate_date_in_season_enhanced(self, record: dict, field: str, session) -> None:
        value = parse_date(record.get(field))
        if value is None:
            return

        start_gc = parse_date(record.get("start_gc"))
        end_gc = parse_date(record.get("end_gc"))

        if (not start_gc or not end_gc) and session:
            start_gc, end_gc = await self._resolve_season_bounds(record, session)

        if start_gc and value < start_gc:
            validation_error(
                f"Cluster Harvest Date ({value.strftime('%Y-%m-%d')}) is before the Season Start Date ({start_gc.strftime('%Y-%m-%d')})."
            )
        if end_gc and value > end_gc:
            validation_error(
                f"Cluster Harvest Date ({value.strftime('%Y-%m-%d')}) is after the Season End Date ({end_gc.strftime('%Y-%m-%d')})."
            )

    async def _resolve_season_bounds(self, record: dict, session) -> tuple[date | None, date | None]:
        start = parse_date(record.get("start_gc"))
        end = parse_date(record.get("end_gc"))
        if start and end:
            return start, end

        if not session:
            return start, end

        land_id = str(record.get("land_id") or "").strip()
        submission_id = record.get("submission_id")
        link_internal_record_id = record.get("link_internal_record_id")
        fayda_fan_id = record.get("fayda_fan_id")

        from sqlalchemy import text

        if submission_id:
            for tbl in ("g2p_intake_form_cultivation_clusters", "g2p_intake_form_clusters"):
                query = f"SELECT start_gc, end_gc FROM {tbl} WHERE submission_id = :sub_id"
                params = {"sub_id": submission_id}
                if land_id:
                    query += " AND land_id = :land_id"
                    params["land_id"] = land_id
                res = await session.execute(text(query), params)
                row = res.fetchone()
                if row and (row[0] or row[1]):
                    return parse_date(row[0]) if row[0] else start, parse_date(row[1]) if row[1] else end

        master_ids = set()
        if link_internal_record_id:
            master_ids.add(str(link_internal_record_id))
        if fayda_fan_id:
            res_m = await session.execute(
                text("SELECT internal_record_id::text FROM g2p_register_crop_sowns WHERE fayda_fan_id = :fayda AND record_status = 'ACTIVE'"),
                {"fayda": fayda_fan_id}
            )
            for r in res_m.fetchall():
                if r[0]:
                    master_ids.add(str(r[0]))

        if master_ids:
            for tbl in ("g2p_register_cultivation_clusters", "g2p_register_clusters"):
                query = f"SELECT start_gc, end_gc FROM {tbl} WHERE link_internal_record_id = ANY(:m_ids) AND record_status = 'ACTIVE'"
                params = {"m_ids": list(master_ids)}
                if land_id:
                    query += " AND land_id = :land_id"
                    params["land_id"] = land_id
                res = await session.execute(text(query), params)
                row = res.fetchone()
                if row and (row[0] or row[1]):
                    return parse_date(row[0]) if row[0] else start, parse_date(row[1]) if row[1] else end

        return start, end

    async def _validate_land_id_matches_sowing(self, record: dict, session) -> None:
        land_id = record.get("land_id")
        if not land_id or not str(land_id).strip():
            return

        submission_id = record.get("submission_id")
        link_internal_record_id = record.get("link_internal_record_id")
        fayda_fan_id = record.get("fayda_fan_id")

        if not submission_id and not link_internal_record_id and not fayda_fan_id:
            return

        from sqlalchemy import text

        sowing_records = []
        if submission_id:
            for tbl in ("g2p_intake_form_sowings",):
                res = await session.execute(
                    text(f"SELECT land_id, commodity, cluster_name, season, cluster_season FROM {tbl} WHERE submission_id = :sub_id"),
                    {"sub_id": submission_id}
                )
                sowing_records.extend(res.fetchall())

            if not fayda_fan_id:
                res_f = await session.execute(
                    text("SELECT fayda_fan_id FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND fayda_fan_id IS NOT NULL"),
                    {"sub_id": submission_id}
                )
                f_row = res_f.fetchone()
                if f_row and f_row[0]:
                    fayda_fan_id = f_row[0]

        master_ids = set()
        if link_internal_record_id:
            master_ids.add(str(link_internal_record_id))

        crop_year = record.get("crop_year")
        prod_season = record.get("production_season")
        if submission_id and (not fayda_fan_id or not crop_year or not prod_season):
            res_f = await session.execute(
                text("SELECT fayda_fan_id, crop_year, production_season FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id"),
                {"sub_id": submission_id}
            )
            f_row = res_f.fetchone()
            if f_row:
                fayda_fan_id = fayda_fan_id or f_row[0]
                crop_year = crop_year or f_row[1]
                prod_season = prod_season or f_row[2]

        if fayda_fan_id:
            query_m = "SELECT internal_record_id::text, crop_year, production_season FROM g2p_register_crop_sowns WHERE fayda_fan_id = :fayda AND record_status = 'ACTIVE'"
            params_m = {"fayda": str(fayda_fan_id).strip()}
            if crop_year:
                query_m += " AND crop_year = :c_year"
                params_m["c_year"] = str(crop_year).strip()
            res_m = await session.execute(text(query_m), params_m)
            for row in res_m.fetchall():
                r_id, r_year, r_season = row[0], row[1], row[2]
                if prod_season and r_season:
                    p_clean = str(prod_season).replace("CROP_SEASON_", "").strip().upper()
                    r_clean = str(r_season).replace("CROP_SEASON_", "").strip().upper()
                    if p_clean == r_clean:
                        master_ids.add(str(r_id))
                else:
                    master_ids.add(str(r_id))

        if master_ids:
            for tbl in ("g2p_register_sowings",):
                res_db = await session.execute(
                    text(f"SELECT land_id, commodity, cluster_name, season, cluster_season FROM {tbl} WHERE link_internal_record_id = ANY(:m_ids) AND record_status = 'ACTIVE'"),
                    {"m_ids": list(master_ids)}
                )
                sowing_records.extend(res_db.fetchall())

        msg_fayda = f" for Fayda ID '{fayda_fan_id}'" if fayda_fan_id else ""
        raw_cs = record.get("cluster_status")
        if isinstance(raw_cs, (list, tuple, set)):
            cs_str = " ".join(str(item) for item in raw_cs if item).upper()
        else:
            cs_str = str(raw_cs or "").upper()
        if "INDEPENDENT" in cs_str or "NON_CLUSTER" in cs_str or "NON-CLUSTER" in cs_str:
            is_clustered = False
        elif "CLUSTERED" in cs_str or cs_str == "CLUSTER" or "CLUSTER_STATUS_CLUSTERED" in cs_str:
            is_clustered = True
        elif "CLUSTER" in cs_str:
            is_clustered = True
        else:
            is_clustered = bool(record.get("cluster_harvest_date") is not None or (record.get("cluster_name") and not record.get("commodity")))

        if is_clustered:
            cluster_name = str(record.get("cluster_name") or "").strip()
            matched = False
            for s_row in sowing_records:
                if str(s_row[0] or "").strip() == land_id and str(s_row[2] or "").strip().upper() == cluster_name.upper():
                    matched = True
                    break
            if not matched:
                validation_error(f"No active Sowing record found for FPC Name '{cluster_name}' and Land ID '{land_id}'{msg_fayda}.")
        else:
            commodity = str(record.get("commodity") or "").strip()
            commodity_vars = get_attribute_variants(commodity, "CROP_COMMODITY") if commodity else []
            land_matched = False
            crop_matched = False
            for s_row in sowing_records:
                if str(s_row[0] or "").strip() == land_id:
                    land_matched = True
                    s_comm = s_row[1]
                    if s_comm and any(v.upper() == str(s_comm).strip().upper() for v in commodity_vars):
                        crop_matched = True
                        break

            if not land_matched:
                validation_error(f"Land ID '{land_id}' in Harvest does not match any Land ID specified in Sowing{msg_fayda}.")
            if not crop_matched:
                validation_error(f"No active Sowing record found for Land ID '{land_id}' and Crop '{commodity}'{msg_fayda}.")

    def _validate_post_harvest_loss(self, record: dict) -> None:
        loss_pct = as_float(record.get("post_harvest_loss_pct"))
        if loss_pct is not None and not 0 <= loss_pct <= 100:
            validation_error("post_harvest_loss_pct must be between 0 and 100")

    def _validate_disposal_quantities(self, record: dict) -> None:
        qty_harvested = as_float(record.get("qty_harvested"))
        if qty_harvested is None:
            return
        disposed = sum(
            value
            for value in (
                as_float(record.get("qty_stored")),
                as_float(record.get("qty_sold")),
            )
            if value is not None
        )
        if disposed > qty_harvested:
            validation_error(
                "qty_stored and qty_sold together must not exceed qty_harvested"
            )

    def construct_search_text(self, payload: dict, extra: list[str] = None) -> str:
        _logger.info("Constructing search text for harvest")

        keys = [
            "functional_record_id",
            "land_id",
            "commodity",
            "crop_maturity_status",
            "harvest_date",
            "area_harvested",
            "qty_harvested",
            "qty_stored",
            "qty_sold",
            "harvested_by",
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
        _logger.info("Constructing record name for harvest")

        keys = ["commodity", "harvest_date", "qty_harvested"]
        record_name = []
        if extra:
            record_name.extend(str(item).strip() for item in extra if str(item).strip())
        record_name.extend(
            str(payload.get(key) or "").strip()
            for key in keys
            if str(payload.get(key) or "").strip()
        )

        return " ".join(record_name).strip()
