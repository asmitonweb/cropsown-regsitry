import logging
from datetime import date

from openg2p_registry_core.services import G2PRegisterDomainService

from .domain_compute_utils import compute_ec_date
from .domain_validation_utils import as_float, parse_date, validation_error

_logger = logging.getLogger("g2p-register-domain-service")


class G2PRegisterDomainServiceInfestation(G2PRegisterDomainService):
    async def validate_domain_attributes(self, records: list[dict], session=None, **kwargs):
        from .field_rules import validate_record_fields
        await validate_record_fields(records, table=True)
        from .domain_validation_utils import is_record_deleted
        active_records = [r for r in records if not is_record_deleted(r)]
        for record in active_records:
            inf_type = record.get("infestation_type")
            if isinstance(inf_type, list):
                record["infestation_type"] = inf_type[0] if inf_type else None
            elif isinstance(inf_type, str) and (inf_type.startswith("[") or inf_type.startswith('["')):
                import json
                try:
                    parsed = json.loads(inf_type)
                    if isinstance(parsed, list):
                        record["infestation_type"] = parsed[0] if parsed else None
                except Exception:
                    record["infestation_type"] = inf_type.replace("[", "").replace("]", "").replace('"', "").replace("'", "").strip()

            from .domain_validation_utils import validate_alphabetical_name, validate_mobile_number
            validate_alphabetical_name(record.get("farmer_name"), "Farmer Name")
            self._validate_observation_date(record)
            self._validate_estimated_damage(record)
            compute_ec_date(record, "observation_date", "observation_date_ec")
            if session:
                await self._validate_infestation_after_sowing(record, session=session)

    async def _validate_infestation_after_sowing(self, record: dict, session) -> None:
        land_id = str(record.get("land_id") or "").strip()
        if not land_id:
            return

        submission_id = record.get("submission_id")
        link_internal_record_id = record.get("link_internal_record_id")
        fayda_fan_id = record.get("fayda_fan_id")
        internal_record_id = record.get("internal_record_id")

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
            is_clustered = bool(record.get("cluster_name") and not record.get("commodity") and not record.get("crop_type_affected"))

        commodity = record.get("commodity")
        from .domain_validation_utils import get_attribute_variants
        commodity_vars = get_attribute_variants(commodity, "CROP_COMMODITY") if commodity else []

        from sqlalchemy import text, select
        from ..models import G2PRegisterSowing

        sowing_found = False

        sowing_date = None

        # 1. Check in Intake Form (current form submission)
        if submission_id:
            query = "SELECT commodity, sowing_date, cluster_name FROM g2p_intake_form_sowings WHERE submission_id = :sub_id AND land_id = :land_id"
            params = {"sub_id": str(submission_id), "land_id": land_id}
            res = await session.execute(text(query), params)
            s_rows = res.fetchall()
            if s_rows:
                if is_clustered:
                    cluster_name = str(record.get("cluster_name") or "").strip()
                    for r in s_rows:
                        if not cluster_name or str(r[2] or "").strip().upper() == cluster_name.upper():
                            sowing_found = True
                            sowing_date = r[1]
                            break
                elif not commodity:
                    sowing_found = True
                    sowing_date = s_rows[0][1]
                else:
                    for r in s_rows:
                        s_comm = r[0]
                        if s_comm and any(v.upper() == str(s_comm).strip().upper() for v in commodity_vars):
                            sowing_found = True
                            sowing_date = r[1]
                            break

        # 2. Build master_ids for Register check if not found in intake
        if not sowing_found:
            master_ids = set()
            if submission_id:
                if not fayda_fan_id:
                    res_f = await session.execute(
                        text("SELECT fayda_fan_id FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND fayda_fan_id IS NOT NULL"),
                        {"sub_id": str(submission_id)}
                    )
                    f_row = res_f.fetchone()
                    if f_row and f_row[0]:
                        fayda_fan_id = f_row[0]

                res_root = await session.execute(
                    text("SELECT internal_record_id::text FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND internal_record_id IS NOT NULL"),
                    {"sub_id": str(submission_id)}
                )
                row_root = res_root.fetchone()
                if row_root and row_root[0]:
                    master_ids.add(str(row_root[0]))

            if link_internal_record_id:
                master_ids.add(str(link_internal_record_id))
            elif internal_record_id:
                res_root = await session.execute(
                    text("SELECT internal_record_id::text FROM g2p_register_crop_sowns WHERE internal_record_id = :rec_id AND record_status = 'ACTIVE'"),
                    {"rec_id": str(internal_record_id)}
                )
                if res_root.fetchone():
                    master_ids.add(str(internal_record_id))
                else:
                    res_child = await session.execute(
                        text("SELECT link_internal_record_id::text FROM g2p_register_infestations WHERE internal_record_id = :rec_id"),
                        {"rec_id": str(internal_record_id)}
                    )
                    row_c = res_child.fetchone()
                    if row_c and row_c[0]:
                        master_ids.add(str(row_c[0]))

            if fayda_fan_id:
                res_m = await session.execute(
                    text("SELECT internal_record_id::text FROM g2p_register_crop_sowns WHERE fayda_fan_id = :fayda AND record_status = 'ACTIVE'"),
                    {"fayda": str(fayda_fan_id)}
                )
                for r in res_m.fetchall():
                    if r[0]:
                        master_ids.add(str(r[0]))

            if master_ids:
                stmt = select(G2PRegisterSowing.commodity, G2PRegisterSowing.sowing_date, G2PRegisterSowing.cluster_name).where(
                    G2PRegisterSowing.link_internal_record_id.in_(list(master_ids)),
                    G2PRegisterSowing.record_status == 'ACTIVE',
                    G2PRegisterSowing.land_id == land_id
                )
                res = await session.execute(stmt)
                reg_sowings = res.all()
                if reg_sowings:
                    if is_clustered:
                        cluster_name = str(record.get("cluster_name") or "").strip()
                        for s_comm, s_date, s_cname in reg_sowings:
                            if not cluster_name or str(s_cname or "").strip().upper() == cluster_name.upper():
                                sowing_found = True
                                sowing_date = s_date
                                break
                    elif not commodity:
                        sowing_found = True
                        sowing_date = reg_sowings[0][1]
                    else:
                        for s_comm, s_date, s_cname in reg_sowings:
                            if s_comm and any(v.upper() == str(s_comm).strip().upper() for v in commodity_vars):
                                sowing_found = True
                                sowing_date = s_date
                                break

        if not sowing_found:
            crop_str = f" for Crop '{commodity}'" if commodity else ""
            validation_error(
                f"No Sowing record found{crop_str} on Land ID '{land_id}'. "
                f"Pest/Disease Infestation incident can only be created for a crop that has a Sowing record."
            )

        obs_date = parse_date(record.get("observation_date"))
        sow_dt = parse_date(sowing_date)
        if obs_date and sow_dt and obs_date <= sow_dt:
            validation_error(
                f"Infestation Observation Date ({obs_date.strftime('%Y-%m-%d')}) must be after "
                f"Sowing Date ({sow_dt.strftime('%Y-%m-%d')}) for Land ID '{land_id}'."
            )

    def _validate_observation_date(self, record: dict) -> None:
        return

    def _validate_estimated_damage(self, record: dict) -> None:
        raw_val = record.get("estimated_damage_pct")
        if raw_val is None or str(raw_val).strip() == "":
            return

        val_str = str(raw_val).strip()

        # Reject any alphabetic characters
        if any(c.isalpha() for c in val_str):
            validation_error("Estimated Crop Damage cannot contain letters. Enter percentage with '%' (e.g. 50%) or pure number for hectares (e.g. 10.5)")

        if "%" in val_str:
            num_part = val_str.replace("%", "").strip()
            try:
                num = float(num_part)
                if not (0 <= num <= 100):
                    validation_error("Percentage damage must be between 0% and 100%")
            except ValueError:
                validation_error("Invalid percentage format for Estimated Crop Damage (e.g., 50%)")
        else:
            try:
                num = float(val_str)
                if num < 0:
                    validation_error("Estimated Crop Damage must be non-negative")
            except ValueError:
                validation_error("Estimated Crop Damage must be numeric (e.g. 10.5 for ha or 50% for percentage)")

    def construct_search_text(self, payload: dict, extra: list[str] = None) -> str:
        _logger.info("Constructing search text for infestation")

        keys = [
            "functional_record_id",
            "land_id",
            "commodity",
            "growth_stage",
            "infestation_type",
            "pest_name",
            "weed_name",
            "disease_name",
            "chemical_used",
            "severity_level",
            "observation_date",
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
        _logger.info("Constructing record name for infestation")

        keys = ["infestation_type", "severity_level", "observation_date"]
        record_name = []
        if extra:
            record_name.extend(str(item).strip() for item in extra if str(item).strip())
        record_name.extend(
            str(payload.get(key) or "").strip()
            for key in keys
            if str(payload.get(key) or "").strip()
        )

        return " ".join(record_name).strip()
