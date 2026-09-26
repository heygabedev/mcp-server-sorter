import pytest

from mcp_sorter.runtime import Runtime
from mcp_sorter.settings import Settings


@pytest.fixture
def runtime(tmp_path):
    result = Runtime(Settings(data_dir=tmp_path))
    yield result
    result.close()
