"""Native domain FAN validators share the reference service's stored format."""

import importlib

import pytest

pytest.importorskip("openg2p_registry_core")
module = importlib.import_module(
    "openg2p_registry_cropsown_extension.register_domain.services.g2p_register_domain_service_crop_sown"
)
service = object.__new__(module.G2PRegisterDomainServiceCropSown)


@pytest.mark.parametrize(
    "value", ["0012345678901234", "0012 3456 7890 1234", " 0012345678901234 "]
)
def test_domain_fan_stores_compact_ascii_without_losing_zeroes(value):
    row = {"fayda_fan_id": value}
    service._validate_fayda_fan_id(row)
    assert row["fayda_fan_id"] == "0012345678901234"


@pytest.mark.parametrize(
    "value",
    [
        "001234567890",
        "00123456789012345",
        "١٢٣٤٥٦٧٨٩٠١٢٣٤٥٦",
        1234567890123456,
        "FAN-0012345678901234",
    ],
)
def test_bad_fan_names_the_field_and_repair(value):
    with pytest.raises(Exception) as failure:
        service._validate_fayda_fan_id({"fayda_fan_id": value})
    assert "Fayda ID (FAN): enter exactly 16 digits" in str(failure.value)


@pytest.mark.parametrize("value",["0012345678901234","0012 3456 7890 1234"])
def test_actual_native_intake_schema_also_stores_compact_fan(value):
    assert _native_schema({"fayda_fan_id": value})["fayda_fan_id"] == "0012345678901234"


def test_actual_native_intake_schema_allows_fin_without_fan_or_unresolved_farmer_id():
    model = _native_schema({"fayda_fin": "001234567890"})
    assert model["fayda_fin"] == "001234567890" and model["fayda_fan_id"] is None and model["farmer_id"] is None


def _native_schema(payload):
    # The native ORM enables extension models only under its selected alias.
    # Initialize that contract in an isolated process, avoiding disabled model
    # classes contaminating the mapper registry used by unrelated unit tests.
    import json
    import subprocess
    import sys

    code = """
import importlib, json, sys
sys.modules['openg2p_registry_extensions'] = importlib.import_module('openg2p_registry_cropsown_extension')
importlib.import_module('openg2p_registry_staff_api.config').Settings.get_config()
importlib.import_module('openg2p_registry_extensions.app').Initializer()
schema = importlib.import_module('openg2p_registry_extensions.register_domain.schemas.crop_sown').G2PIntakeFormSchemaCropSown
model = schema(**json.loads(sys.argv[1]))
print(json.dumps({name: getattr(model, name) for name in ['farmer_id', 'fayda_fin', 'fayda_fan_id']}))
"""
    result = subprocess.run([sys.executable, "-c", code, json.dumps(payload)], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(result.stdout.splitlines()[-1])
