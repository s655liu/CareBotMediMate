import json
import os
import uuid
from datetime import datetime

# Fallback to local storage if ibm_db is not available or fails
LOCAL_DB_FILE = os.path.join(os.path.dirname(__file__), "../data/medical_knowledge.json")
CONVERSATIONS_FILE = os.path.join(os.path.dirname(__file__), "../data/conversations.json")

os.makedirs(os.path.dirname(LOCAL_DB_FILE), exist_ok=True)

DB_AVAILABLE = False
try:
    import ibm_db
    DB_AVAILABLE = True
except Exception as e:
    print(f"Warning: ibm_db driver failed to load ({e}). Using LOCAL JSON FALLBACK.")

def get_db_connection():
    if not DB_AVAILABLE: return None
    dsn = os.getenv("DB2_CREDENTIALS")
    try:
        import ibm_db
        return ibm_db.connect(dsn, "", "")
    except: return None

def initialize_tables():
    if DB_AVAILABLE:
        conn = get_db_connection()
        if conn:
            # (Existing Db2 table initialization logic)
            # ... omitted for brevity ...
            ibm_db.close(conn)
    
    # Ensure local files exist
    if not os.path.exists(LOCAL_DB_FILE):
        with open(LOCAL_DB_FILE, 'w') as f: json.dump([], f)
    if not os.path.exists(CONVERSATIONS_FILE):
        with open(CONVERSATIONS_FILE, 'w') as f: json.dump([], f)

def save_conversation(session_id, mode, user_message, bot_response, urgency=None):
    data = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "mode": mode,
        "user_message": user_message,
        "bot_response": bot_response,
        "urgency": urgency,
        "created_at": datetime.now().isoformat()
    }
    
    # Save locally
    try:
        with open(CONVERSATIONS_FILE, 'r+') as f:
            convs = json.load(f)
            convs.append(data)
            f.seek(0)
            json.dump(convs, f, indent=2)
    except: pass

    # Try Db2 if available
    if DB_AVAILABLE:
        conn = get_db_connection()
        if conn:
            try:
                sql = "INSERT INTO conversations (id, session_id, mode, user_message, bot_response, urgency) VALUES (?, ?, ?, ?, ?, ?)"
                stmt = ibm_db.prepare(conn, sql)
                ibm_db.execute(stmt, (data['id'], session_id, mode, user_message, bot_response, urgency))
                ibm_db.close(conn)
            except: pass

def clear_conversations():
    # Clear locally
    try:
        with open(CONVERSATIONS_FILE, 'w') as f:
            json.dump([], f)
    except: pass
    
    # Clear Db2 if available
    if DB_AVAILABLE:
        conn = get_db_connection()
        if conn:
            try:
                sql = "DELETE FROM conversations"
                ibm_db.exec_immediate(conn, sql)
                ibm_db.close(conn)
            except: pass

def get_all_medical_knowledge():
    if DB_AVAILABLE:
        conn = get_db_connection()
        if conn:
            try:
                sql = "SELECT title, content, embedding FROM medical_knowledge"
                stmt = ibm_db.exec_immediate(conn, sql)
                rows = []
                res = ibm_db.fetch_assoc(stmt)
                while res:
                    rows.append(res)
                    res = ibm_db.fetch_assoc(stmt)
                ibm_db.close(conn)
                return rows
            except: pass
            
    # Fallback to local
    with open(LOCAL_DB_FILE, 'r') as f:
        return json.load(f)
