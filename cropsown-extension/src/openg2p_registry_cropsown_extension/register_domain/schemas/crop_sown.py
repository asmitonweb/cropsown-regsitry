from typing import Optional, Union

from openg2p_registry_core.schemas import (
    G2PRegisterBaseSchema,
    G2PGeoSchema,
    G2PRegisterHistorySchema,
    G2PGeoHistorySchema,
    G2PIntakeFormSchemaBase,
)
import re
from pydantic import field_validator




class G2PSchemaCropSown:
    fayda_fin: Optional[str] = None
    identity_verification_id: Optional[str] = None
    last_authentication_status: Optional[str] = None
    last_authenticated_on: Optional[str] = None
    authentication_expiry_date: Optional[str] = None


    rejection_reason: Optional[str] = None
    edit_count: Optional[int] = None
    # Farmer (identified, held in the farmer registry)
    farmer_uuid: Optional[str] = None
    farmer_id: Optional[str] = None
    fayda_fan_id: Optional[str] = None
    farmer_name: Optional[str] = None
    land_id: Optional[str] = None

    # Address — admin hierarchy from the master-data catalog
    region: Optional[str] = None
    zone: Optional[str] = None
    woreda: Optional[str] = None
    kebele: Optional[str] = None
    latitude: Optional[Union[float, int, str]] = None
    longitude: Optional[Union[float, int, str]] = None
    altitude: Optional[Union[float, int, str]] = None

    @field_validator("latitude", "longitude", "altitude", mode="before")
    @classmethod
    def convert_lat_long_to_str(cls, v: Optional[Union[float, int, str]]) -> Optional[str]:
        if v is not None:
            return str(v)
        return None

    region_name: Optional[str] = None
    zone_name: Optional[str] = None
    woreda_name: Optional[str] = None
    kebele_name: Optional[str] = None
    address_hierarchy: Optional[str] = None

    # Record lifecycle & field staff
    status: Optional[str] = None
    crop_year: Optional[str] = None
    production_season: Optional[str] = None
    lifecycle_stage: Optional[str] = None

    @field_validator("crop_year")
    @classmethod
    def validate_crop_year(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and str(v).strip() != "":
            val_str = str(v)
            if not val_str.isdigit():
                raise ValueError("Crop Year must contain only numeric digits (no letters, spaces, or special characters)")
            year = int(val_str)
            from datetime import date
            current_year = date.today().year
            if year < current_year:
                raise ValueError("Crop Year must not be in the past")
            if year > current_year:
                raise ValueError("Crop Year must not be in the future")
        return v

    @field_validator("fayda_fan_id")
    @classmethod
    def validate_fayda_fan_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and str(v).strip() != "":
            fyda_pattern = r"^\d{4} \d{4} \d{4} \d{4}$"
            if not re.match(fyda_pattern, str(v).strip()):
                raise ValueError("Fayda ID must be 16 digits formatted as 4 groups of 4 digits (e.g. 1234 5678 9000 3456) with no letters, special characters, or extra spaces within groups")
        return v

    @field_validator("land_id")
    @classmethod
    def validate_land_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and str(v).strip() != "":
            if " " in str(v):
                raise ValueError("Land ID must not contain spaces or gaps (e.g. '12345')")
        return v


class G2PRegisterSchemaCropSown(G2PRegisterBaseSchema, G2PGeoSchema, G2PSchemaCropSown):
    """
    Schema for Crop Sown Record register.
    Inherits fields from G2PRegisterBaseSchema, G2PGeoSchema, G2PGeoShapeSchema.
    Attributes inherited from G2PSchemaCropSown are specific to the Crop Sown Record domain,
    and include the land attributes of the single plot the record covers.
    """



class G2PRegisterHistorySchemaCropSown(G2PRegisterHistorySchema, G2PGeoHistorySchema, G2PSchemaCropSown):
    """
    Schema for Crop Sown Record history.
    Inherits fields from G2PRegisterHistorySchema, G2PGeoHistorySchema.
    """



class G2PIntakeFormSchemaCropSown(G2PIntakeFormSchemaBase, G2PRegisterBaseSchema, G2PGeoSchema, G2PSchemaCropSown):
    """
    Schema for Crop Sown Record intake form.
    Inherits fields from G2PRegisterBaseSchema, G2PGeoSchema, G2PGeoShapeSchema.
    Attributes inherited from G2PSchemaCropSown are specific to the Crop Sown Record domain and are included in the intake form schema for data collection.
    """
