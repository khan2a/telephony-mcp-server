import os
import httpx
import logging
import asyncio
import uuid
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

# Dictionary to track active calls
active_calls = {}

logger.info("Telephony MCP server initialized.")

# Function to poll callback server for events
async def poll_call_status(call_uuid, yield_fn):
    """
    Poll the callback server for events related to a specific call UUID.
    
    Args:
        call_uuid: UUID of the call to track
        yield_fn: Function to yield progress updates to the client or log
    """
    try:
        max_attempts = 60  # Poll for up to 5 minutes (60 * 5 seconds)
        attempt = 0
        call_completed = False
        status_sent = set()  # Track already sent statuses to avoid duplicates
        
        # Update call status in our tracking dictionary
        if call_uuid in active_calls:
            active_calls[call_uuid]["status_updates"] = []
            
        try:
            result = yield_fn(f"Call initiated with ID: {call_uuid}. Tracking call progress...")
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.warning(f"Error sending initial status update: {e}")
            
        while attempt < max_attempts and not call_completed:
            attempt += 1
            
            async with httpx.AsyncClient() as client:
                # Query the callback server for events
                response = await client.get(f"{CALLBACK_SERVER_URL}/events?limit=100")
                
                if response.status_code != 200:
                    logger.error(f"Failed to get events from callback server: {response.status_code}")
                    await asyncio.sleep(5)  # Wait before retrying
                    continue
                    
                events_data = response.json()
                
                if not events_data.get("events"):
                    await asyncio.sleep(5)  # Wait before retrying if no events
                    continue
                
                # Process events in reverse chronological order (newest first)
                events = sorted(events_data["events"], key=lambda e: e.get("timestamp", ""), reverse=True)
                
                # Check if any events relate to our call UUID
                for event in events:
                    if not isinstance(event.get("body"), dict):
                        continue
                        
                    body = event.get("body", {})
                    event_uuid = body.get("uuid")
                    
                    if event_uuid != call_uuid:
                        continue
                    
                    # Process this event
                    status = body.get("status")
                    direction = body.get("direction")
                    conversation_uuid = body.get("conversation_uuid")
                    
                    # Specific event types
                    if body.get("type") == "transfer":
                        if status and status not in status_sent:
                            await yield_fn(f"Call transfer {status}")
                            status_sent.add(status)
                            
                    elif "status" in body and status not in status_sent:
                        status_message = f"Call status: {status}"
                        
                        # Enhanced status messages
                        if status == "started":
                            status_message = "Call connected, now in progress."
                        elif status == "ringing":
                            status_message = "Phone is ringing."
                        elif status == "answered":
                            status_message = "Call answered."
                        elif status == "completed":
                            status_message = "Call completed successfully."
                            call_completed = True
                        elif status == "failed":
                            reason = body.get("reason") or "unknown reason"
                            status_message = f"Call failed: {reason}"
                            call_completed = True
                        elif status == "rejected":
                            status_message = "Call rejected by recipient."
                            call_completed = True
                        elif status == "busy":
                            status_message = "Recipient's line is busy."
                            call_completed = True
                        elif status == "timeout":
                            status_message = "Call timed out, no answer."
                            call_completed = True
                            
                        # Store status update in our tracking dictionary
                        if call_uuid in active_calls:
                            if "status_updates" not in active_calls[call_uuid]:
                                active_calls[call_uuid]["status_updates"] = []
                            active_calls[call_uuid]["status_updates"].append(status_message)
                            active_calls[call_uuid]["status"] = status
                            
                        try:
                            result = yield_fn(status_message)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.warning(f"Error sending status update: {e}")
                            
                        status_sent.add(status)
                    
                    # For detailed debugging
                    logger.debug(f"Call {call_uuid} event: {body}")
                
            # Wait before polling again
            await asyncio.sleep(5)
        
        # Final status message if polling ended without completion
        if not call_completed:
            result = yield_fn("Call tracking timed out. Please check Vonage dashboard for final status.")
            if asyncio.iscoroutine(result):
                await result
                
    except Exception as e:
        logger.exception(f"Error while polling call status: {e}")
        result = yield_fn(f"Error tracking call: {str(e)}")
        if asyncio.iscoroutine(result):
            await result


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
    
    # Generate a conversation UUID to track this call
    conversation_id = str(uuid.uuid4())
    
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
            return f"Failed to initiate call: {response.status_code} {response.text}"
        else:
            logger.info(f"Vonage API response: {response.status_code} {response.text}")
            
            if response.status_code == 201:
                # Extract call UUID from response
                try:
                    response_data = response.json()
                    call_uuid = response_data.get("uuid")
                    conversation_uuid = response_data.get("conversation_uuid", "unknown")
                    
                    if not call_uuid:
                        logger.warning("Call initiated but no UUID returned to track progress")
                        return f"Voice call initiated to {to}, but no tracking ID available."
                    
                    # Store call info
                    active_calls[call_uuid] = {
                        "to": to,
                        "from": from_,
                        "message": message,
                        "status": "initiated",
                        "timestamp": asyncio.get_event_loop().time(),
                        "conversation_uuid": conversation_uuid
                    }
                    
                    logger.info(f"Voice call successfully initiated to {to} with UUID {call_uuid}.")
                    
                    # Check if MCP server supports streaming
                    if hasattr(telephony_mcp, 'stream'):
                        # Set up streaming response
                        async def call_monitor_stream():
                            yield f"Voice call initiated to {to}."
                            async for update in telephony_mcp.stream(poll_call_status(call_uuid, lambda x: x)):
                                yield update
                        
                        return call_monitor_stream()
                    else:
                        # Fallback for non-streaming environments
                        # Start background task to poll for call status
                        asyncio.create_task(poll_call_status(
                            call_uuid, 
                            lambda x: logger.info(f"Call {call_uuid} status update: {x}")
                        ))
                        
                        return f"Voice call initiated to {to}. Call tracking started with ID: {call_uuid}. Use check_call_status tool to get updates."
                    
                except Exception as e:
                    logger.exception(f"Error processing call response: {e}")
                    return f"Voice call initiated to {to}, but error setting up progress tracking: {str(e)}"
            
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


