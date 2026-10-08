import pytest
import config


@pytest.fixture(autouse=True)
def isolated_progress_receipts(tmp_path, monkeypatch):
    """Test runs never touch the user's live workflow receipts."""
    monkeypatch.setattr(config, 'DESIGNER_PROGRESS_DB', str(tmp_path / 'progress.sqlite'))
