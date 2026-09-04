"""
Quick manual test for the /interview endpoint.
Run this AFTER starting the server (uvicorn main:app --reload).
"""
import requests

URL = "http://127.0.0.1:8000/interview"

history = []

print("=" * 50)
print("  MediKiosk — Clinical Interview Test")
print("=" * 50)

patient_name = input("\nPatient name: ").strip() or "Unknown"
print(f"\nStarting interview for: {patient_name}")
print("Type 'quit' to stop.\n")

first_message = input("You (patient): ")

while True:
    if first_message.lower() == "quit":
        break

    response = requests.post(
        URL,
        json={
            "history": history,
            "latest_answer": first_message,
        },
        params={"patient_name": patient_name},
    )

    if response.status_code != 200:
        print(f"\n[ERROR] Server returned status {response.status_code}")
        print(response.text)
        break

    data = response.json()
    reply = data["reply"]

    print(f"\nAI: {reply}\n")

    # Save this turn into the conversation history
    history.append({"role": "user", "content": first_message})
    history.append({"role": "assistant", "content": reply})

    # ── Check if the interview is complete ──────────────────────────────
    if data.get("is_complete"):
        print("\n" + "=" * 50)
        print("  📋 CLINICAL SUMMARY GENERATED")
        print("=" * 50)
        print()
        print(data["summary_text"])
        print()
        print(f"💾 Summary saved with ID: {data['summary_id']}")
        print(f"   Retrieve it anytime: GET /summary/{data['summary_id']}")
        print("=" * 50)
        break

    if reply.startswith("RED_FLAG:"):
        print("🚨 --- RED FLAG DETECTED — Interview ended ---")
        break

    first_message = input("You (patient): ")
