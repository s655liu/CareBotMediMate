import json
import asyncio
import os
import re
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Any

from services.watsonx_service import generate_chat_stream
from services.rag_service import search_medical_guidelines
from services.db_service import initialize_tables, save_conversation
from config import TRIAGE_SYSTEM_PROMPT, HEALTH_ASSISTANT_SYSTEM_PROMPT
from services.agent_service import triage_flow, health_flow
import railtracks as rt

app = FastAPI(title="Healthcare AI Diagnoser (Python/Railtracks)")

# Helper for typewriter effect
async def sleep(ms):
    await asyncio.sleep(ms / 1000.0)

# Task tracking for cancellation
# session_id -> asyncio.Task
active_tasks = {}

def register_task(session_id: str, task: Any):
    if not task: return
    # Cancel existing task for this session if any (prevent double-agent)
    if session_id in active_tasks:
        try:
            old_task = active_tasks.get(session_id)
            if old_task and not old_task.done():
                old_task.cancel()
        except: pass
    active_tasks[session_id] = task

def unregister_task(session_id: str):
    if session_id in active_tasks:
        active_tasks.pop(session_id, None)

@app.middleware("http")
async def cleanup_tasks_middleware(request: Request, call_next):
    # This middle ware can help but we'll mainly rely on explicit unregistering in the generators
    response = await call_next(request)
    return response

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    initialize_tables()

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = []
    sessionId: Optional[str] = "anon-session"

async def stream_triage(user_message, history, session_id):
    from services.agent_service import triage_flow, stream_flow_to_sse
    
    full_reply = ""
    yielded_text = ""
    
    async for chunk in stream_flow_to_sse(triage_flow, user_message, history):
        full_reply += chunk
        
        # Aggressively hide potential tags
        temp_text = full_reply
        
        # 1. Hide tags
        options_idx = temp_text.lower().find("[options")
        num_input_idx = temp_text.lower().find("[number_input")
        
        hide_idx = -1
        if options_idx != -1: hide_idx = options_idx
        if num_input_idx != -1 and (hide_idx == -1 or num_input_idx < hide_idx): hide_idx = num_input_idx
        
        if hide_idx != -1:
            temp_text = temp_text[:hide_idx]
        else:
            # 2. Hide partial [ at the end to prevent flickering
            if temp_text.endswith("[") or temp_text.endswith("[O") or temp_text.endswith("[N"):
                temp_text = temp_text.rsplit("[", 1)[0]
        
        # 3. Hide anything from { "type": "assessment" onwards
        assessment_idx = temp_text.find('{"type": "assessment"')
        if assessment_idx == -1:
            assessment_idx = temp_text.find('{"type":"assessment"')
        if assessment_idx == -1:
            assessment_idx = temp_text.find('{ "type": "assessment"')
            
        if assessment_idx != -1:
            temp_text = temp_text[:assessment_idx]
        
        new_text = temp_text[len(yielded_text):]
        if new_text:
            yield f"data: {json.dumps({'type': 'chunk', 'text': new_text})}\n\n"
            yielded_text += new_text
            await asyncio.sleep(0.02) # Pacing for typewriter effect
    
    # Final bot message is the fully cleaned text
    clean_bot_msg = re.sub(r'\[OPTIONS:[\s\S]*$', '', full_reply, flags=re.IGNORECASE)
    clean_bot_msg = re.sub(r'\[NUMBER_INPUT:[\s\S]*$', '', clean_bot_msg, flags=re.IGNORECASE)
    clean_bot_msg = re.sub(r'\{[\s\S]*"type"\s*:\s*"assessment"[\s\S]*$', '', clean_bot_msg)
    clean_bot_msg = clean_bot_msg.strip()
    
    # Send correction to wipe any leaked partial tags
    yield f"data: {json.dumps({'type': 'correct', 'text': clean_bot_msg})}\n\n"
    
    final_bot_msg = clean_bot_msg
    urgency = None
    
    # Extract assessment with lenient regex
    json_match = re.search(r'(\{[\s\S]*"type"\s*:\s*"assessment"[\s\S]*\})', full_reply)
    if json_match:
        try:
            assessment_str = json_match.group(1)
            if assessment_str.count('{') > assessment_str.count('}'):
                assessment_str += '}' * (assessment_str.count('{') - assessment_str.count('}'))
            
            assessment = json.loads(assessment_str)
            final_bot_msg = json.dumps(assessment)
            urgency = assessment.get('urgency')
            yield f"data: {json.dumps({'type': 'assessment', **assessment})}\n\n"
        except: pass

    # Extract options
    options_match = re.search(r'\[OPTIONS:?\s*(\[[\s\S]*?\])\s*\]?', full_reply, flags=re.IGNORECASE)
    if options_match:
        try:
            options_str = options_match.group(1).strip()
            if options_str.count('[') > options_str.count(']'):
                options_str += ']' * (options_str.count('[') - options_str.count(']'))
            
            try:
                options = json.loads(options_str)
            except:
                import ast
                options = ast.literal_eval(options_str)
            if isinstance(options, list):
                yield f"data: {json.dumps({'type': 'options', 'options': options})}\n\n"
        except: pass

    # Extract Number Input
    num_match = re.search(r'\[NUMBER_INPUT:?\s*(\{[\s\S]*?\})\s*\]?', full_reply, flags=re.IGNORECASE)
    if num_match:
        try:
            num_data = json.loads(num_match.group(1))
            yield f"data: {json.dumps({'type': 'number_input', 'data': num_data})}\n\n"
        except: pass

    yield "data: {\"type\": \"done\"}\n\n"
    save_conversation(session_id, "triage", user_message, final_bot_msg, urgency)

