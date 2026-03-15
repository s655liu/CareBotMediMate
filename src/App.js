import React, { useState, useRef, useEffect } from 'react';
import './App.css';

// ─── Mode Config ───────────────────────────────────────────────────────────────
const MODES = {
  triage: {
    id: 'triage',
    label: '🩺 Symptom Triage',
    icon: '🩺',
    name: 'CareBot — AI Triage',
    description: 'Describe your symptoms. I\'ll help determine how urgently you should seek care.',
    placeholder: 'e.g. "I have chest pain when I walk and feel short of breath"',
    endpoint: '/triage',
  },
  health: {
    id: 'health',
    label: '💊 Health Assistant',
    icon: '💊',
    name: 'MedMate — Health Assistant',
    description: 'Your medication coach. Share how you\'re feeling or ask about your treatment.',
    placeholder: 'e.g. "I took my metformin this morning but feel nauseous"',
    endpoint: '/health-assistant',
  },
};

// ─── Urgency Card ──────────────────────────────────────────────────────────────
function UrgencyCard({ urgency, summary, action, reasoning }) {
  const icons = { red: '🚨', yellow: '⚠️', green: '✅' };
  const labels = { red: 'Emergency', yellow: 'See Doctor Soon', green: 'Home Care' };
  return (
    <div className={`urgency-card ${urgency}`}>
      <div className="urgency-header">
        <span>{icons[urgency]}</span>
        <span className="urgency-badge">{labels[urgency]}</span>
        <span className="urgency-title">{summary}</span>
      </div>
      <div className="urgency-action"><strong>Action: </strong>{action}</div>
      {reasoning && <div className="urgency-reasoning">{reasoning}</div>}
    </div>
  );
}

