"""
Utility functions for JWT generation and authentication.
"""

import jwt
import time
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def generate_vonage_jwt(application_id: str, private_key_path: str) -> Optional[str]:
    """
    Generate a JWT token for Vonage API authentication.

    Args:
        application_id: The Vonage application ID
        private_key_path: Path to the private key file

    Returns:
        JWT token as string, or None if an error occurs
    """
    try:
        # Check if application_id is provided
        if not application_id:
            logger.error("Application ID not provided for JWT generation")
            return None

        # Check if private key file exists
        if not os.path.exists(private_key_path):
            logger.error(f"Private key file not found at {private_key_path}")
            return None

        # Read the private key
        with open(private_key_path, "r") as f:
            private_key = f.read()

        # Check if private key is empty
        if not private_key.strip():
            logger.error(f"Private key file is empty at {private_key_path}")
            return None

        # Generate the JWT payload
        iat = int(time.time())
        payload = {
            "application_id": application_id,
            "iat": iat,
            "exp": iat + 60 * 60,  # 1 hour expiry
            "jti": f"{application_id}-{iat}",  # JWT ID
        }

        # Generate the token
        token = jwt.encode(payload, private_key, algorithm="RS256")

        # Log successful token generation
        logger.debug(
            f"Successfully generated JWT token for application {application_id}"
        )
        return token

    except jwt.PyJWTError as e:
        logger.error(f"JWT encoding error: {str(e)}")
        return None
    except IOError as e:
        logger.error(f"I/O error reading private key file: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error generating JWT: {str(e)}")
        return None
