import os
import httpx
import logging
import asyncio
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
            result = yield_fn(
                f"Call initiated with ID: {call_uuid}. Tracking call progress..."
            )
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
                    logger.error(
                        f"Failed to get events from callback server: {response.status_code}"
                    )
                    await asyncio.sleep(5)  # Wait before retrying
                    continue

                events_data = response.json()

                if not events_data.get("events"):
                    await asyncio.sleep(5)  # Wait before retrying if no events
                    continue

                # Process events in reverse chronological order (newest first)
                events = sorted(
                    events_data["events"],
                    key=lambda e: e.get("timestamp", ""),
                    reverse=True,
                )  # Check if any events relate to our call UUID
                for event in events:
                    if not isinstance(event.get("body"), dict):
                        continue

                    body = event.get("body", {})
                    event_uuid = body.get("uuid")
                    event_conversation_uuid = body.get("conversation_uuid")

                    # Check for direct UUID match or for events related to the same conversation
                    call_info = active_calls.get(call_uuid, {})
                    conversation_match = (
                        event_conversation_uuid
                        and event_conversation_uuid
                        == call_info.get("conversation_uuid")
                    )

                    if event_uuid != call_uuid and not conversation_match:
                        continue

                    # Process this event
                    status = body.get("status")

                    # Check for speech recognition results
                    if (
                        call_info.get("is_speech_input")
                        and body.get("dtmf") is None
                        and body.get("speech") is not None
                    ):

                        speech_result = (
                            body.get("speech", {}).get("results", [{}])[0].get("text")
                        )
                        confidence = (
                            body.get("speech", {})
                            .get("results", [{}])[0]
                            .get("confidence")
                        )

                        if speech_result:
                            call_info["speech_result"] = speech_result
                            await yield_fn(
                                f'Speech recognized: "{speech_result}" (confidence: {confidence})'
                            )

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
                            active_calls[call_uuid]["status_updates"].append(
                                status_message
                            )
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
            result = yield_fn(
                "Call tracking timed out. Please check Vonage dashboard for final status."
            )
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
            "premium": False,
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
                response_body = (
                    response.json()
                    if response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else response.text
                )
                logger.error(
                    f"API Error: HTTP {response.status_code} - Request URL: {VONAGE_API_URL}"
                )
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
                    conversation_uuid = response_data.get(
                        "conversation_uuid", "unknown"
                    )

                    if not call_uuid:
                        logger.warning(
                            "Call initiated but no UUID returned to track progress"
                        )
                        return f"Voice call initiated to {to}, but no tracking ID available."

                    # Store call info
                    active_calls[call_uuid] = {
                        "to": to,
                        "from": from_,
                        "message": message,
                        "status": "initiated",
                        "timestamp": asyncio.get_event_loop().time(),
                        "conversation_uuid": conversation_uuid,
                    }

                    logger.info(
                        f"Voice call successfully initiated to {to} with UUID {call_uuid}."
                    )

                    # Check if MCP server supports streaming
                    if hasattr(telephony_mcp, "stream"):
                        # Set up streaming response
                        async def call_monitor_stream():
                            yield f"Voice call initiated to {to}."
                            async for update in telephony_mcp.stream(
                                poll_call_status(call_uuid, lambda x: x)
                            ):
                                yield update

                        return call_monitor_stream()
                    else:
                        # Fallback for non-streaming environments
                        # Start background task to poll for call status
                        asyncio.create_task(
                            poll_call_status(
                                call_uuid,
                                lambda x: logger.info(
                                    f"Call {call_uuid} status update: {x}"
                                ),
                            )
                        )

                        return f"Voice call initiated to {to}. Call tracking started with ID: {call_uuid}. Use check_call_status tool to get updates."

                except Exception as e:
                    logger.exception(f"Error processing call response: {e}")
                    return f"Voice call initiated to {to}, but error setting up progress tracking: {str(e)}"

            logger.error(
                f"Failed to initiate call: {response.status_code} {response.text}"
            )
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
                response_body = (
                    response.json()
                    if response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else response.text
                )
                logger.error(
                    f"SMS API Error: HTTP {response.status_code} - Request URL: {sms_url}"
                )
                logger.error(f"Request data: {payload}")
                logger.error(f"Response body: {response_body}")
            except Exception as e:
                logger.error(f"Failed to parse SMS response body: {e}")
                logger.error(f"Raw SMS response: {response.text}")
        else:
            logger.info(
                f"Vonage SMS API response: {response.status_code} {response.text}"
            )

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
                    response = await client.get(
                        f"{CALLBACK_SERVER_URL}/events?limit=100"
                    )

                    if response.status_code == 200:
                        events_data = response.json()
                        events = events_data.get("events", [])

                        # Find the most recent event for this call
                        for event in sorted(
                            events, key=lambda e: e.get("timestamp", ""), reverse=True
                        ):
                            if (
                                isinstance(event.get("body"), dict)
                                and event["body"].get("uuid") == call_uuid
                            ):
                                # Update our stored status
                                active_calls[call_uuid]["status"] = event["body"].get(
                                    "status", call_info["status"]
                                )
                                break
            except Exception as e:
                logger.error(f"Error retrieving call status from callback server: {e}")

            # Format the response
            status_updates = call_info.get("status_updates", [])
            status_history = (
                "\n".join([f"- {update}" for update in status_updates])
                if status_updates
                else "- No status updates recorded"
            )

            # Check if this is a speech input call and if we have results
            speech_result_info = ""
            if call_info.get("is_speech_input"):
                speech_result = call_info.get("speech_result")
                if speech_result:
                    confidence = call_info.get("speech_confidence", "unknown")
                    speech_timestamp = call_info.get("speech_timestamp", "unknown")

                    # Format timestamp if available
                    timestamp_str = ""
                    if isinstance(speech_timestamp, (int, float)):
                        from datetime import datetime

                        timestamp_str = f" at {datetime.fromtimestamp(speech_timestamp).strftime('%H:%M:%S')}"

                    speech_result_info = (
                        f"\nSpeech recognition details:"
                        f'\n- Result: "{speech_result}"'
                        f"\n- Confidence score: {confidence}"
                        f"\n- Received{timestamp_str}"
                    )

                    # Include any raw speech data for debugging if available
                    if call_info.get("speech_raw_data"):
                        try:
                            import json

                            raw_data_str = json.dumps(
                                call_info["speech_raw_data"], indent=2
                            )
                            speech_result_info += f"\n- Raw speech data: {raw_data_str}"
                        except Exception:
                            pass
                else:
                    speech_result_info = "\nAwaiting speech recognition results..."

            return (
                f"Call to {call_info['to']} (ID: {call_uuid}):\n"
                f"- Current status: {active_calls[call_uuid]['status']}\n"
                f"- From: {call_info['from']}\n"
                f"- Message: {call_info['message']}\n"
                f"- Conversation UUID: {call_info.get('conversation_uuid', 'unknown')}{speech_result_info}\n\n"
                f"Status history:\n{status_history}"
            )
        else:
            return f"No call found with UUID {call_uuid}."

    # Return status of all active calls
    if not active_calls:
        return "No active calls found."

    # List all active calls
    result = "Active calls:\n\n"
    for call_uuid, info in active_calls.items():
        result += (
            f"Call ID: {call_uuid}\n"
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

    for call_uuid, info in active_calls.items():
        # If call is over 1 hour old
        if current_time - info.get("timestamp", current_time) > 3600:
            expired.append(call_uuid)

    # Remove expired calls
    for call_uuid in expired:
        if call_uuid in active_calls:
            logger.info(f"Removing expired call record: {call_uuid}")
            del active_calls[call_uuid]


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
    if hasattr(telephony_mcp, "debug"):
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


@telephony_mcp.tool(
    name="voice_call_with_input",
    description="Make a voice call to a given number and wait for speech input from the recipient. Accepts prompts like 'make a call and check', 'dial this number and ask', 'please call to check'. When these prompts are used, the tool will wait for the speech result to arrive and display it to the user. Returns the recognized speech.",
)
async def voice_call_with_input(
    *,
    to: str,
    from_: str = VONAGE_LVN,
    prompt_message: str,
    wait_for_result: bool = True,
) -> str:
    """
    Initiate a voice call with speech recognition capability.

    Args:
        to: str - The destination phone number.
        from_: str - The source phone number (optional, defaults to VONAGE_LVN).
        prompt_message: str - The message to say during the call, prompting for speech input.
        wait_for_result: bool - Whether to wait for speech recognition results (default: True).

    Returns:
        Information about the call and speech recognition results if available.
    """
    logger.info(
        f"Attempting to initiate call with speech input: from_={from_}, to={to}, prompt_message={prompt_message}"
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

    # Create NCCO (Nexmo Call Control Object) with both talk and input actions
    # The input action will use speech recognition to collect user input
    ncco = [
        {
            "action": "talk",
            "text": prompt_message,
            "language": "en-GB",
            "style": 0,
            "premium": False,
        },
        {
            "action": "input",
            "type": ["speech"],
            "speech": {
                "language": "en-GB",
                "endOnSilence": 2,
                "context": ["general"],
                "startTimeout": 5,
                "maxDuration": 30,
            },
            "eventUrl": [f"{CALLBACK_SERVER_URL}/event"],
            "eventMethod": "POST",
        },
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
                response_body = (
                    response.json()
                    if response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else response.text
                )
                logger.error(
                    f"API Error: HTTP {response.status_code} - Request URL: {VONAGE_API_URL}"
                )
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
                    conversation_uuid = response_data.get(
                        "conversation_uuid", "unknown"
                    )

                    if not call_uuid:
                        logger.warning(
                            "Call initiated but no UUID returned to track progress"
                        )
                        return f"Voice call with input initiated to {to}, but no tracking ID available."

                    # Store call info
                    active_calls[call_uuid] = {
                        "to": to,
                        "from": from_,
                        "message": prompt_message,
                        "status": "initiated",
                        "timestamp": asyncio.get_event_loop().time(),
                        "conversation_uuid": conversation_uuid,
                        "is_speech_input": True,
                        "speech_result": None,
                    }

                    logger.info(
                        f"Voice call with input successfully initiated to {to} with UUID {call_uuid}."
                    )

                    # If not waiting for result, return immediately
                    if not wait_for_result:
                        return f"Voice call with input initiated to {to}. Call tracking started with ID: {call_uuid}. Use check_call_status tool to get updates."

                    # Wait for speech recognition results
                    logger.info(
                        f"Waiting for speech recognition results from call to {to}..."
                    )
                    speech_result = await wait_for_speech_result(call_uuid)

                    if speech_result:
                        # Get additional details
                        call_info = active_calls.get(call_uuid, {})
                        confidence = call_info.get("speech_confidence", "unknown")
                        status = call_info.get("status", "unknown")

                        # Format a detailed response with the speech result
                        return (
                            f"Voice call completed to {to}.\n"
                            f'Speech recognition result: "{speech_result}"\n'
                            f"Confidence: {confidence}\n"
                            f"Call status: {status}\n"
                            f"Call ID: {call_uuid}"
                        )
                    else:
                        return f"Voice call to {to} completed with status '{active_calls.get(call_uuid, {}).get('status', 'unknown')}', but no speech input was recognized. Call ID: {call_uuid}"

                except Exception as e:
                    logger.exception(f"Error processing call response: {e}")
                    return f"Voice call initiated to {to}, but error setting up progress tracking: {str(e)}"

            logger.error(
                f"Failed to initiate call: {response.status_code} {response.text}"
            )
            return f"Failed to initiate call: {response.status_code} {response.text}"
    except Exception as e:
        logger.exception(f"Error initiating call: {e}")
        return f"Error initiating call: {e}"


async def wait_for_speech_result(call_uuid, max_wait_time=120):
    """
    Wait for speech recognition results from the callback server.

    Args:
        call_uuid: str - The UUID of the call to monitor.
        max_wait_time: int - Maximum time to wait in seconds (default: 120).

    Returns:
        The recognized speech or None if no speech was recognized.
    """
    start_time = asyncio.get_event_loop().time()
    polling_interval = 1  # Poll every second for more responsive updates
    timeout_message = (
        f"Waiting for speech input (timeout in {max_wait_time} seconds)..."
    )
    logger.info(timeout_message)

    while (asyncio.get_event_loop().time() - start_time) < max_wait_time:
        try:
            # Check if we already have a result in active_calls
            if call_uuid in active_calls and active_calls[call_uuid].get(
                "speech_result"
            ):
                speech_result = active_calls[call_uuid]["speech_result"]
                confidence = active_calls[call_uuid].get("speech_confidence", "unknown")
                logger.info(
                    f"Speech result found: '{speech_result}' (confidence: {confidence})"
                )
                return speech_result

            # First check the speech-specific endpoint for efficiency
            async with httpx.AsyncClient() as client:
                try:
                    speech_response = await client.get(f"{CALLBACK_SERVER_URL}/event")

                    if speech_response.status_code == 200:
                        speech_data = speech_response.json()
                        speech_events = speech_data.get("speech_events", [])

                        if speech_events:
                            # Get the conversation UUID for our current call
                            call_conversation_uuid = None
                            if call_uuid in active_calls:
                                call_conversation_uuid = active_calls[call_uuid].get(
                                    "conversation_uuid"
                                )

                            # Find matching speech events
                            for event in speech_events:
                                conversation_uuid = event.get("conversation_uuid")

                                if conversation_uuid == call_conversation_uuid:
                                    speech_result = event.get("text", "")
                                    confidence = event.get("confidence", 0)

                                    logger.info(
                                        f"Found speech result via dedicated endpoint for call {call_uuid}: '{speech_result}' (confidence: {confidence})"
                                    )

                                    # Store the result with more details
                                    if call_uuid in active_calls:
                                        active_calls[call_uuid][
                                            "speech_result"
                                        ] = speech_result
                                        active_calls[call_uuid][
                                            "speech_confidence"
                                        ] = confidence
                                        active_calls[call_uuid][
                                            "speech_timestamp"
                                        ] = asyncio.get_event_loop().time()
                                        active_calls[call_uuid]["speech_event_id"] = (
                                            event.get("id")
                                        )

                                        # Store complete event if available
                                        complete_event = event.get("complete_event")
                                        if complete_event:
                                            active_calls[call_uuid][
                                                "speech_raw_data"
                                            ] = (
                                                complete_event.get("body", {})
                                                .get("speech", {})
                                                .get("results", [])
                                            )

                                    return speech_result
                except Exception as e:
                    logger.warning(
                        f"Error accessing speech events endpoint: {e}, falling back to regular events endpoint"
                    )

                # Fallback to regular events endpoint
                response = await client.get(f"{CALLBACK_SERVER_URL}/events?limit=100")

                if response.status_code == 200:
                    events_data = response.json()
                    events = events_data.get("events", [])

                    # Process events in reverse chronological order (newest first)
                    for event in sorted(
                        events, key=lambda e: e.get("timestamp", ""), reverse=True
                    ):
                        body = event.get("body", {})

                        # Check if this is an input event for our call
                        if isinstance(body, dict):
                            # For input events, the UUID is in the conversation_uuid field
                            conversation_uuid = body.get("conversation_uuid")

                            # Match by conversation_uuid since input events use that
                            if (
                                call_uuid in active_calls
                                and conversation_uuid
                                == active_calls[call_uuid].get("conversation_uuid")
                            ):

                                # Check if this is a speech result
                                if (
                                    body.get("dtmf") is None
                                    and body.get("speech") is not None
                                ):
                                    speech_results = body.get("speech", {}).get(
                                        "results", []
                                    )
                                    if speech_results:
                                        speech_result = speech_results[0].get(
                                            "text", ""
                                        )
                                        confidence = speech_results[0].get(
                                            "confidence", 0
                                        )

                                        logger.info(
                                            f"Speech recognition result for call {call_uuid}: '{speech_result}' (confidence: {confidence})"
                                        )

                                        # Store the result with more details
                                        if call_uuid in active_calls:
                                            active_calls[call_uuid][
                                                "speech_result"
                                            ] = speech_result
                                            active_calls[call_uuid][
                                                "speech_confidence"
                                            ] = confidence
                                            active_calls[call_uuid][
                                                "speech_timestamp"
                                            ] = asyncio.get_event_loop().time()
                                            active_calls[call_uuid][
                                                "speech_raw_data"
                                            ] = speech_results

                                        return speech_result

            # Show a periodic update every 10 seconds
            elapsed = int(asyncio.get_event_loop().time() - start_time)
            if elapsed % 10 == 0:
                remaining = max_wait_time - elapsed
                logger.info(
                    f"Still waiting for speech input... ({remaining} seconds remaining)"
                )

            # Wait before polling again
            await asyncio.sleep(polling_interval)

        except Exception as e:
            logger.exception(f"Error while waiting for speech result: {e}")
            await asyncio.sleep(polling_interval)

    logger.warning(f"Timed out waiting for speech result for call {call_uuid}")
    return None


@telephony_mcp.tool(
    name="sms_with_input",
    description="Send an SMS to a recipient and wait for an event at {CALLBACK_SERVER_URL}/event. When an event arrives for the same msisdn, display the text and stop waiting. Uses the Vonage SMS API.",
)
async def sms_with_input(
    *, to: str, from_: str = VONAGE_LVN, text: str, wait_for_result: bool = True
) -> str:
    """
    Send an SMS to a recipient and wait for an event at the callback server. When an event arrives for the same msisdn, display the text and stop waiting.
    Args:
        to: str - The recipient phone number (msisdn).
        from_: str - The sender phone number (optional, defaults to VONAGE_LVN).
        text: str - The message to send.
        wait_for_result: bool - Whether to wait for the event (default: True).
    Returns:
        Information about the SMS and the event result if available.
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
        "callback": f"{CALLBACK_SERVER_URL}/event",
    }
    try:
        logger.info(
            f"Sending POST to Vonage SMS API: {sms_url} with payload: {payload}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(sms_url, data=payload, timeout=10.0)
        logger.info(f"Vonage SMS API response: {response.status_code} {response.text}")
        if response.status_code == 200:
            resp_json = response.json()
            if (
                resp_json.get("messages")
                and resp_json["messages"][0].get("status") == "0"
            ):
                logger.info(f"SMS successfully sent to {to}.")
                if not wait_for_result:
                    return f"SMS sent to {to}. Waiting for event tracking is disabled."
                # Wait for event in callback_events
                logger.info(f"Waiting for event in callback_events for msisdn {to}...")
                event_text = await wait_for_sms_callback_event(to)
                if event_text:
                    return (
                        f"SMS sent to {to}.\n"
                        f'Received reply event: "{event_text}"\n'
                        f"Recipient: {to}"
                    )
                else:
                    return f"SMS sent to {to}, but no event received within timeout."
            else:
                error_text = resp_json["messages"][0].get("error-text", "Unknown error")
                logger.error(f"Failed to send SMS: {error_text}")
                return f"Failed to send SMS: {error_text}"
        logger.error(f"Failed to send SMS: {response.status_code} {response.text}")
        return f"Failed to send SMS: {response.status_code} {response.text}"
    except Exception as e:
        logger.exception(f"Error sending SMS: {e}")
        return f"Error sending SMS: {e}"


async def wait_for_sms_callback_event(
    recipient_msisdn: str, max_wait_time: int = 180
) -> str | None:
    """
    Wait for an event at the callback server for the given msisdn.
    Args:
        recipient_msisdn: str - The msisdn to match in the event.
        max_wait_time: int - Maximum time to wait in seconds (default: 180).
    Returns:
        The event text if received, else None.
    """
    import time

    start_time = time.time()
    polling_interval = 1
    logger.info(
        f"Waiting for SMS callback event for msisdn {recipient_msisdn} (timeout {max_wait_time}s)..."
    )
    already_seen = set()
    while (time.time() - start_time) < max_wait_time:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{CALLBACK_SERVER_URL}/events?limit=100")
                if response.status_code == 200:
                    events_data = response.json()
                    events = events_data.get("events", [])
                    for event in reversed(events):
                        body = event.get("body", {})
                        msisdn = body.get("msisdn")
                        text = body.get("text")
                        event_id = event.get("id")
                        if msisdn == recipient_msisdn and event_id not in already_seen:
                            logger.info(
                                f"Received callback event for msisdn {msisdn}: {text}"
                            )
                            already_seen.add(event_id)
                            return text
        except Exception as e:
            logger.warning(f"Error checking callback server events: {e}")
        await asyncio.sleep(polling_interval)
    logger.warning(
        f"Timed out waiting for SMS callback event for msisdn {recipient_msisdn}"
    )
    return None
