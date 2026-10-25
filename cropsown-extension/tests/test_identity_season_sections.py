"""Identity and Production Season are independently saveable intake sections."""

import asyncio
import importlib
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from openg2p_registry_core.errors import G2PRegistryException
from openg2p_registry_cropsown_extension.register_domain.services.g2p_register_domain_service_crop_sown import (
    G2PRegisterDomainServiceCropSown,
    validate_production_season_fields,
)

YEAR = str(date.today().year)


def validate(record):
    service = G2PRegisterDomainServiceCropSown()
    asyncio.run(service.validate_domain_attributes([record]))


@pytest.mark.parametrize(
    "identity",
    [
        {
            "farmer_id": "FR-000000000001",
            "fayda_fin": "001234567890",
            "fayda_fan_id": None,
        },
        {
            "farmer_id": "FR-000000000001",
            "fayda_fin": None,
            "fayda_fan_id": "0012345678901234",
        },
    ],
)
def test_identity_next_does_not_require_unvisited_production_season(identity):
    validate(dict(identity))


def test_production_season_next_does_not_require_identity_in_same_payload():
    validate({"crop_year": YEAR, "production_season": "SEASON_MEHER"})


@pytest.mark.parametrize(
    "values,field",
    [
        ({"crop_year": "", "production_season": "SEASON_MEHER"}, "Crop Year"),
        ({"crop_year": YEAR, "production_season": None}, "Production Season"),
    ],
)
def test_missing_season_values_name_the_owning_section(values, field):
    with pytest.raises(G2PRegistryException) as error:
        validate(values)
    assert field in error.value.message
    assert "Production Season section" in error.value.message
    assert "Farmer Identity" not in error.value.message


def test_identity_required_values_still_apply():
    with pytest.raises(G2PRegistryException):
        validate({"farmer_id": "", "fayda_fin": "001234567890", "fayda_fan_id": None})
    with pytest.raises(G2PRegistryException):
        validate(
            {"farmer_id": "FR-000000000001", "fayda_fin": None, "fayda_fan_id": None}
        )


def test_submit_validation_keeps_season_required_even_when_not_visited():
    with pytest.raises(G2PRegistryException, match="") as error:
        validate_production_season_fields(
            {"crop_year": None, "production_season": None}
        )
    assert "Crop Year" in error.value.message


def test_unrelated_section_does_not_request_identity_or_season():
    validate({"da_name": "Aster"})


@pytest.mark.parametrize(
    "section",
    [
        "cropsown_cropsown_record_section_01",
        "cropsown_intake_season_section_01",
        "cropsown_common_intake_season_section_01",
    ],
)
@pytest.mark.parametrize(
    "proposed,refused",
    [
        ({"crop_year": YEAR, "production_season": "SEASON_MEHER"}, False),
        ({"production_season": "CROP_SEASON_MEHER"}, False),
        ({"farmer_name": "Aster"}, False),
        ({"crop_year": "1900"}, True),
        ({"production_season": "SEASON_BELG"}, True),
        ({"crop_year": None}, True),
    ],
)
def test_native_record_payload_keeps_established_year_and_season(
    monkeypatch, section, proposed, refused
):
    from openg2p_registry_core.services.g2p_register_change_request_service import (
        G2PRegisterChangeRequestService,
    )

    # The pure fixture has no selected ORM extension. Keep its fake session's
    # model local; actual native ORM/save coverage runs separately below.
    monkeypatch.setitem(
        sys.modules,
        "openg2p_registry_cropsown_extension.register_domain.models",
        SimpleNamespace(G2PRegisterCropSown=object),
    )

    record = SimpleNamespace(
        internal_record_id="record", crop_year=YEAR, production_season="SEASON_MEHER"
    )
    session = SimpleNamespace(get=AsyncMock(return_value=record))
    service = SimpleNamespace(
        _get_change_request_payload=AsyncMock(
            return_value=SimpleNamespace(
                change_payload=[
                    {
                        "internal_record_id": "record",
                        "edit_action": "UPDATE",
                        **proposed,
                    }
                ]
            )
        )
    )
    monkeypatch.setattr(
        G2PRegisterChangeRequestService, "get_component", lambda: service
    )
    cr = SimpleNamespace(
        internal_record_id="record", change_request_id="cr", section_id=section
    )
    check = G2PRegisterDomainServiceCropSown()._check_immutable_header_season_and_year(
        cr, session
    )
    if refused:
        with pytest.raises(G2PRegistryException) as error:
            asyncio.run(check)
        assert "Keep its original value" in error.value.message
    else:
        asyncio.run(check)


