import uuid

import pytest
from app.modules.privacy.schemas import ConsentPut, DeletionCreate, PrivacyPolicy, RetentionPolicy
from penforge_contracts import PrivacyMaintenanceJob
from pydantic import ValidationError


@pytest.mark.parametrize("confirmation", [False, 1, 0, "true", None])
def test_destructive_confirmation_is_a_literal_boolean(confirmation):
    with pytest.raises(ValidationError):
        DeletionCreate(scope="account", confirmed=confirmation)


@pytest.mark.parametrize("version", [True, 0, 2, "1", 1.0])
def test_privacy_payload_version_never_coerces(version):
    with pytest.raises(ValidationError):
        PrivacyMaintenanceJob(schema_version=version, deletion_request_id=str(uuid.uuid4()))


def test_consent_purpose_and_guest_policy_fail_closed():
    with pytest.raises(ValidationError):
        ConsentPut(purpose="model_training", granted="yes", policy_version="model-training-v1")
    with pytest.raises(ValidationError):
        ConsentPut(purpose="all_usage", granted=True, policy_version="model-training-v1")
    with pytest.raises(ValidationError):
        PrivacyPolicy(guest_contributions=True)
    with pytest.raises(ValidationError):
        RetentionPolicy(version=2)
