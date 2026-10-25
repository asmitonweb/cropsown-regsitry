# ruff: noqa: E402
"""Domain initialization using the upstream Farmer 1.2 extension pattern."""
import asyncio
from .config import Settings
_config = Settings.get_config()
from openg2p_fastapi_common.app import Initializer as BaseInitializer
from openg2p_fastapi_common.context import dbengine
from openg2p_registry_core.app import Initializer as CoreInitializer
from .register_domain import models, services
from .farmer_reference.models import REFERENCE_MODELS
from .farmer_reference.runtime import install

MODEL_NAMES = ['G2PRegisterFarmer', 'G2PRegisterHistoryFarmer', 'G2PIntakeFormFarmer', 'G2PRegisterCropSown', 'G2PRegisterHistoryCropSown', 'G2PIntakeFormCropSown', 'G2PRegisterPlanning', 'G2PRegisterHistoryPlanning', 'G2PIntakeFormPlanning', 'G2PRegisterCultivation', 'G2PRegisterHistoryCultivation', 'G2PIntakeFormCultivation', 'G2PRegisterSowing', 'G2PRegisterHistorySowing', 'G2PIntakeFormSowing', 'G2PRegisterProduction', 'G2PRegisterHistoryProduction', 'G2PIntakeFormProduction', 'G2PRegisterHarvest', 'G2PRegisterHistoryHarvest', 'G2PIntakeFormHarvest', 'G2PRegisterInfestation', 'G2PRegisterHistoryInfestation', 'G2PIntakeFormInfestation', 'G2PRegisterCluster', 'G2PRegisterHistoryCluster', 'G2PIntakeFormCluster', 'G2PRegisterCultivationCluster', 'G2PRegisterHistoryCultivationCluster', 'G2PIntakeFormCultivationCluster']
SERVICE_NAMES = ['G2PRegisterDomainServiceCluster', 'G2PRegisterDomainServiceCropSown', 'G2PRegisterDomainServiceCultivation', 'G2PRegisterDomainServiceCultivationCluster', 'G2PRegisterDomainServiceFarmer', 'G2PRegisterDomainServiceHarvest', 'G2PRegisterDomainServiceInfestation', 'G2PRegisterDomainServicePlanning', 'G2PRegisterDomainServiceProduction', 'G2PRegisterDomainServiceSowing']


class Initializer(BaseInitializer):
    def initialize(self, **kwargs):
        super().initialize()
        CoreInitializer().initialize()
        for name in SERVICE_NAMES:
            getattr(services, name)()
        install()
        from .register_domain.services.identity_verification import install_identity_verification
        install_identity_verification()

    async def fastapi_app_startup(self, app):
        await super().fastapi_app_startup(app)
        from importlib.util import find_spec
        if find_spec("openg2p_registry_staff_api"):
            from .controllers import OANIdentityVerificationController
            OANIdentityVerificationController().post_init()

    def migrate_database(self, args):
        async def migrate():
            # Native create_migrate only. Existing-table changes remain gated
            # on an explicit migration and restored-copy preservation evidence.
            for name in MODEL_NAMES:
                await getattr(models, name).create_migrate()
            for model in REFERENCE_MODELS:
                await model.create_migrate()
        # OAN Farmer's migration-loop boundary: don't reuse connections from
        # the core migration's already-closed event loop.
        dbengine.get().sync_engine.dispose(close=False)
        asyncio.run(migrate())
