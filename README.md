# Telephony MCP Server

## Demo Using Claude Desktop
### Agentic Telephony Conversation with Speech Recognition
<img src="./resources/demo-claude-agentic-call-speech-recognition.gif" alt="Agentic Telephony Conversation with Speech Recognition" style="border: 4px solid white; border-radius: 8px;"/>

### <span style="color: yellow;">Use SMS during mid-conversation</span>
<img src="./resources/demo-claude-desktop.gif" alt="Telephony MCP Server Demo" style="border: 4px solid yellow; border-radius: 8px; display: block; margin-left: 0;"/>

### <span style="color: #b5651d;">SMS Enquiry (Send and Receive)</span>
<img src="./resources/demo-claude-sms-enquiry.png" alt="SMS Enquiry" style="border: 4px solid #b5651d; border-radius: 8px; display: block; margin-left: 0;" width="800"/>

## <span style="color: green;">Demo Using GitHub Copilot</span>
<img src="./resources/demo-copilot.gif" alt="Telephony MCP Server Demo" style="border: 4px solid green; border-radius: 8px; display: block; margin-left: 0;"/>

## Introduction

This directory contains MCP (Model Context Protocol) Server tools for telephony operations, such as making voice calls and sending SMS messages using the Vonage API. These tools are designed to be integrated with Large Language Model (LLM) applications, enabling LLMs to perform real-world actions beyond simple text generation.

## LLMs and Tool Integration

LLMs (Large Language Models) are advanced token generators—they can generate text, images, or even video based on input prompts. However, their core capability is limited to generating content; they cannot access external data or perform actions in the real world on their own.

To extend their functionality, LLMs can be connected to external tools. For example, when a user asks, "What's the weather today?" the LLM can invoke a backend API tool like `get_weather(city)` via a system prompt, parse the response, and return the result to the user. This tool-calling mechanism transforms a basic LLM into a powerful LLM Application.

## Tool Calling with MCP and LangChain

- **LangChain** is a popular framework for developing applications powered by LLMs. It provides a collection of pre-built tools (called a Toolkit) that LLMs can use to interact with external systems.
- **MCP** (Model Context Protocol) follows the same concept: it offers a collection of pre-built tools and a framework for writing new tools and handling function calling.
- Both frameworks allow LLMs to invoke tools, parse their outputs, and integrate the results into their responses.

## How This Works

1. **Tool Definition**: In this project, tools like `voice_call` and `send_sms` are defined using the MCP framework. Each tool is a function that can be called by an LLM application.
2. **LLM Application**: When integrated with an LLM (such as OpenAI's GPT, Anthropic's Claude, etc.), the LLM can decide to call these tools based on user prompts.
3. **Execution Flow**:
    - The LLM receives a prompt (e.g., "Call Alice and say hello").
    - The LLM determines that a tool invocation is needed and calls the appropriate MCP tool (e.g., `voice_call`).
    - The tool executes (e.g., initiates a phone call via Vonage) and returns the result.
    - The LLM parses the response and presents it to the user.

## Running the MCP Tools

### Prerequisites

- Python 3.13+
- MCP CLI (`mcp[cli]`), FastAPI, httpx, pyjwt, python-dotenv, uvicorn, pydantic (see `pyproject.toml` for details)
- Vonage API credentials (API key, secret, application ID, private key)
- Public URL for callback server (for production use)

### Setup

1. **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    Or, if using Poetry:
    ```bash
    poetry install
    ```

2. **Configure environment variables**:
    - Create a `.env` file with your Vonage credentials:
      ```
      VONAGE_API_KEY=your_api_key
      VONAGE_API_SECRET=your_api_secret
      VONAGE_APPLICATION_ID=your_app_id
      VONAGE_PRIVATE_KEY_PATH=path/to/private.key
      VONAGE_LVN=your_virtual_number
      VONAGE_API_URL=https://api.nexmo.com/v1/calls
      VONAGE_SMS_URL=https://rest.nexmo.com/sms/json
      CALLBACK_SERVER_URL=https://your-public-url  # URL for Vonage event callbacks
      ```
      
      For the `CALLBACK_SERVER_URL`:
      - In development: You can use `http://localhost:8080` (default if not specified)
      - In production: Use a public URL (such as an ngrok URL or your deployed server)

3. **Run the MCP server**:
    ```bash
    python telephony_server.py
    ```
    The server will start and expose the defined tools for LLM applications.

### Running with Docker

You can also run the telephony MCP server using Docker:

1. **Build and start the Docker container**:
    ```bash
    docker compose up --build
    ```
    Or to run in the background:
    ```bash
    docker compose up --build -d
    ```

2. **Stop the Docker container**:
    ```bash
    docker compose down
    ```

3. **View logs from the Docker container**:
    ```bash
    docker compose logs -f
    ```

### Using with LLM Applications

- **Direct Integration**: Connect your LLM application (e.g., using LangChain via Adapter or a custom MCP client) to the running MCP server. The LLM can now invoke telephony tools as needed.
- **Example**: When the LLM receives a prompt like "Dial this number +123 and read latest news from today", it will call the `voice_call` tool, passing the required parameters.
- **Example**: When the LLM receives a prompt like "Call this number using a British accent", it will call the `voice_call` tool with specific language and style parameters.
- **Example**: When the LLM receives a prompt like "Text the news instead", it will call the `send_sms` tool, passing the required parameters.

### Using with Claude Desktop or other MCP clients

To configure an MCP client (like Claude Desktop) to use your telephony MCP server:

1. **Update your MCP client configuration file** (e.g., `claude_desktop_config.json`):
    ```json
    {
      "mcpServers": {
        "telephony": {
          "command": "docker",
          "args": ["run", "-i", "--rm", "--init", "-e", "DOCKER_CONTAINER=true", "telephony-mcp-server"]
        }
      }
    }
    ```

2. **Build the Docker image** (if not using docker compose):
    ```bash
    docker build -t telephony-mcp-server .
    ```

3. Restart your MCP client to apply the changes.
- 
## Key Concepts

- **LLMs are content generators**: They generate text, images, or video, but need external tools for actions like web search, telephony, or database access.
- **Tool calling**: LLMs can invoke backend APIs (tools) to fetch data or perform actions, then parse and present the results.
- **Frameworks**: Both LangChain and MCP provide a structure for defining, registering, and invoking tools from LLMs.
- **MCP**: Helps you write new tools and manage function calling, making it easy to extend LLM applications with custom capabilities.

## Callback Server for Vonage Events

The Telephony MCP Server also includes a Vonage Callback Server that listens on port 8080. This server is used to receive event notifications from Vonage Voice API, which are sent when voice calls are initiated, completed, or encounter errors.

### Features
- Receives and stores Vonage event callbacks
- Provides endpoints to view and manage stored events
- Runs as a separate service within the same application

### Endpoints
- `GET /` - Health check endpoint
- `POST /event` - Main endpoint for receiving Vonage callbacks
- `GET /events` - List all stored events (with pagination)
- `GET /events/{event_id}` - Get a specific event by ID
- `DELETE /events` - Clear all stored events

### Configuration
To use the callback server with Vonage Voice API, you need to set the `CALLBACK_SERVER_URL` environment variable to your server's public URL. This URL will be used as the `event_url` parameter in Vonage API calls.

```bash
export CALLBACK_SERVER_URL="https://your-public-url"
```

For local development, you can use a service like ngrok to expose your local server to the internet:

```bash
ngrok http 8080
```

Then set the `CALLBACK_SERVER_URL` to the ngrok URL.
