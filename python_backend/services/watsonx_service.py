import os
from dotenv import load_dotenv
from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
from ibm_watsonx_ai.foundation_models import ModelInference, Embeddings
from ibm_watsonx_ai import APIClient, Credentials
import railtracks as rt
from railtracks.llm.models._litellm_wrapper import LiteLLMWrapper
from railtracks.llm import ModelProvider
import litellm
import logging
import asyncio
import time
from typing import Generator, AsyncGenerator, Type, List, Literal
from pydantic import BaseModel

logger = logging.getLogger(__name__)

load_dotenv()

API_KEY = os.getenv("IBM_CLOUD_API_KEY")
PROJECT_ID = os.getenv("WATSONX_PROJECT_ID")
MODEL_ID = "meta-llama/llama-3-3-70b-instruct"
EMBED_MODEL_ID = "ibm/slate-125m-english-rtrvr-v2"

credentials = {
    "url": "https://us-south.ml.cloud.ibm.com",
    "apikey": API_KEY
}

def get_watsonx_model():
    generate_params = {
        GenParams.MAX_NEW_TOKENS: 1024,
        GenParams.STOP_SEQUENCES: ["<|end_of_text|>", "<|eot_id|>"],
        GenParams.TEMPERATURE: 0.2
    }
    
    return ModelInference(
        model_id=MODEL_ID,
        params=generate_params,
        credentials=credentials,
        project_id=PROJECT_ID
    )

def generate_embedding(text):
    try:
        embeddings = Embeddings(
            model_id=EMBED_MODEL_ID,
            credentials=credentials,
            project_id=PROJECT_ID
        )
        result = embeddings.embed_documents(texts=[text])
        return result[0] if result else None
    except Exception as e:
        print(f"Embedding failed: {e}")
        return None

async def generate_chat_stream(system_prompt, history, user_message):
    model = get_watsonx_model()
    
    # Format Llama 3 prompt
    prompt = f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system_prompt}<|eot_id|>"
    
    for msg in history:
        role = "user" if msg['role'] == 'user' else "assistant"
        prompt += f"<|start_header_id|>{role}<|end_header_id|>\n\n{msg['content']}<|eot_id|>"
    
    prompt += f"<|start_header_id|>user<|end_header_id|>\n\n{user_message}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
    
    stream = model.generate_text_stream(prompt=prompt)
    for chunk in stream:
        yield chunk

from railtracks.llm.model import ModelBase
from railtracks.llm.message import AssistantMessage
from railtracks.llm.history import MessageHistory
from railtracks.llm.response import Response, MessageInfo

class WatsonXLLM(ModelBase):
    """
    Direct implementation of WatsonX LLM using ibm-watsonx-ai SDK.
    Bypasses LiteLLM to ensure stability and correct prompt formatting.
    """
    def __init__(self, model_id: str = MODEL_ID, temperature: float = 0.2, stream: bool = False, **kwargs):
        super().__init__(stream=stream)
        self.model_id = model_id
        self.temperature = temperature
        self.kwargs = kwargs

    @property
    def model_name(self) -> str:
        return self.model_id

    @classmethod
    def model_gateway(cls) -> ModelProvider:
        return ModelProvider.UNKNOWN

    def model_provider(self) -> ModelProvider:
        return ModelProvider.UNKNOWN

    def _format_llama3_prompt(self, messages: MessageHistory | list, system_prompt: str = None):
        prompt = "<|begin_of_text|>"
        if system_prompt:
            prompt += f"<|start_header_id|>system<|end_header_id|>\n\n{system_prompt}<|eot_id|>"
        
        msgs = messages if isinstance(messages, list) else getattr(messages, 'messages', messages)
        for msg in msgs:
            role = str(msg.role)
            if role == "system" and system_prompt: continue
            role_map = {"user": "user", "assistant": "assistant", "system": "system"}
            mapped_role = role_map.get(role, "user")
            prompt += f"<|start_header_id|>{mapped_role}<|end_header_id|>\n\n{msg.content}<|eot_id|>"
        
        prompt += "<|start_header_id|>assistant<|end_header_id|>\n\n"
        return prompt

    def _chat(self, messages: MessageHistory) -> Response | Generator[str | Response, None, Response]:
        prompt = self._format_llama3_prompt(messages)
        model = get_watsonx_model()
        if not self.stream:
            result = model.generate_text(prompt=prompt)
            return Response(message=AssistantMessage(content=result))
        return self._stream_handler(prompt)

    async def _achat(self, messages: MessageHistory) -> Response | Generator[str | Response, None, Response]:
        prompt = self._format_llama3_prompt(messages)
        model = get_watsonx_model()
        if not self.stream:
            result = await asyncio.to_thread(model.generate_text, prompt=prompt)
            return Response(message=AssistantMessage(content=result))
        # Railtracks expects a sync Generator even from _achat
        return self._stream_handler(prompt)

    def generator_wrapper(self, generator, message_history):
        """
        Override to be more lenient with yielded types and avoid the strict final assertion.
        This handles cases where middle layers (like tool call nodes) might translate or consume
        Response objects.
        """
        new_response = None
        for g in generator:
            # Check for Response structurally as well as by type
            is_resp = False
            try:
                # Basic check for Response-like object
                if hasattr(g, 'message') and hasattr(g, 'message_info'):
                    is_resp = True
            except: pass
            
            if is_resp:
                try:
                    # Run post hooks if possible
                    new_response = self._run_post_hooks(message_history, g)
                    if new_response:
                        yield new_response
                    else:
                        new_response = g
                except Exception as e:
                    logger.debug(f"Error running post hooks in generator_wrapper: {e}")
                    new_response = g
            
            yield g
        
        # We omit the strict assertion here to avoid crashing when tool nodes
        # consume the response early.
        return new_response

    def _structured(self, messages: MessageHistory, schema: Type[BaseModel]) -> Response | Generator[str | Response, None, Response]:
        return self._chat(messages)

    async def _astructured(self, messages: MessageHistory, schema: Type[BaseModel]) -> Response | Generator[str | Response, None, Response]:
        return await self._achat(messages)

    def _chat_with_tools(self, messages: MessageHistory, tools: list) -> Response | Generator[str | Response, None, Response]:
        return self._chat(messages)

    async def _achat_with_tools(self, messages: MessageHistory, tools: list) -> Response | Generator[str | Response, None, Response]:
        return await self._achat(messages)

    async def ainvoke(self, *args, **kwargs):
        messages = args[0] if args else kwargs.get('messages')
        if isinstance(messages, str):
            messages = MessageHistory([AssistantMessage(role="user", content=messages)])
        return await self.achat(messages)

    def invoke(self, *args, **kwargs):
        messages = args[0] if args else kwargs.get('messages')
        if isinstance(messages, str):
            messages = MessageHistory([AssistantMessage(role="user", content=messages)])
        return self.chat(messages)

    def _stream_handler(self, prompt):
        import time
        start_time = time.time()
        model = get_watsonx_model()
        stream = model.generate_text_stream(prompt=prompt)
        accumulated_content = ""
        for chunk in stream:
            content = str(chunk)
            accumulated_content += content
            yield content
        
        final_response = Response(message=AssistantMessage(content=accumulated_content), 
                       message_info=MessageInfo(latency=time.time() - start_time))
        yield final_response
        return final_response
