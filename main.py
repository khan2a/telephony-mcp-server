import asyncio
import threading
import multiprocessing
from dotenv import load_dotenv
import os
import logging
from servers.telephony_server import telephony_mcp
from servers.callback_server import run_callback_server

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

logger.info("Loading environment variables from .env file...")
load_dotenv()


def start_callback_server_process():
    """Start the callback server in a separate process"""
    logger.info("Starting Vonage Callback Server process")
    run_callback_server()


def main():
    logger.info("== Starting Telephony MCP Server and Vonage Callback Server ==")
    
    # Start the callback server in a separate process
    callback_process = multiprocessing.Process(target=start_callback_server_process)
    callback_process.start()
    logger.info(f"Callback server started with PID: {callback_process.pid}")
    
    try:
        logger.info("Configuring enhanced error logging...")
        # Configure httpx for detailed logging
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.setLevel(logging.DEBUG)
        
        # Configure MCP server with enhanced error logging
        if hasattr(telephony_mcp, 'debug'):
            telephony_mcp.debug = True
        
        # Start the main MCP server in the current process
        telephony_mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.exception(f"Error in MCP server: {e}")
        # Try to extract additional error details if available
        error_body = getattr(e, "body", None) or getattr(e, "detail", None) or getattr(e, "response", None)
        if error_body:
            logger.error(f"Error details: {error_body}")
    finally:
        # Ensure we terminate the callback process when the main process exits
        if callback_process.is_alive():
            logger.info("Terminating callback server")
            callback_process.terminate()
            callback_process.join()


if __name__ == "__main__":
    main()