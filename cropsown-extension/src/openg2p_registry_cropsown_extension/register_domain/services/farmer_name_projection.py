"""Stable-reference Crop identity mirrors through native approval/ingest hooks."""
from ...farmer_reference.projection import refresh_domain_on_ingest


def _models():
    from openg2p_registry_extensions.register_domain.models import G2PRegisterCropSown, G2PRegisterFarmer
    return G2PRegisterFarmer, G2PRegisterCropSown


async def refresh_crop_name_on_ingest(session, crop):
    farmer_model, _ = _models()
    return await refresh_domain_on_ingest(session, farmer_model, crop)
