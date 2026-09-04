"""
Full backend test — tests ALL endpoints including the new /interview/voice.
Run after starting the server: uvicorn main:app --port 8001
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import requests
import json

BASE = "http://127.0.0.1:8001"
PASS = 0
FAIL = 0

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} -- {detail}")


print("=" * 60)
print("  MediKiosk Backend — Full Test Suite")
print("=" * 60)

# ─── TEST 1: Health check ───────────────────────────────────────────
print("\n--- Test 1: Health Check (GET /) ---")
r = requests.get(f"{BASE}/")
test("Returns 200", r.status_code == 200, f"got {r.status_code}")
test("Status message present", "running" in r.json().get("status", "").lower())

# ─── TEST 2: OpenAPI docs (your friend needs this) ─────────────────
print("\n--- Test 2: API Docs (GET /docs) ---")
r = requests.get(f"{BASE}/docs")
test("Swagger docs accessible", r.status_code == 200, f"got {r.status_code}")

r = requests.get(f"{BASE}/openapi.json")
test("OpenAPI JSON accessible", r.status_code == 200, f"got {r.status_code}")
openapi = r.json() if r.status_code == 200 else {}
paths = list(openapi.get("paths", {}).keys())
print(f"    Registered endpoints: {paths}")
test("/interview registered", "/interview" in paths)
test("/interview/voice registered", "/interview/voice" in paths)
test("/transcribe registered", "/transcribe" in paths)
test("/generate-summary registered", "/generate-summary" in paths)
test("/summaries registered", "/summaries" in paths)
test("/summary/{summary_id} registered", "/summary/{summary_id}" in paths)

# ─── TEST 3: Text Interview (7-turn conversation) ──────────────────
print("\n--- Test 3: Text Interview Flow (POST /interview) ---")
history = []
patient_name = "Test Patient"
answers = [
    "English",
    "I have a bad headache",
    "It started 3 days ago",
    "It is moderate",
    "I also feel dizzy sometimes and nauseous",
    "I have diabetes, diagnosed 5 years ago",
    "I take metformin 500mg twice daily",
    "No known allergies",
]

interview_complete = False
summary_id = None

for i, answer in enumerate(answers):
    r = requests.post(f"{BASE}/interview", json={
        "history": history,
        "latest_answer": answer,
    }, params={"patient_name": patient_name})
    
    test(f"Turn {i+1} returns 200", r.status_code == 200, f"got {r.status_code}")
    data = r.json()
    reply = data["reply"]
    print(f"    Patient: {answer}")
    print(f"    AI:      {reply[:90]}{'...' if len(reply) > 90 else ''}")
    
    history.append({"role": "user", "content": answer})
    history.append({"role": "assistant", "content": reply})
    
    if data.get("is_complete"):
        interview_complete = True
        summary_id = data.get("summary_id")
        print(f"\n    >> SUMMARY_READY triggered at turn {i+1}!")
        break

    if reply.startswith("RED_FLAG:"):
        print(f"\n    >> RED FLAG triggered at turn {i+1}!")
        break

test("Interview produced AI responses", len(history) > 0)

# ─── TEST 4: Force-generate summary (if not triggered) ─────────────
if not interview_complete:
    print("\n--- Test 4: Force Generate Summary (POST /generate-summary) ---")
    r = requests.post(f"{BASE}/generate-summary", json={
        "history": history,
        "patient_name": patient_name,
        "source": "text"
    })
    test("Returns 200", r.status_code == 200, f"got {r.status_code}")
    data = r.json()
    if data.get("is_complete"):
        interview_complete = True
        summary_id = data.get("summary_id")
        test("Force summary succeeded", True)
        print(f"    Summary ID: {summary_id}")
    else:
        test("Force summary succeeded", False, f"Reply: {data.get('reply', '')[:100]}")
else:
    print("\n--- Test 4: Force Generate (SKIPPED — summary already triggered) ---")

# ─── TEST 5: Summary Output Format ─────────────────────────────────
print("\n--- Test 5: Summary Format Verification ---")
if summary_id:
    r = requests.get(f"{BASE}/summary/{summary_id}")
    test("GET /summary/{id} returns 200", r.status_code == 200)
    s = r.json()
    
    text = s.get("summary_text", "")
    test("Has 'PATIENT:' line", "PATIENT:" in text, "missing patient header")
    test("Has 'CURRENT COMPLAINT:' line", "CURRENT COMPLAINT:" in text, "missing complaint")
    test("Has 'CURRENT HISTORY' section", "CURRENT HISTORY" in text, "missing current history")
    test("Has 'PREVIOUS HISTORY' section", "PREVIOUS HISTORY" in text, "missing previous history")
    test("Has 'LABS' placeholder", "LABS" in text, "missing labs section")
    test("Has 'NEEDS VERIFICATION' placeholder", "NEEDS VERIFICATION" in text, "missing verification")
    test("Has source tag [Text Interview]", "[Text Interview]" in text, "missing source tag")
    test("Patient name in summary", patient_name in text, f"expected '{patient_name}' in text")
    
    sj = s.get("summary_json", {})
    test("JSON has chief_complaint", "chief_complaint" in sj)
    test("JSON has duration", "duration" in sj)
    test("JSON has severity", "severity" in sj)
    test("JSON has associated_symptoms", "associated_symptoms" in sj)
    test("JSON has medications", "medications" in sj)
    test("JSON has allergies", "allergies" in sj)
    
    print(f"\n    --- Summary Text ---")
    for line in text.split("\n"):
        print(f"    {line}")
    print(f"    --- End ---")
else:
    print("  [SKIP] No summary to verify")

# ─── TEST 6: Summary List ──────────────────────────────────────────
print("\n--- Test 6: List All Summaries (GET /summaries) ---")
r = requests.get(f"{BASE}/summaries")
test("Returns 200", r.status_code == 200)
summaries = r.json()
test("At least 1 summary exists", len(summaries) >= 1, f"got {len(summaries)}")
print(f"    Total summaries in database: {len(summaries)}")
for s in summaries:
    print(f"    - ID:{s['id']} | Patient:{s['patient_name']} | Source:{s['source']} | {s['created_at']}")

# ─── TEST 7: 404 on missing summary ────────────────────────────────
print("\n--- Test 7: Summary Not Found (GET /summary/99999) ---")
r = requests.get(f"{BASE}/summary/99999")
test("Returns 404", r.status_code == 404, f"got {r.status_code}")
test("Error detail present", "not found" in r.json().get("detail", "").lower())

# ─── TEST 8: Voice Interview endpoint exists ───────────────────────
print("\n--- Test 8: Voice Interview Endpoint (POST /interview/voice) ---")
# Test without audio — should get 422 (validation: missing file)
r = requests.post(f"{BASE}/interview/voice")
test("Endpoint exists (not 404)", r.status_code != 404, f"got {r.status_code}")
test("Requires audio file (422)", r.status_code == 422, f"got {r.status_code}")

# Test with a dummy wav file to verify the pipeline runs
# (Whisper may fail on dummy data, but the endpoint logic is exercised)
import struct
# Create minimal valid WAV: 16kHz, 16-bit, mono, 0.5s silence
sample_rate = 16000
num_samples = sample_rate // 2  # 0.5 seconds
wav_data = bytearray()
# WAV header
wav_data += b'RIFF'
data_size = num_samples * 2
wav_data += struct.pack('<I', 36 + data_size)
wav_data += b'WAVE'
wav_data += b'fmt '
wav_data += struct.pack('<I', 16)      # chunk size
wav_data += struct.pack('<H', 1)       # PCM
wav_data += struct.pack('<H', 1)       # mono
wav_data += struct.pack('<I', sample_rate)
wav_data += struct.pack('<I', sample_rate * 2)  # byte rate
wav_data += struct.pack('<H', 2)       # block align
wav_data += struct.pack('<H', 16)      # bits per sample
wav_data += b'data'
wav_data += struct.pack('<I', data_size)
wav_data += b'\x00' * data_size  # silence

r = requests.post(
    f"{BASE}/interview/voice",
    files={"audio": ("test.wav", bytes(wav_data), "audio/wav")},
    data={
        "history": "[]",
        "patient_name": "Voice Test Patient",
        "language": "en",
    },
)
test("Voice endpoint processes audio (200)", r.status_code == 200, f"got {r.status_code}")
if r.status_code == 200:
    vdata = r.json()
    reply = vdata.get("reply", "")
    # It may be a transcription error (silent audio) or an actual AI reply
    if "TRANSCRIPTION_ERROR" in reply:
        print(f"    Transcription failed on silent audio (expected): {reply[:80]}")
        test("Error handled gracefully", True)
    elif "Could not understand" in reply:
        print(f"    Empty transcription handled: {reply}")
        test("Empty audio handled gracefully", True)
    else:
        print(f"    AI reply to voice: {reply[:90]}")
        test("Got AI response from voice input", True)

# ─── TEST 9: Transcribe endpoint standalone ─────────────────────────
print("\n--- Test 9: Transcribe Endpoint (POST /transcribe) ---")
r = requests.post(f"{BASE}/transcribe")
test("Endpoint exists (not 404)", r.status_code != 404, f"got {r.status_code}")
test("Requires audio file (422)", r.status_code == 422, f"got {r.status_code}")

# ─── RESULTS ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  RESULTS: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
print("=" * 60)

if FAIL == 0:
    print("\n  ALL TESTS PASSED!")
else:
    print(f"\n  {FAIL} test(s) need attention.")
