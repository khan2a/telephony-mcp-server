import asyncio
from dotenv import load_dotenv
import os
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

logger.info("Loading environment variables from .env file...")
load_dotenv()


async def main():
    logger.info("== Starting Telephony MCP Server ==")
    

if __name__ == "__main__":
    asyncio.run(main())