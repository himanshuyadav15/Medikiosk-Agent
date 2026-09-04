import asyncio
import base64
import os
from typing import List, Optional
from contextlib import asynccontextmanager

import edge_tts
from fastapi import FastAPI, File, UploadFile, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq
from google import genai
from google.genai import types as genai_types

import db
import summary_generator

VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",
    "hi": "hi-IN-SwaraNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "mr": "mr-IN-AarohiNeural",
    "bn": "bn-IN-TanishaaNeural",
    "gu": "gu-IN-DhwaniNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
}

async def generate_speech(text: str, language: Optional[str] = "en") -> bytes:
    """Synthesizes text into MP3 audio bytes using Microsoft Edge Neural TTS."""
    voice = VOICE_MAP.get((language or "en").lower(), "en-IN-NeerjaNeural")
    communicate = edge_tts.Communicate(text, voice)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks)

# Load GROQ_API_KEY from a local .env file (see .env.example)
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database on startup."""
    db.init_db()
    yield


app = FastAPI(title="MediKiosk Interview Engine", lifespan=lifespan)

# Allow your frontend (running on any localhost port) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key=os.environ["GROQ_API_KEY"])
gemini_client = genai.Client(
    http_options={"api_version": "v1beta"},  # required for Live API per Google's own cookbook
    api_key=os.environ["GEMINI_API_KEY"],
)

# Check Google AI Studio's "Get code" button for the current exact name —
# note the "models/" prefix, confirmed from Google's official cookbook.
GEMINI_LIVE_MODEL = "models/gemini-3.1-flash-live-preview"

# This system prompt is the "brain" of the adaptive questioning.
# Edit this text to change how the interview behaves.
SYSTEM_PROMPT = """You are MediKiosk's clinical history-taking assistant.
You interview a patient BEFORE they see a doctor, in a warm, simple, plain-language way.

IMPORTANT — Language Selection (FIRST STEP):
- Your VERY FIRST message to the patient must ask them which language they prefer to communicate in.
- Offer common options like English, Hindi, Tamil, Telugu, Kannada, Malayalam, Marathi, Bengali, Gujarati, etc.
- Once the patient chooses a language, conduct the ENTIRE remaining interview in that language.
- If the patient picks a language you can handle, switch to it immediately and greet them in it.
- If the patient's choice is unclear, default to English.

Required fields you must collect before finishing:
- chief complaint (main problem)
- onset (when it started)
- duration
- severity (mild / moderate / severe)
- associated symptoms
- past medical history
- current medications
- known allergies

Rules:
1. Ask ONE question at a time. Keep questions short and simple (avoid medical jargon).
2. Use the patient's previous answers to decide the most relevant next question
   (e.g. if they say "chest pain", ask about radiation, breathlessness, timing next).
3. If the patient mentions a possible emergency symptom (e.g. chest pain + breathlessness,
   severe bleeding, sudden weakness/numbness, fainting), respond with exactly:
   "RED_FLAG: <short reason>" and stop asking further questions.
4. Once all required fields above are collected, respond with exactly this format:
   "SUMMARY_READY:" followed by a JSON object with keys:
   chief_complaint, onset, duration, severity, associated_symptoms,
   past_medical_history, medications, allergies.
5. Never diagnose or suggest a condition. You only collect history.
6. Always generate the SUMMARY_READY JSON in English, regardless of the interview language,
   so that doctors can read it consistently.
