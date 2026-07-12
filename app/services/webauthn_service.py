import logging

from app.config import settings

logger = logging.getLogger(__name__)

WEBAUTHN_AVAILABLE = False
try:
    import webauthn  # noqa: F401

    WEBAUTHN_AVAILABLE = True
except ImportError:
    pass


class WebAuthnService:
    def __init__(self):
        self.rp_name = settings.app_name

    @property
    def rp_id(self) -> str:
        return settings.webauthn_rp_id

    @property
    def origin(self) -> str:
        return settings.webauthn_origin

    def is_available(self) -> bool:
        return WEBAUTHN_AVAILABLE and settings.webauthn_enabled

    def get_registration_options(self, user, existing_credentials: list) -> dict:
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("py-webauthn is not installed")
        from webauthn import generate_registration_options
        from webauthn.helpers.cose import COSEAlgorithmIdentifier
        from webauthn.helpers.structs import (
            AuthenticatorSelectionCriteria,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )

        exclude_credentials = [
            {"id": c.credential_id, "type": "public-key"} for c in existing_credentials
        ]

        options = generate_registration_options(
            rp_id=self.rp_id,
            rp_name=self.rp_name,
            user_id=str(user.id).encode(),
            user_name=user.username,
            user_display_name=user.username,
            exclude_credentials=exclude_credentials,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            ],
        )
        return options

    def verify_registration(
        self, credential_json: dict, expected_challenge: bytes
    ) -> dict:
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("py-webauthn is not installed")
        from webauthn import verify_registration_response
        from webauthn.helpers.structs import RegistrationCredential

        credential = RegistrationCredential.parse_raw(str(credential_json))
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=self.rp_id,
            expected_origin=self.origin,
        )
        return {
            "credential_id": verification.credential_id,
            "public_key": verification.credential_public_key,
            "sign_count": verification.sign_count,
        }

    def get_authentication_options(self, existing_credentials: list) -> dict:
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("py-webauthn is not installed")
        from webauthn import generate_authentication_options
        from webauthn.helpers.structs import UserVerificationRequirement

        allow_credentials = [
            {"id": c.credential_id, "type": "public-key"} for c in existing_credentials
        ]

        options = generate_authentication_options(
            rp_id=self.rp_id,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        return options

    def verify_authentication(
        self,
        credential_json: dict,
        expected_challenge: bytes,
        credential_record,
    ) -> int:
        if not WEBAUTHN_AVAILABLE:
            raise RuntimeError("py-webauthn is not installed")
        from webauthn import verify_authentication_response
        from webauthn.helpers.structs import (
            AuthenticationCredential,
        )

        credential = AuthenticationCredential.parse_raw(str(credential_json))
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_rp_id=self.rp_id,
            expected_origin=self.origin,
            credential_public_key=credential_record.public_key,
            credential_current_sign_count=credential_record.sign_count,
        )
        return verification.new_sign_count


webauthn_service = WebAuthnService()
