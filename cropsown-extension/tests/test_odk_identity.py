"""ODK identity remains a typed reference; domain observations remain domain-owned."""
import importlib
import json
import os
from pathlib import Path

import pytest
from jinja2 import Environment

PACKAGE=os.environ.get('REGISTRY_EXTENSION_MODULE','openg2p_registry_cropsown_extension')
ROOT=Path(importlib.import_module(PACKAGE).__file__).parent
IS_CROP='cropsown' in PACKAGE


@pytest.mark.parametrize('identifier,field', [('001234567890','fayda_fin'),('0000123456789012','fayda_fan_id')])
@pytest.mark.parametrize('stage',['planning','cult_land_prep','sowing_details','harvesting'] if IS_CROP else ['livestock'])
def test_odk_one_identifier_is_split_without_importing_farmer_profile(identifier,field,stage):
    identity={'farmer_id':'FR-000000000001','fayda_id':identifier,'farmer_name':'Untrusted',
              'first_name':'Untrusted','gender':'OTHER','mobile_number':'Untrusted'}
    if IS_CROP:
        data={'farmer_identity':identity,'production_season_info':{'crop_year':'2026','production_season':'meher'},
              stage:[{}] if stage!='harvesting' else {}, 'address_info':{'woreda_id':'holding-woreda'}}
        template='csr_odk_transform.j2';section='cs_intake_record' if stage=='planning' else 'cs_common_intake_record'
    else:
        data={'farmer':identity,'location':{'location_source':'mds','region_code':'ET01',
              'zone_code':'ET0101','woreda_code':'ET010101','kebele_code':'ET010101101001'},
              'livestock':[{'species_id':'cattle','breed':'local','gender':'female',
                            'primary_identifier':'ANIMAL-001','date_of_birth':'2020-01-01'}]}
        template='ls_odk_transform.j2';section='ls_livestock_record'
    result=json.loads(Environment().from_string((ROOT/'templates'/template).read_text(encoding='utf-8')).render(expanded=data))
    root=result[section][0]
    assert root['farmer_id']=='FR-000000000001'
    assert root[field]==identifier
    assert root['fayda_fan_id' if field=='fayda_fin' else 'fayda_fin'] is None
    assert not any(name in result for name in ['ls_farmer_identity','ls_farmer_location','cs_farmer_identity'])
    assert not {'farmer_name','first_name','middle_name','last_name','mobile_number','date_of_birth','gender'} & set(root)
    assert 'Untrusted' not in json.dumps(result)
    if IS_CROP:
        assert 'crop_year' not in root and 'production_season' not in root
        season=result['cs_intake_season' if stage=='planning' else 'cs_common_intake_season'][0]
        assert season['crop_year']=='2026'
        assert season['production_season']=='SEASON_MEHER'
    if not IS_CROP:
        assert result['ls_livestock_location'][0]['woreda_id']=='ET010101'
        assert len(result['ls_animal_details'])==1
        assert result['ls_animal_details'][0]['species']=='LIVESTOCK_SPECIES_CATTLE'
        assert result['ls_animal_details'][0]['gender']=='FEMALE'
        assert result['ls_animal_details'][0]['date_of_birth']=='2020-01-01'


@pytest.mark.parametrize('value,expected', [('belg','SEASON_BELG'),('meher','SEASON_MEHER'),
    ('irrigation','SEASON_IRRIGATION'),('SEASON_MEHER','SEASON_MEHER'),('bega','BEGA')])
def test_odk_season_uses_mds_codes_without_reinterpreting_unsupported_bega(value, expected):
    template = (ROOT/'templates'/'csr_odk_transform.j2').read_text(encoding='utf-8')
    result=json.loads(Environment().from_string(template).render(expanded={
        'planning':[{}], 'farmer_identity':{'farmer_id':'FR-000000000001','fayda_id':'001234567890'},
        'production_season_info':{'crop_year':'2026','production_season':value}}))
    assert result['cs_intake_season'][0]['production_season']==expected
