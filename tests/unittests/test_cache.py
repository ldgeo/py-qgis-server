
import pytest
import os

from qgis.core import Qgis

from pathlib import Path
from pyqgisserver.qgscache.cachemanager import QgsCacheManager
from pyqgisserver.config import confservice

def test_aliases() -> None:
    """ Test alias resolution for file
    """
    rootpath = Path(confservice.get('cache','rootdir'))

    cacheservice = QgsCacheManager()

    url = cacheservice.resolve_alias('france_parts')
    assert url.scheme == 'file'
    assert url.path   == str(rootpath / 'france_parts')
    
    url = cacheservice.resolve_alias('file:france_parts')
    assert url.scheme == 'file'
    assert url.path   == str(rootpath / 'france_parts')

    url = cacheservice.resolve_alias('foo:france_parts')
    assert url.scheme == 'file'
    assert url.path   == '/foobar/france_parts'

    url = cacheservice.resolve_alias('test:france_parts')
    assert url.scheme == ''
    assert url.path   == str(rootpath / 'france_parts')

    url = cacheservice.resolve_alias('bar:france_parts')
    assert url.scheme == 'file'
    assert url.path   == 'foobar'
    assert url.query  == 'data=france_parts'



@pytest.mark.skipif(Qgis.QGIS_VERSION_INT <= 31000, reason="Test fail with qgis 3.4")
def test_file_cache() -> None:
    """ Tetst file protocol handler
    """
    rootpath = Path(confservice.get('cache','rootdir'))

    cacheservice = QgsCacheManager()
    details = cacheservice.peek('france_parts')
    assert details is None

    project, updated = cacheservice.lookup('france_parts')
    assert updated
    assert project is not None
    assert project.fileName() == str(rootpath / 'france_parts.qgs')

    details = cacheservice.peek('france_parts')
    assert details is not None
    assert details.project is project


def test_file_not_found() -> None:
    """ Test non existant file return error
    """
    cacheservice = QgsCacheManager()
    with pytest.raises(FileNotFoundError):    
        cacheservice.lookup('I_do_not_exists')

   
@pytest.mark.with_postgres
def test_postgres_cache() -> None:
    """ Test postgres handler
    """
    cacheservice = QgsCacheManager()

    url = 'postgres:///?project=france_parts'

    details = cacheservice.peek(url)
    assert details is None

    project, updated = cacheservice.lookup(url)
    assert updated
    assert project is not None

    details = cacheservice.peek(url)
    assert details is not None
    assert details.project is project


@pytest.mark.with_postgres
def test_postgres_with_pgservice() -> None:
 
    url = 'postgres:///?service=local&project=france_parts'

    cacheservice = QgsCacheManager()

    details = cacheservice.peek(url)
    assert details is None

    project, updated = cacheservice.lookup(url)
    assert updated
    assert project is not None

    details = cacheservice.peek(url)
    assert details is not None
    assert details.project is project


@pytest.mark.with_postgres
def test_postgres_pgservice_fail() -> None:
 
    url = 'postgres:///?service=not_working&project=france_parts'

    cacheservice = QgsCacheManager()

    with pytest.raises(FileNotFoundError):
        cacheservice.lookup(url)

