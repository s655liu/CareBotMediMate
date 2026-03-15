import railtracks as rt
from services.rag_service import search_medical_guidelines
from services.watsonx_service import WatsonXLLM
from config import TRIAGE_SYSTEM_PROMPT, HEALTH_ASSISTANT_SYSTEM_PROMPT
import json
import asyncio
import copy
from railtracks.pubsub.messages import Streaming
from railtracks.llm import MessageHistory, UserMessage, AssistantMessage

# 1. Define Tools
@rt.function_node
async def lookup_medical_knowledge(query: str) -> str:
    """Search for relevant medical guidelines and knowledge based on a query."""
    results = await search_medical_guidelines(query)
    if not results:
        return "No specific medical guidelines found for this query."
    return results

# 2. Define LLM
watson_llm = WatsonXLLM(stream=True)

# 3. Define Triage Agent
triage_agent = rt.agent_node(
    "Triage Assistant",
    tool_nodes=[lookup_medical_knowledge],
    llm=watson_llm,
    system_message=TRIAGE_SYSTEM_PROMPT
)

# 4. Define Health Assistant Agent
health_agent = rt.agent_node(
    "Health Assistant",
    tool_nodes=[lookup_medical_knowledge],
    llm=watson_llm,
    system_message=HEALTH_ASSISTANT_SYSTEM_PROMPT
)

# 5. Define Flows
triage_flow = rt.Flow(name="Triage Flow", entry_point=triage_agent)
health_flow = rt.Flow(name="Health Assistant Flow", entry_point=health_agent)

async def stream_flow_to_sse(flow: rt.Flow, user_message: str, history: list = None):
    """
    Helper to run a flow and yield SSE-formatted chunks.
    """
    try:
        # 1. Map history to Railtracks MessageHistory
        rt_history = MessageHistory([])
        if history:
            for msg in history:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                if role == 'user':
                    rt_history.append(UserMessage(content))
                elif role == 'assistant':
                    rt_history.append(AssistantMessage(content))
        
        # 2. Add current message
        rt_history.append(UserMessage(user_message))

        # ainvoke returns a generator when streaming is enabled in the LLM
        # We pass the history as the user_input to the flow
        result_gen = await flow.ainvoke(rt_history)
        
        if hasattr(result_gen, '__aiter__'):
            async for chunk in result_gen:
                if isinstance(chunk, str):
                    yield chunk
                await asyncio.sleep(0)
        elif hasattr(result_gen, '__iter__'):
            for chunk in result_gen:
                if isinstance(chunk, str):
                    yield chunk
        else:
            # Handle single result
            if hasattr(result_gen, 'content'):
                yield str(result_gen.content)
            elif hasattr(result_gen, 'message') and hasattr(result_gen.message, 'content'):
                yield str(result_gen.message.content)
            else:
                yield str(result_gen)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Flow execution error: {e}")
        yield f"Error: {str(e)}"
