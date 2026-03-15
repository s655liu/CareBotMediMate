# Healthcare AI Diagnoser - Python Backend

This is the Python backend for the Healthcare AI assistant, leveraging the Railtracks framework.

## Features
- **FastAPI Server**: Smooth SSE streaming with typewriter effect.
- **Railtracks Integration**: Robust agent flows for Triage and Health Assistance.
- **WatsonX Integration**: Direct Llama 3 connectivity via WatsonX.
- **Robust RAG**: Semantic retrieval using `numpy` for cosine similarity.
- **Portability**: Automatically falls back to local JSON storage if IBM Db2 drivers are missing.

## How to Run

1. **Setup Env**:
   Ensure you have a `.env` file in the root directory with:
   - `IBM_CLOUD_API_KEY`
   - `WATSONX_PROJECT_ID`
   - `DB2_CREDENTIALS` (Optional)

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Start Server**:
   ```bash
   python main.py
   ```

The backend serves the React frontend automatically at **http://localhost:5000** if the build folder exists.