@unittest.skipUnless(
    os.environ.get("FARMER_TEST_DATABASE_URL"), "Requires disposable PostgreSQL"
)
class NativeSectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = importlib.import_module(
            "test_farmer_reference_native"
        ).NativeReferenceTests()
        await self.fixture.asyncSetUp()
        from openg2p_fastapi_common.context import dbengine

        self.previous_engine = dbengine.get()
        dbengine.set(self.fixture.engine)

    async def asyncTearDown(self):
        from openg2p_fastapi_common.context import dbengine

        dbengine.set(self.previous_engine)
        await self.fixture.asyncTearDown()

    async def test_all_four_forms_replay_and_save_identity_then_season(self):
        from openg2p_registry_core.models import (
            G2PIntakeFormDefinition,
            G2PIntakeFormUITab,
            G2PIntakeFormUITabSection,
            G2PRegisterChangeRequest,
            G2PRegisterSection,
        )
        from sqlalchemy import func, select

        native = importlib.import_module(
            "openg2p_registry_extensions.farmer_reference.native"
        )
        package = importlib.import_module("openg2p_registry_cropsown_extension")
        paths = sorted(
            (Path(package.__file__).parent / "meta_data/register-metadata").glob(
                "*.sql"
            )
        )
        snapshots = []
        for _ in range(2):
            async with self.fixture.engine.begin() as connection:
                raw = await connection.get_raw_connection()
                for path in paths:
                    sql = (
                        path.read_text(encoding="utf-8")
                        .replace('"public".', '"' + self.fixture.schema + '".')
                        .replace("public.", '"' + self.fixture.schema + '".')
                    )
                    await raw.driver_connection.execute(sql)
            async with self.fixture.sessions() as session:
                snapshots.append(
                    list(
                        (
                            await session.execute(
                                select(
                                    G2PIntakeFormUITabSection.tab_id,
                                    G2PIntakeFormUITabSection.section_id,
                                    G2PIntakeFormUITabSection.section_order,
                                ).order_by(G2PIntakeFormUITabSection.tab_section_id)
                            )
                        ).all()
                    )
                )
        self.assertEqual(*snapshots)
        service = native.G2PIntakeFormDataService.get_component()
        root = native.domain_models().G2PIntakeFormCropSown
        async with self.fixture.sessions.begin() as session:
            bindings = (
                await session.execute(
                    select(G2PIntakeFormDefinition, G2PRegisterSection)
                    .join(
                        G2PIntakeFormUITab,
                        G2PIntakeFormUITab.form_id == G2PIntakeFormDefinition.form_id,
                    )
                    .join(
                        G2PIntakeFormUITabSection,
                        G2PIntakeFormUITabSection.tab_id == G2PIntakeFormUITab.tab_id,
                    )
                    .join(
                        G2PRegisterSection,
                        G2PRegisterSection.section_id
                        == G2PIntakeFormUITabSection.section_id,
                    )
                    .where(
                        G2PRegisterSection.section_mnemonic.in_(
                            ["cs_intake_record", "cs_common_intake_record"]
                        )
                    )
                )
            ).all()
            self.assertEqual(len(bindings), 4)
            for form, identity in bindings:
                season_id = identity.section_id.replace(
                    "record_section", "season_section"
                )
                season = await session.get(G2PRegisterSection, season_id)
                submission = await service.save_intake_form_submission_with_session(
                    None,
                    identity.section_id,
                    [
                        {
                            "farmer_id": "FR-000000000001",
                            "fayda_fin": "001234567890",
                            "fayda_fan_id": None,
                        }
                    ],
                    identity.section_register_id,
                    form.form_id,
                    form.register_id,
                    "synthetic-section-test",
                    session,
                )
                # Native references have a time-based default; give each owned
                # scratch submission a unique reference for the rapid form loop.
                submission.application_reference = "section-" + uuid4().hex
                await session.flush()
                row = (
                    await session.execute(
                        select(root).where(
                            root.submission_id == submission.submission_id
                        )
                    )
                ).scalar_one()
                # Submit cannot skip Production Season or create a Farmer CR.
                with self.assertRaises(G2PRegistryException) as missing:
                    await service.finalize_submission_with_session(
                        submission.submission_id, session, bearer_token="not-sent"
                    )
                self.assertIn("Crop Year", missing.exception.message)
                await service.save_intake_form_submission_with_session(
                    submission.submission_id,
                    season.section_id,
                    [
                        {
                            "internal_record_id": row.internal_record_id,
                            "crop_year": YEAR,
                            "production_season": "SEASON_MEHER",
                        }
                    ],
                    season.section_register_id,
                    form.form_id,
                    form.register_id,
                    "synthetic-section-test",
                    session,
                )
                await session.refresh(row)
                self.assertEqual(row.farmer_id, "FR-000000000001")
                self.assertEqual(row.fayda_fin, "001234567890")
                self.assertEqual(row.crop_year, YEAR)
                self.assertEqual(row.production_season, "SEASON_MEHER")
                self.assertEqual(submission.draft_status, "DRAFT")
            self.assertEqual(
                await session.scalar(
                    select(func.count()).select_from(G2PRegisterChangeRequest)
                ),
                0,
            )
