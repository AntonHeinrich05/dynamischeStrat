# KI Trader – Multi-Provider (Gemini, Groq, Grok, DeepSeek, Mistral)

Der KI Trader unterstützt jetzt neben Google Gemini auch weitere **kostenlose** LLM-Provider,
die alle für Krypto-Daytrading-Analysen und den integrierten Chat verwendet werden können.
Der Wechsel des Modells erfolgt bequem im Setup-Dropdown des KI-Panels – jedes Modell wird
dabei automatisch dem passenden Provider zugeordnet.

## Verfügbare Provider und Modelle

| Provider | Modell (Anzeige) | Model-ID | Kosten |
|----------|------------------|----------|--------|
| **Gemini** (Google) | Gemini 3.5 Flash / 3.1 Pro / 3.1 Flash-Lite | `gemini-3.5-flash`, `gemini-3.1-pro-preview`, `gemini-3.1-flash-lite` | Free-Tier |
| **Groq** | Llama 3.3 70B / Llama 3.1 8B Instant / Qwen3 32B | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `qwen/qwen3-32b` | Free-Tier (sehr schnell) |
| **OpenRouter** | **Grok 4 Fast** (xAI) / DeepSeek V3.1 / DeepSeek R1 / Llama 3.3 70B | `x-ai/grok-4-fast:free`, `deepseek/deepseek-chat-v3.1:free`, `deepseek/deepseek-r1:free`, `meta-llama/llama-3.3-70b-instruct:free` | Free-Tier (Modelle mit `:free`-Suffix) |
| **Mistral** | Mistral Small / Open-Mistral 7B | `mistral-small-latest`, `open-mistral-7b` | Free-Tier |

## API-Keys (kostenlos)

Die Keys werden ausschließlich als **Environment-Variablen in Render** hinterlegt und niemals
im Code oder Frontend ausgeliefert. Wer einen Provider nicht nutzen möchte, lässt den
zugehörigen Key einfach leer.

| ENV-Variable | Wo bekommt man den Key? |
|--------------|--------------------------|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys |
| `OPENROUTER_REFERER` *(optional)* | eigene Domain, z.B. `https://krypto-alert.onrender.com` |
| `OPENROUTER_TITLE` *(optional)* | freier Text, z.B. `Krypto Alert KI Trader` |

Die `render.yaml` wurde bereits um Platzhalter für alle vier Keys erweitert. Beim ersten
Deploy müssen die gewünschten Keys in Render manuell befüllt werden (`sync: false`).

## Verhalten & Fallbacks

* Fallback auf schwächere/schnellere Modelle passiert automatisch bei `429`/Rate-Limits –
  aber **nur innerhalb desselben Providers**. Wer z.B. `x-ai/grok-4-fast:free` wählt und
  Rate-Limit läuft, fällt automatisch auf DeepSeek/Llama-3.3-Free zurück.
* Der Chat streamt Live-Tokens für alle Provider (Server-Sent-Events auf `/api/ai/chat`).
* Die strukturierten Handels-Entscheidungen werden bei allen Providern per
  `response_format=json_object` erzwungen (mit sicherem Fallback für Modelle, die den
  Parameter nicht unterstützen).

## Geänderte Dateien

* `backend/services/ai_engine.py` – Multi-Provider-Engine (Gemini + OpenAI-kompatibel)
* `frontend/src/components/AITradingPanel.js` – erweiterte Modell-Auswahl
* `render.yaml` – zusätzliche ENV-Variablen für Groq / OpenRouter / Mistral

Keine neuen Dependencies nötig – der bereits vorhandene `openai` Python-Client wird für
Groq, OpenRouter und Mistral (alle OpenAI-kompatibel) wiederverwendet.