"""


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class InterviewRequest(BaseModel):
    history: List[ChatMessage] = []  # full conversation so far
    latest_answer: str  # the patient's newest message (from text or voice-to-text)
    speak: Optional[bool] = False  # if True, returns audio_base64 with AI speech
    language: Optional[str] = "en"  # language code for TTS voice


class InterviewResponse(BaseModel):
    reply: str
    transcribed_text: Optional[str] = None
    audio_base64: Optional[str] = None  # Base64-encoded MP3 audio of the AI's question
    # These fields are only populated when the interview ends (SUMMARY_READY)
    summary_id: Optional[int] = None
    summary_text: Optional[str] = None
    summary_json: Optional[dict] = None
    is_complete: bool = False


class TTSRequest(BaseModel):
    text: str
    language: Optional[str] = "en"


class GenerateSummaryRequest(BaseModel):
    """Force-generate a summary from raw conversation history."""
    history: List[ChatMessage]
    patient_name: str = "Unknown"
    source: str = "text"  # "text" or "voice"


class SummaryResponse(BaseModel):
    id: int
    patient_name: str
    summary_text: str
    summary_json: dict
    red_flag: Optional[str] = None
    source: str
    created_at: str


@app.get("/")
def health_check():
    return {"status": "MediKiosk interview engine is running"}


@app.get("/kiosk")
def serve_kiosk():
    return FileResponse("live_demo.html")


@app.get("/medikiosk_logo.png")
def serve_logo():
    return FileResponse("medikiosk_logo.png")


@app.post("/interview", response_model=InterviewResponse)
async def interview(req: InterviewRequest, patient_name: str = "Unknown"):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in req.history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.latest_answer})

    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.3,
            max_tokens=500,
        )
        reply = completion.choices[0].message.content
    except Exception as e:
        # TEMPORARY: surface the real error so we can debug it directly.
        reply = f"DEBUG_ERROR: {type(e).__name__}: {e}"

    # Generate speech if requested
    audio_base64 = None
    if req.speak:
        try:
            spoken = reply
            if reply.startswith("SUMMARY_READY:"):
                spoken = "Thank you, I have collected all the needed information. Your clinical summary is now ready for the doctor."
            elif reply.startswith("RED_FLAG:"):
                spoken = "Please alert clinical staff immediately."
            audio_bytes = await generate_speech(spoken, language=req.language)
            audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as e:
            print(f"TTS error in interview: {e}")

    # ── Check if interview is complete ──────────────────────────────────
    if reply.startswith("SUMMARY_READY:"):
        parsed = summary_generator.parse_summary_ready(reply)
        summary_text, summary_dict = summary_generator.generate_clinical_summary(
            parsed_data=parsed,
            patient_name=patient_name,
            red_flag=None,
            source="Text Interview",
        )
        summary_id = db.save_summary(
            patient_name=patient_name,
            summary_json=summary_dict,
            summary_text=summary_text,
            red_flag=None,
            source="text",
        )
        return InterviewResponse(
            reply=reply,
            audio_base64=audio_base64,
            summary_id=summary_id,
            summary_text=summary_text,
            summary_json=summary_dict,
            is_complete=True,
        )

    # Check for red-flag — store it but let the interview end
    red_flag_text = None
    if reply.startswith("RED_FLAG:"):
        red_flag_text = reply.replace("RED_FLAG:", "").strip()

    return InterviewResponse(reply=reply, audio_base64=audio_base64)


# ── Summary retrieval endpoints ─────────────────────────────────────────

@app.get("/summary/{summary_id}", response_model=SummaryResponse)
def get_summary(summary_id: int):
    """Fetch a single saved clinical summary by its ID."""
    row = db.get_summary(summary_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Summary not found")
    return SummaryResponse(**row)


@app.get("/summaries", response_model=List[SummaryResponse])
def list_summaries():
    """List all saved summaries (newest first) — for the Doctor Dashboard."""
    rows = db.get_all_summaries()
    return [SummaryResponse(**r) for r in rows]


@app.post("/generate-summary", response_model=InterviewResponse)
def force_generate_summary(req: GenerateSummaryRequest):
    """
    Force-generate a summary from a conversation history.
    Useful when SUMMARY_READY wasn't triggered naturally.
    Sends the full history back to the LLM with an explicit instruction
    to produce the SUMMARY_READY JSON now.
    """
    summary_prompt = (
        "You are a clinical history summarizer for MediKiosk.\n"
        "Analyze the following patient interview conversation and extract all clinical information.\n"
        "You must respond ONLY with 'SUMMARY_READY:' followed immediately by a single valid JSON object with keys:\n"
        "chief_complaint, onset, duration, severity, associated_symptoms, past_medical_history, medications, allergies.\n"
        "Rules:\n"
        "- All values must be in English.\n"
        "- Use 'Not reported' or 'None reported' for any fields not discussed.\n"
        "- Do NOT ask any questions, do NOT include conversational text, do NOT use markdown fences."
    )
    messages = [{"role": "system", "content": summary_prompt}]
    for m in req.history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({
        "role": "user",
        "content": "Generate the clinical summary JSON now.",
    })

    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.3,
            max_tokens=800,
        )
        reply = completion.choices[0].message.content
    except Exception as e:
        reply = f"DEBUG_ERROR: {type(e).__name__}: {e}"

    if "SUMMARY_READY:" in reply:
        # Extract from wherever SUMMARY_READY: appears in the reply
        idx = reply.index("SUMMARY_READY:")
        summary_part = reply[idx:]
        parsed = summary_generator.parse_summary_ready(summary_part)
        summary_text, summary_dict = summary_generator.generate_clinical_summary(
            parsed_data=parsed,
            patient_name=req.patient_name,
            red_flag=None,
            source="Text Interview" if req.source == "text" else "Voice Interview",
        )
        summary_id = db.save_summary(
            patient_name=req.patient_name,
            summary_json=summary_dict,
            summary_text=summary_text,
            red_flag=None,
            source=req.source,
        )
        return InterviewResponse(
            reply=reply,
            summary_id=summary_id,
            summary_text=summary_text,
            summary_json=summary_dict,
            is_complete=True,
        )

    return InterviewResponse(reply=reply)


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: Optional[str] = Form(None),  # e.g. "hi" for Hindi, "en" for English
):
    """
    Accepts a recorded audio clip from the browser (patient's spoken answer)
    and returns the transcribed text using Groq's Whisper model.
    The 'language' hint is optional but improves accuracy for Indian languages.
    """
    audio_bytes = await audio.read()
    try:
        transcription = client.audio.transcriptions.create(
            file=(audio.filename or "speech.webm", audio_bytes),
            model="whisper-large-v3",
            language=language if language else None,
        )
        return {"text": transcription.text}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@app.post("/interview/voice", response_model=InterviewResponse)
async def voice_interview(
    audio: UploadFile = File(...),
    history: Optional[str] = Form("[]"),       # JSON string of ChatMessage list
    patient_name: Optional[str] = Form("Unknown"),
    language: Optional[str] = Form(None),      # e.g. "hi", "en", "ta"
):
    """
    Combined voice endpoint: transcribe audio → run interview → return reply.

    The frontend sends ONE request with the audio file + conversation history,
    and gets back the AI's next question (or completed summary).

    Form fields:
      - audio:        the recorded audio file (webm/wav/mp3)
      - history:      JSON string like [{"role":"user","content":"..."},...]
      - patient_name: patient's name for the summary
      - language:     optional language hint for Whisper (e.g. "hi" for Hindi)
    """
    import json as _json

    # Step 1: Transcribe audio → text
    audio_bytes = await audio.read()
    try:
        transcription = client.audio.transcriptions.create(
            file=(audio.filename or "speech.webm", audio_bytes),
            model="whisper-large-v3",
            language=language if language else None,
        )
        transcribed_text = transcription.text
    except Exception as e:
        return InterviewResponse(reply=f"TRANSCRIPTION_ERROR: {type(e).__name__}: {e}")

    if not transcribed_text or not transcribed_text.strip():
        return InterviewResponse(reply="Could not understand the audio. Please try again.")

    # Step 2: Parse history from JSON string
    try:
        history_list = _json.loads(history)
    except _json.JSONDecodeError:
        history_list = []

    # Step 3: Run the same interview logic
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history_list:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": transcribed_text})

    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.3,
            max_tokens=500,
        )
        reply = completion.choices[0].message.content
    except Exception as e:
        reply = f"DEBUG_ERROR: {type(e).__name__}: {e}"

    # Step 4: Synthesize AI speech (AI asks questions via voice)
    audio_base64 = None
    try:
        spoken = reply
        if reply.startswith("SUMMARY_READY:"):
            spoken = "Thank you. I have collected all the needed information. Your clinical summary is now ready for the doctor."
        elif reply.startswith("RED_FLAG:"):
            spoken = "Attention. This symptom may require immediate medical attention. Clinical staff is being alerted."
        audio_bytes = await generate_speech(spoken, language=language)
        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        print(f"TTS error in voice_interview: {e}")

    # Step 5: Check for summary / red-flag
    if reply.startswith("SUMMARY_READY:"):
        parsed = summary_generator.parse_summary_ready(reply)
        summary_text, summary_dict = summary_generator.generate_clinical_summary(
            parsed_data=parsed,
            patient_name=patient_name,
            red_flag=None,
            source="Voice Interview",
        )
        summary_id = db.save_summary(
            patient_name=patient_name,
            summary_json=summary_dict,
            summary_text=summary_text,
            red_flag=None,
            source="voice",
        )
        return InterviewResponse(
            reply=reply,
            transcribed_text=transcribed_text,
            audio_base64=audio_base64,
            summary_id=summary_id,
            summary_text=summary_text,
            summary_json=summary_dict,
            is_complete=True,
        )

    return InterviewResponse(reply=reply, transcribed_text=transcribed_text, audio_base64=audio_base64)


# ── TTS Endpoints (Direct Speech Synthesis) ──────────────────────────────

@app.get("/tts")
async def get_tts(text: str, language: Optional[str] = "en"):
    """
    Synthesizes speech from text and returns an audio/mpeg stream.
    Direct browser playback: <audio src="/tts?text=...&language=en" autoplay>
    """
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        audio_bytes = await generate_speech(text, language)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tts")
async def post_tts(req: TTSRequest):
    """
    Accepts text and language in JSON body, returns an audio/mpeg stream.
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        audio_bytes = await generate_speech(req.text, req.language)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/interview")
async def gemini_live_interview(websocket: WebSocket):
    """
    Streams the patient's mic audio straight to Gemini Live and streams
    the spoken response straight back — no separate STT/TTS steps.

    Frontend contract:
    - Sends: raw 16-bit PCM audio chunks, 16kHz, mono (binary WebSocket frames)
    - Receives:
        - binary frames  -> raw 16-bit PCM audio, 24kHz, mono (play immediately)
        - text/JSON frames -> {"type": "summary", "text": "..."} or
                               {"type": "red_flag", "text": "..."}
    """
    await websocket.accept()

    # Built as a typed LiveConnectConfig object, matching Google's official cookbook,
    # rather than a plain dict — safer against SDK version differences.
    live_config = genai_types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_PROMPT,
        output_audio_transcription=genai_types.AudioTranscriptionConfig(),
        speech_config=genai_types.SpeechConfig(
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                    voice_name="Kore"
                )
            )
        ),
    )

    try:
        async with gemini_client.aio.live.connect(
            model=GEMINI_LIVE_MODEL, config=live_config
        ) as session:

            async def browser_to_gemini():
                while True:
                    chunk = await websocket.receive_bytes()
                    # The API now requires the explicit audio= parameter —
                    # the generic input={...} dict maps to a deprecated field.
                    await session.send_realtime_input(
                        audio=genai_types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                    )

            async def gemini_to_browser():
                # session.receive() only yields one turn's worth of responses,
                # then completes — must be called again for every new turn.
                while True:
                    async for response in session.receive():
                        # The SDK exposes these as flat shortcuts directly on the response —
                        # no need to dig through server_content/model_turn/parts manually.
                        if audio_data := response.data:
                            await websocket.send_bytes(audio_data)
                            continue
                        if text := response.text:
                            if "SUMMARY_READY:" in text:
                                await websocket.send_json({"type": "summary", "text": text})
                            elif "RED_FLAG:" in text:
                                await websocket.send_json({"type": "red_flag", "text": text})

            await asyncio.gather(browser_to_gemini(), gemini_to_browser())

    except WebSocketDisconnect:
        print("Patient disconnected from voice session")
    except Exception as e:
        # If the field names above don't match what your SDK version returns,
        # this print is where you'll see the real error to fix it.
        print(f"Gemini Live error: {type(e).__name__}: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
