"""Optional installed-SDK regressions. Only private local copies are modified."""
import os
import shutil
from pathlib import Path
import pytest
from designer import vendor

pytestmark = pytest.mark.skipif(not os.getenv('DESIGNER_TEST_VENDOR'), reason='requires installed vendor SDK and DESIGNER_TEST_MDB')


@pytest.fixture
def bridge(tmp_path):
    original = Path(os.environ['DESIGNER_TEST_MDB']).resolve()
    checksum = vendor.digest(original)
    current = tmp_path/'private.mdb';shutil.copyfile(original,current)
    def call(mode, **payload):
        from uuid import uuid4
        folder=tmp_path/uuid4().hex;folder.mkdir()
        return vendor.run(folder,{'mode':mode,'mdb':str(current),'workspace':'200',**payload})
    yield call
    assert vendor.digest(original)==checksum


def test_rename_and_delete_saved_definitions_by_stable_identity(bridge):
    bridge('apply',operations=[{'action':'create_cdo','name':'ExRegressionOld','parent':'Product'},
                              {'action':'create_field_type','name':'ExRegressionUnused','data_type':'String','max_length':80}])
    result=bridge('apply',operations=[{'action':'patch','kind':'cdo','name':'ExRegressionOld','changes':{'Name':'ExRegressionNew'},'expected':{'Name':'ExRegressionOld'}},
                                     {'action':'delete','kind':'field_type','name':'ExRegressionUnused'}])
    assert result['reload_verified']
    assert bridge('list',kind='cdo',search='ExRegressionNew',limit=20)['total']==1
    assert bridge('list',kind='field_type',search='ExRegressionUnused',limit=20)['total']==0


def test_removed_clf_call_is_persistently_deleted_with_event_binding(bridge):
    bridge('apply',operations=[{'action':'create_cdo','name':'ExRegressionEvent','parent':'Product'},
                              {'action':'create_clf','name':'ExRegressionClf','clf_type':'UserFunctions'},
                              {'action':'add_clf_function','owner':'ExRegressionClf','function':'FieldCreateCDO','sequence':1},
                              {'action':'bind_event','owner':'ExRegressionEvent','event':'BeforeInitialize','clf':'ExRegressionClf','feature':'Base'}])
    call=bridge('list',kind='clf_function',owner='ExRegressionClf',limit=20)['records'][0]
    result=bridge('apply',operations=[{'action':'remove_clf_function','owner':'ExRegressionClf','call_id':call['CLFFunctionID'],'expected_function':'FieldCreateCDO'},
                                     {'action':'unbind_event','owner':'ExRegressionEvent','event':'BeforeInitialize','expected_clf':'ExRegressionClf'}])
    assert result['reload_verified']
    assert bridge('list',kind='clf_function',owner='ExRegressionClf',limit=20)['total']==0
