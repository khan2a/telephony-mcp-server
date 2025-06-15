import asyncio
from dotenv import load_dotenv
import os
import logging
from servers.telephony_server import telephony_mcp

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

logger.info("Loading environment variables from .env file...")
load_dotenv()


def main():
    logger.info("== Starting Telephony MCP Server ==")
    telephony_mcp.run(transport="streamable-http", mount_path="/telephony")
    

if __name__ == "__main__":
    asyncio.run(main())