@telephony_mcp.tool(
    name="check_call_status",
    description="Check status of a previously initiated voice call or get status of all active calls.",
)
async def check_call_status(*, call_uuid: str = None) -> str:
    """
    Check the status of a specific call or all active calls.
    
    Args:
        call_uuid: str - Optional UUID of a specific call to check.
    
    Returns:
        Status information about the requested call(s).
    """
    if call_uuid:
        if call_uuid in active_calls:
            call_info = active_calls[call_uuid]
            # Try to get latest status from callback server
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{CALLBACK_SERVER_URL}/events?limit=100")
                    
                    if response.status_code == 200:
                        events_data = response.json()
                        events = events_data.get("events", [])
                        
                        # Find the most recent event for this call
                        for event in sorted(events, key=lambda e: e.get("timestamp", ""), reverse=True):
                            if isinstance(event.get("body"), dict) and event["body"].get("uuid") == call_uuid:
                                # Update our stored status
                                active_calls[call_uuid]["status"] = event["body"].get("status", call_info["status"])
                                break
            except Exception as e:
                logger.error(f"Error retrieving call status from callback server: {e}")
            
            # Format the response
            status_updates = call_info.get("status_updates", [])
            status_history = "\n".join([f"- {update}" for update in status_updates]) if status_updates else "- No status updates recorded"
            
            return (
                f"Call to {call_info['to']} (ID: {call_uuid}):\n"
                f"- Current status: {active_calls[call_uuid]['status']}\n"
                f"- From: {call_info['from']}\n"
                f"- Message: {call_info['message']}\n"
                f"- Conversation UUID: {call_info.get('conversation_uuid', 'unknown')}\n\n"
                f"Status history:\n{status_history}"
            )
        else:
            return f"No call found with UUID {call_uuid}."
    
    # Return status of all active calls
    if not active_calls:
        return "No active calls found."
    
    # List all active calls
    result = "Active calls:\n\n"
    for uuid, info in active_calls.items():
        result += (
            f"Call ID: {uuid}\n"
            f"- To: {info['to']}\n"
            f"- Status: {info['status']}\n"
            f"- From: {info['from']}\n"
            f"- Initiated: {info.get('timestamp', 'unknown')}\n\n"
        )
    
    return result

# Clean up expired calls periodically
async def clean_expired_calls():
    """Remove calls older than 1 hour from the active_calls dictionary"""
    current_time = asyncio.get_event_loop().time()
    expired = []
    
    for uuid, info in active_calls.items():
        # If call is over 1 hour old
        if current_time - info.get("timestamp", current_time) > 3600:
            expired.append(uuid)
    
    # Remove expired calls
    for uuid in expired:
        if uuid in active_calls:
            logger.info(f"Removing expired call record: {uuid}")
            del active_calls[uuid]

async def periodic_cleanup():
    """Run the cleanup function periodically"""
    while True:
        await asyncio.sleep(300)  # Run every 5 minutes
        try:
            await clean_expired_calls()
        except Exception as e:
            logger.exception(f"Error in call cleanup task: {e}")

if __name__ == "__main__":
    logger.info("Starting Telephony MCP server...")
    
    # Set up call cleanup task manually if we're in an async context
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(periodic_cleanup())
        else:
            logger.warning("No running event loop, skipping periodic cleanup task")
    except RuntimeError:
        logger.warning("No event loop, skipping periodic cleanup task")
    
    # Setup enhanced logging
    logger.info("Setting up enhanced error logging...")
    
    # Set any available debugging flags
    if hasattr(telephony_mcp, 'debug'):
        telephony_mcp.debug = True
        
    # Enhance logger verbosity
    logging.getLogger("mcp").setLevel(logging.DEBUG)
    
    # Use a try-except block directly instead of wrapper
    logger.info("Starting MCP server with enhanced error logging...")
    try:
        telephony_mcp.run(transport="streamable-http")
    except Exception as e:
        logger.exception(f"MCP server error: {e}")
        # Additional error details if available
        error_info = getattr(e, "body", None) or getattr(e, "detail", None)
        if error_info:
            logger.error(f"Error details: {error_info}")
        raise
