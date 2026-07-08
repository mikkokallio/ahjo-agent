"""Create a native (non-hosted) Foundry agent with Azure AI Search grounding."""

import logging
import sys

from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import AzureAISearchTool, AzureAISearchQueryType

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
# Uncomment for debug: logging.getLogger('azure').setLevel(logging.DEBUG)

ENDPOINT = "https://ai-account-mapx4sxw6thvg.services.ai.azure.com/api/projects/ai-project-ahjo-agent"
CONN_ID = "/subscriptions/f6f84135-2f56-47ac-b4bf-4202248dd5ee/resourceGroups/rg-ahjo-agent/providers/Microsoft.CognitiveServices/accounts/ai-account-mapx4sxw6thvg/projects/ai-project-ahjo-agent/connections/search-ahjo-agent"
INDEX_NAME = "strategy-2025-2029"

INSTRUCTIONS = """You are a Helsinki municipal strategy assistant (Helsingin kaupunkistrategian avustaja).
You help users understand Helsinki's City Strategy 2025-2029 (Kaupunkistrategia 2025-2029).

Your capabilities:
- Search and retrieve content from Helsinki City Strategy 2025-2029
- Explain strategic goals, priorities, actions, and timelines
- Connect questions to relevant parts of the strategy

When answering:
- Always use the search tool to ground your answers in the actual strategy document
- Cite specific content from the strategy
- Respond in the same language the user uses (Finnish/English)
- Be concise but thorough
- If the strategy doesn't cover a topic, say so clearly"""


def main():
    credential = DefaultAzureCredential()
    agents_client = AgentsClient(endpoint=ENDPOINT, credential=credential)

    search_tool = AzureAISearchTool(
        index_connection_id=CONN_ID,
        index_name=INDEX_NAME,
        query_type=AzureAISearchQueryType.SIMPLE,
        top_k=5,
    )

    agent = agents_client.create_agent(
        model="gpt-5.4",
        name="ahjo-strategia",
        description="Helsinki City Strategy 2025-2029 assistant with AI Search grounding (native Foundry agent).",
        instructions=INSTRUCTIONS,
        tools=search_tool.definitions,
        tool_resources=search_tool.resources,
    )
    print(f"Created agent: {agent.name}")
    print(f"  ID: {agent.id}")
    print(f"  Model: {agent.model}")
    print(f"  Tools: {[t.type for t in agent.tools]}")

    # Test
    print("\n--- Testing ---")
    thread = agents_client.threads.create()
    agents_client.messages.create(
        thread_id=thread.id, role="user", content="Mitä strategia sanoo asumisesta?"
    )

    run = agents_client.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)
    print(f"Run status: {run.status}")

    if run.last_error:
        print(f"Error: {run.last_error.code} - {run.last_error.message}")

        # Get run steps for more detail
        steps = agents_client.run_steps.list(thread_id=thread.id, run_id=run.id)
        for step in steps:
            print(f"  Step: {step.type} | {step.status}")
            if step.last_error:
                print(f"    Step error: {step.last_error}")
    else:
        messages = agents_client.messages.list(thread_id=thread.id)
        for msg in messages:
            if msg.role == "assistant":
                for block in msg.content:
                    if hasattr(block, "text"):
                        print(f"\nResponse:\n{block.text.value[:2000]}")
                        break
                break


if __name__ == "__main__":
    main()
