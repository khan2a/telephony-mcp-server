import asyncio
import json
import logging
import traceback
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Response
import uvicorn
from typing import Dict, List, Any, Optional, Callable
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Create a FastAPI instance for the callback server
callback_app = FastAPI(title="Vonage Callback Server")

# Middleware for logging requests and responses
@callback_app.middleware("http")
async def log_requests(request: Request, call_next: Callable):
    # Generate request ID
    request_id = f"req_{datetime.now().timestamp()}"
    
    # Log request details
    logger.info(f"Request [{request_id}]: {request.method} {request.url}")
    logger.debug(f"Request headers: {dict(request.headers)}")
    
    # Process request
    try:
        response = await call_next(request)
        
        # Log response status
        logger.info(f"Response [{request_id}]: {response.status_code}")
        
        # For error responses, try to provide more detail
        if response.status_code >= 400:
            logger.error(f"HTTP Error {response.status_code} for request {request_id}")
            # Note: In FastAPI we can't easily access the response body here
            # without consuming it, which would break the response
            
        return response
    except Exception as e:
        logger.exception(f"Unhandled exception in request [{request_id}]: {e}")
        raise

# In-memory storage for callback events
# For production, consider using a database
callback_events: List[Dict[str, Any]] = []


class CallbackEvent(BaseModel):
    """Model to represent a callback event"""
    id: str
    timestamp: datetime
    endpoint: str
    method: str
    headers: Dict[str, str]
    query_params: Dict[str, str]
    body: Dict[str, Any]


@callback_app.get("/")
async def root():
    """Root endpoint for health check"""
    return {"status": "ok", "service": "Vonage Callback Server"}


@callback_app.post("/event")
async def receive_event(request: Request):
    """Main endpoint to receive Vonage Voice API events"""
    try:
        # Log request headers for debugging
        logger.info(f"Received callback request to {request.url}")
        logger.debug(f"Request headers: {dict(request.headers)}")
        
        # Get the raw body
        body_bytes = await request.body()
        
        # Log the raw body for debugging
        logger.debug(f"Raw body: {body_bytes}")
        
        # Parse JSON if possible
        try:
            body = json.loads(body_bytes)
            
            # Log the event with appropriate detail level
            if "speech" in body:
                # This is a speech recognition event - log it prominently
                speech_results = body.get("speech", {}).get("results", [])
                if speech_results:
                    recognized_text = speech_results[0].get("text", "")
                    confidence = speech_results[0].get("confidence", 0)
                    conversation_uuid = body.get("conversation_uuid", "unknown")
                    
                    # Log with highlighting to make it stand out
                    logger.info("=" * 60)
                    logger.info(f"SPEECH RECOGNITION EVENT RECEIVED!")
                    logger.info(f"Text: '{recognized_text}'")
                    logger.info(f"Confidence: {confidence}")
                    logger.info(f"Conversation UUID: {conversation_uuid}")
                    logger.info(f"Complete speech data: {json.dumps(body.get('speech'), indent=2)}")
                    logger.info("=" * 60)
                else:
                    logger.info("Speech recognition event received but no results found")
                    logger.debug(f"Empty speech event data: {json.dumps(body, indent=2)}")
                
            else:
                # Regular event
                logger.info(f"Successfully parsed JSON body: {json.dumps(body, indent=2)}")
                
        except json.JSONDecodeError as json_err:
            body_text = str(body_bytes, 'utf-8', errors='replace')
            logger.error(f"Failed to parse JSON: {json_err}")
            logger.error(f"Raw body content: {body_text}")
            body = {"raw": body_text, "parse_error": str(json_err)}
        
        # Create an event record
        event = {
            "id": f"evt_{len(callback_events) + 1}_{datetime.now().timestamp()}",
            "timestamp": datetime.now().isoformat(),
            "endpoint": str(request.url),
            "method": request.method,
            "headers": dict(request.headers),
            "query_params": dict(request.query_params),
            "body": body
        }
        
        # Store the event
        callback_events.append(event)
        
        logger.info(f"Received callback event: {event['id']}")
        logger.debug(f"Event data: {json.dumps(event, indent=2)}")
        
        # Return a success response to Vonage
        return {"status": "success", "message": "Event received"}
    
    except Exception as e:
        # Get detailed traceback
        tb = traceback.format_exc()
        logger.error(f"Error processing callback event: {str(e)}")
        logger.error(f"Traceback: {tb}")
        
        # Try to extract as much information as possible from the failed request
        error_details = {
            "error": str(e),
            "traceback": tb,
            "url": str(getattr(request, "url", "unknown")),
            "method": getattr(request, "method", "unknown"),
            "headers": dict(getattr(request, "headers", {})),
            "timestamp": datetime.now().isoformat()
        }
        
        # Store the error event
        callback_events.append({
            "id": f"err_{len(callback_events) + 1}_{datetime.now().timestamp()}",
            "error": error_details
        })
        
        # Return a detailed error response
        raise HTTPException(status_code=500, detail=str(error_details))


@callback_app.get("/events")
async def list_events(limit: int = 100, skip: int = 0):
    """Endpoint to retrieve stored events"""
    return {
        "count": len(callback_events),
        "events": callback_events[skip:skip+limit]
    }


@callback_app.get("/events/{event_id}")
async def get_event(event_id: str):
    """Endpoint to retrieve a specific event by ID"""
    for event in callback_events:
        if event["id"] == event_id:
            return event
    raise HTTPException(status_code=404, detail=f"Event with ID {event_id} not found")


@callback_app.get("/event")
async def get_speech_events():
    """Endpoint to retrieve only speech recognition events"""
    speech_events = []
    
    for event in callback_events:
        body = event.get("body", {})
        if isinstance(body, dict) and "speech" in body:
            speech_results = body.get("speech", {}).get("results", [])
            if speech_results:
                speech_events.append({
                    "id": event["id"],
                    "timestamp": event["timestamp"],
                    "conversation_uuid": body.get("conversation_uuid"),
                    "text": speech_results[0].get("text", ""),
                    "confidence": speech_results[0].get("confidence", 0),
                    "complete_event": event
                })
    
    return {
        "count": len(speech_events),
        "speech_events": speech_events
    }


@callback_app.delete("/events")
async def clear_events():
    """Endpoint to clear all stored events"""
    global callback_events
    count = len(callback_events)
    callback_events = []
    return {"status": "success", "cleared": count}


async def start_callback_server():
    """Function to start the callback server in a separate process"""
    # Configure Uvicorn with enhanced logging
    config = uvicorn.Config(
        app=callback_app,
        host="0.0.0.0",
        port=8080,
        log_level="debug",  # More detailed logging
        access_log=True,    # Log all access requests
        timeout_keep_alive=65,  # Longer keep-alive for debugging
    )
    server = uvicorn.Server(config)
    logger.info("Starting Vonage Callback Server on port 8080 with enhanced logging")
    await server.serve()


def run_callback_server():
    """Function to run the callback server in the current thread (blocking)"""
    # Log info before starting
    logger.info("Initializing Vonage Callback Server with enhanced error logging")
    
    # Configure more detailed logging for uvicorn
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s [%(process)d] [%(levelname)s] %(name)s: %(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "DEBUG"},
            "uvicorn.error": {"handlers": ["default"], "level": "DEBUG", "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": "DEBUG", "propagate": False},
        },
    }
    
    # Run the server with enhanced logging
    uvicorn.run(
        callback_app, 
        host="0.0.0.0", 
        port=8080, 
        log_level="debug",
        access_log=True,
        log_config=log_config
    )
