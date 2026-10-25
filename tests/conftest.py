import pytest
import config


@pytest.fixture(autouse=True)
def isolated_progress_receipts(tmp_path, monkeypatch):
    """Test runs never touch the user's live workflow or test receipts."""
    monkeypatch.setattr(config, 'DESIGNER_PROGRESS_DB', str(tmp_path / 'progress.sqlite'))
    monkeypatch.setattr(config, 'DESIGNER_TESTING_DB', str(tmp_path / 'testing.sqlite'))
