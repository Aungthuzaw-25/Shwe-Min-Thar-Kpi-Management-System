from uuid import uuid4

from app.modules.users.schemas import UserResponse


def test_user_response_preserves_existing_email_value_and_schema_format():
    email = "qa.hr@example.test"

    response = UserResponse.model_validate({
        "id": uuid4(),
        "first_name": "QA",
        "last_name": "HR",
        "email": email,
        "is_active": True,
    })

    assert response.email == email
    assert UserResponse.model_json_schema()["properties"]["email"]["format"] == "email"
