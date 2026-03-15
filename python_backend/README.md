# Healthcare AI Diagnoser - Python Backend

This is the Python port of the Healthcare AI backend, designed for portability and Langflow integration.

## Features
- **FastAPI Server**: Smooth SSE streaming with typewriter effect.
- **WatsonX Integration**: Direct Llama 3 connectivity with thinking/internal monologue stripping.
- **Robust RAG**: Semantic retrieval using `numpy` for cosine similarity.
- **Portability**: Automatically falls back to local JSON storage if IBM Db2 drivers are missing or incompatible.
- **Langflow Ready**: Includes a `langflow_service.py` to call visual flows via REST API.

## How to Run

1. **Setup Env**:
   Ensure you have a `.env` file in the root directory (one level up from this folder) with:
   - `IBM_CLOUD_API_KEY`
   - `WATSONX_PROJECT_ID`
   - `DB2_CREDENTIALS` (Optional: will fallback to local JSON if missing)

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Ingest Data** (Only needed once):
   ```bash
   python ingest_rag.py
   ```

4. **Start Server**:
   ```bash
   python main.py
   ```

5. **Access UI**:
   The backend serves the React frontend automatically at **http://localhost:5000**.

## Langflow Integration
The `services/langflow_service.py` is configured to call a Langflow server. To use it instead of direct WatsonX calls:
1. Start your Langflow server (usually port 7860).
2. Set `LANGFLOW_API_URL` and your `FLOW_ID` in your `.env`.
3. Update `main.py` to call `run_langflow_flow` instead of `generate_chat_stream`.
