import asyncio
import importlib
import logging
import uuid
from datetime import datetime

from sqlalchemy import Date as SQLDate, func, inspect, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from openg2p_registry_core.models import (
    ApprovalStatusEnum,
    ChangeRequestSourceEnum,
    G2PFunctionalIdGenerationQueue,
    G2PIntakeFormSubmission,
    G2PIntakeFormSubmissionDocument,
    G2PIntakeFormUITab,
    G2PIntakeFormUITabSection,
    G2PRegisterDefinition,
    G2PRegisterDocumentHistory,
    G2PRegisterSection,
    G2PRegisterSectionDocument,
    IntakeFormStatusEnum,
    ProcessStatusEnum,
    RecordStatusEnum,
    RegisterPurposeEnum,
)
from openg2p_registry_core.services.g2p_outgest_fanout_service import fanout_outgest_rows
from openg2p_registry_core.services.g2p_register_hierarchical_service import (
    G2PRegisterHierarchicalService,
)

from ..app import celery_app
from ..config import Settings
from ..engine import Engine

try:
    from openg2p_registry_core.services.g2p_score_compute_service import G2PScoreComputeService
except ImportError:
    G2PScoreComputeService = None

try:
    from openg2p_registry_core.services.g2p_completion_score_service import G2PCompletionScoreService
except ImportError:
    G2PCompletionScoreService = None

_DOMAIN_MODELS_MODULE = "openg2p_registry_cropsown_extension.register_domain.models"
_DOMAIN_FACTORY_MODULE = "openg2p_registry_cropsown_extension.register_domain.factory"
_DOMAIN_FACTORY_CLASS = "G2PRegisterDomainFactory"

_config = Settings.get_config()
_logger = logging.getLogger(_config.logging_default_logger_name)
# Other Celery task modules run on separate event loops. Sharing Engine's
# pooled async engine can hand this task an asyncpg connection owned by another
# loop. Keep register promotion isolated and close connections after each use.
_async_engine = create_async_engine(Engine.construct_async_db_datasource(), poolclass=NullPool)
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


@celery_app.task(name="intake_form_register_ingest_worker")
def intake_form_register_ingest_worker(submission_id: str) -> None:
    _loop.run_until_complete(_process_submission_async(submission_id))


