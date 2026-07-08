"""Ahjo Meeting Briefing Agent - Foundry hosted agent (responses protocol).

This serves as the HTTP entry point for the Foundry hosted agent runtime.
It implements the OpenAI Responses API protocol on port 8088.
"""

import json
import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from ahjo_tools import TOOLS, handle_tool_call

SYSTEM_PROMPT = """You are a Helsinki municipal meeting briefing assistant. You help users understand 
upcoming meetings in the Ahjo decision-making system and relate them to Helsinki's City Strategy 2025-2029.

Your capabilities:
- List upcoming meetings from the Ahjo calendar API
- Get meeting agenda items (decisions) for a specific meeting
- Retrieve full decision content including attachments
- Extract and summarize text from PDF attachments
- Search Helsinki City Strategy 2025-2029 for strategic context on any topic

When a user asks about upcoming meetings, use get_meetings to find them.
When they want details about a specific meeting, use get_meeting_agenda to list its decisions.
When they want to understand a specific decision, use get_decision_content to get the full text.
When they need a PDF summarized, use extract_pdf_text to get the content.
When relating topics to city strategy or goals, use search_strategy to find relevant passages.

Always respond in the same language the user uses. If they write in Finnish, respond in Finnish.
Be concise but thorough. Focus on what's actionable and relevant.
When discussing meeting topics, proactively note connections to the city strategy when relevant."""

app = FastAPI()

# Lazy-init the OpenAI client
_client = None


def get_client():
    global _client
    if _client is None:
        # The Foundry project endpoint looks like:
        # https://<account>.services.ai.azure.com/api/projects/<project>
        # The OpenAI endpoint is: https://<account>.openai.azure.com/
        endpoint = os.environ.get(
            "FOUNDRY_PROJECT_ENDPOINT",
            os.environ.get("AZURE_AI_PROJECT_ENDPOINT", ""),
        )
        # Derive OpenAI endpoint from account name in Foundry endpoint
        if "services.ai.azure.com" in endpoint:
            account = endpoint.split("//")[1].split(".")[0]
            openai_endpoint = f"https://{account}.openai.azure.com/"
        else:
            openai_endpoint = endpoint

        openai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", openai_endpoint)

        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        _client = AzureOpenAI(
            azure_endpoint=openai_endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2025-04-01-preview",
        )
    return _client


@app.get("/readiness")
def readiness():
    return {"status": "ready"}


def _convert_to_chat_message(msg: dict) -> dict:
    """Convert Responses API message format to Chat Completions format.

    The Responses API uses 'input_text' content type, but Chat Completions
    expects 'text'. Also handles 'output_text' from assistant messages.
    """
    content = msg.get("content")
    if isinstance(content, list):
        converted = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("input_text", "output_text"):
                    converted.append({"type": "text", "text": block.get("text", "")})
                else:
                    converted.append(block)
            else:
                converted.append(block)
        return {"role": msg.get("role", "user"), "content": converted}
    return msg


@app.post("/responses")
async def handle_responses(request: Request):
    """Handle the OpenAI Responses API protocol."""
    try:
        body = await request.json()

        model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
        user_input = body.get("input", "")

        # Build messages from input
        if isinstance(user_input, str):
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input},
            ]
        elif isinstance(user_input, list):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for item in user_input:
                if isinstance(item, dict):
                    messages.append(_convert_to_chat_message(item))
                else:
                    messages.append({"role": "user", "content": str(item)})
        else:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": str(user_input)},
            ]

        client = get_client()

        logging.info(f"Calling model={model} endpoint={client._base_url}")

        # Call with tools, handle tool calls in a loop
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
        )

        # Tool call loop
        while response.choices[0].message.tool_calls:
            assistant_msg = response.choices[0].message
            messages.append(assistant_msg)

            for tc in assistant_msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = handle_tool_call(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
            )

        # Return the final response in responses API format
        final_text = response.choices[0].message.content or ""
        return JSONResponse(content={
            "id": response.id,
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "id": f"msg_{response.id}",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": final_text}],
                }
            ],
        })
    except Exception as e:
        logging.exception("Error in /responses handler")
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": type(e).__name__}},
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8088"))
    uvicorn.run(app, host="0.0.0.0", port=port)
