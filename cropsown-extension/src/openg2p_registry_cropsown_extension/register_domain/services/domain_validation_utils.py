from datetime import date, datetime

from openg2p_registry_core.errors import G2PRegistryErrorCodes, G2PRegistryException


def validation_error(message: str) -> None:
    raise G2PRegistryException(
        code=G2PRegistryErrorCodes.REQUEST_VALIDATION_ERROR.value[1],
        message=message,
    )


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_bool(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return bool(value)


def is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def validate_alphabetical_name(value, field_name: str) -> None:
    if is_blank(value):
        return
    if field_name in {"Farmer Name", "DA Name", "Supervisor Name", "Surveyor Name"}:
        from .field_rules import explain, name_issue
        problem=name_issue(value,"farmer_name")
        if problem:
            validation_error(explain(problem).replace("Farmer Name",field_name))
        return
    import re
    # Non-person botanical/local names retain their separate legacy contract.
    if not re.match(r"^[a-zA-Z0-9\s\.\-_]+$", str(value).strip()):
        validation_error(f"{field_name} must contain only alphabetical characters, numbers, and spaces")


def validate_mobile_number(value, field_name: str) -> None:
    if is_blank(value):
        return
    import re
    # Ethiopian mobile numbers: +251 or 0 prefix, then 7/9, then 8 digits
    # (e.g. +251911234567 or 0911234567).
    if not re.match(r"^(\+251[79]\d{8}|0[79]\d{8})$", str(value).strip()):
        validation_error(
            f"{field_name} must be a valid Ethiopian mobile number "
            f"(e.g. +251911234567 or 0911234567)"
        )


def validate_land_id(value, field_name: str = "Land ID") -> None:
    if is_blank(value):
        return
    if " " in str(value):
        validation_error(f"{field_name} must not contain spaces or gaps (e.g. '12345')")


def get_row_field(row: dict, field_name: str):
    if not isinstance(row, dict):
        return None
    val = row.get(field_name)
    if val is not None and str(val).strip() not in ("", "None", "null", "Select"):
        return val
    for k, v in row.items():
        if isinstance(k, str) and (k.endswith(f".{field_name}") or k == field_name) and v is not None:
            if str(v).strip() not in ("", "None", "null", "Select"):
                return v
    for k, v in row.items():
        if isinstance(v, dict):
            sub_val = v.get(field_name)
            if sub_val is not None and str(sub_val).strip() not in ("", "None", "null", "Select"):
                return sub_val
    return None


def get_attribute_variants(value, prefix: str = "") -> list[str]:
    if not value or not str(value).strip():
        return []
    v_str = str(value).strip()
    clean = v_str
    for p in ("CROP_SEASON_", "CROP_COMMODITY_", "CROP_VARIETY_", "CROP_CATEGORY_"):
        clean = clean.replace(p, "")
    clean = clean.strip().upper()
    variants = {v_str, clean, v_str.upper(), v_str.lower(), clean.capitalize()}
    if prefix:
        variants.add(f"{prefix}_{clean}")
    return [v for v in variants if v]


async def resolve_production_season(record: dict, session) -> str | None:
    if not isinstance(record, dict):
        return None

    prod_season = (
        record.get("production_season")
        or record.get("6b06a95a-9a6c-5a33-a33d-c1625716c59c.production_season")
    )
    if prod_season:
        return prod_season

    for k, v in record.items():
        if isinstance(k, str) and k.endswith("production_season") and v:
            return v

    submission_id = record.get("submission_id")
    if session and submission_id:
        from sqlalchemy import text
        res_hdr = await session.execute(
            text("SELECT production_season FROM g2p_intake_form_crop_sowns WHERE submission_id = :sub_id AND production_season IS NOT NULL LIMIT 1"),
            {"sub_id": submission_id}
        )
        row_hdr = res_hdr.fetchone()
        if row_hdr and row_hdr[0]:
            return row_hdr[0]

    internal_ids = [
        record.get("link_internal_record_id"),
        record.get("internal_record_id"),
        record.get("record_id"),
        record.get("subject_internal_record_id"),
    ]
    internal_ids = [str(x).strip() for x in internal_ids if x and str(x).strip()]

    if session and internal_ids:
        from sqlalchemy import text
        # 1. Direct check on root g2p_register_crop_sowns
        res_reg = await session.execute(
            text("SELECT production_season FROM g2p_register_crop_sowns WHERE internal_record_id = ANY(:rec_ids) AND production_season IS NOT NULL LIMIT 1"),
            {"rec_ids": internal_ids}
        )
        row_reg = res_reg.fetchone()
        if row_reg and row_reg[0]:
            return row_reg[0]

        # 2. Check child tables to find link_internal_record_id
        child_tables = [
            "g2p_register_plannings",
            "g2p_register_cultivations",
            "g2p_register_cultivation_clusters",
            "g2p_register_clusters",
            "g2p_register_sowings",
            "g2p_register_harvests",
            "g2p_register_productions",
        ]
        for tbl in child_tables:
            res_c = await session.execute(
                text(f"SELECT link_internal_record_id FROM {tbl} WHERE internal_record_id = ANY(:rec_ids) AND link_internal_record_id IS NOT NULL LIMIT 1"),
                {"rec_ids": internal_ids}
            )
            row_c = res_c.fetchone()
            if row_c and row_c[0]:
                res_p = await session.execute(
                    text("SELECT production_season FROM g2p_register_crop_sowns WHERE internal_record_id = :p_id AND production_season IS NOT NULL LIMIT 1"),
                    {"p_id": str(row_c[0])}
                )
                row_p = res_p.fetchone()
                if row_p and row_p[0]:
                    return row_p[0]

        # 3. Check intake child tables to find submission_id
        intake_tables = [
            "g2p_intake_form_plannings",
            "g2p_intake_form_cultivations",
            "g2p_intake_form_cultivation_clusters",
            "g2p_intake_form_clusters",
            "g2p_intake_form_sowings",
            "g2p_intake_form_harvests",
            "g2p_intake_form_productions",
        ]
        for itbl in intake_tables:
            res_i = await session.execute(
                text(f"SELECT submission_id FROM {itbl} WHERE internal_record_id = ANY(:rec_ids) AND submission_id IS NOT NULL LIMIT 1"),
                {"rec_ids": internal_ids}
            )
            row_i = res_i.fetchone()
            if row_i and row_i[0]:
                res_ip = await session.execute(
                    text("SELECT production_season FROM g2p_intake_form_crop_sowns WHERE submission_id = :s_id AND production_season IS NOT NULL LIMIT 1"),
                    {"s_id": str(row_i[0])}
                )
                row_ip = res_ip.fetchone()
                if row_ip and row_ip[0]:
                    return row_ip[0]

    if session:
        fayda_fan_id = record.get("fayda_fan_id") or record.get("fayda_id")
        farmer_id = record.get("farmer_id")
        if fayda_fan_id or farmer_id:
            from sqlalchemy import text
            query = "SELECT production_season FROM g2p_register_crop_sowns WHERE record_status = 'ACTIVE' AND production_season IS NOT NULL"
            params = {}
            if farmer_id:
                query += " AND farmer_id = :farmer_id"
                params["farmer_id"] = str(farmer_id).strip()
            elif fayda_fan_id:
                query += " AND (TRIM(fayda_fan_id) = :fayda_id OR REPLACE(fayda_fan_id, ' ', '') = :fayda_id)"
                params["fayda_id"] = str(fayda_fan_id).strip()
            res_reg2 = await session.execute(text(query), params)
            row_reg2 = res_reg2.fetchone()
            if row_reg2 and row_reg2[0]:
                return row_reg2[0]

    return None


def is_record_deleted(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("_delete") or record.get("is_deleted") or record.get("_deleted") or record.get("deleted"):
        return True
    edit_action = str(record.get("edit_action") or "").strip().upper()
    if edit_action in ("DELETE", "REMOVE", "DELETED", "REMOVED"):
        return True
    action = str(record.get("_action") or record.get("action") or "").strip().upper()
    if action in ("DELETE", "REMOVE", "DELETED", "REMOVED"):
        return True
    status = str(record.get("record_status") or record.get("status") or record.get("_status") or "").strip().upper()
    if status in ("DELETED", "REMOVED", "DELETE", "REMOVE"):
        return True
    return False
