# CareBot MediMate (CBMM)

Healthcare AI assistant providing AI Triage and Virtual Health Assistance.

## Features
- **AI Triage Bot**: Pre-clinic symptom assessment and urgency sorting.
- **Virtual Health Assistant**: Post-clinic medication management and side-effect tracking.
- **Powered by Railtracks**: Robust agentic framework integration.
- **WatsonX Integration**: Seamless connectivity with IBM WatsonX AI.

## Getting Started

### Prerequisites
- Node.js and npm
- Python 3.x
- IBM WatsonX API Key and Project ID (set in `.env`)

### Setup and Running

1. **Install Dependencies**
   ```bash
   # Root directory (Frontend)
   npm install

   # Backend directory
   cd python_backend
   pip install -r requirements.txt
   cd ..
   ```

2. **Start Backend**
   ```bash
   python python_backend/main.py
   ```

3. **Start Frontend**
   ```bash
   npm start
   ```

The application will be accessible at http://localhost:3000.
