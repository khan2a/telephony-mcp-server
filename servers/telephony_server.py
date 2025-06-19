import os
import httpx
import logging
from dotenv import load_dotenv
from utils.auth import generate_vonage_jwt
from mcp.server.fastmcp import FastMCP

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Configure httpx logging to see detailed request/response information
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.DEBUG)

# Create an MCP server instance
telephony_mcp = FastMCP(name="Telephony", host="0.0.0.0", port=8000)

logger.info("Loading environment variables from .env file...")
load_dotenv()

VONAGE_API_KEY = os.getenv("VONAGE_API_KEY")
VONAGE_API_SECRET = os.getenv("VONAGE_API_SECRET")
VONAGE_APPLICATION_ID = os.getenv("VONAGE_APPLICATION_ID")
VONAGE_PRIVATE_KEY_PATH = os.getenv("VONAGE_PRIVATE_KEY_PATH")
VONAGE_LVN = os.getenv("VONAGE_LVN")
VONAGE_API_URL = os.getenv("VONAGE_API_URL")
VONAGE_SMS_URL = os.getenv("VONAGE_SMS_URL")
CALLBACK_SERVER_URL = os.getenv("CALLBACK_SERVER_URL", "http://localhost:8080")

logger.info("Telephony MCP server initialized.")


@telephony_mcp.tool(
    name="voice_call",
    description="Make a voice call or phone call to a given number. Accepts prompts like 'dial a number', 'call to mobile', 'make a phone call', 'call a number with a message', 'dial a number and say a message', etc.",
)
async def voice_call(*, to: str, from_: str = VONAGE_LVN, message: str) -> str:
    """
    Initiate a voice call from 'from_' (or VONAGE_LVN) to 'to' with the given message using Vonage Voice API.
    Accepts prompts like 'dial a number', 'call to mobile', 'make a phone call', etc.
    Args:
        to: str - The destination phone number.
        from_: str - The source phone number (optional, defaults to VONAGE_LVN).
        message: str - The message to say during the call.
    """
    logger.info(
        f"Attempting to initiate call: from_={from_}, to={to}, message={message}"
    )
    if not (
        VONAGE_API_KEY
        and VONAGE_API_SECRET
        and VONAGE_APPLICATION_ID
        and VONAGE_PRIVATE_KEY_PATH
    ):
        logger.error("Vonage API credentials are not fully configured.")
        return "Vonage API credentials are not fully configured."
    if not from_:
        from_ = VONAGE_LVN
    if not from_:
        logger.error("Source number (from_) is not provided and VONAGE_LVN is not set.")
        return "Source number (from_) is not provided and VONAGE_LVN is not set."
    # Create NCCO (Nexmo Call Control Object) with event URLs pointing to our callback server
    ncco = [
        {
            "action": "talk",
            "text": message,
            "language": "en-GB",
            "style": 0,
            "premium": False
        }
    ]
    
    data = {
        "to": [{"type": "phone", "number": to}],
        "from": {"type": "phone", "number": from_},
        "event_method": "POST",
        "event_url": [f"{CALLBACK_SERVER_URL}/event"],
        "ncco": ncco,

    }
    # Generate JWT token
    jwt_token = generate_vonage_jwt(VONAGE_APPLICATION_ID, VONAGE_PRIVATE_KEY_PATH)
    if not jwt_token:
        logger.error(
            "Failed to generate JWT token. Check your private key and application ID."
        )
        return None

    logger.info("Successfully generated JWT token")
    try:
        logger.info(f"Sending POST to Vonage API: {VONAGE_API_URL} with data: {data}")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                VONAGE_API_URL,
                json=data,
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            
        # Log detailed response information
        if response.status_code >= 400:
            try:
                response_body = response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
                logger.error(f"API Error: HTTP {response.status_code} - Request URL: {VONAGE_API_URL}")
                logger.error(f"Request data: {data}")
                logger.error(f"Response body: {response_body}")
            except Exception as e:
                logger.error(f"Failed to parse response body: {e}")
                logger.error(f"Raw response: {response.text}")
        else:
            logger.info(f"Vonage API response: {response.status_code} {response.text}")
            
        if response.status_code == 201:
            logger.info(f"Voice call successfully initiated to {to}.")
            return f"Voice call initiated to {to}."
        logger.error(f"Failed to initiate call: {response.status_code} {response.text}")
        return f"Failed to initiate call: {response.status_code} {response.text}"
    except Exception as e:
        logger.exception(f"Error initiating call: {e}")
        return f"Error initiating call: {e}"