// ─── Main App ──────────────────────────────────────────────────────────────────
function App() {
  const [mode, setMode] = useState('triage');
  // Per-mode state stored as { messages, history, flags }
  const [modeState, setModeState] = useState({
    triage: { messages: [{ role: 'bot', text: 'Hi there, I\'m CareBot. Please describe your symptoms. I\'ll help determine how urgently you should seek care.' }], history: [{ role: 'assistant', content: 'Hi there, I\'m CareBot. Please describe your symptoms. I\'ll help determine how urgently you should seek care.' }], flags: [] },
    health: { messages: [{ role: 'bot', text: 'Hello, I\'m MedMate. I\'m your medication coach. Share how you\'re feeling or ask about your treatment.' }], history: [{ role: 'assistant', content: 'Hello, I\'m MedMate. I\'m your medication coach. Share how you\'re feeling or ask about your treatment.' }], flags: [] },
  });
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const abortControllerRef = useRef(null);
  const sessionIdRef = useRef(null);

  // Initialize sessionId once
  if (!sessionIdRef.current) {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      sessionIdRef.current = crypto.randomUUID();
    } else {
      sessionIdRef.current = 'session-' + Math.random().toString(36).substring(2, 11);
    }
  }

  const { messages, flags } = modeState[mode];
  const currentMode = MODES[mode];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const handleSend_fixed = async () => {
    if (!input.trim() || loading) return;
    const userText = input.trim();
    setInput('');

    const currentHistory = modeState[mode].history;

    // Create a fresh AbortController for this request
    const controller = new AbortController();
    abortControllerRef.current = controller;

    // Append user message AND an empty bot message that we will stream into
    setModeState(prev => ({
      ...prev,
      [mode]: { ...prev[mode], messages: [...prev[mode].messages, { role: 'user', text: userText }, { role: 'bot', text: '', isStreaming: true }] }
    }));
    setLoading(true);

    try {
      const timeoutSignal = AbortSignal.timeout(20000); // 30 second timeout
      const combinedSignal = AbortSignal.any
        ? AbortSignal.any([controller.signal, timeoutSignal])
        : controller.signal;

      const res = await fetch(currentMode.endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify({ 
          message: userText, 
          history: currentHistory,
          sessionId: sessionIdRef.current 
        }),
        signal: combinedSignal,
      });

      if (!res.body) throw new Error('ReadableStream not supported.');

      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let doneReading = false;
      let accumulatedText = '';
      let serverAssessment = null;
      let serverFlag = null;
      let serverError = null;
      let serverOptions = null;

      while (!doneReading) {
        const { value, done } = await reader.read();
        if (done) {
          doneReading = true;
          break;
        }

        const chunkString = decoder.decode(value, { stream: true });
        const lines = chunkString.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.replace('data: ', '').trim();
            if (!dataStr) continue;

            try {
              const parsed = JSON.parse(dataStr);

              if (parsed.type === 'chunk') {
                accumulatedText += parsed.text;
                // Update the last message (the streaming bot message) with the accumulated text
                // eslint-disable-next-line no-loop-func
                setModeState(prev => {
                  const newMessages = [...prev[mode].messages];
                  newMessages[newMessages.length - 1] = { role: 'bot', text: accumulatedText, isStreaming: true };
                  return { ...prev, [mode]: { ...prev[mode], messages: newMessages } };
                });
              } else if (parsed.type === 'assessment') {
                serverAssessment = parsed;
              } else if (parsed.type === 'flag') {
                serverFlag = parsed.flag;
              } else if (parsed.type === 'correct') {
                // Retroactively fix the displayed text (e.g. strip [OPTIONS:...] that leaked during streaming)
                accumulatedText = parsed.text;
                // eslint-disable-next-line no-loop-func
                setModeState(prev => {
                  const newMessages = [...prev[mode].messages];
                  newMessages[newMessages.length - 1] = { role: 'bot', text: accumulatedText, isStreaming: true };
                  return { ...prev, [mode]: { ...prev[mode], messages: newMessages } };
                });
              } else if (parsed.type === 'options') {
                serverOptions = parsed.options;
              } else if (parsed.type === 'error') {
                console.error("Server streamed an error:", parsed.text);
                serverError = parsed.text;
              }
            } catch (e) {
              console.error("Error parsing stream chunk", e, dataStr);
            }
          }
        }
      }

      // Stream is fully consumed. Now finalize the state.
      setModeState(prev => {
        const newMessages = [...prev[mode].messages];
        // Remove the temporary streaming bot message
        newMessages.pop();

        if (mode === 'triage') {
          if (serverAssessment) {
            return {
              ...prev,
              [mode]: {
                ...prev[mode],
                messages: [
                  ...newMessages,
                  ...(accumulatedText.trim() ? [{ role: 'bot', text: accumulatedText }] : []),
                  { role: 'assessment', data: serverAssessment }
                ],
                history: [] // Backend history reset — next message is a fresh triage. On-screen messages stay visible.
              }
            };
          } else {
            return {
              ...prev,
              [mode]: {
                ...prev[mode],
                messages: [...newMessages, { role: 'bot', text: serverError ? `⚠️ Error: ${serverError}` : (accumulatedText || 'Could you describe your symptoms more?'), options: serverOptions }],
                history: [
                  ...prev[mode].history,
                  { role: 'user', content: userText },
                  { role: 'assistant', content: serverError || accumulatedText },
                ]
              }
            };
          }
        } else {
          // Health Assistant mode
          return {
            ...prev,
            [mode]: {
              ...prev[mode],
              messages: [...newMessages, { role: 'bot', text: serverError ? `⚠️ Error: ${serverError}` : (accumulatedText || 'I had trouble responding. Please try again.') }],
              history: [
                ...prev[mode].history,
                { role: 'user', content: userText },
                { role: 'assistant', content: serverError || accumulatedText },
              ],
              flags: serverFlag ? [...prev[mode].flags, serverFlag] : prev[mode].flags,
            }
          };
        }
      });

    } catch (err) {
      if (err.name === 'AbortError') {
        // User cleared the chat mid-stream — this is expected, do nothing
        return;
      }
      console.error('API error:', err);
      setModeState(prev => {
        const newMessages = [...prev[mode].messages];
        // Pop the streaming bubble if it failed mid-stream
        if (newMessages[newMessages.length - 1]?.isStreaming) {
          newMessages.pop();
        }
        return {
          ...prev,
          [mode]: {
            ...prev[mode],
            messages: [...newMessages, { role: 'bot', text: '⚠️ Could not reach the server.' }]
          }
        }
      });
    }

    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend_fixed();
    }
  };

  const switchMode = (newMode) => {
    setMode(newMode);
    setInput('');
    setLoading(false);
  };

  const handleClearChat = () => {
    // Abort any in-flight AI request
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setLoading(false);
    // Call the backend to clear stored database conversations for a fully clean slate
    fetch('/clear', { 
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: sessionIdRef.current })
    }).catch(e => console.error("Error clearing backend chat:", e));

    setModeState(prev => ({
      ...prev,
      [mode]: {
        messages: [{ role: 'bot', text: mode === 'triage' ? 'Hi there, I\'m CareBot. Please describe your symptoms. I\'ll help determine how urgently you should seek care.' : 'Hello, I\'m MedMate. I\'m your medication coach. Share how you\'re feeling or ask about your treatment.' }],
        history: [{ role: 'assistant', content: mode === 'triage' ? 'Hi there, I\'m CareBot. Please describe your symptoms. I\'ll help determine how urgently you should seek care.' : 'Hello, I\'m MedMate. I\'m your medication coach. Share how you\'re feeling or ask about your treatment.' }],
        flags: []
      }
    }));
    setInput('');
  };

  // ─── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="app">
      {/* Header */}
      <header className="app-header">
        <div className="logo">
          <span className="logo-icon">🏥</span>
          <div>
            <h1>HealthAI Assistant</h1>
            <div className="subtitle">Powered by IBM watsonx.ai · Not a substitute for professional medical advice</div>
          </div>
        </div>
      </header>

      {/* Mode Tabs */}
      <div className="mode-tabs">
        {Object.values(MODES).map(m => (
          <button
            key={m.id}
            id={`tab-${m.id}`}
            className={`mode-tab ${mode === m.id ? 'active' : ''}`}
            onClick={() => switchMode(m.id)}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Chat */}
      <div className="chat-container">
        <div className="chat-mode-header">
          <span className="mode-icon">{currentMode.icon}</span>
          <div style={{ flex: 1 }}>
            <strong>{currentMode.name}</strong>
            <div>{currentMode.description}</div>
          </div>
          <button
            id="clear-chat-button"
            className="clear-button"
            onClick={handleClearChat}
            title="Clear chat history"
          >
            🗑 Clear
          </button>
        </div>

        <div className="messages">
          {messages.length === 0 && !loading && (
            <div className="empty-state">
              <div className="empty-icon">{currentMode.icon}</div>
              <h3>How can I help you today?</h3>
              <p>{currentMode.description}</p>
            </div>
          )}

          {messages.map((msg, i) => {
            if (msg.role === 'user') {
              if (msg.text === "Please provide the final triage assessment based on my symptoms now.") return null;
              return (
                <div key={i} className="bubble-row user">
                  <div className="avatar user">You</div>
                  <div className="bubble user">{msg.text}</div>
                </div>
              );
            }
            if (msg.role === 'assessment') {
              return (
                <div key={i} className="bubble-row bot">
                  <div className="avatar bot">🩺</div>
                  <UrgencyCard {...msg.data} />
                </div>
              );
            }
            const isLastMessage = i === messages.length - 1;
            return (
              <div key={i} className="bubble-row bot">
                <div className="avatar bot">{currentMode.icon}</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxWidth: '75%' }}>
                  <div className={`bubble bot${msg.isStreaming && !msg.text ? ' typing' : ''}`}>
                    {msg.isStreaming && !msg.text ? 'Thinking…' : msg.text}
                  </div>
                  {msg.options && isLastMessage && !loading && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', paddingLeft: '4px' }}>
                      {msg.options.map((opt, oi) => (
                        <button
                          key={oi}
                          className="option-chip"
                          onClick={() => {
                            setInput(opt);
                            setTimeout(() => document.getElementById('send-button')?.click(), 0);
                          }}
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {loading && !messages.some(m => m.isStreaming) && (
            <div className="bubble-row bot">
              <div className="avatar bot">{currentMode.icon}</div>
              <div className="bubble bot typing">Analyzing…</div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Analyze Now Button for Triage */}
        {mode === 'triage' && messages.length >= 2 && !loading && !messages.some(m => m.role === 'assessment') && (
          <div className="analyze-now-container">
            <button
              className="analyze-now-button"
              onClick={() => {
                setInput("Please provide the final triage assessment based on my symptoms now.");
                setTimeout(() => document.getElementById('send-button')?.click(), 0);
              }}
            >
              ⚡ Analyze Now
            </button>
          </div>
        )}

        {/* Flagged side effects (health mode only) */}
        {mode === 'health' && flags.length > 0 && (
          <div className="flags-panel">
            <h4>⚠️ Flagged Side Effects</h4>
            <div className="flags-list">
              {flags.map((f, i) => (
                <span key={i} className="flag-chip">
                  {f.detail}{f.severity != null ? ` (${f.severity}/10)` : ''}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Input */}
        <div className="input-area">
          <input
            id="chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={currentMode.placeholder}
            disabled={loading}
            autoComplete="off"
          />
          <button
            id="send-button"
            className="send-button"
            onClick={handleSend_fixed}
            disabled={loading || !input.trim()}
          >
            {loading ? '…' : 'Send'}
          </button>
        </div>
      </div>

      <div className="disclaimer">
        ⚠️ This tool is for informational purposes only. Always consult a qualified healthcare professional for medical advice, diagnosis, or treatment.
      </div>
    </div>
  );
}

export default App;
