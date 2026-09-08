import pytest
from fastapi import HTTPException

from app.services.storage_service import _validate_storage_key


@pytest.mark.parametrize(
    "key",
    [
        "../outside.txt",
        "evidence/../../outside.txt",
        r"evidence\outside.txt",
        "/absolute/path.txt",
        "evidence/\x00file.txt",
        "evidence/./file.txt",
    ],
)
def test_storage_keys_reject_path_traversal(key):
    with pytest.raises(HTTPException) as error:
        _validate_storage_key(key)

    assert error.value.status_code == 400


def test_storage_key_accepts_nested_application_key():
    assert _validate_storage_key("task_evidence/task_123/file.pdf") == (
        "task_evidence/task_123/file.pdf"
    )