@telephony_mcp.tool(
    name="send_sms",
    description=(
        "Send an SMS or text message to a given number. Accepts prompts like 'send sms', 'send text', 'message the number', 'text a number', 'send a message', 'sms to mobile', 'text to phone', 'send a code', 'notify by sms', 'alert by text', etc."
    ),
)
async def send_sms(*, to: str, from_: str = VONAGE_LVN, text: str) -> str:
    """
    Send an SMS using the Vonage SMS API.
    Args:
        to: str - The destination phone number.
        from_: str - The sender phone number (optional, defaults to VONAGE_LVN).
        text: str - The message to send.
    """
    logger.info(f"Attempting to send SMS: from_={from_}, to={to}, text={text}")
    if not (VONAGE_API_KEY and VONAGE_API_SECRET):
        logger.error("Vonage API credentials are not fully configured for SMS.")
        return "Vonage API credentials are not fully configured for SMS."
    if not from_:
        from_ = VONAGE_LVN
    if not from_:
        logger.error("Source number (from_) is not provided and VONAGE_LVN is not set.")
        return "Source number (from_) is not provided and VONAGE_LVN is not set."
    sms_url = VONAGE_SMS_URL
    payload = {
        "api_key": VONAGE_API_KEY,
        "api_secret": VONAGE_API_SECRET,
        "to": to,
        "from": from_,
        "text": text,
    }
    try:
        logger.info(
            f"Sending POST to Vonage SMS API: {sms_url} with payload: {payload}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(sms_url, data=payload, timeout=10.0)
            
        # Log detailed response information
        if response.status_code >= 400:
            try:
                response_body = response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
                logger.error(f"SMS API Error: HTTP {response.status_code} - Request URL: {sms_url}")
                logger.error(f"Request data: {payload}")
                logger.error(f"Response body: {response_body}")
            except Exception as e:
                logger.error(f"Failed to parse SMS response body: {e}")
                logger.error(f"Raw SMS response: {response.text}")
        else:
            logger.info(f"Vonage SMS API response: {response.status_code} {response.text}")
            
        if response.status_code == 200:
            resp_json = response.json()
            if (
                resp_json.get("messages")
                and resp_json["messages"][0].get("status") == "0"
            ):
                logger.info(f"SMS successfully sent to {to}.")
                return f"SMS sent to {to}."
            else:
                error_text = resp_json["messages"][0].get("error-text", "Unknown error")
                logger.error(f"Failed to send SMS: {error_text}")
                return f"Failed to send SMS: {error_text}"
        logger.error(f"Failed to send SMS: {response.status_code} {response.text}")
        return f"Failed to send SMS: {response.status_code} {response.text}"
    except Exception as e:
        logger.exception(f"Error sending SMS: {e}")
        return f"Error sending SMS: {e}"


if __name__ == "__main__":
    logger.info("Starting Telephony MCP server...")
    
    # Add event handler for HTTP errors
    @telephony_mcp.on_startup
    def setup_enhanced_logging():
        logger.info("Setting up enhanced error logging...")
        
        # Set any available debugging flags
        if hasattr(telephony_mcp, 'debug'):
            telephony_mcp.debug = True
            
        # Enhance logger verbosity
        logging.getLogger("mcp").setLevel(logging.DEBUG)
    
    # Create a wrapper for fastmcp
    telephony_mcp._original_run = telephony_mcp.run
    
    def run_with_error_handling(*args, **kwargs):
        logger.info("Starting MCP server with enhanced error logging...")
        try:
            telephony_mcp._original_run(*args, **kwargs)
        except Exception as e:
            logger.exception(f"MCP server error: {e}")
            # Additional error details if available
            error_info = getattr(e, "body", None) or getattr(e, "detail", None)
            if error_info:
                logger.error(f"Error details: {error_info}")
            raise
    
    # Replace the run method
    telephony_mcp.run = run_with_error_handling
    
    telephony_mcp.run(transport="streamable-http")
