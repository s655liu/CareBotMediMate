import json
import asyncio
import os
import re
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

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
        
        # 1. Hide anything from [OPTIONS: onwards
        options_idx = temp_text.lower().find("[options")
        if options_idx != -1:
            temp_text = temp_text[:options_idx]
        else:
            # 2. Hide partial [ at the end to prevent flickering
            if temp_text.endswith("[") or temp_text.endswith("[O") or temp_text.endswith("[OP"):
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

    # Final bot message is the fully cleaned text
    # We use a solid regex to strip tags for the final 'correct' event
    clean_bot_msg = re.sub(r'\[OPTIONS:[\s\S]*$', '', full_reply, flags=re.IGNORECASE)
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
            # Try to fix potential missing closing braces if needed, but start with raw
            assessment_str = json_match.group(1)
            # Basic balancing for sanity
            if assessment_str.count('{') > assessment_str.count('}'):
                assessment_str += '}' * (assessment_str.count('{') - assessment_str.count('}'))
            
            assessment = json.loads(assessment_str)
            final_bot_msg = json.dumps(assessment)
            urgency = assessment.get('urgency')
            yield f"data: {json.dumps({'type': 'assessment', **assessment})}\n\n"
        except: pass



    # Extract options with lenient regex
    options_match = re.search(r'\[OPTIONS:\s*(\[[\s\S]*?\])[\]\)]', full_reply)
    if options_match:
        try:
            options_str = options_match.group(1)
            # Balance brackets if needed
            if options_str.count('[') > options_str.count(']'):
                options_str += ']' * (options_str.count('[') - options_str.count(']'))
            
            options = json.loads(options_str)
            yield f"data: {json.dumps({'type': 'options', 'options': options})}\n\n"
        except: pass

    yield "data: {\"type\": \"done\"}\n\n"
    save_conversation(session_id, "triage", user_message, final_bot_msg, urgency)

async def stream_health(user_message, history, session_id):
    from services.agent_service import health_flow, stream_flow_to_sse
    
    full_reply = ""
    is_thinking = True
    
    yielded_text = ""
    
    async for chunk in stream_flow_to_sse(health_flow, user_message, history):
        full_reply += chunk
        
        # Hide anything from [FLAG: onwards
        temp_text = full_reply
        flag_idx = temp_text.find("[FLAG")
        if flag_idx == -1:
            flag_idx = temp_text.find("[F") # Prevent flickering
        if flag_idx != -1:
            temp_text = temp_text[:flag_idx]
            
        new_text = temp_text[len(yielded_text):]
        if new_text:
            yield f"data: {json.dumps({'type': 'chunk', 'text': new_text})}\n\n"
            yielded_text += new_text

    final_bot_msg = ""
    severity = None
    
    final_bot_msg = full_reply.split("[FLAG:")[0].strip()
    
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

    yield "data: {\"type\": \"done\"}\n\n"
    save_conversation(session_id, "health", user_message, final_bot_msg, severity)

@app.post("/triage")
async def triage_endpoint(request: ChatRequest):
    return StreamingResponse(stream_triage(request.message, request.history, request.sessionId), media_type="text/event-stream")

@app.post("/health-assistant")
async def health_endpoint(request: ChatRequest):
    return StreamingResponse(stream_health(request.message, request.history, request.sessionId), media_type="text/event-stream")

@app.post("/clear")
async def clear_endpoint():
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
        return {"message": "Python backend is running. (Build folder not found)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