async def stream_health(user_message, history, session_id):
    from services.agent_service import health_flow, stream_flow_to_sse
    
    full_reply = ""
    yielded_text = ""
    
    async for chunk in stream_flow_to_sse(health_flow, user_message, history):
        full_reply += chunk
        
        # Hide tags
        temp_text = full_reply
        flag_idx = temp_text.find("[FLAG")
        num_idx = temp_text.find("[NUMBER_INPUT")
        
        hide_idx = -1
        if flag_idx != -1: hide_idx = flag_idx
        if num_idx != -1 and (hide_idx == -1 or num_idx < hide_idx): hide_idx = num_idx
        
        if hide_idx != -1:
            temp_text = temp_text[:hide_idx]
        else:
            if temp_text.endswith("[") or temp_text.endswith("[F") or temp_text.endswith("[N"):
                temp_text = temp_text.rsplit("[", 1)[0]
            
        new_text = temp_text[len(yielded_text):]
        if new_text:
            yield f"data: {json.dumps({'type': 'chunk', 'text': new_text})}\n\n"
            yielded_text += new_text
            await asyncio.sleep(0.02) # Pacing for typewriter effect

    final_bot_msg = ""
    severity = None
    
    final_bot_msg = full_reply.split("[FLAG:")[0].split("[NUMBER_INPUT:")[0].strip()
    
    # Send a correction event to finalize the text exactly
    yield f"data: {json.dumps({'type': 'correct', 'text': final_bot_msg})}\n\n"
        
    flag_match = re.search(r'\[FLAG:\s*(\{[\s\S]*?\})\]', full_reply)
    if flag_match:
        try:
            flag = json.loads(flag_match.group(1))
            final_bot_msg = f"{final_bot_msg}\n(Severity: {flag.get('severity', 'unknown')})"
            severity = flag.get('severity')
            yield f"data: {json.dumps({'type': 'flag', **flag})}\n\n"
        except: pass

    # Extract Number Input
    num_match_health = re.search(r'\[NUMBER_INPUT:?\s*(\{[\s\S]*?\})\s*\]?', full_reply, flags=re.IGNORECASE)
    if num_match_health:
        try:
            num_data = json.loads(num_match_health.group(1))
            yield f"data: {json.dumps({'type': 'number_input', 'data': num_data})}\n\n"
        except: pass

    yield "data: {\"type\": \"done\"}\n\n"
    save_conversation(session_id, "health", user_message, final_bot_msg, severity)

@app.post("/triage")
async def triage_endpoint(request: ChatRequest):
    async def wrapped_generator():
        session_id = request.sessionId or "anon-session"
        register_task(session_id, asyncio.current_task())
        try:
            async for chunk in stream_triage(request.message, request.history, session_id):
                yield chunk
        finally:
            unregister_task(session_id)
            
    return StreamingResponse(wrapped_generator(), media_type="text/event-stream")

@app.post("/health-assistant")
async def health_endpoint(request: ChatRequest):
    async def wrapped_generator():
        session_id = request.sessionId or "anon-session"
        register_task(session_id, asyncio.current_task())
        try:
            async for chunk in stream_health(request.message, request.history, session_id):
                yield chunk
        finally:
            unregister_task(session_id)
            
    return StreamingResponse(wrapped_generator(), media_type="text/event-stream")

class ClearRequest(BaseModel):
    sessionId: Optional[str] = None

@app.post("/clear")
async def clear_endpoint(request: ClearRequest):
    if request.sessionId and request.sessionId in active_tasks:
        try:
            task = active_tasks.get(request.sessionId)
            if task and not task.done():
                task.cancel()
        except: pass
        unregister_task(request.sessionId)
    from services.db_service import clear_conversations
    clear_conversations()
    return {"status": "cleared"}

# Serve React App
build_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../build"))
if os.path.exists(build_path):
    app.mount("/static", StaticFiles(directory=os.path.join(build_path, "static")), name="static")
    @app.get("/{full_path:path}")
    async def serve_react(full_path: str):
        if full_path.startswith("triage") or full_path.startswith("health-assistant") or full_path == "":
            return FileResponse(os.path.join(build_path, "index.html"))
        target = os.path.join(build_path, full_path)
        if os.path.exists(target):
            return FileResponse(target)
        return FileResponse(os.path.join(build_path, "index.html"))
else:
    @app.get("/")
    def read_root():
        return {"message": "Python backend is running."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