async def _process_submission_async(submission_id: str) -> None:
    session_maker = async_sessionmaker(bind=_async_engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            async with session.begin():
                await _mark_processing(submission_id, session)

        async with session_maker() as session:
            async with session.begin():
                submission = await _get_submission(submission_id, session)
                sections = await _get_unique_form_sections(submission.form_id, session)
                # docs ingestion with register-level section dedupe
                documents_by_register = await _get_submission_documents_by_register(
                    submission.submission_id, session
                )
                inserted_records: list[tuple[G2PRegisterDefinition, object]] = []
                # Maps the intake-row internal_record_id of a REGISTER (master)
                # record onto the live record it resolved to, so child TABLE
                # rows in the same submission attach to the existing parent
                # instead of a freshly-minted duplicate. Sections come back
                # master-first (ordered by tab/section order), so this is
                # populated before the child rows that consume it.
                root_record_id_mapping: dict = {}
                for section in sections:
                    register_definition, intake_class, register_class, history_class = await _resolve_classes(
                        section.section_register_id,
                        session,
                    )
                    intake_rows = await _get_intake_rows(intake_class, submission.submission_id, session)
                    register_documents = documents_by_register.get(
                        section.section_register_id, []
                    )
                    for intake_row in intake_rows:
                        if register_definition.register_purpose == RegisterPurposeEnum.TABLE.value:
                            old_link = getattr(intake_row, "link_internal_record_id", None)
                            if old_link and old_link in root_record_id_mapping:
                                intake_row.link_internal_record_id = root_record_id_mapping[old_link]
                            elif root_record_id_mapping:
                                intake_row.link_internal_record_id = next(iter(root_record_id_mapping.values()))
                        register_row = await _insert_register_row(
                            submission,
                            register_definition,
                            intake_row,
                            register_class,
                            session,
                            root_record_id_mapping,
                        )
                        await _insert_history_row(
                            submission,
                            section,
                            register_row,
                            history_class,
                            session,
                        )
                        if register_documents:
                            await _upsert_live_documents(
                                submission,
                                register_row.internal_record_id,
                                register_documents,
                                session,
                            )
                        await _run_post_ingest_hook(register_definition, register_row, session)
                        inserted_records.append((register_definition, register_row))
                await _fanout_outgest_rows(submission, inserted_records, session)
                _mark_processed(submission)
                
        # Trigger score computation for approved intake submissions in a separate session
        await _trigger_score_computation_for_submission(submission_id, session_maker)
        await _trigger_completion_score_computation_for_submission(submission_id, session_maker)
    except Exception as error:
        _logger.error("Submission ingest failed for %s: %s", submission_id, error)
        await _mark_failed_or_pending(submission_id, str(error), session_maker)
        raise


async def _mark_processing(submission_id: str, session) -> G2PIntakeFormSubmission:
    submission = await _get_submission(submission_id, session)
    if submission.register_ingest_process_status != ProcessStatusEnum.PENDING.value:
        raise ValueError(f"Submission '{submission_id}' is not pending ingest")
    if submission.draft_status != IntakeFormStatusEnum.FINAL.value:
        raise ValueError(f"Submission '{submission_id}' must be FINAL before ingest")
    if submission.approval_status != ApprovalStatusEnum.APPROVED.value:
        raise ValueError(f"Submission '{submission_id}' must be APPROVED before ingest")

    submission.register_ingest_process_status = ProcessStatusEnum.PROCESSING.value
    submission.register_ingest_process_attempts = (submission.register_ingest_process_attempts or 0) + 1
    submission.register_ingest_processed_timestamp = datetime.now()
    session.add(submission)
    return submission


async def _get_submission(submission_id: str, session) -> G2PIntakeFormSubmission:
    submission = await session.get(G2PIntakeFormSubmission, submission_id)
    if not submission:
        raise ValueError(f"Submission '{submission_id}' was not found")
    return submission


async def _get_unique_form_sections(form_id: str, session) -> list[G2PRegisterSection]:
    sections = (
        await session.execute(
            select(G2PRegisterSection)
            .join(
                G2PIntakeFormUITabSection,
                G2PRegisterSection.section_id == G2PIntakeFormUITabSection.section_id,
            )
            .join(
                G2PIntakeFormUITab,
                G2PIntakeFormUITabSection.tab_id == G2PIntakeFormUITab.tab_id,
            )
            .where(G2PIntakeFormUITab.form_id == form_id)
            .order_by(
                G2PIntakeFormUITab.tab_order.asc(),
                G2PIntakeFormUITabSection.section_order.asc(),
                G2PRegisterSection.section_id.asc(),
            )
        )
    ).scalars().all()

    unique_sections = []
    seen_register_ids = set()
    for section in sections:
        if section.section_register_id in seen_register_ids:
            continue
        seen_register_ids.add(section.section_register_id)
        unique_sections.append(section)
    return unique_sections


async def _resolve_classes(section_register_id: str, session):
    register_definition = await session.get(G2PRegisterDefinition, section_register_id)
    if not register_definition:
        raise ValueError(f"Register definition '{section_register_id}' was not found")

    module = importlib.import_module(_DOMAIN_MODELS_MODULE)
    register_mnemonic = register_definition.register_mnemonic
    return (
        register_definition,
        getattr(module, f"G2PIntakeForm{register_mnemonic}"),
        getattr(module, f"G2PRegister{register_mnemonic}"),
        getattr(module, f"G2PRegisterHistory{register_mnemonic}"),
    )


async def _get_intake_rows(intake_class, submission_id: str, session) -> list[object]:
    return (
        await session.execute(select(intake_class).where(intake_class.submission_id == submission_id))
    ).scalars().all()


async def _get_submission_documents_by_register(
    submission_id: str,
    session,
) -> dict[str, list[tuple[str, str, str]]]:
    """Map section_register_id -> list of (document_id, label, section_id).

    Documents are stored against the UI section they were uploaded on. Ingest
    dedupes form sections by register, so lookup must be by register otherwise 
    docs might be dropped on dedupe.
    """
    document_rows = (
        await session.execute(
            select(G2PIntakeFormSubmissionDocument).where(
                G2PIntakeFormSubmissionDocument.submission_id == submission_id
            )
        )
    ).scalars().all()
    if not document_rows:
        return {}

    section_ids = {row.section_id for row in document_rows}
    sections = (
        await session.execute(
            select(G2PRegisterSection).where(G2PRegisterSection.section_id.in_(section_ids))
        )
    ).scalars().all()
    section_to_register = {section.section_id: section.section_register_id for section in sections}

    documents_by_register: dict[str, list[tuple[str, str, str]]] = {}
    for row in document_rows:
        register_id = section_to_register.get(row.section_id)
        if not register_id:
            _logger.warning(
                "Skipping intake document %s: section %s not found for submission %s",
                row.document_id,
                row.section_id,
                submission_id,
            )
            continue
        documents_by_register.setdefault(register_id, []).append(
            (row.document_id, row.label, row.section_id)
        )
    return documents_by_register


async def _upsert_live_documents(
    submission: G2PIntakeFormSubmission,
    internal_record_id: str,
    documents: list[tuple[str, str, str]],
    session,
) -> None:
    """Promote intake submission documents onto the live register record.

    Each document keeps the section_id it was uploaded against (not the
    deduped ingest section), so register UI docs sections still resolve them.
    """
    for document_id, label, section_id in documents:
        session.add(
            G2PRegisterDocumentHistory(
                internal_record_id=internal_record_id,
                change_request_id=None,
                submission_id=submission.submission_id,
                change_request_source=ChangeRequestSourceEnum.INTAKE_FORM.value,
                section_id=section_id,
                document_id=document_id,
                label=label,
                created_by=submission.created_by,
                created_at=submission.first_created_at,
                approved_by=submission.approved_by or "system",
                approved_at=submission.approved_at or submission.last_updated_at,
            )
        )

        existing = (
            await session.execute(
                select(G2PRegisterSectionDocument).where(
                    G2PRegisterSectionDocument.internal_record_id == internal_record_id,
                    G2PRegisterSectionDocument.document_id == document_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.section_id = section_id
            existing.label = label
        else:
            session.add(
                G2PRegisterSectionDocument(
                    internal_record_id=internal_record_id,
                    document_id=document_id,
                    section_id=section_id,
                    label=label,
                )
            )


async def _insert_register_row(
    submission: G2PIntakeFormSubmission,
    register_definition: G2PRegisterDefinition,
    intake_row,
    register_class,
    session,
    root_record_id_mapping: dict = None,
):
    record_data = _build_register_row_data(submission, intake_row, register_class)
    old_internal_id = record_data["internal_record_id"]

    existing = await session.get(register_class, old_internal_id)

    # For a master (REGISTER) record with no row of its own id yet, look for an
    # existing parent for the same farmer/year/season and fold into it, so that
    # child intake forms (sowing, harvest, ...) land on the crop-sown record the
    # planning/cultivation forms already created instead of a new duplicate.
    if not existing and register_definition.register_purpose == RegisterPurposeEnum.REGISTER.value:
        match = await _resolve_existing_master(record_data, register_class, session)
        if match is not None:
            existing = match
            record_data["internal_record_id"] = existing.internal_record_id
        elif str(getattr(submission, "form_id", "")) in ("4a7c2c98-0ffb-41d8-8fea-f22349823675", "52150a37-153d-496d-9cd9-2a3815d6e13b"):
            raise ValueError("Sowing and Harvesting forms are not allowed to create a new Crop Sown record. Existing master record was not found.")

    if existing:
        _update_existing_register_row(existing, record_data, register_class)
        register_row = existing
    else:
        _queue_functional_id_if_required(register_definition, record_data, session)
        register_row = register_class(**record_data)
        session.add(register_row)

    await session.flush()

    if (
        root_record_id_mapping is not None
        and register_definition.register_purpose == RegisterPurposeEnum.REGISTER.value
    ):
        root_record_id_mapping[old_internal_id] = register_row.internal_record_id

    return register_row


async def _resolve_existing_master(record_data: dict, register_class, session):
    """Find an existing master register record for the same farmer + crop year +
    production season. Mirrors the synchronous ingest path in
    openg2p_registry_core so both routes merge identically."""
    fayda_id = str(record_data.get("fayda_fan_id") or "").strip()
    farmer_id = str(record_data.get("farmer_id") or "").strip()
    crop_yr = record_data.get("crop_year") or record_data.get("production_year")
    prod_sn = (
        record_data.get("production_season")
        or record_data.get("season")
        or record_data.get("cluster_season")
    )

    query_conditions = []
    if fayda_id and hasattr(register_class, "fayda_fan_id"):
        query_conditions.append(func.trim(register_class.fayda_fan_id) == fayda_id)
        query_conditions.append(func.replace(register_class.fayda_fan_id, " ", "") == fayda_id.replace(" ", ""))
    elif farmer_id and hasattr(register_class, "farmer_id"):
        query_conditions.append(func.trim(register_class.farmer_id) == farmer_id)

    if not query_conditions:
        return None

    stmt = select(register_class).where(or_(*query_conditions))
    if hasattr(register_class, "crop_year") and crop_yr is not None:
        stmt = stmt.where(
            or_(
                register_class.crop_year == str(crop_yr).strip(),
                register_class.crop_year == None,  # noqa: E711
            )
        )
    if hasattr(register_class, "production_season") and prod_sn:
        sn_str = str(prod_sn).strip()
        clean_sn = sn_str.replace("CROP_SEASON_", "").strip().upper()
        stmt = stmt.where(
            or_(
                register_class.production_season == sn_str,
                register_class.production_season == f"CROP_SEASON_{clean_sn}",
                register_class.production_season == clean_sn,
                register_class.production_season == None,  # noqa: E711
            )
        )
    if hasattr(register_class, "record_status"):
        stmt = stmt.where(register_class.record_status == RecordStatusEnum.ACTIVE.value)
    if hasattr(register_class, "created_at"):
        stmt = stmt.order_by(register_class.created_at.asc())

    return (await session.execute(stmt)).scalars().first()


def _update_existing_register_row(existing, record_data: dict, register_class) -> None:
    """Copy incoming values onto an existing record without touching identity or
    clobbering a set value with a None."""
    mapper = inspect(register_class)
    for key, value in record_data.items():
        if key in {"internal_record_id", "submission_id", "section_id", "functional_record_id"}:
            continue
        if key not in mapper.columns:
            continue
        if value is None and getattr(existing, key, None) is not None:
            continue
        setattr(existing, key, value)


def _build_register_row_data(submission: G2PIntakeFormSubmission, intake_row, register_class) -> dict:
    mapper = inspect(register_class)
    row_data = _serialize_model(intake_row, {"submission_id"})
    record_data = {key: value for key, value in row_data.items() if key in mapper.columns}
    _set_data_if_column(record_data, register_class, "created_by", submission.created_by)
    _set_data_if_column(record_data, register_class, "created_at", submission.first_created_at)
    _set_data_if_column(record_data, register_class, "last_approved_by", submission.approved_by or "system")
    _set_data_if_column(record_data, register_class, "last_approved_at", submission.approved_at or datetime.now())
    return _convert_date_strings_to_objects(record_data, register_class)


def _queue_functional_id_if_required(
    register_definition: G2PRegisterDefinition,
    record_data: dict,
    session,
) -> None:
    if (
        register_definition.register_purpose == RegisterPurposeEnum.REGISTER.value
        and register_definition.functional_id_generation_required
        and not record_data.get("functional_record_id")
    ):
        session.add(
            G2PFunctionalIdGenerationQueue(
                register_id=register_definition.register_id,
                internal_record_id=record_data["internal_record_id"],
            )
        )
        record_data["functional_record_id"] = f"TEMP-{uuid.uuid4().hex}"


async def _insert_history_row(
    submission: G2PIntakeFormSubmission,
    section: G2PRegisterSection,
    register_row,
    history_class,
    session,
) -> None:
    history_data = _build_history_row_data(submission, section, register_row, history_class)
    session.add(history_class(**history_data))


def _build_history_row_data(
    submission: G2PIntakeFormSubmission,
    section: G2PRegisterSection,
    register_row,
    history_class,
) -> dict:
    history_data = {
        "history_record_id": str(uuid.uuid4()),
        "internal_record_id": register_row.internal_record_id,
        "tab_id": submission.form_id,
        "section_id": section.section_id,
        "change_request_id": None,
        "submission_id": submission.submission_id,
        "change_request_source": ChangeRequestSourceEnum.STAFF_PORTAL.value,
        "is_primary_section": section.section_register_id == submission.register_id,
        "created_by": submission.created_by,
        "created_at": submission.first_created_at,
        "approved_by": submission.approved_by or "system",
        "approved_at": submission.approved_at or datetime.now(),
    }
    history_columns = inspect(history_class).columns
    for key, value in _serialize_model(register_row).items():
        if key in history_columns and key not in history_data:
            history_data[key] = value
    return _convert_date_strings_to_objects(history_data, history_class)


def _mark_processed(submission: G2PIntakeFormSubmission) -> None:
    submission.register_ingest_process_status = ProcessStatusEnum.PROCESSED.value
    submission.register_ingest_processed_timestamp = datetime.now()
    submission.register_ingest_process_last_error_code = None


async def _mark_failed_or_pending(submission_id: str, error_message: str, session_maker) -> None:
    async with session_maker() as session:
        async with session.begin():
            submission = await session.get(G2PIntakeFormSubmission, submission_id)
            if not submission:
                return
            submission.register_ingest_processed_timestamp = datetime.now()
            submission.register_ingest_process_last_error_code = error_message
            if (submission.register_ingest_process_attempts or 0) >= _config.worker_max_attempts:
                submission.register_ingest_process_status = ProcessStatusEnum.FAILED.value
            else:
                submission.register_ingest_process_status = ProcessStatusEnum.PENDING.value
            session.add(submission)


def _set_data_if_column(record_data: dict, model_class, column_name: str, value) -> None:
    if column_name in inspect(model_class).columns:
        record_data[column_name] = value


def _serialize_model(row, exclude: set[str] | None = None) -> dict:
    exclude = exclude or set()
    return {
        column.name: getattr(row, column.name)
        for column in inspect(row.__class__).columns
        if column.name not in exclude
    }


def _convert_date_strings_to_objects(data_dict: dict, model_class) -> dict:
    mapper = inspect(model_class)
    converted = data_dict.copy()
    for key, value in converted.items():
        if value is None or key not in mapper.columns:
            continue
        column = mapper.columns[key]
        if isinstance(column.type, SQLDate):
            if isinstance(value, str):
                if not value.strip():
                    converted[key] = None
                    continue
                try:
                    converted[key] = datetime.strptime(value, "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    pass
            elif isinstance(value, datetime):
                converted[key] = value.date()
    return converted


def _get_domain_service_by_register_mnemonic(register_mnemonic: str):
    try:
        module = importlib.import_module(_DOMAIN_FACTORY_MODULE)
        domain_factory_class = getattr(module, _DOMAIN_FACTORY_CLASS)
        g2p_registry_domain_factory = domain_factory_class.get_component()
        if not g2p_registry_domain_factory:
            g2p_registry_domain_factory = domain_factory_class()
        return g2p_registry_domain_factory.get_domain_service(register_mnemonic)
    except Exception as error:
        _logger.warning(
            "Unable to resolve domain service for register mnemonic '%s': %s",
            register_mnemonic,
            error,
        )
        return None


async def _run_post_ingest_hook(register_definition, register_row, session):
    domain_service = _get_domain_service_by_register_mnemonic(register_definition.register_mnemonic)
    if domain_service:
        await domain_service.post_ingest(register_definition.register_id, register_row, session)


async def _fanout_outgest_rows(
    submission: G2PIntakeFormSubmission,
    inserted_records: list[tuple[G2PRegisterDefinition, object]],
    session,
) -> None:
    if not inserted_records:
        return

    hierarchical_service = G2PRegisterHierarchicalService()

    for register_definition, register_row in inserted_records:
        await fanout_outgest_rows(
            register_definition,
            register_row,
            session,
            intake_form_submission_id=submission.submission_id,
            changed_by=submission.approved_by or "system",
            changed_at=submission.approved_at or datetime.now(),
            approved_by=submission.approved_by,
            approved_at=submission.approved_at,
            hierarchical_service=hierarchical_service,
        )


async def _trigger_score_computation_for_submission(submission_id: str, session_maker) -> None:
    """
    Trigger score computation for an approved intake submission.
    
    This function:
    1. Gets all distinct section_register_id from the submission's form
    2. Iterates through each register definition to check for score definitions
    3. Calls the score compute service to enqueue score computations
    
    Args:
        submission_id: The approved intake submission ID
        session_maker: Session maker for creating new database sessions
    """
    if G2PScoreComputeService is None:
        _logger.warning("G2PScoreComputeService not available, skipping score computation")
        return
    
    try:
        async with session_maker() as session:
            async with session.begin():
                # Get the submission details
                submission = await _get_submission(submission_id, session)
                
                # Get all distinct section_register_id from the submission's form
                sections = await _get_unique_form_sections(submission.form_id, session)
                section_register_ids = [section.section_register_id for section in sections]
                
                if not section_register_ids:
                    _logger.info(f"No section register IDs found for submission {submission_id}")
                    return
                
                _logger.info(f"Triggering score computation for submission {submission_id} with registers: {section_register_ids}")
                
                # Initialize the score compute service
                score_compute_service = G2PScoreComputeService()
                
                # Enqueue score computations for all register definitions in the submission
                await score_compute_service.enqueue_score_computations_for_intake_submission(
                    submission_id=submission_id,
                    section_register_ids=section_register_ids,
                    session=session,
                )
                
                _logger.info(f"Successfully enqueued score computations for submission {submission_id}")
        
    except Exception as error:
        _logger.error(f"Failed to trigger score computation for submission {submission_id}: {error}")


async def _trigger_completion_score_computation_for_submission(submission_id: str, session_maker) -> None:
    if G2PCompletionScoreService is None:
        _logger.warning("G2PCompletionScoreService not available, skipping completion score computation")
        return

    try:
        async with session_maker() as session:
            async with session.begin():
                submission = await _get_submission(submission_id, session)
                sections = await _get_unique_form_sections(submission.form_id, session)
                section_register_ids = [section.section_register_id for section in sections]

                if not section_register_ids:
                    _logger.info(f"No section register IDs found for submission {submission_id}")
                    return

                _logger.info(
                    f"Triggering completion score computation for submission {submission_id} "
                    f"with registers: {section_register_ids}"
                )

                completion_score_service = G2PCompletionScoreService()
                await completion_score_service.enqueue_completion_score_computations_for_submissions(
                    submission_id=submission_id,
                    section_register_ids=section_register_ids,
                    session=session,
                )

                _logger.info(f"Successfully enqueued completion score computations for submission {submission_id}")

    except Exception as error:
        _logger.error(f"Failed to trigger completion score computation for submission {submission_id}: {error}")
